# core/agents/supervisor.py
"""
JarvisSupervisor -- routes a request to agents and runs them in the background.

``handle()`` never awaits an agent: every agent run is dispatched with
``asyncio.create_task`` so the voice/input loop stays responsive (the desktop
bridge gives ``handle()`` ~2 s before falling back to chat).

What it does beyond dispatch:

* Manager   -- agents that expose ``tools()`` (Email so far) are driven by the tool
               loop (tool_loop.py): the model picks tools from one catalogue and the
               loop runs them, with budgets, loop detection, failure escalation,
               provenance checks, and pause/resume around confirmations. Agents not
               converted yet appear in that catalogue as one ``ask_<agent>`` tool.
               The keyword router below stays as the fast path for everything else.
* Events    -- ``handle_event()`` is the second entry point, for things JARVIS
               notices itself (``agent_event`` on the bus); it may read and draft,
               never act.
* Routing   -- LLM planner (Groq, using the existing GROQ_API_KEY; local Ollama as a
               slower backup) that can split a request into steps and chain agents
               ("find X and email it"). Keyword matching stays as the instant fallback.
* Results   -- parallel answers to one request are merged into a single spoken line.
* Control   -- per-task timeout, one retry for read-only agents, cancel by voice
               ("cancel that", "stop the search"), undo of a send still in its window.
* Confirm   -- pending confirmations expire, are numbered when several are waiting,
               and "yes to the second one" is tied to exactly one action.
* Restart   -- pending confirmations and in-flight tasks are persisted to
               data/agents/supervisor_state.json and restored on the next start.
* Audit     -- every dispatch / result / confirmation / execution / cancel is
               appended to data/agents/audit.jsonl (secrets redacted).

Shared helpers used by the agents live here too: ``llm_chat()`` (one LLM entry
point for all agents), ``agent_state_dir()`` and ``write_audit()``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.agents.base_agent import AgentResult, BaseAgent, RiskTier
from core.agents.narration_queue import (
    PRIORITY_CONFIRMATION,
    PRIORITY_FAILURE,
    PRIORITY_RESULT,
)
from core.agents.tool_loop import Budget, ToolLoop, delegate_tool, new_run_id

ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_TASK_TIMEOUT_S = 120.0
DEFAULT_CONFIRMATION_TTL_S = 120.0
LLM_ROUTE_BUDGET_S = 1.8  # must leave room inside the desktop bridge's 2 s dispatch budget
# Planner descriptions for registered agents that don't carry their own ``description``.
_FALLBACK_DESCRIPTIONS = {
    "calendar": "Google Calendar: what's on today/tomorrow/a weekday, move a meeting, schedule an event",
}


# ═══════════════════════════════════════════════════════════════
# Shared infrastructure (state dir, audit log, LLM)
# ═══════════════════════════════════════════════════════════════
def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def agent_state_dir() -> Optional[Path]:
    """Where agents keep runtime state. None (= in-memory only) while tests run,
    so the test-suite never reads or writes the operator's real state."""
    override = os.environ.get("JARVIS_AGENT_STATE_DIR")
    if override:
        return Path(override)
    if _running_under_pytest():
        return None
    return ROOT / "data" / "agents"


