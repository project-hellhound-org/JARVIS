# core/case_brief.py
"""
Gemma-driven case brief parsing.
Sends raw investigator notes to the local SLM to extract structured
hints (employer, suspect repos, CTF context, etc.) that drive
additional recon pivots after the baseline pipeline.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import json
import httpx
from dataclasses import dataclass, field
from typing import Dict

# Reuse Ollama constants from jarvis_voice — don't duplicate the HTTP client
from narrative.jarvis_voice import OLLAMA_URL, SLM_MODEL


@dataclass
class CaseBrief:
    """Structured investigation brief parsed from free-text user input."""
    raw_text: str
    hints: Dict = field(default_factory=dict)


_EXTRACT_SYSTEM = """You are a structured data extractor. You receive free-text investigation notes and extract actionable OSINT hints as a JSON object.

Output ONLY a valid JSON object with these optional keys (omit keys that aren't mentioned):
- "employer": string — company or organization the target works/worked at
- "suspect_old_github": boolean — true if the user suspects the target has old/leaked GitHub repos
- "real_name": string — target's real name if mentioned
- "known_platforms": list of strings — platforms the target is known to use
- "ctf_context": string — any CTF-specific context
- "additional_usernames": list of strings — other usernames to try
- "keywords": list of strings — specific search terms to use

Output raw JSON only. No markdown, no explanation, no code fences."""


def _normalize_hints(hints: Dict) -> Dict:
    if not isinstance(hints, dict):
        return {}

    normalized = {}

    # String keys
    string_keys = {"employer", "real_name", "ctf_context"}
    for key in string_keys:
        val = hints.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            normalized[key] = ", ".join(str(item) for item in val if item is not None)
        elif isinstance(val, (dict, bool, int, float)):
            normalized[key] = str(val)
        elif isinstance(val, str):
            normalized[key] = val
        else:
            normalized[key] = str(val)

    # List keys
    list_keys = {"additional_usernames", "known_platforms", "keywords"}
    for key in list_keys:
        val = hints.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            normalized[key] = [str(item) for item in val if item is not None]
        elif isinstance(val, str):
            normalized[key] = [val]
        else:
            normalized[key] = [str(val)]

    # Boolean keys
    bool_keys = {"suspect_old_github"}
    for key in bool_keys:
        val = hints.get(key)
        if val is None:
            continue
        if isinstance(val, bool):
            normalized[key] = val
        elif isinstance(val, str):
            normalized[key] = val.lower() in ("true", "yes", "1")
        elif isinstance(val, (int, float)):
            normalized[key] = bool(val)
        else:
            normalized[key] = False

    return normalized


def parse_brief_with_slm(raw_text: str) -> CaseBrief:
    """
    Send free-text notes to local Gemma and extract structured hints.
    Falls back to empty hints on any failure — never crashes.
    """
    brief = CaseBrief(raw_text=raw_text)

    if not raw_text or not raw_text.strip():
        return brief

    try:
        client = httpx.Client(timeout=30)
        r = client.post(
            OLLAMA_URL,
            json={
                "model": SLM_MODEL,
                "system": _EXTRACT_SYSTEM,
                "prompt": f"Extract hints from this investigation note:\n\n{raw_text}",
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "num_predict": 300,
                    "num_ctx": 2048,
                },
            },
        )
        response_text = r.json().get("response", "").strip()
        client.close()

        # Try to parse JSON — handle common LLM quirks
        # Strip markdown code fences if the model wraps it
        cleaned = response_text
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        # Find the JSON object in the response
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(cleaned[start:end])
            if isinstance(parsed, dict):
                brief.hints = _normalize_hints(parsed)

    except json.JSONDecodeError:
        pass  # Non-JSON response from Gemma — fall back to empty hints
    except httpx.ConnectError:
        pass  # Ollama not running
    except Exception:
        pass  # Any other failure — degrade gracefully

    return brief


@dataclass
class InvestigationPlan:
    reasoning: str
    priority_order: list = field(default_factory=list)
    extra_hints: dict = field(default_factory=dict)
    skip_candidates: list = field(default_factory=list)
    raw_text: str = ""


VALID_MODULES = {"social_enum", "github_recon", "paste_search", "email_recon",
                 "domain_intel", "ip_recon", "wayback"}


_PLAN_SYSTEM = """You are an OSINT investigation strategist. Given context about a target, decide the optimal investigation approach.
You must choose which modules to prioritize and which to deprioritize/skip. However, you should never suggest to skip baseline coverage entirely; your job is to decide the order and emphasis.

Available modules (use ONLY these exact names in "priority_order" or "skip_candidates"):
- social_enum: scan 300+ platforms for username
- github_recon: GitHub commits, code, profile, README
- paste_search: search paste/leak sites
- email_recon: email breach lookup
- domain_intel: WHOIS, DNS, subdomains
- ip_recon: IP geolocation
- wayback: Wayback Machine historical snapshots

Output ONLY a valid JSON object with the following keys:
- "reasoning": 2-3 sentences explaining your strategy in J.A.R.V.I.S.'s voice (swagger, cocky, sharp, direct, profanity-laced)
- "priority_order": list of module names from the whitelist above to run first (ordered by relevance)
- "skip_candidates": list of module names from the whitelist above that are low-value for this specific case (run last or deprioritized)
- "extra_hints": JSON object containing any of these optional keys:
    - "employer": string
    - "suspect_old_github": boolean
    - "real_name": string
    - "known_platforms": list of strings
    - "ctf_context": string
    - "additional_usernames": list of strings
    - "keywords": list of strings

Output raw JSON only. No markdown, no explanation, no code fences."""


def plan_investigation(raw_text: str, target: str, target_type: str) -> InvestigationPlan:
    """
    Send free-text notes and target info to local Gemma and plan investigation strategy.
    Falls back to a default empty plan on any failure — never crashes.
    """
    plan = InvestigationPlan(reasoning="", raw_text=raw_text)
    if not raw_text or not raw_text.strip():
        return plan

    try:
        client = httpx.Client(timeout=60)
        r = client.post(
            OLLAMA_URL,
            json={
                "model": SLM_MODEL,
                "system": _PLAN_SYSTEM,
                "prompt": f"Target: {target} (type: {target_type})\nContext: {raw_text}",
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "num_predict": 500,
                    "num_ctx": 2048,
                },
            },
        )
        response_text = r.json().get("response", "").strip()
        client.close()

        # Try to parse JSON — handle common LLM quirks
        cleaned = response_text
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(cleaned[start:end])
            if isinstance(parsed, dict):
                reasoning = str(parsed.get("reasoning", ""))
                
                priority_order = parsed.get("priority_order") or []
                if not isinstance(priority_order, list):
                    priority_order = [priority_order]
                priority_order = [str(m).strip() for m in priority_order if str(m).strip() in VALID_MODULES]

                skip_candidates = parsed.get("skip_candidates") or []
                if not isinstance(skip_candidates, list):
                    skip_candidates = [skip_candidates]
                skip_candidates = [str(m).strip() for m in skip_candidates if str(m).strip() in VALID_MODULES]

                raw_hints = parsed.get("extra_hints") or {}
                extra_hints = _normalize_hints(raw_hints) if isinstance(raw_hints, dict) else {}

                plan.reasoning = reasoning
                plan.priority_order = priority_order
                plan.skip_candidates = skip_candidates
                plan.extra_hints = extra_hints

    except Exception:
        pass

    return plan

