# core/agents/system_ops_agent.py
"""SystemOpsAgent -- runs system tasks for the operator, matched to their OS.

* OS-matched commands: PowerShell on Windows (run through SystemCommander via
  ``powershell -EncodedCommand`` so quoting can't break), bash on Linux/macOS.
* Natural phrasing: known intents are matched instantly; anything else is translated
  by the LLM (supervisor's ``llm_chat``) and ALWAYS shown for confirmation first.
* Plain-word explanations of output ("your C: drive is 92% full, mostly in Downloads").
* Deletion goes to the Recycle Bin (Windows) / Trash (Linux, macOS) and can be undone;
  drives without a recycle bin are refused rather than deleted permanently.
* Protected paths are never touched: system folders, drive roots, your profile root,
  and JARVIS's own folder.
* Apps and windows (open / close / switch), volume, brightness, Wi-Fi, Bluetooth.
* Multi-step jobs ("clean up temp files and tell me how much I freed").
* A background watcher warns about CPU spikes, low disk, low memory and hung apps.
* Risky actions show the exact command before running; "dry run ..." shows without running.
* Every command has a timeout; "cancel that" kills the command JARVIS started.

DESTRUCTIVE actions are never run here: they come back as ``needs_confirmation``.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import system_commander
from core.agents.base_agent import AgentResult, BaseAgent, RiskTier, ToolSpec

_SUMMARY_MAX_CHARS = 200
ROOT = Path(__file__).resolve().parent.parent.parent


def _detect_wsl() -> bool:
    """True when this Linux process is running under WSL on a Windows host."""
    if os.name == "nt":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except Exception:
        return False


IS_WSL = _detect_wsl()

# Under WSL the operator's drives, files and applications all belong to the Windows
# host, so commands are PLANNED for Windows and run through interop. Native Win32
# calls (ctypes: the Recycle Bin, hung-window detection) are a separate question --
# this is still a Linux process and cannot make them -- so those keep their own
# ``os.name == "nt"`` guards and correctly fall back to the POSIX implementations.
CURRENT_PLATFORM = "windows" if (os.name == "nt" or IS_WSL) else ("mac" if sys.platform == "darwin" else "linux")

_WSL_MOUNT_RE = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")
_WIN_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_WIN_PATH_IN_TEXT_RE = re.compile(r"\b([A-Za-z]):\\([^\s\"']*)")


def to_windows_path(p: str) -> str:
    """/mnt/f/Projects -> F:\\Projects. Anything else is returned unchanged."""
    m = _WSL_MOUNT_RE.match(str(p or ""))
    if not m:
        return str(p or "")
    rest = (m.group(2) or "").lstrip("/").replace("/", "\\")
    return f"{m.group(1).upper()}:\\{rest}"


def to_wsl_path(p: str) -> str:
    """F:\\Projects -> /mnt/f/Projects. Anything else is returned unchanged."""
    m = _WIN_DRIVE_RE.match(str(p or ""))
    if not m:
        return str(p or "")
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")


def _wsl_normalise_text(text: str) -> str:
    """Rewrite every Windows-style path inside a command string to its WSL form, so
    one spelling can be compared against the protected list."""
    return _WIN_PATH_IN_TEXT_RE.sub(
        lambda m: f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/"), text or "")


def _wsl_drive_mounts() -> List[str]:
    """Drive letters currently mounted under /mnt (c, d, e, ...)."""
    try:
        return sorted(e.name for e in os.scandir("/mnt")
                      if e.is_dir() and len(e.name) == 1 and e.name.isalpha())
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# Legacy POSIX translation (kept for compatibility; the agent uses the OS-aware planner)
# ═══════════════════════════════════════════════════════════════
def _q(arg: str) -> str:
    return shlex.quote(arg.strip().strip("'\"`"))


def _clean_target(raw: str) -> str:
    return re.sub(r"^(?:the|my|this|that)\s+", "", (raw or "").strip().rstrip("?.!"), flags=re.I).strip()


_INTENTS = [
    (re.compile(r"\b(?:run|execute|exec)\s*(?:the\s+)?(?:command\s*)?[:]?\s*`([^`]+)`", re.I), lambda m: m.group(1).strip()),
    (re.compile(r"\b(?:run|execute)\s+(?:the\s+)?command\s*:?\s+(.+)$", re.I), lambda m: m.group(1).strip().strip("'\"")),
    (re.compile(r"\b(?:run|execute)\s*:\s*(.+)$", re.I), lambda m: m.group(1).strip().strip("'\"")),
    (re.compile(r"\b(?:shut\s*down|power\s+off|turn\s+off)\b(?:\s+(?:the|my)?\s*(?:computer|machine|pc|system|laptop))?", re.I), lambda m: "shutdown now"),
    (re.compile(r"\b(?:reboot|restart\s+(?:the|my)?\s*(?:computer|machine|pc|system|laptop))\b", re.I), lambda m: "reboot"),
    (re.compile(r"\b(?:kill|stop|end|terminate)\s+(?:the\s+)?(?:process\s+)?(?:pid\s+)?(\d+)\b", re.I), lambda m: f"kill {m.group(1)}"),
    (re.compile(r"\b(?:kill|terminate|force\s+quit|end)\s+(?:the\s+)?(?:process\s+|app\s+)?([\w.\-]+)", re.I),
     lambda m: f"pkill -f {_q(m.group(1))}"),
    (re.compile(r"\b(?:delete|remove|erase)\s+(?:the\s+)?(?:folder|directory|dir)\s+(.+)$", re.I),
     lambda m: f"rm -r {_q(_clean_target(m.group(1)))}"),
    (re.compile(r"\b(?:delete|remove|erase)\s+(?:the\s+)?(?:file\s+)?(.+)$", re.I),
     lambda m: f"rm {_q(_clean_target(m.group(1)))}"),
    (re.compile(r"\b(?:make|create|new)\s+(?:a\s+)?(?:folder|directory|dir)\s+(?:called\s+|named\s+)?(.+)$", re.I),
     lambda m: f"mkdir -p {_q(_clean_target(m.group(1)))}"),
    (re.compile(r"\b(?:make|create|new)\s+(?:a\s+|an\s+)?(?:empty\s+)?file\s+(?:called\s+|named\s+)?(.+)$", re.I),
     lambda m: f"touch {_q(_clean_target(m.group(1)))}"),
    (re.compile(r"\b(?:disk\s+(?:space|usage)|free\s+space|storage\s+left)\b", re.I), lambda m: "df -h"),
    (re.compile(r"\b(?:memory|ram)\s+(?:usage|use|left|free)\b|\bfree\s+memory\b", re.I), lambda m: "free -h"),
    (re.compile(r"\buptime\b|\bhow\s+long\s+(?:has\s+)?(?:the\s+)?(?:system|machine|computer)\s+been\s+(?:up|on|running)\b", re.I), lambda m: "uptime"),
    (re.compile(r"\b(?:running\s+processes|top\s+processes|what'?s\s+running|list\s+processes)\b", re.I),
     lambda m: "ps aux --sort=-%cpu | head -n 15"),
    (re.compile(r"\b(?:current|working)\s+directory\b|\bwhere\s+am\s+i\b", re.I), lambda m: "pwd"),
    (re.compile(r"\bgit\s+status\b", re.I), lambda m: "git status --short --branch"),
    (re.compile(r"\blist\s+(?:the\s+)?files(?:\s+in\s+(.+))?$", re.I),
     lambda m: f"ls -la {_q(_clean_target(m.group(1)))}" if m.group(1) else "ls -la"),
]


def goal_to_command(goal: str) -> Optional[str]:
    """Legacy POSIX-only translation (Milestone 2). The agent itself uses ``plan_ops``."""
    text = (goal or "").strip()
    for pattern, build in _INTENTS:
        m = pattern.search(text)
        if m:
            return build(m) or None
    return None


def _brief_output(res: Dict[str, Any]) -> str:
    out = (res.get("stdout") or "").strip() or (res.get("stderr") or "").strip()
    lines = [l.strip() for l in out.splitlines() if l.strip()][:3]
    brief = " | ".join(lines)
    if len(brief) > _SUMMARY_MAX_CHARS:
        brief = brief[: _SUMMARY_MAX_CHARS - 1] + "…"
    return brief


# ═══════════════════════════════════════════════════════════════
# Protected paths
# ═══════════════════════════════════════════════════════════════
def _norm(p: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(p)))).rstrip("\\/")
    except Exception:
        return str(p)


def _protected_sets() -> Tuple[List[str], List[str]]:
    """(trees: nothing inside may be touched, exact: the folder itself may not be touched)."""
    home = Path.home()
    trees: List[Path] = []
    exact: List[Path] = [home, home.parent, home / "Desktop", home / "Documents", home / "Downloads",
                         home / "Pictures", home / "Music", home / "Videos", home / "AppData",
                         home / "AppData" / "Local", home / "AppData" / "Roaming"]
    jarvis_root = ROOT.parent if ROOT.parent.name.lower() == "jarvis" and len(ROOT.parent.parts) > 1 else ROOT
    trees.append(jarvis_root)
    if os.name == "nt":
        sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        trees += [sysroot, Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                  Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
                  Path(os.environ.get("ProgramData", r"C:\ProgramData"))]
        import string
        for d in string.ascii_uppercase:
            exact.append(Path(f"{d}:\\"))
            trees += [Path(f"{d}:\\$Recycle.Bin"), Path(f"{d}:\\System Volume Information")]
    else:
        trees += [Path(p) for p in ("/bin", "/sbin", "/usr", "/etc", "/boot", "/lib", "/lib32", "/lib64", "/var",
                                    "/sys", "/proc", "/dev", "/opt", "/root", "/System", "/Library", "/Applications",
                                    "/private")]
        exact += [Path("/"), Path("/home"), Path("/Users")]
        if IS_WSL:
            # The Windows host's own locations, as WSL sees them. Without these a
            # Windows-style command could reach C:\Windows or a drive root unchecked.
            exact.append(Path("/mnt"))
            for d in _wsl_drive_mounts():
                exact.append(Path(f"/mnt/{d}"))
                trees += [Path(f"/mnt/{d}/Windows"), Path(f"/mnt/{d}/Program Files"),
                          Path(f"/mnt/{d}/Program Files (x86)"), Path(f"/mnt/{d}/ProgramData"),
                          Path(f"/mnt/{d}/$Recycle.Bin"), Path(f"/mnt/{d}/System Volume Information")]
    return [_norm(p) for p in trees], [_norm(p) for p in exact]


def protected_reason(path: str) -> Optional[str]:
    """Why a path must never be touched, or None if it's fair game."""
    # Under WSL the same folder has two spellings; compare in the WSL one.
    p = _norm(to_wsl_path(path) if IS_WSL else path)
    trees, exact = _protected_sets()
    for t in trees:
        if p == t or p.startswith(t + os.sep):
            jarvis = _norm(ROOT.parent if ROOT.parent.name.lower() == "jarvis" else ROOT)
            if t == jarvis:
                return "it's part of JARVIS itself"
            return "it's a protected system location"
    if p in exact:
        return "it's a top-level folder (drive root or your profile folder)"
    return None


