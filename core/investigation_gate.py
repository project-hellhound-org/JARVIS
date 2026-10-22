"""
Central Investigation Gate Registry for J.A.R.V.I.S.

Provides a clean architectural separation so that core modules (skills, reasoning loop,
agent router) never import frontend.desktop directly.
JarvisAPI registers its central gate handler at startup via register_handler.
If no handler is registered (e.g. CLI or standalone core tests), it falls back to
direct investigation execution.
"""

from typing import Callable, Optional, Dict, Any
import threading

_gate_lock = threading.Lock()
_registered_handler: Optional[Callable[..., Dict[str, Any]]] = None


def register_handler(handler: Callable[..., Dict[str, Any]]) -> None:
    """Registers the central investigation gate handler (typically JarvisAPI.request_investigation)."""
    global _registered_handler
    with _gate_lock:
        _registered_handler = handler


def unregister_handler(handler: Optional[Callable] = None) -> None:
    """Unregisters the investigation handler."""
    global _registered_handler
    with _gate_lock:
        if handler is None or _registered_handler == handler:
            _registered_handler = None


def get_handler() -> Optional[Callable[..., Dict[str, Any]]]:
    """Returns the currently registered investigation handler, if any."""
    with _gate_lock:
        return _registered_handler


def request_investigation(
    text_or_target: str,
    source: str = "typed",
    via: str = "direct",
    **kwargs
) -> Dict[str, Any]:
    """
    Dispatches an investigation request through the central gate.
    
    If a handler is registered (UI desktop environment), delegates to it.
    If no handler is registered (CLI or headless tests), falls back to direct
    investigation execution.
    
    Returns a contract dictionary:
      {"handled": bool, "action": "brief_modal" | "started" | "clarify", "target": str, ...}
    """
    handler = get_handler()
    if handler is not None:
        return handler(text_or_target, source=source, via=via, **kwargs)
    
    # Standalone headless / CLI fallback
    return _default_fallback_investigate(text_or_target, source=source, via=via, **kwargs)


def _default_fallback_investigate(
    text_or_target: str,
    source: str = "typed",
    via: str = "direct",
    **kwargs
) -> Dict[str, Any]:
    """Fallback investigation runner when running without a registered UI handler."""
    raw = str(text_or_target or "").strip()
    from core.agent_router import extract_investigation_intent, normalize_sentence, _clean_target_str
    from core.orchestrator import Orchestrator
    import asyncio

    inv = extract_investigation_intent(normalize_sentence(raw), raw_text=raw)
    target_str = inv["target"] if inv else _clean_target_str(raw)
    if not target_str:
        return {
            "handled": True,
            "action": "clarify",
            "target": "",
            "message": "No target specified for investigation."
        }

    orch = Orchestrator()
    kwargs_to_pass = {k: v for k, v in kwargs.items() if k in ("brief", "plan", "task_id") and v is not None}

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                res = pool.submit(lambda: asyncio.run(orch.investigate(target_str, **kwargs_to_pass))).result()
        else:
            res = loop.run_until_complete(orch.investigate(target_str, **kwargs_to_pass))
    except RuntimeError:
        res = asyncio.run(orch.investigate(target_str, **kwargs_to_pass))

    return {
        "handled": True,
        "action": "started",
        "target": target_str,
        "result": res
    }