def load_json_state(name: str, default: Any) -> Any:
    d = agent_state_dir()
    if d is None:
        return default
    try:
        return json.loads((d / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_state(name: str, data: Any) -> None:
    d = agent_state_dir()
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, d / name)
    except Exception as exc:
        print(f"[agents] could not save {name} ({type(exc).__name__})")


_AUDIT_LOCK = threading.Lock()
AUDIT_MEMORY: List[dict] = []  # always populated (tests and runtime); capped


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        try:
            from narrative.jarvis_voice import redact_secrets
            return redact_secrets(value)
        except Exception:
            return value
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items() if not callable(v)}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def write_audit(event: str, **fields: Any) -> dict:
    """Append one audit record. Secrets are redacted; callables are dropped."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **_redact(fields)}
    with _AUDIT_LOCK:
        AUDIT_MEMORY.append(record)
        del AUDIT_MEMORY[:-500]
        d = agent_state_dir()
        if d is not None:
            try:
                d.mkdir(parents=True, exist_ok=True)
                with open(d / "audit.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
            except Exception:
                pass
    return record


class LLMUnavailable(Exception):
    pass


_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_FALLBACK_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
_llm_cooldown_until = {"groq": 0.0}


def llm_enabled() -> bool:
    flag = os.environ.get("JARVIS_AGENT_LLM")
    if flag is not None:
        return flag.strip() not in ("0", "false", "no", "")
    return not _running_under_pytest()


def _config(name: str, default: str = "") -> str:
    try:
        from core.config import get_config
        return str(get_config(name, default) or default)
    except Exception:
        return default


async def llm_chat(system: str, user: str, *, json_mode: bool = False, max_tokens: int = 700,
                   timeout: float = 15.0, temperature: float = 0.2, allow_local: bool = True) -> str:
    """One LLM entry point for every agent.

    Uses an EXISTING key only: Groq (GROQ_API_KEY). If Groq is missing, rate-limited
    or failing, falls back to the local Ollama model (no key). Raises LLMUnavailable
    when neither answers. Never logs prompts, keys or responses.
    """
    if not llm_enabled():
        raise LLMUnavailable("LLM disabled")
    import httpx

    errors = []
    key = _config("GROQ_API_KEY")
    if key and time.monotonic() >= _llm_cooldown_until["groq"]:
        models = [m for m in [_config("groq_model")] + _GROQ_FALLBACK_MODELS if m]
        seen = set()
        async with httpx.AsyncClient(timeout=timeout) as http:
            for model in models:
                if model in seen:
                    continue
                seen.add(model)
                body: Dict[str, Any] = {
                    "model": model, "temperature": temperature, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                }
                if "gpt-oss" in model:
                    body["reasoning_effort"] = "low"
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                try:
                    resp = await http.post(_GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body)
                except (httpx.HTTPError, OSError) as exc:
                    errors.append(type(exc).__name__)
                    break
                if resp.status_code == 429:
                    _llm_cooldown_until["groq"] = time.monotonic() + 60
                    errors.append("groq rate-limited")
                    break
                if resp.status_code >= 400:
                    errors.append(f"groq {resp.status_code}")
                    continue  # model retired / not found -> try the next one
                content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
                content = _strip_think(content)
                if content:
                    return content
                errors.append("groq empty")
    if allow_local:
        try:
            base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
            body = {"model": os.environ.get("JARVIS_AGENT_LOCAL_MODEL", "qwen2.5:3b"), "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if json_mode:
                body["format"] = "json"
            async with httpx.AsyncClient(timeout=max(timeout, 60.0)) as http:
                resp = await http.post(f"{base}/api/chat", json=body)
                resp.raise_for_status()
                content = _strip_think((resp.json().get("message") or {}).get("content", ""))
                if content:
                    return content
        except Exception as exc:
            errors.append(f"ollama {type(exc).__name__}")
    raise LLMUnavailable("; ".join(errors) or "no LLM configured")


async def llm_chat_tools(messages: List[dict], tools: List[dict], *, max_tokens: int = 900,
                         timeout: float = 20.0, temperature: float = 0.1) -> dict:
    """One tool-calling turn. Groq only (EXISTING GROQ_API_KEY): local models are
    not reliable enough at function calling to drive actions.

    Returns ``{"content": str, "tool_calls": [{"id", "name", "arguments"}], "tokens": int,
    "message": <assistant message to append to the history>}``. ``arguments`` is
    the raw JSON string the model produced. Raises LLMUnavailable.
    """
    if not llm_enabled():
        raise LLMUnavailable("LLM disabled")
    key = _config("GROQ_API_KEY")
    if not key:
        raise LLMUnavailable("no Groq key configured")
    import httpx

    errors = []
    # Each Groq model has its own per-minute limit, so a rate-limited model is
    # skipped (until its cooldown ends) and the next one is tried.
    now = time.monotonic()
    models = [m for m in dict.fromkeys(m for m in [_config("groq_model")] + _GROQ_FALLBACK_MODELS if m)
              if now >= _tool_model_cooldown.get(m, 0.0)]
    if not models:
        raise LLMUnavailable("groq rate-limited")
    async with httpx.AsyncClient(timeout=timeout) as http:
        waited = False
        queue = list(models)
        while queue:
            model = queue.pop(0)
            body: Dict[str, Any] = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                                    "messages": messages}
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"
            if "gpt-oss" in model:
                body["reasoning_effort"] = "low"
            try:
                resp = await http.post(_GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body)
            except (httpx.HTTPError, OSError) as exc:
                errors.append(type(exc).__name__)
                break
            if resp.status_code == 429:
                # Per-minute token limits are common on the free tier: a short wait
                # usually clears it, so wait once if Groq says it's brief.
                retry = _retry_after(resp)
                if not waited and retry is not None and retry <= 4:
                    waited = True
                    await asyncio.sleep(retry + 0.25)
                    queue.insert(0, model)
                    continue
                _tool_model_cooldown[model] = time.monotonic() + min(60.0, retry if retry is not None else 60.0)
                errors.append("groq rate-limited")
                continue  # next model: separate limit
            if resp.status_code >= 400:
                errors.append(f"groq {resp.status_code}")
                continue  # model retired, or doesn't support tools -> next one
            data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            calls = []
            for c in msg.get("tool_calls") or []:
                fn = c.get("function") or {}
                if fn.get("name"):
                    calls.append({"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}", "name": fn["name"],
                                  "arguments": fn.get("arguments") or "{}"})
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                assistant["tool_calls"] = [{"id": c["id"], "type": "function",
                                            "function": {"name": c["name"], "arguments": c["arguments"]}}
                                           for c in calls]
            return {"content": _strip_think(msg.get("content") or ""), "tool_calls": calls,
                    "tokens": int((data.get("usage") or {}).get("total_tokens") or 0), "message": assistant}
    raise LLMUnavailable("; ".join(errors) or "groq unavailable")


def _retry_after(resp) -> Optional[float]:
    """Seconds Groq asks us to wait (Retry-After header, or 'try again in 2.5s' in the body)."""
    try:
        value = resp.headers.get("retry-after")
        if value:
            return float(value)
    except (TypeError, ValueError):
        pass
    try:
        m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", resp.text or "")
        if m:
            return float(m.group(1) or 0) * 60 + float(m.group(2))
    except Exception:
        pass
    return None


_tool_model_cooldown: Dict[str, float] = {}


def tool_llm_available() -> bool:
    """Can the tool loop run at all? (Cheap check: no network.) True while at least
    one Groq model isn't cooling down after a rate limit."""
    if not (llm_enabled() and _config("GROQ_API_KEY")):
        return False
    now = time.monotonic()
    models = [m for m in [_config("groq_model")] + _GROQ_FALLBACK_MODELS if m]
    return any(now >= _tool_model_cooldown.get(m, 0.0) for m in models)


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()


def parse_json_reply(text: str) -> Any:
    """Parse an LLM JSON reply, tolerating ```json fences and leading chatter."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


# ═══════════════════════════════════════════════════════════════
# Control-phrase parsing (cancel / undo / qualified yes-no)
# ═══════════════════════════════════════════════════════════════
_ORDINALS = {"first": 1, "1st": 1, "one": 1, "1": 1, "second": 2, "2nd": 2, "two": 2, "2": 2,
             "third": 3, "3rd": 3, "three": 3, "3": 3, "fourth": 4, "4th": 4, "four": 4, "4": 4,
             "fifth": 5, "5th": 5, "five": 5, "5": 5, "last": -1, "latest": -1}
_CANCEL_RE = re.compile(r"^\s*(?:please\s+)?(?:cancel|stop|abort|halt|never\s*mind)\b(?P<rest>.*)$", re.I)
_UNDO_RE = re.compile(r"^\s*(?:please\s+)?(?:undo|unsend|take\s+(?:that|it)\s+back|restore)\b(?P<rest>.*)$", re.I)
_YES_Q_RE = re.compile(r"^\s*(?:yes|yeah|yep|approve|approved|confirm|go\s+ahead|do\s+it|send\s+it|ok(?:ay)?)\b(?P<rest>.+)$", re.I)
_NO_Q_RE = re.compile(r"^\s*(?:no|nope|deny|reject|don'?t|do\s+not|skip)\b(?P<rest>.+)$", re.I)
_TASK_WORDS = {
    "email": ("email", "mail", "reply", "draft", "send"),
    "calendar": ("calendar", "meeting", "event", "schedule", "appointment"),
    "system_ops": ("command", "terminal", "delete", "kill", "shutdown", "reboot", "cleanup", "clean"),
    "local_research": ("search", "file", "files", "document", "lookup"),
}


def _ordinal_in(text: str) -> Optional[int]:
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if word in _ORDINALS:
            return _ORDINALS[word]
    return None


def _agent_named_in(text: str) -> Optional[str]:
    low = (text or "").lower()
    for agent, words in _TASK_WORDS.items():
        if any(re.search(rf"\b{w}\b", low) for w in words):
            return agent
    return None


# ═══════════════════════════════════════════════════════════════
# Supervisor
# ═══════════════════════════════════════════════════════════════
_PLANNER_SYSTEM = (
    "You route a voice assistant's request to agents. Agents:\n{agents}\n"
    "Return JSON: {{\"steps\": [{{\"agent\": <name>, \"goal\": <that step's part of the request, COPIED in the user's own "
    "words, not paraphrased>, \"uses_previous\": <true if it needs the previous step's result, e.g. refers to 'it'>}}]}}.\n"
    "Use one step per distinct action, in order; a single action is a single step. Use only the agents listed. If no agent fits "
    "(chit-chat, general knowledge, anything else), return {{\"steps\": []}}. Output JSON only."
)


class _PendingOwner:
    """Who a loop's pending confirmation belongs to (for TTL, restart replay, 'yes to the email')."""

    def __init__(self, name: str, cls: Optional[type]):
        self.name = name
        self.confirmation_ttl = getattr(cls, "confirmation_ttl", None)


