# core/agents/assistant_agent.py
"""AssistantAgent -- general knowledge tools folded in from the legacy cognitive loop.

The cognitive loop (core/jarvis_reasoning_loop.py) used to offer web search and
memory recall next to its tactical tools, which meant two plausible tools for
one job. They live here now, in the agents' catalogue; the cognitive loop keeps
only the tactical / OSINT namespace.

Tools (category / risk):
  read  web_search     -- live web results (DuckDuckGo via SystemSkillEngine)
  read  memory_recall  -- what JARVIS remembers: the operator's rules, habits,
                          lessons and logged notes (data/jarvis_memory.json),
                          searched by the words asked about

Web pages and remembered text are outside content: both results are untrusted.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Tuple

from core.agents.base_agent import AgentResult, BaseAgent, RiskTier, ToolSpec

_WEB_RE = re.compile(r"\b(?:search\s+(?:the\s+)?(?:web|internet|online)|web\s+search|google(?:\s+search)?|"
                     r"look\s+(?:it\s+)?up\s+online|on\s+the\s+(?:web|internet)|online|latest\s+news|news\s+(?:about|on))\b", re.I)
_MEMORY_RE = re.compile(r"\b(?:do\s+you\s+remember|what\s+do\s+you\s+(?:remember|know\s+about\s+me)|did\s+i\s+(?:tell|say|ask)|"
                        r"remind\s+me\s+what\s+i\s+(?:said|told)|my\s+rules?|what\s+did\s+i\s+(?:say|tell\s+you))\b", re.I)
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "about", "what", "did", "do", "you", "i", "me",
         "my", "remember", "say", "said", "tell", "told", "is", "was", "that", "it", "search", "web", "online", "google",
         "please", "can", "could", "would", "look", "up", "find"}


class AssistantAgent(BaseAgent):
    name = "assistant"
    description = ("general knowledge: search the WEB for news, facts, people and websites (not the operator's own files), "
                   "and recall what JARVIS remembers from earlier conversations (the operator's rules, habits, notes)")
    default_tier = RiskTier.READ
    # Narrow phrases only: "google chrome" is an app and "is it online" isn't a search.
    route_keywords = ("google search", "google it", "google for", "web search", "search the web", "search online",
                      "look it up online", "latest news", "news about", "do you remember", "did i tell you",
                      "what did i say")
    # "search the web" is not a file search, whatever the word "search" suggests.
    route_strong_keywords = ("search the web", "search online", "web search", "google search", "google for",
                             "on the internet", "do you remember", "did i tell you", "what did i say")
    route_strong_overrides = ("local_research", "system_ops")

    # ── Providers (overridable / mockable) ─────────────────────
    def _live_search(self, query: str) -> Tuple[str, List[Dict[str, Any]]]:
        from core.system_skills import SystemSkillEngine
        return SystemSkillEngine().perform_live_search(query)

    def _memory(self) -> Dict[str, Any]:
        from core.jarvis_memory import JarvisMemory
        return JarvisMemory().load_memory()

    # ── Catalogue ──────────────────────────────────────────────
    def tools(self) -> List[ToolSpec]:
        R = RiskTier.READ
        return [
            ToolSpec("web_search",
                     "Search the web for news, facts, people, products and websites. Not for the operator's own files "
                     "(search_files) or their email (check_inbox). Not for finding someone's online accounts or a "
                     "domain's infrastructure: that is the tactical OSINT console, outside these tools.",
                     {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                     "read", R, self.web_search, untrusted_output=True, progress="Searching the web"),
            ToolSpec("memory_recall",
                     "Look up what JARVIS remembers about the operator: their stated rules and phrase meanings, "
                     "habits, lessons learned and logged notes. Not their files or email.",
                     {"type": "object", "properties": {"query": {"type": "string", "description": "What to recall; "
                                                                                                    "empty = the most recent"}}},
                     "read", R, self.memory_recall, untrusted_output=True, progress="Checking my memory"),
        ]

    # ── Fallback entry point (no planning model) ───────────────
    async def run(self, goal: str, context: dict) -> AgentResult:
        text = (goal or "").strip()
        if _MEMORY_RE.search(text):
            return await self.memory_recall({"query": text}, {})
        query = re.sub(r"^\s*(?:please\s+)?(?:(?:can|could)\s+you\s+)?(?:google|search\s+(?:the\s+)?(?:web|internet|online)|"
                       r"web\s+search|look\s+up)\s+(?:for\s+|about\s+)?", "", text, flags=re.I).strip(" ?.!")
        query = re.sub(r"\s+(?:online|on\s+the\s+(?:web|internet))\s*$", "", query, flags=re.I).strip()
        return await self.web_search({"query": query or text}, {})

    # ── Tools ──────────────────────────────────────────────────
    async def web_search(self, args: dict, ctx: dict) -> AgentResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return AgentResult(status="failed", summary="What should I search the web for?")
        try:
            summary, results = await asyncio.wait_for(asyncio.to_thread(self._live_search, query), 15)
        except Exception as exc:
            return AgentResult(status="failed", summary=f"The web search failed ({type(exc).__name__}).")
        real = [r for r in results or [] if r.get("snippet") and not str(r.get("title", "")).startswith("Google Search:")]
        if not real:
            return AgentResult(status="failed", summary=f"The web search for '{query}' came back empty; the search "
                                                        "service may be unreachable.")
        self.hud({"type": "web_results", "query": query, "results": real[:6]})
        top = "; ".join(f"{r['title']}" for r in real[:3])
        return AgentResult(status="done", summary=f"{len(real)} web results for '{query}'. Top: {top}",
                           data=[{"title": r.get("title"), "snippet": r.get("snippet"), "url": r.get("url")} for r in real[:6]])

    async def memory_recall(self, args: dict, ctx: dict) -> AgentResult:
        query = str(args.get("query") or "").strip()
        try:
            mem = await asyncio.to_thread(self._memory)
        except Exception as exc:
            return AgentResult(status="failed", summary=f"Memory isn't available ({type(exc).__name__}).")
        entries: List[Tuple[str, str]] = []
        entries += [("rule", str(r)) for r in mem.get("custom_rules", []) or []]
        entries += [("habit", str(p)) for p in mem.get("user_speech_patterns", []) or []]
        entries += [("lesson", str(l)) for l in mem.get("failures_and_lessons", []) or []]
        entries += [(str(h.get("category") or "note"), str(h.get("entry")))
                    for h in mem.get("history_log", []) or [] if isinstance(h, dict) and h.get("entry")]
        words = [w for w in re.findall(r"[a-z0-9']{3,}", query.lower()) if w not in _STOP]
        if words:
            scored = [(sum(w in text.lower() for w in words), i, kind, text) for i, (kind, text) in enumerate(entries)]
            hits = [(kind, text) for score, i, kind, text in sorted(scored, key=lambda s: (-s[0], -s[1])) if score > 0][:6]
        else:
            hits = list(reversed(entries))[:6]
        if not hits:
            return AgentResult(status="done", data=[],
                               summary=f"I don't have anything remembered about {', '.join(words) or 'that'}.")
        return AgentResult(status="done", data=[{"kind": k, "text": t} for k, t in hits],
                           summary=f"{len(hits)} thing{'s' if len(hits) != 1 else ''} remembered"
                                   + (f" about {', '.join(words)}" if words else "") + ": " + "; ".join(t for _, t in hits[:3]))
