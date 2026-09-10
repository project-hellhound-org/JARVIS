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
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Tuple

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

class SystemCommander:
    def __init__(self):
        self.task_manager: TaskManager = get_task_manager()
        self._recent_tasks: Dict[str, Tuple[float, Task]] = {}

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

        # Graceful git chaining: If git commit is chained before git push, guard against clean working tree exit code 1
        exec_cmd = cmd
        if "git commit" in exec_cmd and ("&&" in exec_cmd or ";" in exec_cmd):
            exec_cmd = re.sub(r'git\s+commit\s+([^&|;]+)', r'(git diff --cached --quiet || git commit \1)', exec_cmd)

        try:
            process = subprocess.Popen(
                exec_cmd,
                shell=True,
                executable="/bin/bash",
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

            return {
                "success": exit_code == 0,
                "command": cmd,
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
        cwd: Optional[str] = None
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

            # Closed-Loop Agentic Feedback: notify registered listener (JARVIS loop)
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