class JarvisSupervisor:
    """Routes requests; runs the tool loop for agents that expose tools().

    Entry points: ``handle(user_text)`` for the operator, ``handle_event(event)``
    for things JARVIS notices by itself (follow-ups due, watchers...).
    """

    def __init__(self, event_bus, narration_queue, registry: Dict[str, type], *,
                 llm_router: Optional[bool] = None, task_timeout: float = DEFAULT_TASK_TIMEOUT_S,
                 confirmation_ttl: float = DEFAULT_CONFIRMATION_TTL_S, persist: Optional[bool] = None,
                 tool_llm: Optional[Callable] = None, loop_budget: Optional[Budget] = None):
        self.event_bus = event_bus
        self.narration = narration_queue
        self.registry = registry
        # In-flight agent tasks. Shared by reference with the NarrationQueue so it
        # can tell how many things are running (for spoken awareness only).
        existing = getattr(narration_queue, "active_tasks_ref", None)
        self.active: Dict[str, asyncio.Task] = existing if isinstance(existing, dict) else {}
        narration_queue.active_tasks_ref = self.active
        # Pending confirmations, insertion order == the order they were asked.
        self.pending: Dict[str, dict] = {}
        self.llm_router = llm_enabled() if llm_router is None else llm_router
        self.task_timeout = task_timeout
        self.confirmation_ttl = confirmation_ttl
        self.persist = (agent_state_dir() is not None) if persist is None else persist
        self._agents: Dict[str, BaseAgent] = {}   # task_id -> agent (for cancel hooks)
        self._meta: Dict[str, dict] = {}          # task_id -> {agent, goal, context, undoable, started}
        self._expiry_handles: Dict[str, asyncio.TimerHandle] = {}
        self._restored = False
        self.audit_log = AUDIT_MEMORY
        self._tool_llm = tool_llm            # injected for tests; default: Groq via llm_chat_tools
        self.loop_budget = loop_budget or Budget()
        self.loops: Dict[str, ToolLoop] = {}  # paused / running tool loops by run id
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        from core.agents.sensing import WorldModel, register_listener
        self.world = WorldModel(self)
        self.deferred_events: List[dict] = []   # routine events held while the operator is busy
        self._flush_handle: Optional[asyncio.TimerHandle] = None
        try:
            event_bus.subscribe("agent_event", self._on_agent_event)
            register_listener(event_bus)  # sensors now hand their observations to us instead of speaking
        except Exception:
            pass

    # ── Routing ────────────────────────────────────────────────
    def _route(self, user_text: str, context: Optional[dict] = None) -> List[str]:
        """Instant keyword router (also the fallback when the LLM is slow or unavailable).

        An agent matching one of its ``route_strong_keywords`` cannot be overridden
        by another agent's ``route_overrides``.
        """
        text = (user_text or "").lower()
        picked, strong = [], set()
        for name, cls in self.registry.items():
            if any(re.search(rf"\b{re.escape(kw.lower())}\b", text) for kw in getattr(cls, "route_strong_keywords", ())):
                picked.append(name)
                strong.add(name)
                continue
            for kw in getattr(cls, "route_keywords", ()):
                if re.search(rf"\b{re.escape(kw.lower())}\b", text):
                    picked.append(name)
                    break
        overridden = {o for name in picked for o in getattr(self.registry[name], "route_overrides", ())}
        # A strong match may also push out agents it names in ``route_strong_overrides``
        # ("open the second one" is a file follow-up, not an app launch).
        overridden |= {o for name in strong for o in getattr(self.registry[name], "route_strong_overrides", ())}
        return [name for name in picked if name in strong or name not in overridden]

    def _keyword_plan(self, user_text: str) -> List[dict]:
        """Keyword fallback that still chains: 'find X and email it' -> two ordered steps."""
        parts = [p.strip() for p in re.split(r"\s*(?:,\s*)?\b(?:and then|then|and also|and)\b\s*", user_text or "", flags=re.I)
                 if p.strip()]
        if len(parts) > 1:
            steps = []
            for part in parts:
                names = self._route(part)
                if len(names) != 1:
                    steps = []
                    break
                uses_prev = bool(re.search(r"\b(it|them|that|those|this|these|the result|results)\b", part, re.I))
                steps.append({"agent": names[0], "goal": part, "uses_previous": uses_prev and bool(steps)})
            agents = [s["agent"] for s in steps]
            if steps and len(set(agents)) > 1:
                return steps
        return [{"agent": n, "goal": user_text, "uses_previous": False} for n in self._route(user_text)]

    async def _llm_plan(self, user_text: str, timeout: float) -> Optional[List[dict]]:
        """LLM routing/decomposition. None = LLM unavailable/slow (use keywords)."""
        if not self.llm_router or not self.registry:
            return None
        lines = [f"- {name}: {_describe(name, cls)}" for name, cls in self.registry.items()]
        try:
            raw = await asyncio.wait_for(
                llm_chat(_PLANNER_SYSTEM.format(agents="\n".join(lines)), user_text, json_mode=True,
                         max_tokens=400, timeout=timeout, temperature=0.0, allow_local=False),
                timeout)
            data = parse_json_reply(raw)
        except Exception:
            return None
        steps = []
        for step in (data.get("steps") if isinstance(data, dict) else None) or []:
            if isinstance(step, dict) and step.get("agent") in self.registry and str(step.get("goal") or "").strip():
                goal = str(step["goal"]).strip()
                refers_back = bool(re.search(r"\b(?:it|them|that|those|these|this|the\s+results?)\b", goal, re.I))
                steps.append({"agent": step["agent"], "goal": goal,
                              "uses_previous": bool(steps) and (bool(step.get("uses_previous")) or refers_back)})
        steps = steps[:5]
        if len(steps) == 1:
            steps[0]["goal"] = user_text  # agents parse the exact wording; never act on a paraphrase
        return steps

    # ── Entry point ─────────────────────────────────────────────
    async def handle(self, user_text: str, context: Optional[dict] = None) -> List[str]:
        """Dispatch the request and return task ids immediately (never waits for agents).

        An empty list means nothing claimed it (caller falls back to chat).
        """
        await self._ensure_restored()
        self._purge_expired()
        context = dict(context or {})
        text = (user_text or "").strip()
        if not text:
            return []

        control = await self._handle_control(text)
        if control is not None:
            return control

        keyword_steps = self._keyword_plan(text)
        if self._loop_wanted(keyword_steps):
            # A converted agent is involved: the manager loop owns the whole request.
            return [self._start_loop(text, context, world_lines=await self._world_lines())]
        if keyword_steps:
            if not self.llm_router:
                return self._dispatch_plan(text, keyword_steps, context)
            # Keywords say "this is for agents": let the LLM refine/decompose in the
            # background, falling back to the keyword plan, so handle() stays instant.
            plan_id = f"plan-{uuid.uuid4().hex[:8]}"
            self._meta[plan_id] = {"agent": "supervisor", "goal": text, "context": _json_safe(context),
                                   "started": time.time()}
            self.active[plan_id] = asyncio.create_task(self._plan_then_run(plan_id, text, keyword_steps, context),
                                                       name=f"plan:{plan_id}")
            return [plan_id]

        # No keyword hit: give the LLM a short, bounded chance to claim it.
        steps = await self._llm_plan(text, LLM_ROUTE_BUDGET_S)
        if self._loop_wanted(steps):
            return [self._start_loop(text, context, world_lines=await self._world_lines())]
        if steps:
            return self._dispatch_plan(text, steps, context)
        return []

    async def _plan_then_run(self, plan_id: str, text: str, fallback: List[dict], context: dict) -> Optional[AgentResult]:
        try:
            steps = await self._llm_plan(text, 8.0)
            if not steps:
                steps = fallback  # LLM unavailable or said "none": trust the keyword hit
            write_audit("plan", task_id=plan_id, goal=text, steps=steps)
            if self._loop_wanted(steps):
                loop_id = self._start_loop(text, context, world_lines=await self._world_lines())
                if plan_id in self._meta:
                    self._meta[plan_id]["children"] = [loop_id]
                task = self.active.get(loop_id)
                return await task if task else None
            ids = self._dispatch_plan(text, steps, context, parent=plan_id)
            if plan_id in self._meta:
                self._meta[plan_id]["children"] = list(ids)
            tasks = [self.active[i] for i in ids if i in self.active]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return next((r for r in results if isinstance(r, AgentResult)), None)
        finally:
            self.active.pop(plan_id, None)
            self._meta.pop(plan_id, None)

    def _dispatch_plan(self, text: str, steps: List[dict], context: dict, parent: Optional[str] = None) -> List[str]:
        steps = [s for s in steps if s.get("agent") in self.registry]
        if not steps:
            return []
        if len(steps) == 1:
            return [self._spawn(steps[0]["agent"], steps[0]["goal"], context)]
        if any(s.get("uses_previous") for s in steps):
            chain_id = f"chain-{uuid.uuid4().hex[:8]}"
            self._meta[chain_id] = {"agent": "supervisor", "goal": text, "context": _json_safe(context),
                                    "started": time.time()}
            self.active[chain_id] = asyncio.create_task(self._run_chain(chain_id, text, steps, context),
                                                        name=f"chain:{chain_id}")
            write_audit("dispatch_chain", task_id=chain_id, goal=text, steps=steps)
            return [chain_id]
        # Independent steps: run in parallel, merge the answers into one line.
        ids = [self._spawn(s["agent"], s["goal"], context, announce=False) for s in steps]
        group_id = f"group-{uuid.uuid4().hex[:8]}"
        self.active[group_id] = asyncio.create_task(self._merge_group(group_id, ids), name=f"group:{group_id}")
        return ids

    def _spawn(self, name: str, goal: str, context: dict, *, announce: bool = True,
               task_id: Optional[str] = None) -> str:
        cls = self.registry[name]
        task_id = task_id or f"{name}-{uuid.uuid4().hex[:8]}"
        agent: BaseAgent = cls(self.event_bus, task_id, name=name, goal=goal)
        ctx = dict(context)
        if self.pending:  # lets e.g. EmailAgent edit a draft that is still awaiting confirmation
            ctx["pending"] = self._pending_summaries()
        self._agents[task_id] = agent
        self._meta[task_id] = {"agent": name, "goal": goal, "context": _json_safe(context), "started": time.time()}
        self.narration.track_task(task_id, agent.name, goal)
        self.active[task_id] = asyncio.create_task(
            self._run_and_collect(agent, goal, ctx, announce=announce), name=f"agent:{task_id}")
        write_audit("dispatch", task_id=task_id, agent=name, goal=goal)
        self._save_state()
        return task_id

    # ── Tool loop (the manager) ─────────────────────────────────
    @staticmethod
    def _is_tool_agent(cls: Optional[type]) -> bool:
        return cls is not None and getattr(cls, "tools", BaseAgent.tools) is not BaseAgent.tools

    def _loop_available(self) -> bool:
        if self._tool_llm is not None:
            return True
        return bool(self.llm_router) and tool_llm_available()

    def _loop_wanted(self, steps: Optional[List[dict]]) -> bool:
        return bool(steps) and self._loop_available() and any(
            self._is_tool_agent(self.registry.get(s.get("agent"))) for s in steps)

    def _start_loop(self, text: str, context: dict, *, origin: str = "user", event_data: Any = None,
                    categories: Optional[tuple] = None, world_lines: Optional[List[str]] = None) -> str:
        """Build the catalogue for this request and run the loop in the background."""
        run_id = new_run_id()
        tools, lines = [], list(world_lines or [])
        for name, cls in self.registry.items():
            if self._is_tool_agent(cls):
                agent = cls(self.event_bus, run_id, name=name, goal=text)
                for spec in agent.tools():
                    if categories is None or spec.category in categories:
                        spec.agent = spec.agent or name
                        tools.append(spec)
                ctx_fn = getattr(cls, "loop_context", None)
                if callable(ctx_fn):
                    try:
                        lines += list(ctx_fn() or [])
                    except Exception:
                        pass
            else:
                spec = delegate_tool(name, cls,
                                     lambda name=name, cls=cls: cls(self.event_bus, run_id, name=name, goal=text),
                                     _describe(name, cls))
                if categories is None or spec.category in categories:
                    tools.append(spec)

        def _say(fact: str) -> None:
            self.event_bus.emit("agent_progress", {"task_id": run_id, "agent": "supervisor", "goal": text,
                                                   "fact": fact, "priority": 5})

        loop = ToolLoop(run_id=run_id, goal=text, tools=tools, llm=self._tool_llm or llm_chat_tools,
                        origin=origin, budget=self.loop_budget, context_lines=lines, event_data=event_data,
                        say=_say, audit=write_audit, pending_lookup=self._pending_for_draft,
                        on_outcome=_record_feedback)
        self.loops[run_id] = loop
        self._meta[run_id] = {"agent": "supervisor", "goal": text, "context": _json_safe(context), "started": time.time()}
        self.narration.track_task(run_id, "supervisor", text)
        self.active[run_id] = asyncio.create_task(self._run_loop(run_id, text, loop.run()), name=f"loop:{run_id}")
        write_audit("dispatch_loop", task_id=run_id, goal=text, origin=origin, tools=[t.name for t in tools])
        return run_id

    async def _run_loop(self, run_id: str, goal: str, step) -> Optional[AgentResult]:
        try:
            result = await step
            self.narration.finish_task(run_id)
            self._finish_loop_step(run_id, goal, result)
            return result
        except asyncio.CancelledError:
            write_audit("cancelled", task_id=run_id, agent="supervisor")
            self.loops.pop(run_id, None)
            raise
        finally:
            self.narration.finish_task(run_id)
            self.active.pop(run_id, None)
            self._meta.pop(run_id, None)
            self._save_state()

    def _finish_loop_step(self, run_id: str, goal: str, result: AgentResult) -> None:
        """Speak a loop's outcome, or park it as a pending confirmation (paused)."""
        if result.status == "needs_confirmation":
            owner_name = (result.pending_action or {}).get("agent") or "supervisor"
            owner = _PendingOwner(owner_name, self.registry.get(owner_name))
            pending_id = f"{run_id}-c{uuid.uuid4().hex[:4]}"
            result.pending_action["loop_id"] = run_id
            self._add_pending(pending_id, owner, goal, result)
            return
        self.loops.pop(run_id, None)
        write_audit("result", task_id=run_id, agent="supervisor", status=result.status, summary=result.summary)
        priority, kind = (PRIORITY_RESULT, "result") if result.status == "done" else (PRIORITY_FAILURE, "failure")
        self.narration.enqueue(result.summary, priority, kind=kind, task_id=run_id,
                               context={"agent": "supervisor", "goal": goal})

    def _pending_for_draft(self, details: dict) -> Optional[str]:
        """A new send for a draft replaces an older unanswered send of the same draft."""
        draft_id = (details or {}).get("draft_id")
        if not draft_id:
            return None
        for tid, entry in reversed(list(self.pending.items())):
            if (entry.get("pending_action") or {}).get("draft_id") == draft_id:
                return tid
        return None

    # ── Events: things JARVIS notices by itself ─────────────────
    def _on_agent_event(self, payload: dict) -> None:
        """Event-bus callback (any thread): schedule handle_event on the supervisor's loop."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        target = self._event_loop
        if running is not None and (target is None or running is target):
            running.create_task(self.handle_event(payload))
        elif target is not None and not target.is_closed():
            asyncio.run_coroutine_threadsafe(self.handle_event(payload), target)

    async def handle_event(self, event: dict) -> List[str]:
        """Second entry point. ``event`` = {source, kind, summary, goal?, data?}.

        With a ``goal`` and the tool loop available, the loop works on it with
        read and draft tools only: an event can prepare things but never act.
        Otherwise the summary is simply spoken.
        """
        await self._ensure_restored()
        event = dict(event or {})
        write_audit("event", source=event.get("source"), kind=event.get("kind"), summary=event.get("summary"),
                    severity=event.get("severity"))
        snap = await self.world.snapshot()
        if snap.busy() and event.get("severity") != "critical" and not event.get("timely"):
            # In a meeting / quiet mode: hold routine news, deliver it afterwards in one go.
            self.deferred_events.append(event)
            del self.deferred_events[:-20]
            write_audit("event_deferred", kind=event.get("kind"), in_meeting=snap.in_meeting, quiet=snap.quiet_mode)
            self._schedule_flush()
            return []
        await self.flush_deferred(snap)
        goal = str(event.get("goal") or "").strip()
        if goal and self._loop_available():
            return [self._start_loop(goal, {}, origin="event", event_data=event.get("data"),
                                     categories=("read", "draft"), world_lines=snap.context_lines())]
        summary = str(event.get("summary") or "").strip()
        if summary:
            self.narration.enqueue(summary, PRIORITY_RESULT, kind="result",
                                   task_id=f"event-{uuid.uuid4().hex[:6]}",
                                   context={"agent": event.get("source") or "supervisor"})
        return []

    async def flush_deferred(self, snap=None) -> int:
        """Deliver events held during a meeting / quiet mode, as one spoken line."""
        if not self.deferred_events:
            return 0
        snap = snap or await self.world.snapshot()
        if snap.busy():
            return 0
        held, self.deferred_events = self.deferred_events, []
        parts = list(dict.fromkeys(str(e.get("summary") or "").strip().rstrip(".") for e in held if e.get("summary")))
        if parts:
            self.narration.enqueue("While you were busy: " + "; ".join(parts[:5])
                                   + (f"; and {len(parts) - 5} more" if len(parts) > 5 else ""),
                                   PRIORITY_RESULT, kind="result", task_id=f"deferred-{uuid.uuid4().hex[:6]}",
                                   context={"agent": "supervisor"})
        write_audit("events_delivered", count=len(held))
        return len(held)

    def _schedule_flush(self, delay: float = 120.0) -> None:
        if self._flush_handle is not None and not self._flush_handle.cancelled():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _tick():
            self._flush_handle = None
            if self.deferred_events:
                self._flush_task = loop.create_task(self._flush_then_reschedule(), name="deferred-events")

        self._flush_handle = loop.call_later(delay, _tick)

    async def _flush_then_reschedule(self) -> None:
        try:
            self.world.invalidate()  # the meeting may have ended: ask the calendar again
            await self.flush_deferred()
        finally:
            if self.deferred_events:
                self._schedule_flush()

    async def _world_lines(self) -> List[str]:
        """Context for a user request: cheap (no calendar call on the request path)."""
        try:
            snap = await self.world.snapshot(refresh_calendar=False)
            if snap.in_meeting is None:  # unknown yet: refresh in the background for next time
                asyncio.get_running_loop().create_task(self.world.snapshot())
            return snap.context_lines()
        except Exception:
            return []

    # ── Running ────────────────────────────────────────────────
    async def _run_agent(self, agent: BaseAgent, goal: str, context: dict) -> AgentResult:
        """run() with timeout and (read-only agents only) one retry on an exception."""
        timeout = float(getattr(agent, "timeout_s", None) or self.task_timeout)
        attempts = 2 if getattr(agent, "default_tier", RiskTier.READ) is RiskTier.READ else 1
        last_exc: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(agent.run(goal, context), timeout)
                if not isinstance(result, AgentResult):
                    return AgentResult(status="failed", summary=f"The {agent.name} agent returned nothing usable.")
                return result
            except asyncio.TimeoutError:
                write_audit("timeout", task_id=agent.task_id, agent=agent.name, seconds=timeout)
                await self._call_cancel_hook(agent)
                return AgentResult(status="failed", summary=f"The {agent.name} task timed out after {int(timeout)} seconds.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    write_audit("retry", task_id=agent.task_id, agent=agent.name, error=type(exc).__name__)
                    await asyncio.sleep(0.5)
        return AgentResult(status="failed", summary=f"The {agent.name} agent hit an error ({type(last_exc).__name__}).")

    async def _run_and_collect(self, agent: BaseAgent, goal: str, context: dict, announce: bool = True) -> AgentResult:
        task_id = agent.task_id
        try:
            try:
                result = await self._run_agent(agent, goal, context)
            except asyncio.CancelledError:
                result = AgentResult(status="failed", summary=f"Cancelled the {agent.name} task.")
                self.narration.finish_task(task_id)
                write_audit("cancelled", task_id=task_id, agent=agent.name)
                raise
            self.narration.finish_task(task_id)  # no more filler once we have an answer
            write_audit("result", task_id=task_id, agent=agent.name, status=result.status, summary=result.summary)
            self._announce(task_id, agent, goal, result, speak_done=announce)
            return result
        finally:
            self.narration.finish_task(task_id)
            self.active.pop(task_id, None)
            self._agents.pop(task_id, None)
            self._meta.pop(task_id, None)
            self._save_state()

    def _announce(self, task_id: str, agent: BaseAgent, goal: str, result: AgentResult, *, speak_done: bool = True) -> None:
        ctx = {"agent": agent.name, "goal": goal}
        if result.status == "needs_confirmation":
            self._add_pending(task_id, agent, goal, result)
        elif result.status == "done":
            if speak_done:
                self.narration.enqueue(result.summary, PRIORITY_RESULT, kind="result", task_id=task_id, context=ctx)
        else:
            self.narration.enqueue(result.summary, PRIORITY_FAILURE, kind="failure", task_id=task_id, context=ctx)

    async def _merge_group(self, group_id: str, ids: List[str]) -> None:
        """Wait for parallel siblings; speak their 'done' answers as one merged line."""
        try:
            tasks = [self.active[i] for i in ids if i in self.active]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            done = [r.summary.rstrip(". ") for r in results if isinstance(r, AgentResult) and r.status == "done"]
            if done:
                merged = done[0] if len(done) == 1 else ". Also: ".join(done)
                concluded = getattr(self.narration, "_concluded", None)
                if isinstance(concluded, set):  # late "searching..." lines from members are now stale
                    concluded.update(ids)
                self.narration.enqueue(merged, PRIORITY_RESULT, kind="result", task_id=group_id,
                                       context={"agent": "supervisor", "merged": len(done)})
                write_audit("merged", task_id=group_id, parts=len(done))
        finally:
            self.active.pop(group_id, None)

    async def _run_chain(self, chain_id: str, text: str, steps: List[dict], context: dict) -> Optional[AgentResult]:
        """Sequential steps; each later step gets the previous result in context['previous']."""
        previous: Optional[AgentResult] = None
        try:
            for i, step in enumerate(steps):
                last = i == len(steps) - 1
                ctx = dict(context)
                if previous is not None and step.get("uses_previous", True):
                    ctx["previous"] = {"agent": steps[i - 1]["agent"], "summary": previous.summary,
                                       "data": previous.data}
                step_id = f"{step['agent']}-{uuid.uuid4().hex[:8]}"
                self._spawn(step["agent"], step["goal"], ctx, announce=last, task_id=step_id)
                if chain_id in self._meta:
                    self._meta[chain_id]["children"] = [step_id]
                task = self.active.get(step_id)
                previous = await task if task else None
                if previous is None:
                    return None
                if previous.status == "failed":
                    return previous  # failure already spoken; stop the chain
                if previous.status == "needs_confirmation" and not last:
                    self.narration.enqueue(f"I'll carry on with the rest once you've answered that ({len(steps) - i - 1} "
                                           f"step{'s' if len(steps) - i - 1 != 1 else ''} left — ask again after)",
                                           PRIORITY_RESULT, kind="progress", task_id=chain_id)
                    return previous
            return previous
        finally:
            self.active.pop(chain_id, None)
            self._meta.pop(chain_id, None)

    # ── Control phrases: cancel / undo / qualified confirmations ─
    async def _handle_control(self, text: str) -> Optional[List[str]]:
        m = _YES_Q_RE.match(text) or _NO_Q_RE.match(text)
        if m and self.pending and (_ordinal_in(m.group("rest")) is not None or _agent_named_in(m.group("rest"))):
            approved = bool(_YES_Q_RE.match(text))
            target = self._resolve_pending(m.group("rest"))
            if target:
                await self.confirm(target, approved)
                return [target]
        m = _UNDO_RE.match(text)
        if m:
            undoable = [tid for tid, meta in self._meta.items() if meta.get("undoable") and tid in self.active]
            if undoable:
                await self.cancel(undoable[-1], reason="undo")
                return [undoable[-1]]
            return None  # nothing in an undo window: let agents (e.g. system_ops' trash undo) take it
        m = _CANCEL_RE.match(text)
        if m and not self.pending:
            rest = m.group("rest")
            running = [tid for tid in self.active
                       if (tid in self._agents or tid in self._meta) and not tid.startswith("group-")]
            generic = re.match(r"^\s*(?:that|it|this|everything|all|the\s+task)?\s*[.!]?\s*$", rest, re.I)
            if running and (generic or _agent_named_in(rest)):
                if re.search(r"\b(everything|all)\b", rest, re.I):
                    ids = list(running)
                    for tid in ids:
                        await self.cancel(tid)
                    return ids
                agent = _agent_named_in(rest)
                target = next((t for t in reversed(running) if agent and self._meta.get(t, {}).get("agent") == agent),
                              running[-1])
                await self.cancel(target)
                return [target]
        return None

    def _resolve_pending(self, phrase: str) -> Optional[str]:
        ids = list(self.pending)
        if not ids:
            return None
        n = _ordinal_in(phrase)
        if n is not None:
            idx = n - 1 if n > 0 else len(ids) - 1
            return ids[idx] if 0 <= idx < len(ids) else None
        agent = _agent_named_in(phrase)
        matches = [tid for tid in ids if self.pending[tid]["agent"] == agent]
        return matches[-1] if matches else None

    async def cancel(self, task_id: Optional[str] = None, reason: str = "cancel") -> bool:
        """Cancel a running task (default: the most recent). Calls the agent's on_cancel hook."""
        if task_id is None:
            task_id = next(reversed(self.active), None) if self.active else None
        task = self.active.get(task_id) if task_id else None
        if task is None or task.done():
            self.narration.enqueue("There's nothing running to cancel", PRIORITY_FAILURE, kind="failure")
            return False
        meta = self._meta.get(task_id, {})
        agent = self._agents.get(task_id)
        if agent is not None:
            await self._call_cancel_hook(agent)
        task.cancel()
        for child in meta.get("children", []):  # a plan/chain: stop whatever it already started
            child_task = self.active.get(child)
            if child_task is not None and not child_task.done():
                child_agent = self._agents.get(child)
                if child_agent is not None:
                    await self._call_cancel_hook(child_agent)
                child_task.cancel()
                self.narration.finish_task(child)
        self.narration.finish_task(task_id)
        write_audit("cancelled" if reason == "cancel" else "undone", task_id=task_id, agent=meta.get("agent"),
                    goal=meta.get("goal"))
        what = "Undone; it was not sent" if reason == "undo" else f"Cancelled '{_short(meta.get('goal') or 'the last task', 50)}'"
        self.narration.enqueue(what, PRIORITY_RESULT, kind="result", task_id=f"{task_id}-cancel",
                               context={"agent": meta.get("agent")})
        return True

    async def _call_cancel_hook(self, agent: Any) -> None:
        hook = getattr(agent, "on_cancel", None)
        if callable(hook):
            try:
                out = hook()
                if inspect.isawaitable(out):
                    await out
            except Exception:
                pass

    # ── Confirmation ───────────────────────────────────────────
    def pending_task_ids(self) -> List[str]:
        """Pending confirmations, oldest first (the last one is the most recently asked)."""
        self._purge_expired()
        return list(self.pending)

    def _pending_summaries(self) -> List[dict]:
        return [{"task_id": tid, "agent": e["agent"], "summary": e["summary"],
                 "details": _json_safe({k: v for k, v in (e.get("pending_action") or {}).items() if k != "execute"})}
                for tid, e in self.pending.items()]

    def _add_pending(self, task_id: str, agent: BaseAgent, goal: str, result: AgentResult) -> None:
        action = result.pending_action or {}
        superseded = action.get("supersedes")
        if superseded and superseded in self.pending:
            self._drop_pending(superseded)
            write_audit("superseded", task_id=superseded, by=task_id)
        ttl = float(getattr(agent, "confirmation_ttl", None) or self.confirmation_ttl)
        self.pending[task_id] = {
            "agent": agent.name, "goal": goal, "summary": result.summary, "pending_action": action,
            "created": time.time(), "expires_at": time.time() + ttl,
        }
        try:
            self._expiry_handles[task_id] = asyncio.get_running_loop().call_later(ttl, self._expire, task_id)
        except RuntimeError:
            pass
        n = len(self.pending)
        prompt = result.summary if n == 1 else f"Number {n}: {result.summary}"
        self.narration.enqueue(prompt, PRIORITY_CONFIRMATION, kind="confirmation", task_id=task_id,
                               context={"agent": agent.name, "goal": goal, "pending_count": n})
        write_audit("confirmation_requested", task_id=task_id, agent=agent.name, summary=result.summary,
                    expires_in=ttl)
        self._save_state()

    def _drop_pending(self, task_id: str) -> Optional[dict]:
        handle = self._expiry_handles.pop(task_id, None)
        if handle:
            handle.cancel()
        entry = self.pending.pop(task_id, None)
        self._save_state()
        return entry

    def _expire(self, task_id: str) -> None:
        entry = self.pending.get(task_id)
        if not entry:
            return
        self._drop_pending(task_id)
        self.loops.pop((entry.get("pending_action") or {}).get("loop_id"), None)
        write_audit("expired", task_id=task_id, agent=entry["agent"], summary=entry["summary"])
        self.narration.enqueue(f"I've dropped the unanswered request ({_short(entry['summary'])}); nothing was changed",
                               PRIORITY_RESULT, kind="result", task_id=f"{task_id}-expired")

    def _purge_expired(self) -> None:
        now = time.time()
        for tid in [t for t, e in self.pending.items() if e.get("expires_at", now + 1) <= now]:
            self._expire(tid)

    async def confirm(self, task_id: str, approved: bool) -> Optional[asyncio.Task]:
        """Approve or discard ONE pending action.

        Approved actions run in the background like any agent; the returned task
        resolves to an AgentResult. Returns None if nothing ran (denied/unknown/expired).
        """
        await self._ensure_restored()
        self._purge_expired()
        entry = self._drop_pending(task_id) if task_id in self.pending else None
        if entry is None:
            self.narration.enqueue("There's nothing waiting on your confirmation", PRIORITY_FAILURE,
                                   kind="failure", task_id=task_id)
            return None
        ctx = {"agent": entry["agent"], "goal": entry["goal"]}
        if not approved:
            self.loops.pop((entry.get("pending_action") or {}).get("loop_id"), None)
            if (entry.get("pending_action") or {}).get("tool"):
                _record_feedback(entry["pending_action"]["tool"], False, "operator declined")
            write_audit("denied", task_id=task_id, agent=entry["agent"], summary=entry["summary"])
            self.narration.enqueue("Cancelled; nothing was changed", PRIORITY_RESULT, kind="result",
                                   task_id=task_id, context=ctx)
            return None

        write_audit("approved", task_id=task_id, agent=entry["agent"], summary=entry["summary"])
        run_id = f"{task_id}-exec"
        self.narration.track_task(run_id, entry["agent"], entry["goal"])
        self._meta[run_id] = {"agent": entry["agent"], "goal": entry["goal"], "context": {}, "started": time.time(),
                              "undoable": bool((entry.get("pending_action") or {}).get("undo_window_s"))}
        task = asyncio.create_task(self._execute_pending(run_id, entry), name=f"confirm:{task_id}")
        self.active[run_id] = task
        return task

    async def _execute_pending(self, run_id: str, entry: dict) -> AgentResult:
        ctx = {"agent": entry["agent"], "goal": entry["goal"]}
        try:
            action = entry["pending_action"]
            # An agent may offer an async executor (e.g. email's undo-send window) next to the plain one.
            execute = action.get("execute_async") or action["execute"]
            try:
                if inspect.iscoroutinefunction(execute):
                    outcome: Any = await execute()
                else:
                    outcome = await asyncio.to_thread(execute)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcome = AgentResult(status="failed", summary=f"The action failed ({type(exc).__name__}).")
            if not isinstance(outcome, AgentResult):
                outcome = AgentResult(status="done", summary=str(outcome) if outcome else "Done")
            self.narration.finish_task(run_id)
            write_audit("executed", task_id=run_id, agent=entry["agent"], status=outcome.status, summary=outcome.summary)
            loop_id = action.get("loop_id")
            if loop_id:
                # A resumed tool loop: it may have finished, or paused again on a later step.
                self._finish_loop_step(loop_id, entry["goal"], outcome)
                return outcome
            priority, kind = (PRIORITY_RESULT, "result") if outcome.status == "done" else (PRIORITY_FAILURE, "failure")
            self.narration.enqueue(outcome.summary, priority, kind=kind, task_id=run_id, context=ctx)
            return outcome
        finally:
            self.narration.finish_task(run_id)
            self.active.pop(run_id, None)
            self._meta.pop(run_id, None)

    # ── Persistence (restart survival) ─────────────────────────
    def _save_state(self) -> None:
        if not self.persist:
            return
        pending = {}
        for tid, e in self.pending.items():
            action = e.get("pending_action") or {}
            pending[tid] = {"agent": e["agent"], "goal": e["goal"], "summary": e["summary"],
                            "created": e.get("created"), "expires_at": e.get("expires_at"),
                            "replay": _json_safe(action.get("replay"))}
        active = {tid: {"agent": m["agent"], "goal": m["goal"], "context": m.get("context", {})}
                  for tid, m in self._meta.items()
                  if m.get("agent") in self.registry and tid in self.active and not tid.endswith("-exec")}
        save_json_state("supervisor_state.json", {"pending": pending, "active": active, "saved": time.time()})

    async def _ensure_restored(self) -> None:
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        if self._restored:
            return
        self._restored = True
        if not self.persist:
            return
        state = load_json_state("supervisor_state.json", {})
        now = time.time()
        restored_pending, dropped, resumed, interrupted = 0, [], 0, []
        for tid, e in (state.get("pending") or {}).items():
            cls = self.registry.get(e.get("agent"))
            replay = e.get("replay")
            rehydrate = getattr(cls, "rehydrate", None) if cls else None
            execute = None
            if replay and callable(rehydrate) and (e.get("expires_at") or 0) > now:
                try:
                    execute = rehydrate(self.event_bus, replay)
                except Exception:
                    execute = None
            if execute is None:
                dropped.append(e.get("summary", "a request"))
                write_audit("restore_dropped", task_id=tid, agent=e.get("agent"), summary=e.get("summary"))
                continue
            self.pending[tid] = {"agent": e["agent"], "goal": e["goal"], "summary": e["summary"],
                                 "pending_action": {"execute": execute, "replay": replay},
                                 "created": e.get("created"), "expires_at": e["expires_at"]}
            try:
                self._expiry_handles[tid] = asyncio.get_running_loop().call_later(
                    max(1.0, e["expires_at"] - now), self._expire, tid)
            except RuntimeError:
                pass
            restored_pending += 1
            write_audit("restored_pending", task_id=tid, agent=e["agent"], summary=e["summary"])
        for tid, m in (state.get("active") or {}).items():
            cls = self.registry.get(m.get("agent"))
            if cls is not None and getattr(cls, "default_tier", RiskTier.READ) is RiskTier.READ:
                self._spawn(m["agent"], m["goal"], m.get("context") or {})
                resumed += 1
                write_audit("resumed", task_id=tid, agent=m["agent"], goal=m["goal"])
            else:
                interrupted.append(m.get("goal", "a task"))
                write_audit("interrupted", task_id=tid, agent=m.get("agent"), goal=m.get("goal"))
        bits = []
        if restored_pending:
            bits.append(f"{restored_pending} request{'s' if restored_pending != 1 else ''} still waiting on your yes or no")
        if resumed:
            bits.append(f"resumed {resumed} interrupted search{'es' if resumed != 1 else ''}")
        if interrupted:
            bits.append("these were interrupted by the restart and not re-run: " + "; ".join(_short(g) for g in interrupted))
        if dropped:
            bits.append("dropped unconfirmed: " + "; ".join(_short(s) for s in dropped))
        if bits:
            self.narration.enqueue("Since the restart: " + "; ".join(bits), PRIORITY_RESULT, kind="progress")
        self._save_state()


def _record_feedback(tool: str, ok: bool, note: str = "") -> None:
    try:
        from core.agents.sensing import Feedback
        Feedback.record(tool, ok, note)
    except Exception:
        pass


def _describe(name: str, cls: type) -> str:
    return (getattr(cls, "description", "") or _FALLBACK_DESCRIPTIONS.get(name)
            or ((cls.__doc__ or "").strip().splitlines() or [""])[0])


def _short(text: str, n: int = 60) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=lambda o: None if callable(o) else str(o)))
    except Exception:
        return None
