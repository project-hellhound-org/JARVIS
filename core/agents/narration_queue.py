# core/agents/narration_queue.py
"""
NarrationQueue -- the "speaks like a real buddy" layer.

* Agents emit plain facts ("agent_progress" events). This queue is the ONLY
  place a fact is turned into an in-character line, via ``persona_phrase_fn``.
* Each tracked task has an idle timer. If the task goes quiet for a while, a
  short low-priority filler line is spoken ("Still sorting through those files").
  Real updates reset the timer and always beat filler.
* Lines are paced: a minimum gap separates any two spoken lines.
* One async worker speaks lines through ``asyncio.to_thread`` so TTS never
  blocks the event loop or the agents.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import random
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Lower number = spoken sooner.
PRIORITY_CONFIRMATION = 1
PRIORITY_FAILURE = 2
PRIORITY_RESULT = 3
PRIORITY_PROGRESS = 5
PRIORITY_FILLER = 9


@dataclass
class _Line:
    kind: str  # "progress" | "result" | "confirmation" | "failure" | "filler"
    fact: str
    task_id: Optional[str]
    context: dict
    created_at: float = 0.0
    filler_epoch: int = 0  # for filler: the task's update epoch when it was generated


@dataclass
class _TaskState:
    agent: str
    goal: Optional[str]
    epoch: int = 0  # bumped on every real update
    timer: Optional[asyncio.TimerHandle] = None
    filler_pending: bool = False
    finished: bool = False
    extra: dict = field(default_factory=dict)


class NarrationQueue:
    def __init__(
        self,
        event_bus,
        tts_speak_fn: Callable[[str], Any],
        persona_phrase_fn: Callable[[str, dict], str],
        active_tasks_ref: Optional[dict] = None,
        *,
        idle_min: float = 8.0,
        idle_max: float = 14.0,
        min_gap: float = 2.5,
        multi_task_chance: float = 0.5,
        rng: Optional[random.Random] = None,
    ):
        if idle_min <= 0 or idle_max < idle_min:
            raise ValueError("need 0 < idle_min <= idle_max")
        self.event_bus = event_bus
        self.tts_speak_fn = tts_speak_fn
        self.persona_phrase_fn = persona_phrase_fn
        # Shared reference to the supervisor's in-flight task dict (read-only here).
        self.active_tasks_ref = active_tasks_ref if active_tasks_ref is not None else {}
        self.idle_min = idle_min
        self.idle_max = idle_max
        self.min_gap = min_gap
        self.multi_task_chance = multi_task_chance
        self.rng = rng or random.Random()

        self._queue: Optional[asyncio.PriorityQueue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._seq = itertools.count()
        self._tasks: dict[str, _TaskState] = {}
        self._last_spoken_at: Optional[float] = None
        self._running = False
        self._backlog: list[tuple[int, _Line]] = []  # lines enqueued before start()
        self._backlog_lock = threading.Lock()
        self._concluded: set[str] = set()  # task_ids whose final answer was already spoken

        event_bus.subscribe("agent_progress", self._on_progress_event)

    # ── Event-bus entry point (may be called from any thread) ──
    def _on_progress_event(self, payload: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            # Not started yet: keep the fact, speak it once the worker is up.
            self._buffer(payload)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._ingest_progress(payload)
        else:
            loop.call_soon_threadsafe(self._ingest_progress, payload)

    def _buffer(self, payload: dict) -> None:
        line = _Line("progress", str(payload.get("fact", "")), payload.get("task_id"),
                     {"agent": payload.get("agent"), "goal": payload.get("goal")})
        with self._backlog_lock:
            self._backlog.append((int(payload.get("priority", PRIORITY_PROGRESS)), line))

    def _ingest_progress(self, payload: dict) -> None:
        task_id = payload.get("task_id")
        fact = str(payload.get("fact", "")).strip()
        if not fact:
            return
        priority = int(payload.get("priority", PRIORITY_PROGRESS))
        if task_id in self._tasks:
            self._note_real_update(task_id)
        self._put(priority, _Line("progress", fact, task_id,
                                  {"agent": payload.get("agent"), "goal": payload.get("goal")}))

    # ── Task tracking (called by the supervisor) ────────────────
    def track_task(self, task_id: str, agent: str, goal: Optional[str] = None) -> None:
        """Start idle commentary for a dispatched task."""
        self._tasks[task_id] = _TaskState(agent=agent, goal=goal)
        self._arm_timer(task_id)

    def finish_task(self, task_id: str) -> None:
        """Stop idle commentary for a task; any queued filler for it is dropped."""
        state = self._tasks.pop(task_id, None)
        if state:
            state.finished = True
            if state.timer:
                state.timer.cancel()

    def tracked_tasks(self) -> list[str]:
        return list(self._tasks)

    # ── Direct enqueue (supervisor results / confirmations) ─────
    def enqueue(self, fact: str, priority: int = PRIORITY_PROGRESS, *, kind: str = "progress",
                task_id: Optional[str] = None, context: Optional[dict] = None) -> None:
        """Queue a plain fact to be phrased and spoken. Safe from the loop thread."""
        if task_id in self._tasks and kind != "filler":
            self._note_real_update(task_id)
        line = _Line(kind, fact, task_id, dict(context or {}))
        if self._loop is None:
            with self._backlog_lock:
                self._backlog.append((priority, line))
            return
        self._put(priority, line)

    # ── Idle timers ─────────────────────────────────────────────
    def _note_real_update(self, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if not state:
            return
        state.epoch += 1  # any queued filler for this task is now stale
        state.filler_pending = False
        self._arm_timer(task_id)

    def _arm_timer(self, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if state is None or self._loop is None:
            return  # armed later in start() if not running yet
        if state.timer:
            state.timer.cancel()
        delay = self.rng.uniform(self.idle_min, self.idle_max)
        state.timer = self._loop.call_later(delay, self._on_idle, task_id)

    def _on_idle(self, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if state is None or state.finished:
            return
        if self.active_tasks_ref and task_id not in self.active_tasks_ref:
            # Supervisor no longer considers it running.
            self.finish_task(task_id)
            return
        # Cap: at most one filler per idle period -- don't stack them up.
        if not state.filler_pending:
            state.filler_pending = True
            active_count = max(len(self.active_tasks_ref), len(self._tasks))
            mention_others = active_count > 1 and self.rng.random() < self.multi_task_chance
            context = {
                "agent": state.agent,
                "goal": state.goal,
                "active_count": active_count,
                "mention_others": mention_others,
            }
            if mention_others:
                fact = f"{active_count} tasks still running; still working on: {state.goal or state.agent}"
            else:
                fact = f"still working on: {state.goal or state.agent}"
            line = _Line("filler", fact, task_id, context, filler_epoch=state.epoch)
            self._put(PRIORITY_FILLER, line)
        # Next idle period.
        state.timer = self._loop.call_later(self.rng.uniform(self.idle_min, self.idle_max),
                                            self._on_idle, task_id)

    def _is_stale(self, line: _Line) -> bool:
        if line.kind == "progress":
            # Don't narrate "searching..." after the answer has already been given.
            return line.task_id is not None and line.task_id in self._concluded
        if line.kind != "filler":
            return False
        state = self._tasks.get(line.task_id)
        if state is None or state.finished:
            return True  # task finished before the filler got its turn
        return state.epoch != line.filler_epoch  # a real update arrived after it was queued

    # ── Queue plumbing ──────────────────────────────────────────
    def _put(self, priority: int, line: _Line) -> None:
        line.created_at = self._loop.time() if self._loop else 0.0
        self._queue.put_nowait((priority, next(self._seq), line))

    def stop(self) -> None:
        self._running = False
        for task_id in list(self._tasks):
            self.finish_task(task_id)
        if self._queue is not None:
            self._queue.put_nowait((float("-inf"), next(self._seq), None))

    async def start(self) -> None:
        """Single worker loop. Run it with ``asyncio.create_task(queue.start())``."""
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.PriorityQueue()
        self._running = True
        with self._backlog_lock:
            backlog, self._backlog = self._backlog, []
        for priority, line in backlog:
            self._put(priority, line)
        for task_id, state in self._tasks.items():
            if state.timer is None:
                self._arm_timer(task_id)

        while self._running:
            # Pacing first, so whatever is most urgent *after* the gap wins.
            if self._last_spoken_at is not None:
                wait = self.min_gap - (self._loop.time() - self._last_spoken_at)
                if wait > 0:
                    await asyncio.sleep(wait)
            _, _, line = await self._queue.get()
            if line is None:
                break
            if self._is_stale(line):
                continue
            if line.kind == "filler":
                state = self._tasks.get(line.task_id)
                if state:
                    state.filler_pending = False
            if line.kind in ("result", "failure", "confirmation") and line.task_id:
                self._concluded.add(line.task_id)
            await self._speak(line)

    async def _speak(self, line: _Line) -> None:
        context = {"kind": line.kind, "task_id": line.task_id, **line.context}
        try:
            text = await self._phrase(line.fact, context)
        except Exception as exc:
            print(f"[narration] persona phrasing failed ({type(exc).__name__}); skipping line")
            return
        if not text:
            return
        try:
            await asyncio.to_thread(self.tts_speak_fn, text)
        except Exception as exc:
            print(f"[narration] TTS failed ({type(exc).__name__})")
        finally:
            self._last_spoken_at = self._loop.time()

    async def _phrase(self, fact: str, context: dict) -> str:
        fn = self.persona_phrase_fn
        if inspect.iscoroutinefunction(fn):
            return await fn(fact, context)
        # Sync persona functions may hit an LLM; keep them off the loop.
        return await asyncio.to_thread(fn, fact, context)
