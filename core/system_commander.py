# core/system_commander.py
"""
J.A.R.V.I.S. Autonomous System Commander ("JARVIS" Engine).
Executes shell commands, native security tools, diagnostics, and scripts
with real-time output capture, safety guardrails, and TaskSurface integration.
"""

import os
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Tuple, List

from core.task import Task, TaskType, TaskFinding, TaskState
from core.task_manager import get_task_manager, TaskManager

# Catastrophic destructive patterns that must never be executed automatically
_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+/(?:\s|$|\*)", re.I),
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+/\w+", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bdd\s+if=.*?of=/dev/([shn]|nvme)", re.I),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.I),  # Fork bomb
    re.compile(r"\bchmod\s+-[a-zA-Z]*R\s+777\s+/(?:\s|$)", re.I),
    re.compile(r">\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|hd[a-z]|loop\d+)", re.I),
    re.compile(r"\bshutdown\b|\breboot\b|\binit\s+0\b", re.I),
]

# ── Risk classification (used by core/agents/system_ops_agent.py) ──────────
# Broader than _DESTRUCTIVE_PATTERNS: those are blocked outright; these merely
# require the operator's confirmation before running. Errs on the side of
# DESTRUCTIVE -- a false positive costs one "yes", a false negative can't be undone.
_CMD_START = r"(?:^|[;&|`(\n{]|\$\()\s*(?:(?:sudo|doas|nohup|exec|xargs|time|env|command)\b[^;&|\n]*?\s+)?"
_SYSTEM_PATHS = (
    r"(?:/(?:etc|usr|bin|sbin|boot|lib|lib32|lib64|var|opt|sys|proc|dev|root|System|Library|Applications)(?:/|\b)"
    r"|[A-Za-z]:[\\/](?:Windows|Program Files|Program Files \(x86\)|ProgramData)\b"
    r"|/\s*$|/\s+)"
)
_RISK_PATTERNS = [
    # Deletion
    re.compile(_CMD_START + r"(?:rm|rmdir|unlink|shred|srm|wipe|del|erase|rd|Remove-Item|trash|trash-put|gio\s+trash)\b", re.I),
    re.compile(r"\bfind\b[^;&|\n]*\s-(?:delete\b|exec(?:dir)?\s+(?:rm|shred|unlink)\b)", re.I),
    re.compile(r"\bgit\s+(?:rm\b|clean\s+-[a-zA-Z]*f|reset\s+--hard|push\b[^;&|\n]*(?:--force\b|\s-f\b)|branch\s+-D\b|stash\s+(?:drop|clear)\b)", re.I),
    re.compile(r"\btruncate\b|>\s*/dev/(?!null\b)", re.I),
    # Moves into (or onto) system paths, or into the void
    re.compile(_CMD_START + r"(?:mv|move|Move-Item)\b[^;&|\n]*\s['\"]?(?:" + _SYSTEM_PATHS + r"|/dev/null\b)", re.I),
    re.compile(_CMD_START + r"(?:cp|copy|Copy-Item|install|ln)\b[^;&|\n]*\s['\"]?" + _SYSTEM_PATHS, re.I),
    re.compile(r"\b(?:chmod|chown|chgrp)\b[^;&|\n]*\s-[a-zA-Z]*R\b", re.I),
    # Process killing
    re.compile(_CMD_START + r"(?:kill|pkill|killall|xkill|skill|taskkill|Stop-Process|spps)\b", re.I),
    # Power state / services
    re.compile(_CMD_START + r"(?:shutdown|reboot|poweroff|halt|Restart-Computer|Stop-Computer|logoff)\b", re.I),
    re.compile(r"\binit\s+[06]\b|\btelinit\s+[06]\b", re.I),
    re.compile(r"\b(?:systemctl|loginctl)\s+(?:poweroff|reboot|halt|suspend|hibernate|kexec|stop|disable|mask|kill|terminate-session)\b", re.I),
    re.compile(r"\b(?:service\s+\S+\s+stop|launchctl\s+(?:unload|remove|bootout))\b", re.I),
    # Disks / filesystems
    re.compile(_CMD_START + r"(?:mkfs(?:\.\w+)?|fdisk|sfdisk|gdisk|parted|wipefs|diskpart|format|Format-Volume|Clear-Disk)\b", re.I),
    re.compile(r"\bdd\b[^;&|\n]*\bof=", re.I),
    # Package / account removal
    re.compile(r"\b(?:apt(?:-get)?|dnf|yum|zypper|pacman|brew|snap|winget|choco|pip3?|npm|conda)\s+(?:remove|purge|uninstall|autoremove|-R\w*)\b", re.I),
    re.compile(_CMD_START + r"(?:userdel|deluser|groupdel|passwd)\b", re.I),
    re.compile(r"\bcrontab\s+-r\b", re.I),
]


