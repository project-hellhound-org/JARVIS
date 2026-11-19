# core/agents/desktop_bridge.py
"""
AgentBridge -- connects the threaded desktop app to the asyncio agent framework.

The desktop app (frontend/desktop.py) is thread-based. The supervisor, the agents
and the NarrationQueue are asyncio-based. This bridge owns ONE long-lived event
loop running in its own daemon thread, creates one NarrationQueue + one
JarvisSupervisor on it, and exposes a small blocking-but-fast API for the
desktop's input worker threads:

    outcome = bridge.submit(text, ctx)
    if outcome.handled: return          # agents took it (or it was a yes/no)
    ...existing chat path...

``submit()`` only waits for routing/dispatch (``supervisor.handle`` returns task
ids immediately; agents run in the background), bounded by ``dispatch_timeout``.
On timeout or any error the request falls through to the chat path.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from core.agents.narration_queue import NarrationQueue
from core.agents.supervisor import JarvisSupervisor

# ── Spoken yes / no while an action is waiting on confirmation ──────────
_FILLER_WORDS = r"(?:jarvis|please|sir|ma'?am|thanks|thank you|okay|ok|then|just|now)"
_YES_CORE = (
    r"yes|yeah|yep|yup|ya|yea|sure|affirmative|confirm(?:ed)?|approved?|proceed|"
    r"do it|go ahead|go for it|send it|go on|make it so|absolutely|of course|correct|right"
)
_NO_CORE = (
    r"no|nope|nah|negative|cancel(?:led)?|abort|stop|don'?t|do not|don'?t do it|"
    r"hold off|never ?mind|forget it|leave it|deny|denied"
)
_YES_RE = re.compile(rf"^(?:{_FILLER_WORDS}\s+)*(?:{_YES_CORE})(?:\s+(?:{_YES_CORE}|{_FILLER_WORDS}))*$")
_NO_RE = re.compile(rf"^(?:{_FILLER_WORDS}\s+)*(?:{_NO_CORE})(?:\s+(?:{_NO_CORE}|{_FILLER_WORDS}))*$")


def parse_confirmation(text: str) -> Optional[bool]:
    """True for a spoken "yes", False for "no", None for anything else.

    Deliberately strict: only short, unambiguous replies count, so a new request
    like "no, find my tax notes instead" is not swallowed as a denial.
    """
    norm = re.sub(r"[^\w\s']", " ", (text or "").lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm or len(norm.split()) > 6:
        return None
    if _NO_RE.match(norm):
        return False
    if _YES_RE.match(norm):
        return True
    return None


# ── Phrases the existing app already owns ───────────────────────────────
# The agents' keyword router is broad ("find", "search", "run", "book"...). These
# cues belong to existing desktop fast paths (globe/tactical layers, OSINT gate,
# window/screen navigation), so such requests skip the agents entirely.
_DEFER_TO_APP_RE = re.compile(
    r"\b(?:flights?|aircraft|planes?|airspace|radar|weather|traffic|satellites?|iss|orbit(?:al)?|"
    r"vessels?|ships?|conflicts?|globe|map|earth|telemetry|god'?s eye|hud|console|cold room|"
    r"investigat\w*|osint|recon|dossier|stalk\w*|username|geolocat\w*|"
    r"navigate to|fly to|go to|zoom (?:in|out|to)|route (?:to|from)|directions)\b"
)


def defers_to_app(text: str) -> bool:
    return bool(_DEFER_TO_APP_RE.search((text or "").lower()))


@dataclass
class BridgeOutcome:
    handled: bool
    kind: str = "none"  # "agents" | "confirmation" | "none"
    task_ids: List[str] = field(default_factory=list)


class AgentBridge:
    def __init__(
        self,
        speak_fn: Callable[[str], Any],
        *,
        display_fn: Optional[Callable[[str], Any]] = None,
        is_busy_fn: Optional[Callable[[], bool]] = None,
        salutation_fn: Optional[Callable[[], Optional[str]]] = None,
        phrase_fn: Optional[Callable[[str, dict], str]] = None,
        event_bus=None,
        registry: Optional[dict] = None,
        dispatch_timeout: float = 2.0,
        busy_wait_max: float = 45.0,
        busy_poll: float = 0.15,
        narration_kwargs: Optional[dict] = None,
    ):
        if event_bus is None:
            from core.event_bus import get_event_bus
            event_bus = get_event_bus()
        if registry is None:
            from core.agents.registry import AGENT_REGISTRY
            registry = AGENT_REGISTRY
        if phrase_fn is None:
            from narrative.jarvis_voice import phrase_agent_line
            phrase_fn = phrase_agent_line

        self._speak_fn = speak_fn
        self._display_fn = display_fn
        self._is_busy_fn = is_busy_fn
        self._salutation_fn = salutation_fn
        self._phrase_fn = phrase_fn
        self.dispatch_timeout = dispatch_timeout
        self.busy_wait_max = busy_wait_max
        self.busy_poll = busy_poll
        self._stopping = threading.Event()

        self.narration = NarrationQueue(
            event_bus,
            tts_speak_fn=self._speak_line,
            persona_phrase_fn=self._phrase,
            **(narration_kwargs or {}),
        )
        self.supervisor = JarvisSupervisor(event_bus, self.narration, registry)

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._worker: Optional[asyncio.Task] = None

    # ── Lifecycle ───────────────────────────────────────────────
    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    def is_running(self) -> bool:
        return self._loop is not None and self._loop.is_running() and not self._stopping.is_set()

    def start(self) -> "AgentBridge":
        if self._thread is not None:
            return self
        ready = threading.Event()

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._worker = loop.create_task(self.narration.start(), name="narration-worker")
            loop.call_soon(ready.set)
            try:
                loop.run_forever()
            finally:
                try:
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                finally:
                    loop.close()

        self._thread = threading.Thread(target=_run, name="jarvis-agents-loop", daemon=True)
        self._thread.start()
        if not ready.wait(5.0):
            raise RuntimeError("agent event loop did not start")
        return self

    def stop(self, timeout: float = 3.0) -> None:
        self._stopping.set()
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self.narration.stop)
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        if self._thread is not None:
            self._thread.join(timeout)

    # ── Input path (called from desktop worker threads) ─────────
    def submit(self, text: str, context: Optional[dict] = None) -> BridgeOutcome:
        """Route one user utterance. Returns quickly; never waits for agents to finish."""
        if not self.is_running() or not (text or "").strip():
            return BridgeOutcome(False)
        fut = asyncio.run_coroutine_threadsafe(self._dispatch(text, dict(context or {})), self._loop)
        try:
            return fut.result(timeout=self.dispatch_timeout)
        except FutureTimeout:
            fut.cancel()
            print("[agents] dispatch timed out; falling back to chat")
        except Exception as exc:
            print(f"[agents] dispatch failed ({type(exc).__name__}); falling back to chat")
        return BridgeOutcome(False)

    async def _dispatch(self, text: str, context: dict) -> BridgeOutcome:
        # Runs on the agent loop, so reading supervisor.pending here is race-free.
        pending = self.supervisor.pending_task_ids()
        if pending:
            answer = parse_confirmation(text)
            if answer is not None:
                await self.supervisor.confirm(pending[-1], answer)
                return BridgeOutcome(True, "confirmation", [pending[-1]])
        if defers_to_app(text):
            return BridgeOutcome(False)
        task_ids = await self.supervisor.handle(text, context)
        if task_ids:
            return BridgeOutcome(True, "agents", list(task_ids))
        return BridgeOutcome(False)

    def pending_task_ids(self) -> List[str]:
        if not self.is_running():
            return []
        fut = asyncio.run_coroutine_threadsafe(self._pending(), self._loop)
        try:
            return fut.result(timeout=self.dispatch_timeout)
        except Exception:
            return []

    async def _pending(self) -> List[str]:
        return self.supervisor.pending_task_ids()

    # ── Narration callbacks (run in asyncio.to_thread workers) ──
    def _phrase(self, fact: str, context: dict) -> str:
        ctx = dict(context or {})
        if self._salutation_fn and not ctx.get("salutation"):
            try:
                sal = self._salutation_fn()
                if sal:
                    ctx["salutation"] = sal
            except Exception:
                pass
        return self._phrase_fn(fact, ctx)

    def _speak_line(self, text: str) -> None:
        """Speak one narration line through the app's TTS without talking over anything.

        Waits (bounded) while the app is already speaking or the operator is
        holding push-to-talk; barge-in during this line is handled by the app's
        own cancel path, which interrupts the underlying speak call.
        """
        if self._is_busy_fn is not None:
            deadline = time.monotonic() + self.busy_wait_max
            while not self._stopping.is_set() and time.monotonic() < deadline:
                try:
                    if not self._is_busy_fn():
                        break
                except Exception:
                    break
                time.sleep(self.busy_poll)
        if self._stopping.is_set():
            return
        if self._display_fn is not None:
            try:
                self._display_fn(text)
            except Exception as exc:
                print(f"[agents] display failed ({type(exc).__name__})")
        self._speak_fn(text)
