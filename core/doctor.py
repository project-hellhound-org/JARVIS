# core/doctor.py
"""
Diagnostic system doctor for J.A.R.V.I.S.
Checks status of required OSINT tools and configured API keys.
"""

import os
import re
import subprocess
from typing import Dict, List
from core.tool_locator import find_tool, build_subprocess_env
from core.config import CONFIG_KEY_SPECS, is_key_configured


def extract_tool_version(tool_path: str, env: dict) -> str:
    """
    Query tool version using --version or --help with custom subprocess environment.
    Returns clean version string or 'unknown'.
    """
    # 1. Try --version
    try:
        p = subprocess.run(
            [tool_path, "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (p.stdout or "").strip() or (p.stderr or "").strip()
        if p.returncode == 0 and out:
            # Return first non-empty line
            return out.splitlines()[0].strip()
    except Exception:
        pass

    # 2. Try --help (fallback for tools like holehe that do not support --version)
    try:
        p = subprocess.run(
            [tool_path, "--help"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        combined = (p.stdout or "") + "\n" + (p.stderr or "")
        tool_base = os.path.basename(tool_path)
        m = re.search(rf"\b{re.escape(tool_base)}\s+v?(\d+\.\d+(?:\.\d+)?)\b", combined, re.IGNORECASE)
        if m:
            return m.group(0).strip()
        m2 = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", combined)
        if m2:
            return m2.group(0).strip()
    except Exception:
        pass

    return "unknown"


def check_tool_status(tool_name: str) -> Dict[str, str]:
    """Check existence, path, and version of an OSINT tool."""
    path = find_tool(tool_name)
    if not path:
        return {
            "name": tool_name,
            "found": "NO",
            "path": "not found",
            "version": "not found",
        }

    env = build_subprocess_env()
    version = extract_tool_version(path, env)
    return {
        "name": tool_name,
        "found": "YES",
        "path": path,
        "version": version,
    }


def run_doctor() -> None:
    """Run full diagnostic checks and print output to stdout."""
    print("=== JARVIS System Doctor ===")
    print("\nOSINT Tools:")
    tools = ["sherlock", "maigret", "holehe"]
    for t in tools:
        status = check_tool_status(t)
        print(f"[{status['name']}]")
        print(f"  Found: {status['found']}")
        print(f"  Path: {status['path']}")
        print(f"  Version: {status['version']}")

    print("\nOptional API Keys:")
    for key in CONFIG_KEY_SPECS:
        cfg = "YES" if is_key_configured(key) else "NO"
        print(f"  {key}: Configured: {cfg}")