def _mentions_protected(command: str) -> Optional[str]:
    if os.name == "nt":
        probe, fold = (command or "").lower().replace("/", "\\"), True
    elif IS_WSL:
        # A Windows-planned command spells paths F:\..., the protected list /mnt/f/...
        probe, fold = _wsl_normalise_text(command or "").lower(), True
    else:
        probe, fold = (command or ""), False
    trees, exact = _protected_sets()
    # Longest first, and only on a path boundary: a bare substring test reports
    # "/sys" for a command mentioning C:\Windows\System32.
    for t in sorted((t for t in trees if t), key=len, reverse=True):
        needle = t.lower() if fold else t
        start = probe.find(needle)
        while start != -1:
            after = probe[start + len(needle):start + len(needle) + 1]
            if after in ("", "/", "\\", "'", '"', " ", "\t"):
                return t
            start = probe.find(needle, start + 1)
    return None


# ═══════════════════════════════════════════════════════════════
# Ops
# ═══════════════════════════════════════════════════════════════
@dataclass
class Op:
    """One planned action. ``kind`` is 'shell' (run via SystemCommander) or 'python'."""
    kind: str
    display: str                       # what the operator sees ("the exact command")
    tier: RiskTier = RiskTier.REVERSIBLE
    exec_cmd: Optional[str] = None     # shell: the string SystemCommander runs
    fn: Optional[Callable[[], "AgentResult"]] = None  # python ops
    explain: Optional[Callable[[Dict[str, Any]], str]] = None
    timeout: float = 60.0
    replay: Dict[str, Any] = field(default_factory=dict)
    note: str = ""                     # extra words for the confirmation prompt
    llm_generated: bool = False


def _powershell_exe() -> str:
    if IS_WSL:
        # Windows binaries are launched straight from WSL via binfmt interop.
        for drive in _wsl_drive_mounts() or ["c"]:
            exe = f"/mnt/{drive}/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            if os.path.exists(exe):
                return exe
        return "powershell.exe"
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return exe.replace("\\", "/") if os.path.exists(exe) else "powershell.exe"


