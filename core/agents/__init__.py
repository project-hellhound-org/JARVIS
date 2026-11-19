# core/agents/__init__.py
"""JARVIS multi-agent framework: supervisor, narration layer and task agents."""

from core.agents.base_agent import AgentResult, BaseAgent, RiskTier
from core.agents.narration_queue import NarrationQueue
from core.agents.registry import AGENT_REGISTRY
from core.agents.supervisor import JarvisSupervisor

__all__ = [
    "AgentResult",
    "BaseAgent",
    "RiskTier",
    "NarrationQueue",
    "JarvisSupervisor",
    "AGENT_REGISTRY",
]
