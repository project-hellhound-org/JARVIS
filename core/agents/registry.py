# core/agents/registry.py
"""Agent name -> agent class. Add new agents here."""

from core.agents.assistant_agent import AssistantAgent
from core.agents.calendar_agent import CalendarAgent
from core.agents.email_agent import EmailAgent
from core.agents.local_research_agent import LocalResearchAgent
from core.agents.system_ops_agent import SystemOpsAgent

AGENT_REGISTRY = {
    LocalResearchAgent.name: LocalResearchAgent,
    SystemOpsAgent.name: SystemOpsAgent,
    EmailAgent.name: EmailAgent,
    CalendarAgent.name: CalendarAgent,
    AssistantAgent.name: AssistantAgent,
}
