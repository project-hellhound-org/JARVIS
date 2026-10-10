#!/usr/bin/env python3
# jarvis.py
import sys
import os
from pathlib import Path

# CRITICAL — add project root to sys.path so all modules resolve
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import bootstrap_environment

# Bootstrap configuration, environment variables, fontconfig, and audio flags
bootstrap_environment()

# Handle diagnostic doctor command early without launching GUI or interactive CLI
if any(arg in sys.argv[1:] for arg in ("doctor", "--doctor")):
    from core.doctor import run_doctor
    run_doctor()
    sys.exit(0)

# Check for GUI backend bindings (GTK / Qt)
def _needs_gui_fallback():
    try:
        import gi
        return False
    except ImportError:
        pass
    try:
        import qtpy
        return False
    except ImportError:
        pass
    return True

if _needs_gui_fallback():
    if sys.prefix != sys.base_prefix:
        print("[jarvis] Note: Missing GUI backend bindings (PyGObject / python3-gi or PyQt5) in virtual environment.")
        print("      To enable desktop GUI, install: pip install PyGObject  (or: pip install PyQt5)")
        print("      Continuing in current environment...\n")
    elif os.path.exists("/usr/bin/python3") and sys.executable != "/usr/bin/python3" and "JARVIS_SYS_EXEC" not in os.environ:
        os.environ["JARVIS_SYS_EXEC"] = "1"
        os.execv("/usr/bin/python3", ["/usr/bin/python3"] + sys.argv)

# Now safe to import everything else
def boot_checks():
    issues = []
    import socket
    ollama_online = False
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            ollama_online = True
    except Exception:
        ollama_online = False

    if ollama_online:
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                if not models:
                    issues.append("No local Ollama models pulled.\n  Run: ollama pull <model> (e.g., llama3.2:3b, qwen2.5:3b, gemma2:2b)")
        except ImportError:
            issues.append("HTTP client dependency 'httpx' is missing from active environment.")
        except Exception:
            issues.append("Ollama not responding properly.")
    else:
        issues.append("Ollama not running.\n  Run: ollama serve")

    if issues:
        print("\n[jarvis] Pre-flight notice:\n")
        for issue in issues:
            print(f"  ⓘ {issue}\n")
        print("  J.A.R.V.I.S. can still run using Cloud APIs (NVIDIA NIM / Gemini) or your configured local model.\n")


def launch_cli(initial_target: str = None):
    from tui.jarvis_cli import run
    run(initial_target=initial_target)


def launch_desktop(mode: str = "full"):
    try:
        import webview
        from frontend.desktop import JarvisDesktop
        JarvisDesktop().launch(mode=mode)
    except ImportError as e:
        print(f"[jarvis] Desktop GUI unavailable: {e}")
        print("      To enable the GUI interface, install: pip install pywebview proxy_tools")
        print("      Falling back to CLI...\n")
        launch_cli()
    except Exception as e:
        print(f"[jarvis] Desktop error: {e}")
        print("      Falling back to CLI...\n")
        launch_cli()


def main():
    args = sys.argv[1:]
    if any(arg in args for arg in ("doctor", "--doctor")):
        from core.doctor import run_doctor
        run_doctor()
        return

    boot_checks()

    if "--hud" in args or "--mini" in args or "--pill" in args or "--voiceos" in args:
        launch_desktop(mode="hud")
    elif "--cli" in args or "--tui" in args:
        launch_cli()
    elif args and args[0] in ("investigate", "stalk") and len(args) > 1:
        launch_cli(initial_target=args[1])
    elif args and args[0] == "resume" and len(args) > 1:
        from tui.jarvis_cli import JarvisCLI
        cli = JarvisCLI()
        cli._resume(args[1])
        cli._loop()
    elif args and args[0] in ("-h", "--help"):
        launch_cli()
    else:
        launch_desktop()


if __name__ == "__main__":
    main()