def classify_risk(cmd: str):
    """Classify a shell command as RiskTier.DESTRUCTIVE or RiskTier.REVERSIBLE.

    DESTRUCTIVE: deletion, process killing, shutdown/reboot, moving files into
    system paths, disk/partition tools, package/user removal -- and anything the
    hard safety guard (_DESTRUCTIVE_PATTERNS) would block. Everything else is
    REVERSIBLE. Empty commands are treated as DESTRUCTIVE (never auto-run).
    """
    from core.agents.base_agent import RiskTier  # local import: avoids a core <-> core.agents cycle

    text = (cmd or "").strip()
    if not text:
        return RiskTier.DESTRUCTIVE
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat.search(text):
            return RiskTier.DESTRUCTIVE
    for pat in _RISK_PATTERNS:
        if pat.search(text):
            return RiskTier.DESTRUCTIVE
    return RiskTier.REVERSIBLE


_POSIX_SHELL = "/bin/bash"
# Directories whose bash.exe is the WSL launcher, not a native bash: running a
# command through it would execute inside the Linux VM, not on this machine.
_WSL_BASH_DIRS = ("\\windows\\system32", "\\windows\\sysnative", "\\microsoft\\windowsapps")


def _is_wsl_bash(path: str) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    return any(d in p for d in _WSL_BASH_DIRS)


