# core/agents/base_agent.py
"""
Base types for the JARVIS multi-agent framework.

Agents report PLAIN FACTS via ``.say()``. They never phrase anything in
character -- the NarrationQueue is the only place a fact becomes a JARVIS line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union


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


TOOL_CATEGORIES = ("read", "draft", "action")


@dataclass
class ToolSpec:
    """One capability an agent offers to the supervisor's tool loop.

    category  -- "read" (observes), "draft" (prepares something local and
                 revisable; no effect outside JARVIS), "action" (changes the
                 world: sends, deletes, archives...).
    tier      -- risk tier. DESTRUCTIVE tools always pause the loop for the
                 operator's yes/no; ``confirm`` can ask for it on other tiers.
    handler   -- ``async handler(args, ctx) -> AgentResult``. ``ctx`` carries the
                 running agent and anything the loop passes through.
    preview   -- for confirmed tools: ``async preview(args, ctx) -> AgentResult``
                 that validates the call and returns the read-back summary
                 (status "done") or a failure, without doing anything.
    untrusted_output -- the result contains outside content (mail, invites,
                 file text); the loop fences it and records it as untrusted.
    grounded_args -- argument names whose values must come from the operator
                 (or trusted lookups), never only from untrusted content.
    """

    name: str
    description: str
    parameters: Dict[str, Any]
    category: str
    tier: RiskTier
    handler: Callable[[dict, dict], Awaitable[AgentResult]]
    preview: Optional[Callable[[dict, dict], Awaitable[AgentResult]]] = None
    confirm: Union[bool, Callable[[dict], bool]] = False
    untrusted_output: bool = False
    grounded_args: Tuple[str, ...] = ()
    progress: str = ""
    agent: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.category not in TOOL_CATEGORIES:
            raise ValueError(f"ToolSpec.category must be one of {TOOL_CATEGORIES}, got {self.category!r}")
        if not isinstance(self.tier, RiskTier):
            raise ValueError("ToolSpec.tier must be a RiskTier")

    def needs_confirmation(self, args: dict) -> bool:
        if self.tier is RiskTier.DESTRUCTIVE:
            return True
        return bool(self.confirm(args)) if callable(self.confirm) else bool(self.confirm)

    def schema(self) -> dict:
        """OpenAI/Groq function-calling schema. Category and tier are stated in the
        description so the model knows what each tool costs."""
        return {"type": "function", "function": {
            "name": self.name,
            "description": f"[{self.category}; risk: {self.tier.value}] {self.description}",
            "parameters": self.parameters,
        }}


class BaseAgent:
    """Common plumbing for every agent.

    Subclasses set ``name``, ``default_tier`` and optionally ``route_keywords``
    (used by the supervisor's keyword router), then implement ``run()``.
    Agents converted to the tool loop also implement ``tools()``.
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

    def tools(self) -> list:
        """Tool schemas for the supervisor's loop. Empty = not converted yet; the
        loop then offers the whole agent as a single delegate tool."""
        return []
