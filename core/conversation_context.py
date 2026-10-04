# core/conversation_context.py
"""
Rolling Short-Term Conversational Context Manager for J.A.R.V.I.S.
Maintains session-scoped turns (last N turns) decoupled from permanent memory.
Handles:
- Classification: new_command | follow_up | clarification_answer | general_covo
- Entity / slot extraction and inheritance
- Missing slot clarification ("Which target username or domain, Sir?")
- Local-only decision logging to data/intent_decisions.log (never exported or sent to telemetry)
"""

import os
import re
import time
import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path


@dataclass
class ConversationTurn:
    turn_id: str
    timestamp: float
    user_raw: str
    classification: str          # "new_command" | "follow_up" | "clarification_answer" | "general_covo"
    intent: str                  # "investigate" | "geo_nav" | "system_skill" | "media_control" | "general_covo"
    entities: Dict[str, Any] = field(default_factory=dict)
    pending_slots: List[str] = field(default_factory=list)
    resolved_query: str = ""
    agent_response: str = ""
    latency_ms: float = 0.0


class ConversationContextManager:
    """Manages short-term rolling conversation turns and reference resolution."""

    def __init__(self, max_turns: int = 8, log_path: Optional[Path] = None):
        self.max_turns = max_turns
        self.turns: List[ConversationTurn] = []
        self._pending_slot_request: Optional[Dict[str, Any]] = None

        if log_path is None:
            self.log_path = Path(__file__).parent.parent / "data" / "intent_decisions.log"
        else:
            self.log_path = Path(log_path)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def get_last_turn(self) -> Optional[ConversationTurn]:
        return self.turns[-1] if self.turns else None

    def get_active_pending_slot(self) -> Optional[Dict[str, Any]]:
        return self._pending_slot_request

    def clear_pending_slot(self):
        self._pending_slot_request = None

    def set_pending_slot(self, intent: str, slot_name: str, prompt_asked: str):
        """Set a slot expectation from a question JARVIS just asked the user."""
        self._pending_slot_request = {
            "intent": intent,
            "slot": slot_name,
            "prompt_asked": prompt_asked,
            "timestamp": time.time(),
        }

    def classify_and_resolve(self, user_text: str, current_target_name: Optional[str] = None, active_location: Optional[str] = None) -> Tuple[str, str, Dict[str, Any], str]:
        start_t = time.time()
        clean = (user_text or "").strip()
        lower = clean.lower()
        last_turn = self.get_last_turn()

        # 1. Check if user is answering a pending clarification question from JARVIS
        if self._pending_slot_request and (time.time() - self._pending_slot_request.get("timestamp", 0)) < 45.0:
            pending = self._pending_slot_request
            slot_name = pending["slot"]
            intent = pending["intent"]
            self.clear_pending_slot()

            entities = {slot_name: clean}
            if intent == "investigate":
                resolved = f"investigate {clean}"
            elif intent == "geo_nav":
                resolved = f"go to {clean}"
            else:
                resolved = f"{intent} {clean}"

            classification = "clarification_answer"
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Answered pending clarification slot")
            return classification, intent, entities, resolved

        # 1.5 Check for geographic / spatial follow-up ("what is the traffic there", "weather there", "cameras there")
        has_spatial_ref = bool(re.search(r'\b(?:there|here|this area|this place|the area)\b', lower))
        has_spatial_domain = any(w in lower for w in ["traffic", "weather", "cctv", "camera", "cameras", "radar", "flights"])
        effective_loc = active_location or (last_turn.entities.get("location") if last_turn else None)

        if has_spatial_ref and has_spatial_domain and effective_loc:
            classification = "follow_up"
            intent = "geo_query"
            entities = {"location": effective_loc}
            # Replace 'there' / 'here' / etc. with 'in <effective_loc>'
            resolved = re.sub(
                r'\b(?:in\s+there|there|in\s+here|here|in\s+this\s+area|this\s+area|this\s+place|the\s+area)\b',
                f"in {effective_loc}",
                clean,
                flags=re.IGNORECASE
            )
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note=f"Resolved spatial follow-up to {effective_loc}")
            return classification, intent, entities, resolved

        # 2. Check for follow-up / refinement signals
        followup_patterns = [
            r'^(?:what\s+about|how\s+about|and|check|search|find)\s+(?:his|her|their|its|the)\s+(.+)$',
            r'^(?:what\s+about|how\s+about)\s+([a-zA-Z0-9_\-\.\s]+)$',
            r'^(?:do\s+that|run\s+that|repeat\s+that|try\s+that)\s+(?:for|on|with|in)\s+(.+)$',
            r'^(?:make\s+it|turn\s+it)\s+(louder|softer|quieter|higher|lower|faster|slower)$',
            r'^(?:closer|zoom\s+in|zoom\s+out|tilt\s+up|tilt\s+down)$',
            r'^(?:pivot\s+to|switch\s+to|look\s+at)\s+(?:his|her|their|that)\s+(.+)$',
        ]

        is_followup = False
        for pat in followup_patterns:
            m = re.search(pat, lower)
            if m:
                is_followup = True
                break

        has_pronoun = bool(re.search(r'\b(?:he|him|his|she|her|they|them|their|it|that|there|this)\b', lower))
        is_memory_ref = any(m in lower for m in ["personal memory", "save rule", "memory rule", "stored memory", "remove rule", "delete rule", "forget that", "clear memory", "in memory"])

        if (is_followup or has_pronoun) and last_turn and not is_memory_ref:
            classification = "follow_up"
            inherited_entities = dict(last_turn.entities)
            intent = last_turn.intent

            target_val = inherited_entities.get("target") or (current_target_name if current_target_name else "")
            entities = dict(inherited_entities)

            # Check for file/note follow-up references ("rename this one", "add to it", "edit the note")
            # Must NOT match general conversation, questions, or memory/rule commands
            is_file_action = any(w in lower for w in [
                "rename this", "rename it", "rename the", "edit the note", "edit this", "edit it",
                "add to it", "add to this", "append to it", "update the note", "update this",
                "delete the note", "delete this note", "delete the notebook", "remove the note", "remove the notebook"
            ])
            if is_file_action:
                try:
                    from core.system_commander import get_system_commander
                    cmdr = get_system_commander()
                    if getattr(cmdr, 'last_affected_file', None):
                        entities["target_file"] = cmdr.last_affected_file
                        # Resolve pronoun to concrete file path, specifically tied to the action verb
                        resolved = re.sub(
                            r'\b(rename|edit|append(?:\s+to)?|add(?:\s+[\w\s\'-]+)?\s+to|update|delete|remove)\s+(?:this\s+one|this|it|the\s+file|the\s+note|the\s+notebook)\b',
                            rf'\1 {cmdr.last_affected_file}',
                            clean,
                            flags=re.IGNORECASE
                        )
                        # Also handle standalone "this one" / "the note" / "the notebook" only when file action is explicit
                        if resolved == clean:
                            resolved = re.sub(
                                r'\b(?:this\s+one|the\s+note|the\s+notebook|the\s+file)\b',
                                cmdr.last_affected_file,
                                clean,
                                flags=re.IGNORECASE
                            )
                        latency = (time.time() - start_t) * 1000.0
                        self._log_decision(clean, classification, "file_operation", entities, resolved, latency, note="Resolved file pronoun follow-up")
                        return classification, "file_operation", entities, resolved
                except Exception:
                    pass

            platform_match = re.search(r'\b(twitter|x|github|linkedin|instagram|facebook|tiktok|reddit|email|domain|whois|dns)\b', lower)
            if platform_match:
                entities["platform"] = platform_match.group(1)

            if target_val:
                entities["target"] = target_val
                if entities.get("platform"):
                    resolved = f"investigate {target_val} on {entities['platform']}"
                else:
                    resolved = f"{clean} (regarding target {target_val})"
            else:
                resolved = clean

            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Resolved follow-up context")
            return classification, intent, entities, resolved

        # 3. Direct Fast-Path Intent Recognition
        nav_match = re.match(r'^(?:go\s+to|fly\s+to|navigate\s+to|show\s+me|take\s+me\s+to)\s+([a-zA-Z0-9\s,\.\-]{2,50})$', lower)
        if nav_match:
            dest = nav_match.group(1).strip()
            classification = "new_command"
            intent = "geo_nav"
            entities = {"location": dest}
            resolved = clean
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Direct navigation fast-path")
            return classification, intent, entities, resolved

        is_map_context = any(w in lower for w in ["camera", "cctv", "map", "route", "corridor", "globe", "satellite", "flight", "traffic", "weather"])
        inv_match = re.match(r'^(?:investigate|deep_scan|recon|scan|trace|lookup|pivot|stalk|dox)\s+(\S+)$', lower)
        if not is_map_context and inv_match and inv_match.group(1) not in ("me", "jarvis", "us", "again", "them"):
            target_extracted = inv_match.group(1).strip()
            classification = "new_command"
            intent = "investigate"
            entities = {"target": target_extracted}
            resolved = clean
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Direct investigation fast-path")
            return classification, intent, entities, resolved

        # Cockpit chase fast-path
        chase_match = re.match(r'^(?:chase|follow|cockpit(?:\s+view)?|chase\s+cam)\s*(.*)$', lower)
        if chase_match:
            target_callsign = chase_match.group(1).strip().upper()
            classification = "new_command"
            intent = "cockpit_chase"
            entities = {"target": target_callsign} if target_callsign else {}
            resolved = clean
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Cockpit chase command")
            return classification, intent, entities, resolved

        # Target lock fast-path
        lock_match = re.match(r'^(?:lock(?:\s+(?:target|plane|aircraft|contact))?)\s*(.*)$', lower)
        if lock_match and not lower.startswith("lock screen"):
            target_callsign = lock_match.group(1).strip().upper()
            classification = "new_command"
            intent = "target_lock"
            entities = {"target": target_callsign} if target_callsign else {}
            resolved = clean
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Target lock command")
            return classification, intent, entities, resolved

        # Unlock / release fast-path
        if lower in ("unlock", "release lock", "release target", "exit chase", "leave cockpit", "exit cockpit"):
            classification = "new_command"
            intent = "target_unlock"
            entities = {}
            resolved = clean
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Release lock / exit chase")
            return classification, intent, entities, resolved

        ambiguous_inv_pat = r'^(?:start\s+(?:an?\s+)?(?:investigation|recon)|investigate(?:\s+(?:someone|something|target|person))?|recon(?:\s+(?:someone|something|target|person))?|stalk\s+(?:someone|target))$'
        if re.match(ambiguous_inv_pat, lower):
            classification = "new_command"
            intent = "investigate"
            entities = {}
            resolved = clean
            self.set_pending_slot("investigate", "target", "Which target username, email, domain, or IP shall we investigate, Sir?")
            latency = (time.time() - start_t) * 1000.0
            self._log_decision(clean, classification, intent, entities, resolved, latency, note="Missing required slot: target")
            return classification, intent, entities, resolved

        classification = "new_command"
        intent = "general_covo"
        entities = {}
        resolved = clean
        latency = (time.time() - start_t) * 1000.0
        self._log_decision(clean, classification, intent, entities, resolved, latency, note="General conversation frame")
        return classification, intent, entities, resolved

    def record_turn(
        self,
        user_raw: str,
        classification: str,
        intent: str,
        entities: Dict[str, Any],
        resolved_query: str,
        agent_response: str = "",
        latency_ms: float = 0.0
    ) -> ConversationTurn:
        turn = ConversationTurn(
            turn_id=f"turn_{int(time.time() * 1000)}",
            timestamp=time.time(),
            user_raw=user_raw,
            classification=classification,
            intent=intent,
            entities=entities,
            pending_slots=list(self._pending_slot_request.keys()) if self._pending_slot_request else [],
            resolved_query=resolved_query,
            agent_response=agent_response,
            latency_ms=latency_ms
        )
        self.turns.append(turn)
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]
        return turn

    def get_context_summary_for_prompt(self) -> str:
        if not self.turns:
            return ""
        lines = ["[SHORT-TERM CONVERSATION CONTEXT (Rolling Window)]:"]
        for t in self.turns[-4:]:
            lines.append(f"- User: \"{t.user_raw}\" [Intent: {t.intent}, Classification: {t.classification}]")
            if t.entities:
                lines.append(f"  Slots: {json.dumps(t.entities)}")
            if t.agent_response:
                resp_preview = t.agent_response[:120].replace('\n', ' ')
                lines.append(f"  J.A.R.V.I.S.: \"{resp_preview}\"")
        return "\n".join(lines)

    def _log_decision(
        self,
        utterance: str,
        classification: str,
        intent: str,
        entities: dict,
        resolved: str,
        latency_ms: float,
        note: str = ""
    ):
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "utterance": utterance,
                "classification": classification,
                "intent": intent,
                "entities": entities,
                "resolved_query": resolved,
                "latency_ms": round(latency_ms, 2),
                "note": note,
            }
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
