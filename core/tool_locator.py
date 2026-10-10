# core/tool_locator.py
"""
OSINT and system tool locator for J.A.R.V.I.S.
Resolves tool binaries across standard and user installation paths:
  1. shutil.which(name)
  2. ~/.local/bin
  3. any ~/.local/pipx/venvs/*/bin (and ~/.local/share/pipx/venvs/*/bin)
  4. active venv's bin (sys.prefix / "bin")
  5. /usr/local/bin
  6. ~/go/bin
"""

import os
import sys
import shutil
from pathlib import Path
from typing import Dict, List, Optional

_TOOL_CACHE: Dict[str, Optional[str]] = {}


def clear_tool_cache() -> None:
    """Clear internal tool lookup cache (primarily for tests)."""
    _TOOL_CACHE.clear()


def find_tool(name: str) -> Optional[str]:
    """
    Locate an executable binary by name across standard and user directories.
    Results are cached for the lifetime of the process.
    """
    if name in _TOOL_CACHE:
        return _TOOL_CACHE[name]

    resolved_path: Optional[str] = None

    # 1. shutil.which(name)
    which_path = shutil.which(name)
    if which_path:
        resolved_path = os.path.abspath(which_path)

    # 2. ~/.local/bin
    home = Path.home()
    if not resolved_path:
        local_bin = home / ".local" / "bin" / name
        if local_bin.is_file() and os.access(local_bin, os.X_OK):
            resolved_path = str(local_bin.resolve())

    # 3. any ~/.local/pipx/venvs/*/bin or ~/.local/share/pipx/venvs/*/bin
    if not resolved_path:
        pipx_dirs = [
            home / ".local" / "pipx" / "venvs",
            home / ".local" / "share" / "pipx" / "venvs",
        ]
        for p_dir in pipx_dirs:
            if p_dir.is_dir():
                for venv_bin in p_dir.glob("*/bin"):
                    candidate = venv_bin / name
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        resolved_path = str(candidate.resolve())
                        break
            if resolved_path:
                break

    # 4. the active venv's bin
    if not resolved_path:
        active_bin = Path(sys.prefix) / "bin" / name
        if active_bin.is_file() and os.access(active_bin, os.X_OK):
            resolved_path = str(active_bin.resolve())

    # 5. /usr/local/bin
    if not resolved_path:
        usr_local_bin = Path("/usr/local/bin") / name
        if usr_local_bin.is_file() and os.access(usr_local_bin, os.X_OK):
            resolved_path = str(usr_local_bin.resolve())

    # 6. ~/go/bin
    if not resolved_path:
        go_bin = home / "go" / "bin" / name
        if go_bin.is_file() and os.access(go_bin, os.X_OK):
            resolved_path = str(go_bin.resolve())

    _TOOL_CACHE[name] = resolved_path
    return resolved_path


# Attach cache_clear to find_tool for convenient test patching
find_tool.cache_clear = clear_tool_cache  # type: ignore


def build_subprocess_env() -> Dict[str, str]:
    """
    Construct an environment dictionary with tool binary paths appended to PATH,
    and Python user site-packages appended to PYTHONPATH so tools that call each
    other (and Python scripts installed in user site-packages) execute correctly.
    """
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    paths = current_path.split(os.pathsep) if current_path else []

    home = Path.home()
    extra_dirs = [
        str(home / ".local" / "bin"),
    ]

    pipx_dirs = [
        home / ".local" / "pipx" / "venvs",
        home / ".local" / "share" / "pipx" / "venvs",
    ]
    for p_dir in pipx_dirs:
        if p_dir.is_dir():
            for venv_bin in p_dir.glob("*/bin"):
                if venv_bin.is_dir():
                    extra_dirs.append(str(venv_bin))

    active_bin = Path(sys.prefix) / "bin"
    if active_bin.is_dir():
        extra_dirs.append(str(active_bin))

    extra_dirs.extend([
        "/usr/local/bin",
        str(home / "go" / "bin"),
    ])

    for d in extra_dirs:
        if d not in paths:
            paths.append(d)

    env["PATH"] = os.pathsep.join(paths)

    # Ensure user site-packages are accessible in PYTHONPATH (e.g. holehe under ~/.local/lib/python3.*/site-packages)
    py_paths = env.get("PYTHONPATH", "").split(os.pathsep) if env.get("PYTHONPATH") else []
    user_lib = home / ".local" / "lib"
    if user_lib.is_dir():
        for sp in user_lib.glob("python3.*/site-packages"):
            if sp.is_dir() and str(sp) not in py_paths:
                py_paths.append(str(sp))
    if py_paths:
        env["PYTHONPATH"] = os.pathsep.join(py_paths)

    return env