def _find_windows_bash() -> Optional[str]:
    """Locate a native bash (Git Bash / MSYS2 / Cygwin) on Windows, never WSL's."""
    import shutil

    # Prefer Git for Windows' bin\bash.exe: unlike usr\bin\bash.exe it puts the
    # MSYS coreutils (ls, df, ps, ...) on PATH even when they aren't already.
    git = shutil.which("git")
    if git:
        # git.exe lives in <root>\cmd, <root>\bin or <root>\mingw64\bin.
        for git_root in Path(git).resolve().parents[:3]:
            for cand in (git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"):
                if cand.is_file() and not _is_wsl_bash(str(cand)):
                    return str(cand)

    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        cand = os.path.join(d, "bash.exe")
        if os.path.isfile(cand) and not _is_wsl_bash(cand):
            return cand

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for rel in (("Git", "bin", "bash.exe"), ("Programs", "Git", "bin", "bash.exe")):
            cand = os.path.join(base, *rel)
            if os.path.isfile(cand):
                return cand
    return None


def _is_windows() -> bool:
    return os.name == "nt"


_shell_cache: Dict[str, Optional[str]] = {}


def resolve_shell_executable() -> Optional[str]:
    """Shell for subprocess.Popen(shell=True, executable=...).

    POSIX: always /bin/bash (unchanged behaviour). Windows: a native bash if one
    is installed, else None so Popen falls back to the default shell (cmd.exe).
    """
    if not _is_windows():
        return _POSIX_SHELL
    if "nt" not in _shell_cache:
        _shell_cache["nt"] = _find_windows_bash()
    return _shell_cache["nt"]


def _shell_popen_args(cmd: str) -> Tuple[Any, Dict[str, Any]]:
    """Return (args, kwargs) for subprocess.Popen to run `cmd` in a shell.

    POSIX: (cmd, shell=True, executable=/bin/bash) -- exactly as before.
    Windows + bash: Popen(shell=True) would build `<exe> /c "<cmd>"`, which is
    cmd.exe syntax bash rejects, so pass an explicit argv [bash, -c, cmd].
    Windows without bash: (cmd, shell=True) -- the default shell, cmd.exe.
    """
    shell = resolve_shell_executable()
    if not _is_windows():
        return cmd, {"shell": True, "executable": shell}
    if shell:
        return [shell, "-c", cmd], {"shell": False}
    return cmd, {"shell": True}


class SystemCommander:
    def __init__(self):
        self.task_manager: TaskManager = get_task_manager()
        self._recent_tasks: Dict[str, Tuple[float, Task]] = {}
        self.last_affected_file: Optional[str] = None
        self.recent_files: List[Dict[str, Any]] = []

    def _extract_and_track_files(self, cmd: str, work_dir: str):
        """Extract created or modified files from command and update session file memory."""
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            normalized_cmd = re.sub(r'\$\(\s*date\s+\+[%A-Za-z0-9_-]+\s*\)', today_str, cmd)

            # 1. Check touch / creation: touch <path>
            m_touch = re.findall(r'\btouch\s+([^\s;&|]+)', normalized_cmd)
            for target in m_touch:
                target = os.path.expanduser(target.strip("'\""))
                if not os.path.isabs(target):
                    target = os.path.abspath(os.path.join(work_dir, target))
                self.last_affected_file = target
                self.recent_files.append({"path": target, "action": "create", "time": time.time()})

            # 2. Check redirection: > <path> or >> <path>
            m_redir = re.findall(r'(?:>{1,2})\s*([^\s;&|]+)', normalized_cmd)
            for target in m_redir:
                target = os.path.expanduser(target.strip("'\""))
                if not os.path.isabs(target):
                    target = os.path.abspath(os.path.join(work_dir, target))
                self.last_affected_file = target
                self.recent_files.append({"path": target, "action": "write", "time": time.time()})

            # 3. Check mv <src> <dest>
            m_mv = re.search(r'\bmv\s+(?:-[a-zA-Z]+\s+)?([^\s;&|]+)\s+([^\s;&|]+)', normalized_cmd)
            if m_mv:
                dest = os.path.expanduser(m_mv.group(2).strip("'\""))
                if not os.path.isabs(dest):
                    if self.last_affected_file and os.path.dirname(self.last_affected_file):
                        dest = os.path.abspath(os.path.join(os.path.dirname(self.last_affected_file), dest))
                    else:
                        dest = os.path.abspath(os.path.join(work_dir, dest))
                self.last_affected_file = dest
                self.recent_files.append({"path": dest, "action": "rename", "time": time.time()})
        except Exception as e:
            print(f"[SystemCommander] File tracking notice: {e}")

    @staticmethod
    def is_safe(cmd: str) -> Tuple[bool, str]:
        """Check if command passes safety guardrails."""
        clean_cmd = cmd.strip()
        if not clean_cmd:
            return False, "Empty command"
        
        for pat in _DESTRUCTIVE_PATTERNS:
            if pat.search(clean_cmd):
                return False, f"Blocked potentially destructive system command: pattern match '{pat.pattern}'"
        
        return True, "Safe"

    def execute(
        self,
        cmd: str,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        """
        Execute command synchronously or with streaming line callback.
        Returns execution result dictionary.
        """
        safe, reason = self.is_safe(cmd)
        if not safe:
            return {
                "success": False,
                "command": cmd,
                "exit_code": -1,
                "stdout": "",
                "stderr": reason,
                "elapsed_sec": 0.0,
                "error": reason,
            }

        work_dir = cwd or str(Path(__file__).parent.parent.resolve())
        start_time = time.time()
        stdout_lines = []
        stderr_lines = []

        # Auto-heal missing source files in `mv` or `>>` if referring to recently created document
        exec_cmd = cmd
        if self.last_affected_file and os.path.exists(self.last_affected_file):
            m_mv = re.search(r'\bmv\s+(?:-[a-zA-Z]+\s+)?([^\s;&|]+)\s+([^\s;&|]+)', exec_cmd)
            if m_mv:
                src_raw = m_mv.group(1).strip("'\"")
                src_path = os.path.expanduser(src_raw)
                src_full = src_path if os.path.isabs(src_path) else os.path.join(work_dir, src_path)
                if not os.path.exists(src_full):
                    generic_names = ["notebook.txt", "notebook.md", "note.txt", "notes.txt", "notes.md", "today.md", "file.txt"]
                    if (src_raw.lower() in generic_names or
                        src_raw.lower() in os.path.basename(self.last_affected_file).lower() or
                        "notebook" in src_raw.lower() or
                        "note" in src_raw.lower()):
                        dest_raw = m_mv.group(2).strip("'\"")
                        dest_path = os.path.expanduser(dest_raw)
                        if not os.path.isabs(dest_path):
                            dest_path = os.path.join(os.path.dirname(self.last_affected_file), dest_path)
                        healed_mv = f"mv {shlex.quote(self.last_affected_file)} {shlex.quote(dest_path)}"
                        exec_cmd = exec_cmd.replace(m_mv.group(0), healed_mv)
                        print(f"[SystemCommander] Auto-healed command: `{m_mv.group(0)}` -> `{healed_mv}`")

        # Graceful git chaining: If git commit is chained before git push, guard against clean working tree exit code 1
        if "git commit" in exec_cmd and ("&&" in exec_cmd or ";" in exec_cmd):
            exec_cmd = re.sub(r'git\s+commit\s+([^&|;]+)', r'(git diff --cached --quiet || git commit \1)', exec_cmd)

        try:
            popen_args, shell_kwargs = _shell_popen_args(exec_cmd)
            process = subprocess.Popen(
                popen_args,
                **shell_kwargs,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=work_dir,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            # Read output lines in real-time
            def _reader(pipe, dest_list, is_err=False):
                try:
                    for line in iter(pipe.readline, ""):
                        if line:
                            dest_list.append(line)
                            if on_output:
                                prefix = "[stderr] " if is_err else ""
                                on_output(f"{prefix}{line.rstrip()}")
                except Exception:
                    pass
                finally:
                    pipe.close()

            t_out = threading.Thread(target=_reader, args=(process.stdout, stdout_lines, False), daemon=True)
            t_err = threading.Thread(target=_reader, args=(process.stderr, stderr_lines, True), daemon=True)
            t_out.start()
            t_err.start()

            # Wait for completion or timeout
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                stderr_lines.append(f"\n[Command timed out after {timeout} seconds]")

            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)

            elapsed = round(time.time() - start_time, 2)
            full_stdout = "".join(stdout_lines)
            full_stderr = "".join(stderr_lines)
            exit_code = process.returncode
            if exit_code == 0:
                self._extract_and_track_files(exec_cmd, work_dir)

            return {
                "success": exit_code == 0,
                "command": exec_cmd,
                "exit_code": exit_code,
                "stdout": full_stdout,
                "stderr": full_stderr,
                "elapsed_sec": elapsed,
                "error": full_stderr if exit_code != 0 and not full_stdout else None,
            }

        except Exception as e:
            elapsed = round(time.time() - start_time, 2)
            return {
                "success": False,
                "command": cmd,
                "exit_code": -1,
                "stdout": "".join(stdout_lines),
                "stderr": str(e),
                "elapsed_sec": elapsed,
                "error": str(e),
            }

    def run_as_task(
        self,
        cmd: str,
        title: Optional[str] = None,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        skip_debrief: bool = False
    ) -> Task:
        """
        Spawns a TERMINAL_COMMAND task and runs execution asynchronously in a background thread.
        Updates task progress with live output and completes it upon process exit.
        """
        cmd_key = cmd.strip()
        now = time.time()
        if hasattr(self, '_recent_tasks') and cmd_key in self._recent_tasks:
            prev_time, prev_task = self._recent_tasks[cmd_key]
            if (now - prev_time) < 4.0:
                print(f"[SystemCommander] Debounced duplicate command task within 4s: {cmd_key}")
                return prev_task

        cmd_lower = cmd.lower()
        is_traffic_query = any(k in cmd_lower for k in ["openstreetmap.org", "tomtom.com", "traffic/services", "/map?bbox="])

        task_data = {"command": cmd, "output_lines": []}
        if is_traffic_query:
            task_type = TaskType.TRAFFIC_INTEL.value
            task_title = title or "Traffic & GIS Telemetry"
            m_bbox = re.search(r'bbox=([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)', cmd)
            if m_bbox:
                minlon, minlat, maxlon, maxlat = map(float, m_bbox.groups())
                task_data["bbox"] = [minlon, minlat, maxlon, maxlat]
                task_data["lat"] = round((minlat + maxlat) / 2, 4)
                task_data["lon"] = round((minlon + maxlon) / 2, 4)
            m_pt = re.search(r'point=([0-9.]+),([0-9.]+)', cmd)
            if m_pt:
                lat, lon = map(float, m_pt.groups())
                task_data["lat"] = lat
                task_data["lon"] = lon
                task_data["bbox"] = [round(lon - 0.05, 4), round(lat - 0.04, 4), round(lon + 0.05, 4), round(lat + 0.04, 4)]
        else:
            task_type = TaskType.TERMINAL_COMMAND.value
            task_title = title or f"Command: {cmd[:30]}"

        task = self.task_manager.create_task(
            type_=task_type,
            title=task_title,
            data=task_data
        )
        self._recent_tasks[cmd_key] = (now, task)


        def _worker():
            self.task_manager.update_progress(task.task_id, 10, f"Executing: {cmd[:40]}...")
            
            output_buffer = []
            last_progress_time = [0.0]

            def _on_line(line: str):
                output_buffer.append(line)
                # Keep last 150 lines
                if len(output_buffer) > 150:
                    output_buffer.pop(0)
                
                # Rate limit UI progress events to at most once every 150ms to prevent flooding webview
                now = time.time()
                if now - last_progress_time[0] >= 0.15:
                    last_progress_time[0] = now
                    recent_tail = "\n".join(output_buffer[-15:])
                    self.task_manager.update_progress(
                        task.task_id,
                        50,
                        message=recent_tail
                    )

            res = self.execute(cmd, timeout=timeout, cwd=cwd, on_output=_on_line)
            
            # Store full command results in task data
            task_extra = {
                "exit_code": res["exit_code"],
                "elapsed_sec": res["elapsed_sec"],
                "stdout": res["stdout"],
                "stderr": res["stderr"],
                "success": res["success"],
            }

            if is_traffic_query:
                try:
                    from modules.maps_nav import MapsNavigationEngine
                    nav = MapsNavigationEngine()
                    tloc = "Kotagiri"
                    if "kotagiri" in cmd_lower:
                        tloc = "Kotagiri"
                    elif "coonoor" in cmd_lower:
                        tloc = "Coonoor"
                    elif "ooty" in cmd_lower:
                        tloc = "Ooty"
                    tdata = nav.get_traffic_intel(tloc)
                    if task_data.get("lat") and task_data.get("lon"):
                        tdata["lat"] = task_data["lat"]
                        tdata["lon"] = task_data["lon"]
                    if task_data.get("bbox"):
                        tdata["bbox"] = task_data["bbox"]
                        tdata["osm_embed_url"] = f"https://www.openstreetmap.org/export/embed.html?bbox={tdata['bbox'][0]},{tdata['bbox'][1]},{tdata['bbox'][2]},{tdata['bbox'][3]}&layer=mapnik&marker={tdata['lat']},{tdata['lon']}"
                    task_extra.update(tdata)
                except Exception as ex:
                    print(f"[SystemCommander] Traffic metadata attachment warning: {ex}")

            # Add finding for output
            self.task_manager.add_finding(task.task_id, TaskFinding(
                title=f"{'Traffic Telemetry' if is_traffic_query else 'Exit'} {res['exit_code']} ({res['elapsed_sec']}s)",
                snippet=res["stdout"][:500] if res["stdout"] else res["stderr"][:500],
                source="traffic_intel" if is_traffic_query else "terminal",
                extra=task_extra
            ))


            summary = f"Process finished with exit code {res['exit_code']} in {res['elapsed_sec']}s"
            if res["success"]:
                self.task_manager.complete_task(task.task_id, summary=summary, extra_data=task_extra)
            else:
                self.task_manager.fail_task(task.task_id, error_msg=res.get("error") or summary)

            # Closed-Loop Agentic Feedback: notify registered listener (JARVIS loop) unless skipped
            if not skip_debrief:
                cb = getattr(self, '_debrief_callback', None)
                if callable(cb):
                    try:
                        cb(cmd, res, task.task_id)
                    except Exception as e:
                        print(f"[SystemCommander] Debrief callback error: {e}")

        threading.Thread(target=_worker, daemon=True).start()
        return task

    def set_debrief_callback(self, callback: Optional[Callable[[str, Dict[str, Any], str], None]]):
        """Register a callback for closed-loop tool output debriefing."""
        self._debrief_callback = callback

    # Convenient alias
    execute_async = run_as_task

_commander_instance = None

def get_system_commander() -> SystemCommander:
    global _commander_instance
    if _commander_instance is None:
        _commander_instance = SystemCommander()
    return _commander_instance