def ps_command(script: str) -> str:
    """A PowerShell script as a single shell-safe command line (UTF-16LE base64)."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return f"{_powershell_exe()} -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {enc}"


def _psq(s: str) -> str:
    """PowerShell single-quoted literal.

    Under WSL a path still reads as /mnt/f/... here, but PowerShell runs on the
    Windows side and only understands F:\\..., so translate it on the way in.
    Only strings that actually look like a /mnt/<drive> path are touched, so app
    and process names pass through untouched."""
    text = str(s)
    if IS_WSL:
        text = to_windows_path(text)
    return "'" + text.replace("'", "''") + "'"


def shell_op(display: str, *, platform: str, tier: Optional[RiskTier] = None, powershell: Optional[bool] = None,
             explain=None, timeout: float = 60.0, note: str = "", llm_generated: bool = False) -> Op:
    is_ps = platform == "windows" if powershell is None else powershell
    exec_cmd = ps_command(display) if is_ps else display
    # Built-in ops carry an explicit tier (we wrote them; e.g. PowerShell's "| Format-Table" is not
    # the disk 'format' command). Commands typed by the user or written by the LLM are classified.
    risk = tier if tier is not None else system_commander.classify_risk(display)
    if explain is None and tier is not None and not llm_generated:
        explain = _stdout_explainer  # built-in ops print human-readable results
    return Op("shell", display, risk, exec_cmd=exec_cmd, explain=explain, timeout=timeout, note=note,
              replay={"op": "shell", "display": display, "powershell": is_ps, "timeout": timeout},
              llm_generated=llm_generated)


_LOOKS_LIKE_PS = re.compile(r"^\s*(?:\$|[A-Z][a-z]+-[A-Z][A-Za-z]+\b|Get-|Set-|New-|Remove-|Start-|Stop-)")

# App name -> what to launch / which process to look for (Windows)
_WIN_APPS = {
    "calculator": ("calc", "CalculatorApp"), "calc": ("calc", "CalculatorApp"), "notepad": ("notepad", "notepad"),
    "paint": ("mspaint", "mspaint"), "file explorer": ("explorer", "explorer"), "explorer": ("explorer", "explorer"),
    "command prompt": ("cmd", "cmd"), "terminal": ("wt", "WindowsTerminal"), "powershell": ("powershell", "powershell"),
    "task manager": ("taskmgr", "Taskmgr"), "settings": ("ms-settings:", "SystemSettings"),
    "chrome": ("chrome", "chrome"), "google chrome": ("chrome", "chrome"), "edge": ("msedge", "msedge"),
    "microsoft edge": ("msedge", "msedge"), "firefox": ("firefox", "firefox"), "word": ("winword", "WINWORD"),
    "excel": ("excel", "EXCEL"), "powerpoint": ("powerpnt", "POWERPNT"), "outlook": ("outlook", "OUTLOOK"),
    "vs code": ("code", "Code"), "vscode": ("code", "Code"), "visual studio code": ("code", "Code"),
    "spotify": ("spotify", "Spotify"), "teams": ("ms-teams", "ms-teams"), "discord": ("discord", "Discord"),
    "whatsapp": ("whatsapp", "WhatsApp"), "vlc": ("vlc", "vlc"), "steam": ("steam", "steam"),
}
_NEVER_KILL = {"system", "idle", "csrss", "smss", "wininit", "winlogon", "lsass", "services", "svchost", "dwm",
               "explorer", "fontdrvhost", "registry", "memory compression", "init", "systemd", "launchd", "kernel_task",
               "loginwindow", "windowserver", "python", "pythonw"}


def _app_name(raw: str) -> str:
    return re.sub(r"\s+(?:app|application|program|window)$", "", _clean_target(raw).strip(" .?!").lower()).strip()


# ═══════════════════════════════════════════════════════════════
# Recycle bin / trash with undo
# ═══════════════════════════════════════════════════════════════
class Trash:
    """Windows: real Recycle Bin (SHFileOperationW + $I index for undo).
    Linux: freedesktop ~/.local/share/Trash. macOS: ~/.Trash. Undo restores the last batch."""

    STATE = "system_ops_trash.json"

    @staticmethod
    def _load() -> List[dict]:
        from core.agents.supervisor import load_json_state
        return list(load_json_state(Trash.STATE, {}).get("batches", []))

    @staticmethod
    def _save(batches: List[dict]) -> None:
        from core.agents.supervisor import save_json_state
        save_json_state(Trash.STATE, {"batches": batches[-50:]})

    _memory: List[dict] = []  # used when persistence is off (tests)

    @classmethod
    def batches(cls) -> List[dict]:
        from core.agents.supervisor import agent_state_dir
        return cls._load() if agent_state_dir() is not None else cls._memory

    @classmethod
    def _store(cls, batches: List[dict]) -> None:
        from core.agents.supervisor import agent_state_dir
        if agent_state_dir() is not None:
            cls._save(batches)
        else:
            cls._memory = batches

    # -- move to trash --
    @classmethod
    def send(cls, paths: List[str]) -> AgentResult:
        items, problems = [], []
        for p in paths:
            reason = protected_reason(p)
            if reason:
                problems.append(f"{p} ({reason})")
                continue
            if not os.path.lexists(p):
                problems.append(f"{p} (not found)")
                continue
            try:
                items.append(cls._send_one(p))
            except Exception as exc:
                problems.append(f"{os.path.basename(p)} ({exc})")
        if items:
            batches = cls.batches()
            batches.append({"id": uuid.uuid4().hex[:8], "time": time.time(), "items": items})
            cls._store(batches)
        moved = ", ".join(os.path.basename(i["original"]) for i in items)
        where = "the Recycle Bin" if os.name == "nt" else "the Trash"
        if items and not problems:
            return AgentResult(status="done", summary=f"Moved {moved} to {where}. Say 'undo' to put it back.",
                               data={"trashed": items})
        if items:
            return AgentResult(status="done", data={"trashed": items, "problems": problems},
                               summary=f"Moved {moved} to {where}; skipped {'; '.join(problems)}.")
        return AgentResult(status="failed", summary="Nothing was deleted: " + "; ".join(problems), data={"problems": problems})

    @classmethod
    def _send_one(cls, path: str) -> dict:
        path = os.path.abspath(path)
        if os.name == "nt":
            return cls._win_recycle(path)
        if sys.platform == "darwin":
            dest_dir = Path.home() / ".Trash"
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / f"{Path(path).name} {time.strftime('%H.%M.%S')}"
            shutil.move(path, dest)
            return {"original": path, "stored": str(dest)}
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Trash"
        (base / "files").mkdir(parents=True, exist_ok=True)
        (base / "info").mkdir(parents=True, exist_ok=True)
        name = Path(path).name
        dest = base / "files" / name
        n = 1
        while dest.exists():
            dest = base / "files" / f"{name}.{n}"
            n += 1
        info = base / "info" / f"{dest.name}.trashinfo"
        info.write_text(f"[Trash Info]\nPath={path}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
        shutil.move(path, dest)
        return {"original": path, "stored": str(dest), "info": str(info)}

    @staticmethod
    def _win_recycle(path: str) -> dict:
        import ctypes
        from ctypes import wintypes
        drive = os.path.splitdrive(path)[0] + "\\"
        if ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:  # DRIVE_FIXED: only fixed drives have a Recycle Bin
            raise OSError("that drive has no Recycle Bin, so I won't delete permanently")

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        before = time.time() - 2
        op = SHFILEOPSTRUCTW(None, 3, path + "\0\0", None, 0x40 | 0x10 | 0x04 | 0x400, False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted or os.path.lexists(path):
            raise OSError(f"Windows refused (code {rc})")
        entry = Trash._find_recycle_entry(path, before)
        return {"original": path, **(entry or {})}

    @staticmethod
    def _parse_i_file(data: bytes) -> Tuple[Optional[str], float]:
        import struct
        if len(data) < 24:
            return None, 0.0
        version = struct.unpack_from("<q", data, 0)[0]
        filetime = struct.unpack_from("<q", data, 16)[0]
        when = filetime / 1e7 - 11644473600 if filetime else 0.0
        if version == 2 and len(data) >= 28:
            n = struct.unpack_from("<i", data, 24)[0]
            return data[28: 28 + n * 2].decode("utf-16-le").rstrip("\0"), when
        return data[24: 24 + 520].decode("utf-16-le", errors="ignore").split("\0", 1)[0], when

    @staticmethod
    def _find_recycle_entry(original: str, after: float) -> Optional[dict]:
        bin_root = Path(os.path.splitdrive(original)[0] + "\\$Recycle.Bin")
        best = None
        try:
            for sid_dir in bin_root.iterdir():
                try:
                    for ifile in sid_dir.glob("$I*"):
                        path, when = Trash._parse_i_file(ifile.read_bytes())
                        if path and _norm(path) == _norm(original) and when >= after - 5:
                            rfile = ifile.with_name("$R" + ifile.name[2:])
                            if rfile.exists() and (best is None or when > best[0]):
                                best = (when, str(rfile), str(ifile))
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            return None
        return {"stored": best[1], "info": best[2]} if best else None

    # -- undo --
    @classmethod
    def undo(cls, name_hint: Optional[str] = None) -> AgentResult:
        batches = cls.batches()
        if not batches:
            return AgentResult(status="failed", summary="There's nothing I've deleted that I can put back.")
        idx = len(batches) - 1
        if name_hint:
            hint = name_hint.lower()
            idx = next((i for i in range(len(batches) - 1, -1, -1)
                        if any(hint in os.path.basename(it["original"]).lower() for it in batches[i]["items"])), -1)
            if idx < 0:
                return AgentResult(status="failed", summary=f"I didn't delete anything called '{name_hint}'.")
        batch = batches[idx]
        restored, problems = [], []
        for it in batch["items"]:
            orig, stored = it["original"], it.get("stored")
            if os.path.lexists(orig):
                problems.append(f"{os.path.basename(orig)} (something already exists there)")
                continue
            if not stored or not os.path.lexists(stored):
                problems.append(f"{os.path.basename(orig)} (no longer in the {'Recycle Bin' if os.name == 'nt' else 'Trash'})")
                continue
            try:
                os.makedirs(os.path.dirname(orig), exist_ok=True)
                shutil.move(stored, orig)
                if it.get("info") and os.path.exists(it["info"]):
                    os.remove(it["info"])
                restored.append(os.path.basename(orig))
            except Exception as exc:
                problems.append(f"{os.path.basename(orig)} ({type(exc).__name__})")
        del batches[idx]
        cls._store(batches)
        if restored:
            extra = f"; couldn't restore {'; '.join(problems)}" if problems else ""
            return AgentResult(status="done", summary=f"Put back {', '.join(restored)}{extra}", data={"restored": restored})
        return AgentResult(status="failed", summary="Couldn't restore: " + "; ".join(problems))

    @staticmethod
    def empty() -> AgentResult:
        if os.name == "nt":
            import ctypes
            rc = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
            ok = rc in (0, -2147418113)  # S_OK, or E_UNEXPECTED when it is already empty
            Trash._store([])
            return AgentResult(status="done" if ok else "failed",
                               summary="Emptied the Recycle Bin" if ok else f"Windows couldn't empty the Recycle Bin (code {rc})")
        target = Path.home() / ".Trash" if sys.platform == "darwin" else \
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Trash"
        freed = _dir_size(target, 20.0)[0]
        for sub in ([target] if sys.platform == "darwin" else [target / "files", target / "info"]):
            for child in sub.glob("*") if sub.exists() else []:
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        Trash._store([])
        return AgentResult(status="done", summary=f"Emptied the Trash ({_human(freed)} freed)")


# ═══════════════════════════════════════════════════════════════
# Measurements / explanations (psutil)
# ═══════════════════════════════════════════════════════════════
def _human(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _dir_size(path: Path, budget_s: float = 4.0) -> Tuple[int, bool]:
    """(bytes, complete?) -- stops at the time budget."""
    total, deadline = 0, time.monotonic() + budget_s
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
            if time.monotonic() > deadline:
                return total, False
    except OSError:
        pass
    return total, True


def _stdout_explainer(res: Dict[str, Any]) -> str:
    out = [l.strip() for l in (res.get("stdout") or "").splitlines() if l.strip()]
    return "; ".join(out[:4])[:_SUMMARY_MAX_CHARS]


def explain_disk(_res: Optional[dict] = None) -> str:
    import psutil
    parts = []
    worst = None
    for part in psutil.disk_partitions(all=False):
        if os.name == "nt" and "cdrom" in (part.opts or ""):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        label = part.device.rstrip("\\") if os.name == "nt" else part.mountpoint
        parts.append(f"{label} is {u.percent:.0f}% full ({_human(u.free)} free)")
        if worst is None or u.percent > worst[1]:
            worst = (part.mountpoint, u.percent, label)
    text = "; ".join(parts) or "I couldn't read any drives"
    if worst and worst[1] >= 80:
        home = Path.home()
        if _norm(str(home)).startswith(_norm(worst[0])):
            sizes = []
            for name in ("Downloads", "Documents", "Videos", "Pictures", "Desktop", "Music"):
                size, _ = _dir_size(home / name, 2.0)
                sizes.append((size, name))
            tmp = Path(os.environ.get("TEMP", "/tmp"))
            sizes.append((_dir_size(tmp, 2.0)[0], "temp files"))
            sizes.sort(reverse=True)
            if sizes and sizes[0][0] > 1024 ** 3:
                top = ", ".join(f"{n} ({_human(s)})" for s, n in sizes[:2] if s > 512 * 1024 ** 2)
                text += f". {worst[2]} is getting full; the biggest of your folders are {top}"
    return text


def explain_memory(_res: Optional[dict] = None) -> str:
    import psutil
    vm = psutil.virtual_memory()
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"] or "?"))
        except Exception:
            pass
    agg: Dict[str, int] = {}
    for rss, name in procs:
        agg[name] = agg.get(name, 0) + rss
    top = sorted(agg.items(), key=lambda kv: -kv[1])[:3]
    return (f"{vm.percent:.0f}% of memory in use ({_human(vm.used)} of {_human(vm.total)}); "
            f"biggest users: " + ", ".join(f"{n} ({_human(s)})" for n, s in top))


def explain_uptime(_res: Optional[dict] = None) -> str:
    import psutil
    secs = time.time() - psutil.boot_time()
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    return f"Up for {d} day{'s' if d != 1 else ''}, {h} hour{'s' if h != 1 else ''}" if d else f"Up for {h} hours, {rem // 60} minutes"


def explain_processes(_res: Optional[dict] = None) -> str:
    import psutil
    procs = [p for p in psutil.process_iter(["name", "pid"]) if p.info["pid"] not in (0,)
             and (p.info["name"] or "").lower() not in ("system idle process", "idle")]
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    total = psutil.cpu_percent(interval=1.0)  # also the sampling window for the per-process numbers
    usage = []
    for p in procs:
        try:
            usage.append((p.cpu_percent(None) / max(psutil.cpu_count() or 1, 1), p.info["name"] or "?"))
        except Exception:
            pass
    agg: Dict[str, float] = {}
    for c, n in usage:
        agg[n] = agg.get(n, 0.0) + c
    top = [(c, n) for n, c in sorted(agg.items(), key=lambda kv: -kv[1])[:3] if c > 0.5]
    if not top:
        return f"CPU at {total:.0f}%; nothing is working hard right now"
    return f"CPU at {total:.0f}%; busiest: " + ", ".join(f"{n} ({c:.0f}%)" for c, n in top)


# ═══════════════════════════════════════════════════════════════
# Temp clean-up job
# ═══════════════════════════════════════════════════════════════
def temp_dirs() -> List[Path]:
    dirs = []
    for var in ("TEMP", "TMP"):
        if os.environ.get(var):
            dirs.append(Path(os.environ[var]))
    if os.name != "nt":
        dirs.append(Path("/tmp"))
    out, seen = [], set()
    for d in dirs:
        key = _norm(str(d))
        if d.is_dir() and key not in seen:
            seen.add(key)
            out.append(d)
    return out


def scan_temp(dirs: List[Path], older_than_h: float = 24.0, budget_s: float = 8.0) -> Tuple[List[str], int]:
    cutoff = time.time() - older_than_h * 3600
    files, total, deadline = [], 0, time.monotonic() + budget_s
    me = _norm(str(ROOT))
    for d in dirs:
        for dirpath, dirnames, filenames in os.walk(d):
            if _norm(dirpath).startswith(me):
                dirnames[:] = []
                continue
            for f in filenames:
                p = os.path.join(dirpath, f)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    files.append(p)
                    total += st.st_size
            if time.monotonic() > deadline:
                return files, total
    return files, total


def clean_temp(dirs: List[str], older_than_h: float = 24.0) -> AgentResult:
    files, _ = scan_temp([Path(d) for d in dirs], older_than_h)
    freed, skipped = 0, 0
    for p in files:
        try:
            size = os.lstat(p).st_size
            os.remove(p)
            freed += size
        except OSError:
            skipped += 1  # in use / no permission
    for d in dirs:  # tidy now-empty folders (deepest first)
        for dirpath, dirnames, filenames in sorted(os.walk(d), key=lambda x: -len(x[0])):
            if dirpath != d and not os.listdir(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
    extra = f"; skipped {skipped} file{'s' if skipped != 1 else ''} still in use" if skipped else ""
    return AgentResult(status="done", summary=f"Cleaned up temp files and freed {_human(freed)}{extra}",
                       data={"freed_bytes": freed, "skipped": skipped})


# ═══════════════════════════════════════════════════════════════
# System watcher
# ═══════════════════════════════════════════════════════════════
class SystemWatcher:
    """Background checks every ``interval`` s; warnings go out as high-priority narration."""

    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.event_bus = None
        self.interval = 30.0
        self._cooldown: Dict[str, float] = {}
        self._cpu_high = 0
        self.enabled = True

    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, event_bus, interval: Optional[float] = None) -> bool:
        if self.running():
            return False
        self.event_bus, self.enabled = event_bus, True
        if interval:
            self.interval = interval
        try:
            self.task = asyncio.get_running_loop().create_task(self._loop(), name="system-watcher")
        except RuntimeError:
            return False
        return True

    def stop(self) -> bool:
        self.enabled = False
        if self.running():
            self.task.cancel()
            return True
        return False

    def _warn(self, key: str, fact: str, cooldown: float) -> None:
        now = time.time()
        if now < self._cooldown.get(key, 0):
            return
        self._cooldown[key] = now + cooldown
        try:
            from core.agents.supervisor import write_audit
            write_audit("watch_warning", agent="system_ops", warning=fact)
        except Exception:
            pass
        if self.event_bus is not None:
            # An event, not a canned warning: the supervisor's handle_event looks into it
            # (read-only tools: it can explain and suggest, never act) and speaks.
            self.event_bus.emit("agent_event", watch_event(key, fact))

    async def _loop(self) -> None:
        import psutil
        psutil.cpu_percent(None)
        while self.enabled:
            await asyncio.sleep(self.interval)
            try:
                for key, fact, cooldown in await asyncio.to_thread(self.check_once):
                    self._warn(key, fact, cooldown)
            except Exception:
                pass

    def check_once(self) -> List[Tuple[str, str, float]]:
        import psutil
        out = []
        cpu = psutil.cpu_percent(None)
        self._cpu_high = self._cpu_high + 1 if cpu >= 90 else 0
        if self._cpu_high >= 3:
            top = explain_processes()
            out.append(("cpu", f"CPU has been above 90% for a while; {top.split('; ', 1)[-1]}", 600))
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            if u.percent >= 95 or (u.free < 2 * 1024 ** 3 and u.total > 20 * 1024 ** 3):
                label = part.device.rstrip("\\") if os.name == "nt" else part.mountpoint
                out.append((f"disk:{label}", f"{label} is almost full: {u.percent:.0f}% used, {_human(u.free)} left", 3600))
        vm = psutil.virtual_memory()
        if vm.percent >= 93:
            out.append(("mem", f"Memory is nearly exhausted ({vm.percent:.0f}% used)", 900))
        for name in hung_apps():
            out.append((f"hung:{name}", f"{name} has stopped responding", 900))
        return out


def watch_event(key: str, fact: str) -> Dict[str, Any]:
    """The event the watcher raises for one problem, with what to look into."""
    kind = key.split(":", 1)[0]
    goals = {
        "cpu": "Find out which programs are using the CPU (cpu_and_processes) and tell the operator in one sentence. "
               "Don't end anything; offer to if one looks stuck.",
        "disk": "Check the drives (disk_usage) and tell the operator what is filling the nearly full one, and that "
                "they can ask you to clean temp files or empty the Recycle Bin. Don't delete anything.",
        "mem": "Check memory (memory_usage) and tell the operator which programs use the most. Don't end anything.",
        "hung": "Tell the operator this app has stopped responding and offer to close it or end it. Don't do it yourself.",
    }
    return {"source": "system_ops", "kind": f"watch_{kind}", "summary": fact,
            "goal": f"System watch: {fact}. " + goals.get(kind, "Tell the operator briefly."),
            "data": {"key": key, "fact": fact}}


def hung_apps() -> List[str]:
    """Names of apps with a window that isn't responding (Windows only)."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
        user32 = ctypes.windll.user32
        names: set = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and user32.IsHungAppWindow(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    names.add(psutil.Process(pid.value).name())
                except Exception:
                    pass
            return True

        user32.EnumWindows(_cb, 0)
        return sorted(names)
    except Exception:
        return []


WATCHER = SystemWatcher()


# ═══════════════════════════════════════════════════════════════
# Op builders: one function per capability, shared by the tools and the
# keyword fallback (plan_ops). Each returns (op, problem).
# ═══════════════════════════════════════════════════════════════
OpOrProblem = Tuple[Optional[Op], Optional[str]]


def op_disk(platform: str) -> Op:
    cmd = ("Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object { "
           "'{0}: {1:N1} GB used, {2:N1} GB free' -f $_.Name, ($_.Used/1GB), ($_.Free/1GB) }") if platform == "windows" else "df -h"
    return shell_op(cmd, platform=platform, explain=explain_disk, tier=RiskTier.READ)


def op_memory(platform: str) -> Op:
    cmd = ("Get-CimInstance Win32_OperatingSystem | ForEach-Object { '{0:N1} GB free of {1:N1} GB' -f "
           "($_.FreePhysicalMemory/1MB), ($_.TotalVisibleMemorySize/1MB) }") if platform == "windows" else \
        ("vm_stat" if platform == "mac" else "free -h")
    return shell_op(cmd, platform=platform, explain=explain_memory, tier=RiskTier.READ)


def op_uptime(platform: str) -> Op:
    cmd = ("(Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime | Select-Object Days,Hours,Minutes"
           if platform == "windows" else "uptime")
    return shell_op(cmd, platform=platform, explain=explain_uptime, tier=RiskTier.READ)


def op_processes(platform: str) -> Op:
    cmd = ("Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,Id,"
           "@{n='CPU(s)';e={[int]$_.CPU}},@{n='MB';e={[int]($_.WorkingSet64/1MB)}} | Format-Table -AutoSize | Out-String -Width 200"
           ) if platform == "windows" else ("ps aux -r | head -n 15" if platform == "mac" else "ps aux --sort=-%cpu | head -n 15")
    return shell_op(cmd, platform=platform, explain=explain_processes, tier=RiskTier.READ)


def op_pwd(platform: str) -> Op:
    return shell_op("(Get-Location).Path" if platform == "windows" else "pwd", platform=platform, tier=RiskTier.READ)


def op_git_status(platform: str) -> Op:
    return shell_op("git status --short --branch", platform=platform, powershell=False, tier=RiskTier.READ)


def op_list_files(platform: str, target: str = "") -> Op:
    target = _clean_target(target) if target else ""
    cmd = (f"Get-ChildItem -Force {_psq(target) if target else ''} | Select-Object Mode,LastWriteTime,Length,Name | "
           "Format-Table -AutoSize | Out-String -Width 200") if platform == "windows" else \
        (f"ls -la {_q(target)}" if target else "ls -la")
    return shell_op(cmd, platform=platform, tier=RiskTier.READ)


def op_open_app(platform: str, raw: str) -> Op:
    app = _app_name(raw)
    if platform == "windows":
        target = _WIN_APPS.get(app, (app, app))[0]
        script = (f"$n = {_psq(target)}; $q = {_psq(app)}\n"
                  "if ($n -like '*:') { Start-Process $n; \"started $q\"; exit 0 }\n"
                  "$c = Get-Command $n -ErrorAction SilentlyContinue | Select-Object -First 1\n"
                  "if ($c) { Start-Process $c.Source; \"started $q\"; exit 0 }\n"
                  "$l = Get-ChildItem \"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\",\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\" "
                  "-Recurse -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object { $_.BaseName -like \"*$q*\" } | Select-Object -First 1\n"
                  "if ($l) { Start-Process $l.FullName; \"started $($l.BaseName)\"; exit 0 }\n"
                  "$a = Get-StartApps | Where-Object { $_.Name -like \"*$q*\" } | Select-Object -First 1\n"
                  "if ($a) { Start-Process \"shell:AppsFolder\\$($a.AppID)\"; \"started $($a.Name)\"; exit 0 }\n"
                  "Write-Error \"no app called $q\"; exit 1")
        return shell_op(script, platform=platform, tier=RiskTier.REVERSIBLE, timeout=30)
    cmd = f"open -a {_q(app)}" if platform == "mac" else f"(gtk-launch {_q(app)} || {_q(app)} >/dev/null 2>&1 &)"
    return shell_op(cmd, platform=platform, tier=RiskTier.REVERSIBLE, timeout=30)


def op_close_app(platform: str, raw: str) -> Op:
    app = _app_name(raw)
    if platform == "windows":
        proc = _WIN_APPS.get(app, (app, app))[1]
        script = (f"$p = Get-Process -Name {_psq(proc)} -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }}\n"
                  f"if (-not $p) {{ $p = Get-Process | Where-Object {{ $_.MainWindowTitle -like {_psq('*' + app + '*')} }} }}\n"
                  f"if (-not $p) {{ Write-Error 'no open window for {app}'; exit 1 }}\n"
                  "$p | ForEach-Object { [void]$_.CloseMainWindow() }; \"asked $($p.Count) window(s) to close\"")
        return shell_op(script, platform=platform, tier=RiskTier.REVERSIBLE, timeout=20,
                        note="It closes politely, so the app can still ask you to save.")
    quit_script = _q('quit app "' + app + '"')
    cmd = f"osascript -e {quit_script}" if platform == "mac" else f"wmctrl -c {_q(app)}"
    return shell_op(cmd, platform=platform, tier=RiskTier.REVERSIBLE, timeout=20)


def op_switch_app(platform: str, raw: str) -> Op:
    app = _app_name(raw)
    if platform == "windows":
        proc = _WIN_APPS.get(app, (app, app))[1]
        script = (f"$p = Get-Process | Where-Object {{ $_.MainWindowTitle -and ($_.ProcessName -like {_psq('*' + proc + '*')} "
                  f"-or $_.MainWindowTitle -like {_psq('*' + app + '*')}) }} | Select-Object -First 1\n"
                  f"if (-not $p) {{ Write-Error 'no window for {app}'; exit 1 }}\n"
                  "$w = New-Object -ComObject WScript.Shell; [void]$w.AppActivate($p.Id); \"switched to $($p.MainWindowTitle)\"")
        return shell_op(script, platform=platform, tier=RiskTier.REVERSIBLE, timeout=15)
    activate = _q('tell application "' + app + '" to activate')
    cmd = f"osascript -e {activate}" if platform == "mac" else f"wmctrl -a {_q(app)}"
    return shell_op(cmd, platform=platform, tier=RiskTier.REVERSIBLE, timeout=15)


def op_volume(platform: str, action: str, n: int = 10) -> OpOrProblem:
    if action not in ("mute", "unmute", "up", "down", "set"):
        return None, "Volume action must be up, down, set, mute or unmute."
    n = max(0, min(100, int(n)))
    if platform == "windows":
        keys = {"mute": "1..1 | % { $w.SendKeys([char]173) }", "unmute": "1..1 | % { $w.SendKeys([char]173) }",
                "up": f"1..{max(1, n // 2)} | % {{ $w.SendKeys([char]175) }}",
                "down": f"1..{max(1, n // 2)} | % {{ $w.SendKeys([char]174) }}",
                "set": f"1..50 | % {{ $w.SendKeys([char]174) }}; 1..{max(0, n // 2) or 1} | % {{ $w.SendKeys([char]175) }}"
                + ("" if n else "; $w.SendKeys([char]174)")}[action]
        script = f"$w = New-Object -ComObject WScript.Shell; {keys}; 'volume {action}{(' ' + str(n) + '%') if action in ('set', 'up', 'down') else ''}'"
        return shell_op(script, platform=platform, tier=RiskTier.REVERSIBLE, timeout=15), None
    if platform == "mac":
        cmd = {"mute": "osascript -e 'set volume with output muted'", "unmute": "osascript -e 'set volume without output muted'",
               "set": f"osascript -e 'set volume output volume {n}'",
               "up": f"osascript -e 'set volume output volume ((output volume of (get volume settings)) + {n})'",
               "down": f"osascript -e 'set volume output volume ((output volume of (get volume settings)) - {n})'"}[action]
    else:
        cmd = {"mute": "pactl set-sink-mute @DEFAULT_SINK@ 1", "unmute": "pactl set-sink-mute @DEFAULT_SINK@ 0",
               "set": f"pactl set-sink-volume @DEFAULT_SINK@ {n}%", "up": f"pactl set-sink-volume @DEFAULT_SINK@ +{n}%",
               "down": f"pactl set-sink-volume @DEFAULT_SINK@ -{n}%"}[action]
    return shell_op(cmd, platform=platform, tier=RiskTier.REVERSIBLE, timeout=15), None


def op_brightness(platform: str, percent: Optional[int] = None, delta: int = 0) -> Op:
    if platform == "windows":
        value = (str(int(percent)) if percent is not None else
                 f"[math]::Max(0,[math]::Min(100,(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness + ({delta})))")
        script = ("$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop\n"
                  f"$v = {value}\n"
                  "Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{Timeout=1; Brightness=[byte]$v} | Out-Null\n"
                  "\"brightness $v%\"")
        return shell_op(script, platform=platform, tier=RiskTier.REVERSIBLE, timeout=15,
                        note="Only built-in laptop screens support this.")
    cmd = (f"brightnessctl set {int(percent)}%" if percent is not None else f"brightnessctl set {abs(delta)}%{'+' if delta > 0 else '-'}")
    return shell_op(cmd, platform=platform, tier=RiskTier.REVERSIBLE, timeout=15)


def op_radio(platform: str, kind: str, state: Optional[str]) -> Op:
    """kind 'WiFi' | 'Bluetooth'; state 'On' | 'Off' | None (= status). Wi-Fi off is DESTRUCTIVE."""
    if platform == "windows":
        if state is None:
            script = _RADIO_PS + f"$r = $radios | Where-Object {{ $_.Kind -eq '{kind}' }}\n" \
                     f"if (-not $r) {{ 'no {kind} radio' }} else {{ $r | ForEach-Object {{ \"$($_.Name): $($_.State)\" }} }}\n" + \
                     ("netsh wlan show interfaces | Select-String -Pattern ' SSID|State|Signal' | ForEach-Object { $_.Line.Trim() }"
                      if kind == "WiFi" else "")
            return shell_op(script, platform=platform, tier=RiskTier.READ, timeout=20)
        script = _RADIO_PS + (f"$r = $radios | Where-Object {{ $_.Kind -eq '{kind}' }}\n"
                              f"if (-not $r) {{ Write-Error 'no {kind} radio'; exit 1 }}\n"
                              f"$r | ForEach-Object {{ [void](Await ($_.SetStateAsync('{state}')) ([Windows.Devices.Radios.RadioAccessStatus])) }}\n"
                              f"'{kind} {state.lower()}'")
        tier = RiskTier.DESTRUCTIVE if (kind == "WiFi" and state == "Off") else RiskTier.REVERSIBLE
        note = "That cuts your internet connection (and JARVIS's cloud services)." if tier is RiskTier.DESTRUCTIVE else ""
        return shell_op(script, platform=platform, tier=tier, timeout=20, note=note)
    if platform == "mac":
        cmd = ("networksetup -setairportpower en0 " + (state or "").lower()) if kind == "WiFi" and state else \
              ("networksetup -getairportpower en0" if kind == "WiFi" else "system_profiler SPBluetoothDataType | head -n 20")
    else:
        cmd = (f"nmcli radio wifi {(state or '').lower()}" if state else "nmcli radio wifi") if kind == "WiFi" else \
              (f"rfkill {'unblock' if state == 'On' else 'block'} bluetooth" if state else "rfkill list bluetooth")
    tier = RiskTier.READ if state is None else (RiskTier.DESTRUCTIVE if (kind == "WiFi" and state == "Off") else RiskTier.REVERSIBLE)
    return shell_op(cmd, platform=platform, tier=tier, timeout=20)


def op_kill_pid(platform: str, pid: int) -> OpOrProblem:
    if pid == os.getpid():
        return None, "That's JARVIS's own process; I won't end it."
    if pid in (0, 1, 4):
        return None, f"Process {pid} is part of the operating system; I won't end it."
    return shell_op(f"Stop-Process -Id {pid}" if platform == "windows" else f"kill {pid}", platform=platform,
                    tier=RiskTier.DESTRUCTIVE), None


def op_kill_name(platform: str, raw: str) -> OpOrProblem:
    app = _app_name(raw)
    proc = _WIN_APPS.get(app, (app, app))[1] if platform == "windows" else app
    if not proc:
        return None, "Which program?"
    if proc.lower().removesuffix(".exe") in _NEVER_KILL:
        return None, f"{proc} is essential to the system (or to JARVIS); I won't kill it."
    cmd = (f"Stop-Process -Name {_psq(proc.removesuffix('.exe'))} -Force" if platform == "windows" else f"pkill -f {_q(proc)}")
    return shell_op(cmd, platform=platform, tier=RiskTier.DESTRUCTIVE, note="Unsaved work in it will be lost."), None


def op_power(platform: str, action: str) -> OpOrProblem:
    if action not in ("shutdown", "restart"):
        return None, "Power action must be shutdown or restart."
    W = platform == "windows"
    cmd = ("shutdown /s /t 30" if W else "shutdown now") if action == "shutdown" else ("shutdown /r /t 30" if W else "reboot")
    # SystemCommander's hard guard still has the final say.
    return shell_op(cmd, platform=platform, powershell=False, tier=RiskTier.DESTRUCTIVE), None


def op_create(platform: str, raw: str, kind: str = "folder") -> OpOrProblem:
    target = _clean_target(raw)
    if not target:
        return None, "Create what, where?"
    if protected_reason(os.path.abspath(os.path.expanduser(target))):
        return None, f"I won't create anything in {target}: it's a protected location."
    W = platform == "windows"
    if kind == "folder":
        cmd = f"New-Item -ItemType Directory -Force -Path {_psq(target)} | Out-Null; 'created {target}'" if W else f"mkdir -p {_q(target)}"
    else:
        cmd = (f"if (-not (Test-Path -LiteralPath {_psq(target)})) {{ New-Item -ItemType File -Path {_psq(target)} | Out-Null }}; "
               f"'created {target}'") if W else f"touch {_q(target)}"
    return shell_op(cmd, platform=platform, tier=RiskTier.REVERSIBLE), None


def op_trash(platform: str, raw: str, cwd: Optional[str] = None) -> OpOrProblem:
    found, name = resolve_user_path(raw, cwd)
    if not found:
        return None, f"I can't find '{name}'. Give me the full path or the folder it's in."
    if len(found) > 1:
        return None, f"There's more than one '{name}': " + "; ".join(found[:3]) + ". Which one?"
    reason = protected_reason(found[0])
    if reason:
        return None, f"I won't delete {found[0]}: {reason}."
    target = found[0]
    bin_name = "Recycle Bin" if platform == "windows" else "Trash"
    return Op("python", f"move {target} to the {bin_name}", RiskTier.DESTRUCTIVE, fn=lambda: Trash.send([target]),
              note="You can say 'undo' afterwards.", replay={"op": "trash", "paths": [target]}), None


def op_empty_trash(platform: str) -> Op:
    return Op("python", "empty the Recycle Bin" if platform == "windows" else "empty the Trash", RiskTier.DESTRUCTIVE,
              fn=Trash.empty, note="This can't be undone.", replay={"op": "empty_trash"})


def op_undo(platform: str, hint: Optional[str]) -> Op:
    bin_name = "Recycle Bin" if platform == "windows" else "Trash"
    return Op("python", f"restore {hint or 'the last deleted item'} from the {bin_name}", RiskTier.REVERSIBLE,
              fn=lambda: Trash.undo(hint), replay={"op": "undo", "hint": hint})


def op_clean_temp(platform: str, older_than_h: float = 24.0) -> OpOrProblem:
    dirs = temp_dirs()
    files, size = scan_temp(dirs, older_than_h)
    if not files:
        return None, f"There are no temp files older than {_hours(older_than_h)} to clean up."
    where = ", ".join(str(d) for d in dirs)
    return Op("python", f"permanently delete {len(files)} temp files older than {_hours(older_than_h)} ({_human(size)}) in {where}",
              RiskTier.DESTRUCTIVE, fn=lambda: clean_temp([str(d) for d in dirs], older_than_h),
              note="Temp files skip the Recycle Bin, so this can't be undone.",
              replay={"op": "clean_temp", "dirs": [str(d) for d in dirs], "hours": older_than_h}), None


def _hours(h: float) -> str:
    return "1 day" if h == 24 else (f"{h / 24:g} days" if h % 24 == 0 else f"{h:g} hours")


# ═══════════════════════════════════════════════════════════════
# Keyword fallback: request text -> list of Ops (used only without the tool loop)
# ═══════════════════════════════════════════════════════════════
_DRY_RUN_RE = re.compile(r"^\s*(?:dry[\s-]?run|simulate|what\s+would\s+(?:happen|you\s+(?:run|do))\s+(?:if\s+(?:i\s+said|you)\s+|to\s+)?)"
                         r"[:,]?\s*", re.I)
_REPORT_ONLY = re.compile(r"^\s*(?:and\s+)?(?:tell|show|let)\s+me\b.*|^\s*(?:and\s+)?report\b.*|^\s*how\s+much\b.*", re.I)
_PCT = re.compile(r"(\d{1,3})\s*(?:%|percent)")


def resolve_user_path(raw: str, cwd: Optional[str] = None) -> Tuple[List[str], str]:
    """Candidate absolute paths for what the user called ``raw``."""
    name = _clean_target(raw).strip("'\"`")
    if not name:
        return [], name
    p = Path(os.path.expanduser(name))
    if p.is_absolute():
        return ([str(p)] if p.exists() else []), name
    home = Path.home()
    bases = [Path(cwd)] if cwd else []
    bases += [home / "Desktop", home / "Downloads", home / "Documents", home]
    found = []
    for b in bases:
        cand = b / name
        if cand.exists() and str(cand) not in found:
            found.append(str(cand))
    return found, name


def _one(built: OpOrProblem) -> Tuple[List[Op], Optional[str]]:
    op, problem = built
    return ([op] if op else []), problem


def plan_ops(goal: str, platform: str = CURRENT_PLATFORM, cwd: Optional[str] = None) -> Tuple[List[Op], Optional[str]]:
    """Keyword fallback, used only when the tool loop isn't available (no Groq).
    Ops for a request, or ([], problem-message) / ([], None) when nothing matches."""
    text = (goal or "").strip()
    W = platform == "windows"

    # an explicit command the operator typed themselves (never one a model wrote)
    m = re.search(r"\b(?:run|execute|exec)\s*(?:the\s+)?(?:command\s*)?[:]?\s*`([^`]+)`", text, re.I) or \
        re.search(r"\b(?:run|execute)\s+(?:the\s+)?command\s*:?\s+(.+)$", text, re.I) or \
        re.search(r"\b(?:run|execute)\s*:\s*(.+)$", text, re.I)
    if m:
        cmd = m.group(1).strip().strip("'\"")
        return [shell_op(cmd, platform=platform, powershell=W and bool(_LOOKS_LIKE_PS.match(cmd)),
                         explain=None, timeout=120)], None

    low = text.lower()
    if re.search(r"\b(?:empty|clear)\s+(?:the\s+)?(?:recycle\s*bin|trash|bin)\b", low):
        return [op_empty_trash(platform)], None
    m = re.search(r"^\s*(?:undo|undelete|restore|put\s+back|bring\s+back)\b(?:\s+(?:that|it|the\s+delete|the\s+last\s+delete))?"
                  r"(?:\s+(?P<name>.+))?$", text, re.I)
    if m:
        hint = (m.group("name") or "").strip(" .?!")
        hint = re.sub(r"^(?:the\s+)?(?:file|folder)\s+", "", hint, flags=re.I) or None
        return [op_undo(platform, hint)], None
    if re.search(r"\b(?:clean(?:\s+up)?|clear|delete|remove|purge)\s+(?:out\s+)?(?:the\s+|my\s+|all\s+)?(?:temp|temporary)\s+files?\b", low):
        return _one(op_clean_temp(platform))
    m = re.search(r"\b(?:delete|remove|erase|trash|bin)\s+(?:the\s+)?(?:(?:file|folder|directory|dir)\s+)?(?P<t>.+)$", text, re.I)
    if m and not re.search(r"\b(?:email|mail|event|meeting|appointment)\b", low):
        return _one(op_trash(platform, m.group("t"), cwd))
    if re.search(r"\b(?:shut\s*down|power\s+off|turn\s+off)\b(?:\s+(?:the|my)?\s*(?:computer|machine|pc|system|laptop))?", low) \
            and not re.search(r"\b(?:wi-?fi|bluetooth|screen|monitor|display)\b", low):
        return _one(op_power(platform, "shutdown"))
    if re.search(r"\b(?:reboot|restart\s+(?:the|my)?\s*(?:computer|machine|pc|system|laptop))\b", low):
        return _one(op_power(platform, "restart"))
    m = re.search(r"\b(?:kill|stop|end|terminate)\s+(?:the\s+)?(?:process\s+)?(?:pid\s+)?(\d+)\b", text, re.I)
    if m:
        return _one(op_kill_pid(platform, int(m.group(1))))
    m = re.search(r"\b(?:kill|terminate|force[\s-]+(?:quit|close)|end\s+task)\s+(?:the\s+)?(?:process\s+|app\s+)?(?P<n>[\w .\-]+)$", text, re.I)
    if m:
        return _one(op_kill_name(platform, m.group("n")))
    m = re.search(r"^\s*(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+|my\s+)?(?P<n>[\w .\-+]+?)(?:\s+app)?\s*[.!]?$", text, re.I)
    if m and not re.search(r"\b(?:file|folder|document|pdf|docx?|notes?|it|that|this|one)\b", m.group("n"), re.I):
        return [op_open_app(platform, m.group("n"))], None
    m = re.search(r"^\s*(?:please\s+)?(?:close|quit|exit)\s+(?:the\s+|my\s+)?(?P<n>[\w .\-+]+?)(?:\s+(?:app|window))?\s*[.!]?$", text, re.I)
    if m:
        return [op_close_app(platform, m.group("n"))], None
    m = re.search(r"^\s*(?:please\s+)?(?:switch|go|change)\s+(?:back\s+)?to\s+(?:the\s+|my\s+)?(?P<n>[\w .\-+]+?)(?:\s+window)?\s*[.!]?$|"
                  r"^\s*(?:focus|bring\s+up|show)\s+(?:the\s+|my\s+)?(?P<n2>[\w .\-+]+?)\s+window\s*[.!]?$", text, re.I)
    if m:
        return [op_switch_app(platform, m.group("n") or m.group("n2"))], None
    if re.search(r"\b(?:volume|sound|audio)\b|\b(?:mute|unmute)\b|\b(?:louder|quieter|softer)\b", low):
        pct = _PCT.search(low)
        action = ("unmute" if re.search(r"\bunmute\b", low) else "mute" if re.search(r"\bmute\b", low)
                  else "set" if pct and re.search(r"\b(?:set|to|at)\b", low)
                  else "up" if re.search(r"\b(?:up|raise|increase|louder|higher|turn\s+it\s+up)\b", low)
                  else "down" if re.search(r"\b(?:down|lower|decrease|quieter|softer|reduce)\b", low) else None)
        if action:
            return _one(op_volume(platform, action, int(pct.group(1)) if pct else 10))
    if re.search(r"\b(?:brightness|brighter|dimmer|dim\s+the\s+screen|screen\s+(?:brighter|darker))\b", low):
        pct = _PCT.search(low)
        delta = 0 if pct else (15 if re.search(r"\b(?:up|brighter|increase|raise)\b", low) else -15)
        return [op_brightness(platform, int(pct.group(1)) if pct else None, delta)], None
    m = re.search(r"\b(?P<what>wi-?fi|wireless|bluetooth)\b", low)
    if m:
        kind = "Bluetooth" if m.group("what").startswith("blue") else "WiFi"
        state = "On" if re.search(r"\b(?:on|enable|start|connect)\b", low) else \
            ("Off" if re.search(r"\b(?:off|disable|stop|disconnect)\b", low) else None)
        return [op_radio(platform, kind, state)], None
    if re.search(r"\b(?:disk\s+(?:space|usage)|free\s+space|storage\s+(?:left|space)|drive\s+space|how\s+full)\b", low):
        return [op_disk(platform)], None
    if re.search(r"\b(?:memory|ram)\s+(?:usage|use|left|free)\b|\bfree\s+memory\b|\bhow\s+much\s+(?:memory|ram)\b", low):
        return [op_memory(platform)], None
    if re.search(r"\buptime\b|\bhow\s+long\s+(?:has\s+)?(?:the\s+)?(?:system|machine|computer|pc)\s+been\s+(?:up|on|running)\b", low):
        return [op_uptime(platform)], None
    if re.search(r"\b(?:running\s+processes|top\s+processes|what'?s\s+running|list\s+processes|what'?s\s+(?:using|eating)\s+(?:my\s+)?(?:cpu|memory|ram))\b|\bcpu\s+usage\b", low):
        return [op_processes(platform)], None
    if re.search(r"\b(?:current|working)\s+directory\b|\bwhere\s+am\s+i\b", low):
        return [op_pwd(platform)], None
    if re.search(r"\bgit\s+status\b", low):
        return [op_git_status(platform)], None
    m = re.search(r"\blist\s+(?:the\s+)?files(?:\s+in\s+(?P<p>.+))?$", text, re.I)
    if m:
        return [op_list_files(platform, m.group("p") or "")], None
    m = re.search(r"\b(?:make|create|new)\s+(?:a\s+)?(?:folder|directory|dir)\s+(?:called\s+|named\s+)?(?P<t>.+)$", text, re.I)
    if m:
        return _one(op_create(platform, m.group("t"), "folder"))
    m = re.search(r"\b(?:make|create|new)\s+(?:a\s+|an\s+)?(?:empty\s+)?file\s+(?:called\s+|named\s+)?(?P<t>.+)$", text, re.I)
    if m:
        return _one(op_create(platform, m.group("t"), "file"))
    return [], None


_RADIO_PS = (
    "Add-Type -AssemblyName System.Runtime.WindowsRuntime\n"
    "$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and "
    "$_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]\n"
    "Function Await($WinRtTask, $ResultType) { $asTask = $asTaskGeneric.MakeGenericMethod($ResultType); "
    "$netTask = $asTask.Invoke($null, @($WinRtTask)); $netTask.Wait(-1) | Out-Null; $netTask.Result }\n"
    "[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null\n"
    "[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null\n"
    "[void](Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]))\n"
    "$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) "
    "([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])\n"
)


def split_steps(goal: str) -> List[str]:
    parts = [p.strip(" ,.") for p in re.split(r"\s*(?:,\s*)?\b(?:and\s+then|then|after\s+that|and)\b\s*", goal or "", flags=re.I)]
    return [p for p in parts if p]


# ═══════════════════════════════════════════════════════════════
# Agent
# ═══════════════════════════════════════════════════════════════
class SystemOpsAgent(BaseAgent):
    name = "system_ops"
    description = ("this computer: disk/memory/CPU/process info explained in plain words, delete to Recycle Bin and "
                   "undo, empty the bin, clean temp files, open/close/switch apps, volume, brightness, Wi-Fi, "
                   "Bluetooth, end processes, shutdown/restart, folders, watching the system for problems")
    # Per-tool tiers are what count; this is the agent's baseline.
    default_tier = RiskTier.REVERSIBLE
    route_keywords = (
        "run", "execute", "terminal", "command", "kill", "terminate", "shutdown", "shut down",
        "reboot", "restart", "delete", "remove", "erase", "disk space", "free space", "disk usage",
        "memory usage", "ram usage", "uptime", "running processes", "processes", "where am i",
        "current directory", "git status", "list files", "create folder", "make folder",
        "create directory", "make directory", "create file", "power off",
        "open", "launch", "close", "quit", "switch to", "volume", "mute", "unmute", "louder", "quieter",
        "brightness", "wifi", "wi-fi", "bluetooth", "recycle bin", "trash", "undo", "undelete", "temp files",
        "clean up", "cpu", "watch my system", "monitor my system", "stop watching", "dry run", "how full",
    )
    # Action requests that mention files ("delete my notes file") are ours, not research.
    route_overrides = ("local_research",)
    timeout_s = 300.0

    platform = CURRENT_PLATFORM

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._started_at: Optional[float] = None

    # ── Dependencies (overridable / mockable) ───────────────────
    def _commander(self):
        return system_commander.get_system_commander()

    # ── Execution ───────────────────────────────────────────────
    def _execute(self, cmd: str, timeout: float = 60.0, display: Optional[str] = None,
                 explain: Optional[Callable[[dict], str]] = None) -> AgentResult:
        """Blocking: run through the existing SystemCommander and summarise. Called off-loop."""
        shown = display or cmd
        if "\n" in shown or len(shown) > 160:  # long built-in script: talk about the task, not the code
            shown = f"that ({_short(self.goal or 'the task', 60)})"
        self._started_at = time.time()
        try:
            res = self._commander().execute(cmd, timeout=timeout)
        except TypeError:  # commander without a timeout parameter
            res = self._commander().execute(cmd)
        brief = _brief_output(res)
        self.hud({"type": "system_command", "command": shown, "exit_code": res.get("exit_code"),
                  "stdout": res.get("stdout", ""), "stderr": res.get("stderr", ""),
                  "elapsed_sec": res.get("elapsed_sec")})
        if str(res.get("error") or "").startswith("Blocked"):
            # SystemCommander's hard guard refuses some commands even after confirmation.
            return AgentResult(status="failed", data=res,
                               summary=f"`{shown}` is on SystemCommander's hard block list, so it was not run")
        if "[Command timed out after" in (res.get("stderr") or ""):
            return AgentResult(status="failed", data=res, summary=f"{_tick(shown)} hung, so I stopped it after {int(timeout)} seconds")
        if res.get("success"):
            plain = ""
            if explain:
                try:
                    plain = explain(res)
                except Exception:
                    plain = ""
            if plain:
                return AgentResult(status="done", summary=plain, data=res)
            summary = f"{_tick(shown)} finished" + (f": {brief}" if brief else " with no output")
            return AgentResult(status="done", summary=summary, data=res)
        err = _brief_output({"stdout": "", "stderr": _ps_error(res.get("stderr") or "")}) or brief
        summary = f"{_tick(shown)} failed (exit {res.get('exit_code')})" + (f": {err}" if err else "")
        return AgentResult(status="failed", summary=summary, data=res)

    def _run_op(self, op: Op) -> AgentResult:
        if op.kind == "python":
            return op.fn()
        return self._execute(op.exec_cmd or op.display, timeout=op.timeout, display=op.display, explain=op.explain)

    def _run_ops(self, ops: List[Op]) -> AgentResult:
        if len(ops) == 1:
            return self._run_op(ops[0])
        parts, data, ok = [], [], True
        for op in ops:
            r = self._run_op(op)
            parts.append(r.summary.rstrip(". "))
            data.append(r.data)
            if r.status != "done":
                ok = False
                break
        return AgentResult(status="done" if ok else "failed", summary=". Then ".join(parts), data=data)

    def on_cancel(self) -> None:
        """Voice cancel / timeout: kill the shell commands this agent started."""
        if self._started_at is None:
            return
        try:
            import psutil
            for child in psutil.Process().children(recursive=True):
                try:
                    if child.create_time() >= self._started_at - 0.5:
                        child.kill()
                except Exception:
                    pass
        except Exception:
            pass

    # ── The guard every tool runs through ──────────────────────
    def _guard(self, op: Op) -> Optional[str]:
        """Checks that hold whoever asked: a model, the keyword path, a restored action."""
        if op.kind == "shell":
            bad = _mentions_protected(op.display)
            if bad and op.tier is not RiskTier.READ:
                return f"I won't run that: it touches {bad}, which is protected."
        return None

    async def _run_guarded(self, built: Any, args: dict, ctx: dict) -> AgentResult:
        """Build -> guard -> (dry run | refuse unconfirmed destructive | execute)."""
        ops, problem = (built if isinstance(built, tuple) else ([built], None))
        if isinstance(ops, Op):
            ops = [ops]
        if problem:
            return AgentResult(status="failed", summary=problem)
        if not ops or ops[0] is None:
            return AgentResult(status="failed", summary="Nothing to do.")
        for op in ops:
            refusal = self._guard(op)
            if refusal:
                return AgentResult(status="failed", summary=refusal)
        shown = "; then ".join(op.display for op in ops)
        if args.get("dry_run"):
            risky = any(op.tier is RiskTier.DESTRUCTIVE for op in ops)
            return AgentResult(status="done", data={"dry_run": True, "ops": [o.display for o in ops]},
                               summary=f"Dry run, nothing executed. I would run: {_short(shown, 300)}"
                                       + (" (and I'd ask you first)" if risky else ""))
        if any(op.tier is RiskTier.DESTRUCTIVE for op in ops) and not ctx.get("confirmed"):
            # Defence in depth: even if something upstream skipped the approval step.
            return AgentResult(status="failed", summary="That needs your go-ahead first; nothing was run.")
        if "run_id" not in ctx or all(op.tier is not RiskTier.READ for op in ops):
            # In the loop a read tool already announced itself (its progress line).
            readable = len(shown) <= 160 and "\n" not in shown
            self.say("Running " + (_tick(_short(shown, 120)) if readable else "it"))
        try:
            return await asyncio.to_thread(self._run_ops, ops)
        except Exception as exc:
            return AgentResult(status="failed", summary=f"{_tick(_short(shown))} could not be started ({type(exc).__name__}).")

    def _preview(self, built: Any) -> AgentResult:
        """The read-back for a confirmed tool: exactly what would run, plus its note."""
        ops, problem = (built if isinstance(built, tuple) else ([built], None))
        if isinstance(ops, Op):
            ops = [ops]
        if problem:
            return AgentResult(status="failed", summary=problem)
        for op in ops:
            refusal = self._guard(op)
            if refusal:
                return AgentResult(status="failed", summary=refusal)
        notes = " ".join(dict.fromkeys(op.note for op in ops if op.note))
        if len(ops) == 1 and ops[0].kind == "shell" and len(ops[0].display) <= 160 and "\n" not in ops[0].display:
            summary = f"Run `{ops[0].display}`?" + (f" {notes}" if notes else "")
        else:
            summary = f"{_capital('; then '.join(op.display if op.kind == 'python' else _short(self.goal or 'that', 80) for op in ops))}?" \
                      + (f" {notes}" if notes else "")
        replay = {"op": "sequence", "steps": [o.replay for o in ops]} if len(ops) > 1 else ops[0].replay
        return AgentResult(status="done", summary=summary, data={"cmd": "; then ".join(o.display for o in ops), "replay": replay})

    # ── Catalogue ──────────────────────────────────────────────
    def tools(self) -> List[ToolSpec]:
        R, REV, D = RiskTier.READ, RiskTier.REVERSIBLE, RiskTier.DESTRUCTIVE
        P = self.platform
        dry = {"dry_run": {"type": "boolean", "description": "Only say what would run"}}

        def obj(props: dict, required: Tuple[str, ...] = ()) -> dict:
            return {"type": "object", "properties": {**props, **dry}, "required": list(required)}

        def tool(name, desc, props, required, category, tier, build, *, confirm=False, progress="",
                 untrusted=False, grounded=(), timeout_note=""):
            async def handler(args, ctx):
                return await self._run_guarded(build(args), args, ctx)

            async def preview(args, ctx):
                return self._preview(build(args))
            needs = tier is D or confirm
            return ToolSpec(name, desc, obj(props, required), category, tier, handler,
                            preview=preview if needs else None, confirm=confirm, untrusted_output=untrusted,
                            grounded_args=grounded, progress=progress)

        name_arg = {"name": {"type": "string", "description": "App name as the operator said it, e.g. 'notepad', 'chrome'"}}
        return [
            # read
            tool("disk_usage", "How full each drive is, and what's taking the space if one is nearly full.", {}, (),
                 "read", R, lambda a: op_disk(P), progress="Checking your drives"),
            tool("memory_usage", "Memory in use and the biggest users.", {}, (), "read", R, lambda a: op_memory(P),
                 progress="Checking memory"),
            tool("cpu_and_processes", "CPU load and the busiest programs.", {}, (), "read", R, lambda a: op_processes(P),
                 progress="Checking what's running"),
            tool("uptime", "How long since the computer was restarted.", {}, (), "read", R, lambda a: op_uptime(P)),
            tool("list_directory", "List a folder's contents.", {"path": {"type": "string"}}, (), "read", R,
                 lambda a: op_list_files(P, str(a.get("path") or "")), untrusted=True),
            tool("git_status", "git status of JARVIS's working folder.", {}, (), "read", R, lambda a: op_git_status(P)),
            tool("radio_status", "Whether Wi-Fi or Bluetooth is on (and the Wi-Fi network).",
                 {"radio": {"type": "string", "enum": ["wifi", "bluetooth"]}}, ("radio",), "read", R,
                 lambda a: op_radio(P, _radio_kind(a), None), progress="Checking the radio"),
            # reversible actions
            tool("open_app", "Start an application.", name_arg, ("name",), "action", REV,
                 lambda a: op_open_app(P, str(a.get("name") or "")), progress="Opening it"),
            tool("close_app", "Ask an application's windows to close (it can still ask to save).", name_arg, ("name",),
                 "action", REV, lambda a: op_close_app(P, str(a.get("name") or "")), progress="Closing it"),
            tool("switch_to_app", "Bring an application's window to the front.", name_arg, ("name",), "action", REV,
                 lambda a: op_switch_app(P, str(a.get("name") or ""))),
            tool("set_volume", "Change the speaker volume.",
                 {"action": {"type": "string", "enum": ["up", "down", "set", "mute", "unmute"]},
                  "percent": {"type": "integer", "description": "Target for 'set', step for up/down (default 10)"}},
                 ("action",), "action", REV, lambda a: op_volume(P, str(a.get("action")), int(a.get("percent") or 10))),
            tool("set_brightness", "Change screen brightness (laptop screens).",
                 {"percent": {"type": "integer"}, "change": {"type": "integer", "description": "+/- step instead of a value"}},
                 (), "action", REV, lambda a: op_brightness(P, a.get("percent"), int(a.get("change") or 0) or (0 if a.get("percent") is not None else 15))),
            tool("set_radio", "Turn Wi-Fi or Bluetooth on or off. Turning Wi-Fi off needs the operator's approval "
                              "(it cuts the internet).",
                 {"radio": {"type": "string", "enum": ["wifi", "bluetooth"]}, "state": {"type": "string", "enum": ["on", "off"]}},
                 ("radio", "state"), "action", REV,
                 lambda a: op_radio(P, _radio_kind(a), "On" if str(a.get("state")).lower() == "on" else "Off"),
                 confirm=lambda a: _radio_kind(a) == "WiFi" and str(a.get("state")).lower() == "off"),
            tool("create_folder", "Create a folder (or an empty file with kind='file').",
                 {"path": {"type": "string"}, "kind": {"type": "string", "enum": ["folder", "file"]}}, ("path",),
                 "action", REV, lambda a: op_create(P, str(a.get("path") or ""), str(a.get("kind") or "folder")),
                 grounded=("path",)),
            tool("restore_deleted", "Put back what JARVIS last moved to the Recycle Bin (optionally by name).",
                 {"name": {"type": "string"}}, (), "action", REV, lambda a: op_undo(P, a.get("name") or None),
                 progress="Restoring it"),
            # confirmed actions
            tool("delete_to_recycle_bin", "Move a file or folder to the Recycle Bin / Trash (can be undone). "
                                          "Protected locations are refused.",
                 {"path": {"type": "string", "description": "Full path, or a name on the Desktop/Downloads/Documents"}},
                 ("path",), "action", D, lambda a: op_trash(P, str(a.get("path") or "")), grounded=("path",)),
            tool("empty_recycle_bin", "Permanently empty the Recycle Bin / Trash.", {}, (), "action", D,
                 lambda a: op_empty_trash(P)),
            tool("clean_temp_files", "Permanently delete old temporary files and report the space freed.",
                 {"older_than_hours": {"type": "number", "description": "Default 24"}}, (), "action", D,
                 lambda a: op_clean_temp(P, float(a.get("older_than_hours") or 24))),
            tool("end_process", "Force a program to quit (unsaved work is lost). By process id or name.",
                 {"pid": {"type": "integer"}, "name": {"type": "string"}}, (), "action", D,
                 lambda a: op_kill_pid(P, int(a["pid"])) if a.get("pid") else op_kill_name(P, str(a.get("name") or ""))),
            tool("power", "Shut down or restart the computer (30-second delay on Windows).",
                 {"action": {"type": "string", "enum": ["shutdown", "restart"]}}, ("action",), "action", D,
                 lambda a: op_power(P, str(a.get("action")))),
            ToolSpec("system_watch", "Start, stop or check the background watch for CPU spikes, full disks, low "
                                     "memory and frozen apps.",
                     {"type": "object", "properties": {"action": {"type": "string", "enum": ["start", "stop", "check"]}},
                      "required": ["action"]},
                     "action", REV, self.system_watch, progress="Checking the system"),
        ]

    async def system_watch(self, args: dict, ctx: dict) -> AgentResult:
        action = str(args.get("action") or "check")
        if action == "stop":
            return AgentResult(status="done", summary="Stopped watching the system" if WATCHER.stop() else "I wasn't watching the system")
        started = WATCHER.start(self.event_bus) if action == "start" else False
        issues = await asyncio.to_thread(WATCHER.check_once)
        now = "; ".join(f for _, f, _ in issues) or "nothing wrong right now"
        lead = ("Watching CPU, disk, memory and frozen apps; I'll speak up if anything goes wrong. "
                if started else ("Already watching. " if action == "start" else ""))
        return AgentResult(status="done", summary=f"{lead}{_capital(now)}", data={"issues": [f for _, f, _ in issues]})

    # ── Fallback entry point (no planning model) ───────────────
    async def run(self, goal: str, context: dict) -> AgentResult:
        """Keyword path, used only when the tool loop can't run (no Groq). Same guard."""
        context = context or {}
        text = (goal or "").strip()

        if re.search(r"\b(?:stop|quit|end)\s+(?:watching|monitoring)\b", text, re.I):
            return await self.system_watch({"action": "stop"}, {})
        if re.search(r"\b(?:watch|monitor|keep\s+an\s+eye\s+on)\s+(?:my\s+|the\s+)?(?:system|computer|pc|machine|laptop)\b", text, re.I):
            return await self.system_watch({"action": "start"}, {})
        if WATCHER.enabled and not WATCHER.running():
            WATCHER.start(self.event_bus)  # background watching starts with the first system request

        dry = bool(context.get("dry_run")) or bool(_DRY_RUN_RE.match(text))
        text = _DRY_RUN_RE.sub("", text) if dry else text

        cmd_override = context.get("command")
        if cmd_override:
            ops, problem = [shell_op(cmd_override, platform=self.platform)], None
        else:
            ops, problem = plan_ops(text, self.platform, context.get("cwd"))
            if not ops and problem is None:
                steps = [s for s in split_steps(text) if not _REPORT_ONLY.match(s)]
                if len(steps) > 1:
                    planned = [plan_ops(s, self.platform, context.get("cwd")) for s in steps]
                    if all(p[0] for p in planned):
                        ops = [op for p in planned for op in p[0]]
                    else:
                        problem = next((p[1] for p in planned if p[1]), None)
        if problem:
            return AgentResult(status="failed", summary=problem)
        if not ops:
            return AgentResult(status="failed",
                               summary="I couldn't work out which command you meant. Try: run `<command>`.")
        for op in ops:
            refusal = self._guard(op)
            if refusal:
                return AgentResult(status="failed", summary=refusal)
        if dry:
            return await self._run_guarded((ops, None), {"dry_run": True}, {})
        if any(op.tier is RiskTier.DESTRUCTIVE for op in ops):
            prev = self._preview((ops, None))
            agent = self
            return self.confirmation_required(prev.summary, lambda: agent._run_ops(ops),
                                              cmd=prev.data["cmd"], replay=prev.data["replay"])
        return await self._run_guarded((ops, None), {}, {})

    # ── Restart survival (used by the supervisor) ──────────────
    @classmethod
    def rehydrate(cls, event_bus, replay: Dict[str, Any]) -> Optional[Callable[[], AgentResult]]:
        agent = cls(event_bus, f"system_ops-restored-{uuid.uuid4().hex[:6]}", name=cls.name)

        def build(spec: Dict[str, Any]) -> Optional[Op]:
            kind = spec.get("op")
            if kind == "shell":
                return shell_op(spec["display"], platform=agent.platform, powershell=spec.get("powershell"),
                                timeout=float(spec.get("timeout") or 60))
            if kind == "trash":
                paths = list(spec.get("paths") or [])
                return Op("python", "trash", RiskTier.DESTRUCTIVE, fn=lambda: Trash.send(paths))
            if kind == "empty_trash":
                return Op("python", "empty", RiskTier.DESTRUCTIVE, fn=Trash.empty)
            if kind == "clean_temp":
                dirs, hours = list(spec.get("dirs") or []), float(spec.get("hours") or 24)
                return Op("python", "clean temp", RiskTier.DESTRUCTIVE, fn=lambda: clean_temp(dirs, hours))
            if kind == "undo":
                hint = spec.get("hint")
                return Op("python", "undo", RiskTier.REVERSIBLE, fn=lambda: Trash.undo(hint))
            return None

        specs = replay.get("steps") if replay.get("op") == "sequence" else [replay]
        ops = [build(s) for s in specs or []]
        if not ops or any(o is None for o in ops):
            return None
        for op in ops:
            if agent._guard(op):  # a restored action is checked again, as if it were new
                return None
        return lambda: agent._run_ops(ops)  # type: ignore[arg-type]


def _radio_kind(args: dict) -> str:
    return "Bluetooth" if str(args.get("radio") or "").lower().startswith("blue") else "WiFi"


def _tick(shown: str) -> str:
    return shown if shown.startswith("that (") else f"`{_short(shown)}`"


def _ps_error(stderr: str) -> str:
    """First meaningful line of a PowerShell error (drops the CategoryInfo/FullyQualifiedErrorId noise)."""
    lines = [l.strip() for l in stderr.splitlines() if l.strip() and not re.match(
        r"^(?:\+|At line|CategoryInfo|FullyQualifiedErrorId|~|#< CLIXML|<Objs)", l.strip())]
    return lines[0] if lines else stderr.strip()


def _short(text: str, n: int = 160) -> str:
    text = str(text or "").replace("\n", "; ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text
