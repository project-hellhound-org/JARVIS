# core/agents/base_agent.py
"""
Base types for the JARVIS multi-agent framework.

Agents report PLAIN FACTS via ``.say()``. They never phrase anything in
character -- the NarrationQueue is the only place a fact becomes a JARVIS line.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class RiskTier(str, Enum):
    """How much damage an agent action can do.

    READ        -- observes only (search, list, summarise).
    REVERSIBLE  -- changes state, but can be undone.
    DESTRUCTIVE -- cannot be undone, or affects other people. Must never run
                   without the operator's explicit confirmation.
    """

    READ = "read"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"


VALID_STATUSES = ("done", "needs_confirmation", "failed")


@dataclass
class AgentResult:
    status: str  # "done" | "needs_confirmation" | "failed"
    summary: str
    data: Any = None
    pending_action: Optional[dict] = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"AgentResult.status must be one of {VALID_STATUSES}, got {self.status!r}")
        if self.status == "needs_confirmation":
            execute = (self.pending_action or {}).get("execute")
            if not callable(execute):
                raise ValueError("needs_confirmation results require pending_action={'execute': <callable>}")


class BaseAgent:
    """Common plumbing for every agent.

    Subclasses set ``name``, ``default_tier`` and optionally ``route_keywords``
    (used by the supervisor's keyword router), then implement ``run()``.
    """

    name: str = "base"
    default_tier: RiskTier = RiskTier.READ
    route_keywords: tuple[str, ...] = ()

    def __init__(self, event_bus, task_id: str, name: Optional[str] = None, goal: Optional[str] = None):
        self.event_bus = event_bus
        self.task_id = task_id
        if name:
            self.name = name
        self.goal = goal

    # ── Reporting ──────────────────────────────────────────────
    def say(self, fact: str, priority: int = 5) -> None:
        """Report a plain fact. No personality here -- phrasing happens downstream."""
        self.event_bus.emit("agent_progress", {
            "task_id": self.task_id,
            "agent": self.name,
            "goal": self.goal,
            "fact": fact,
            "priority": priority,
        })

    def hud(self, payload: dict) -> None:
        """Push structured data to the HUD (results panels etc.)."""
        self.event_bus.emit("agent_hud", {
            "task_id": self.task_id,
            "agent": self.name,
            "payload": payload,
        })

    # ── Risk gating ────────────────────────────────────────────
    def confirmation_required(self, summary: str, execute: Callable, **details: Any) -> AgentResult:
        """Build the only legal result for a DESTRUCTIVE action: ask first, run later."""
        return AgentResult(
            status="needs_confirmation",
            summary=summary,
            pending_action={"execute": execute, "tier": RiskTier.DESTRUCTIVE.value, **details},
        )

    # ── Work ───────────────────────────────────────────────────
    async def run(self, goal: str, context: dict) -> AgentResult:
        raise NotImplementedError(f"{type(self).__name__}.run() is not implemented")
