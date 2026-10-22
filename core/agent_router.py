import re
import json
import shutil
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from core.task import Task, TaskType, TaskFinding
from core.task_manager import get_task_manager, TaskManager
from core.event_bus import get_event_bus, EventBus

# ---------------------------------------------------------------------------
# Leading address, wake-word, and filler word definitions for normalization
# ---------------------------------------------------------------------------
LEADING_MULTI_WORD_FILLERS = [
    "okay so",
    "by the way",
    "and then",
    "i want you to",
    "i need you to",
    "go ahead and",
    "can you please",
    "could you please",
    "would you please",
    "can you",
    "could you",
    "would you",
    "wake up jarvis",
    "wake up",
    "alright jarvis",
    "alright",
    "hey jarvis",
    "ok jarvis",
    "okay jarvis",
    "yo jarvis",
]

LEADING_SINGLE_WORD_FILLERS = [
    # Expanded discourse markers & conversational fillers:
    "actually", "well", "now", "just", "also", "and", "but", "so", "then",
    "right", "listen", "look", "alright", "anyway", "oh",
    # User-specified words:
    "buddy", "hey", "hi", "hello", "ok", "okay", "yo", "please",
    "sir", "mate", "man", "dude", "um", "uh",
    # Fuzzy wake-word spellings:
    "jarvis", "jarc", "jarvik", "jarbis", "jervis", "javis",
]

ACRONYM_WAKE_REGEX = re.compile(
    r'^(?:j\s*\.?\s*a\s*\.?\s*r\s*\.?\s*v\s*\.?\s*i\s*\.?\s*s\.?|j\s*\.?\s*a\s*\.?\s*r\s*\.?\s*c\.?)\b',
    re.IGNORECASE
)

# Documented constant list of investigation verbs and phrases
INVESTIGATION_PHRASES = [
    "investigation into",
    "investigation of",
    "investigation on",
    "investigation",
    "look into",
    "dig into",
    "run osint on",
    "osint on",
    "recon",
    "stalk",
    "trace",
    "find profiles for",
    "find profiles of",
    "find profile for",
    "find profile of",
    "find accounts for",
    "find accounts of",
    "find account for",
    "find account of",
    "find out who",
    "who is",
    "check if",
    "search for the username",
    "search for the user",
    "search for the account",
    "search for the profile",
    "search for username",
    "search for user",
    "search for account",
    "search for profile",
    "search username",
    "search user",
    "search account",
    "search profile",
    "deep_scan",
    "dox",
    "investigate",
]

CONVERSATIONAL_INVESTIGATION_PATTERNS = [
    # E.g. "do you think the police will investigate", "should I investigate", "I don't want to investigate"
    re.compile(r'\b(?:do\s+you\s+think|should\s+(?:i|we|anyone|the\s+police)|(?:i|we)\s+don\'?t\s+want\s+to|why\s+(?:would|did|should)\s+(?:anyone|they|you|we|the\s+police)|will\s+(?:they|the\s+police|anyone)|did\s+(?:they|the\s+police)|is\s+it\s+(?:legal|safe|possible|worth\s+it)\s+to|how\s+do\s+(?:people|they|police))\b.*\b(?:investigate|investigation|recon|stalk|trace)\b', re.IGNORECASE),
    re.compile(r'\b(?:what\s+do\s+you\s+think\s+about|tell\s+me\s+about\s+the\s+concept\s+of|how\s+does\s+an?)\b.*\b(?:investigate|investigation)\b', re.IGNORECASE),
    re.compile(r'\b(?:what\s+is\s+an?\s+investigation|definition\s+of\s+investigation)\b', re.IGNORECASE),
]

EXCLUSION_PATTERNS = [
    # Plain search engine queries
    re.compile(r'^(?:search\s+google(?:\s+for)?|google\s+search(?:\s+for)?|search\s+the\s+web(?:\s+for)?|web\s+search(?:\s+for)?)\b', re.IGNORECASE),
    # Weather queries
    re.compile(r'\b(?:weather|temperature|forecast|rain|radar)\b', re.IGNORECASE),
    # News queries
    re.compile(r'^(?:latest\s+news|world\s+news|breaking\s+news|show\s+news|news\s+briefing)\b', re.IGNORECASE),
    # General opinion / discussion queries without target
    re.compile(r'^(?:what\s+do\s+you\s+think\s+about|how\s+do\s+you\s+feel\s+about|tell\s+me\s+about)\b', re.IGNORECASE),
    # Technical Osiris / Cyber recon commands
    re.compile(r'\bcyber\s+recon\b', re.IGNORECASE),
]


def normalize_sentence(text: str) -> str:
    """
    Repeatedly strips leading address, wake-word, and filler words/phrases until
    nothing more is removed. Preserves original casing/formatting of the remaining sentence.
    """
    if not text:
        return ""
    cur = text.strip()
    while True:
        prev = cur
        # Strip leading punctuation/whitespace
        cur = re.sub(r'^[,\s:;.-]+', '', cur)
        if not cur:
            break

        # Check acronym wake words
        m_acr = ACRONYM_WAKE_REGEX.match(cur)
        if m_acr:
            cur = cur[m_acr.end():].strip()
            continue

        # Check multi-word filler phrases
        cur_lower = cur.lower()
        matched_multi = False
        for phrase in LEADING_MULTI_WORD_FILLERS:
            if cur_lower.startswith(phrase):
                rest = cur[len(phrase):]
                if not rest or rest[0] in " \t\n\r,.:;!?-":
                    cur = rest.strip()
                    matched_multi = True
                    break
        if matched_multi:
            continue

        # Check single-word filler words
        matched_single = False
        cur_lower = cur.lower()
        for word in LEADING_SINGLE_WORD_FILLERS:
            if cur_lower.startswith(word):
                rest = cur[len(word):]
                if not rest or rest[0] in " \t\n\r,.:;!?-":
                    # Do not strip 'look' if it forms the action verbs 'look into', 'look up', or 'look at'
                    if word == "look" and (rest.strip().lower().startswith("into") or rest.strip().lower().startswith("up") or rest.strip().lower().startswith("at")):
                        continue
                    cur = rest.strip()
                    matched_single = True
                    break
        if matched_single:
            continue

        if cur == prev:
            break

    return re.sub(r'^[,\s:;.-]+', '', cur).strip()


def _clean_target_str(raw: str) -> str:
    """Strips quotes, trailing punctuation, descriptors, determiners, and filters out generic filler targets."""
    if not raw:
        return ""
    cleaned = raw.strip().strip("'\"<>:;,.?!()[]{}").strip()

    descriptor_pattern = re.compile(
        r'^(?:'
        r'(?:the\s+user\s+called|someone\s+called|a\s+person\s+named|this\s+guy\s+named)\s+|'
        r'(?:this|that|the|a|an|my|his|her|their)\s+(?:user(?:name)?|account|handle|profile|person|target|guy)\s+(?:called|named)\s+|'
        r'(?:user(?:name)?|account|handle|profile|person|target|guy)\s+(?:called|named)\s+|'
        r'(?:this|that|the|a|an|my|his|her|their)\s+(?:user(?:name)?|account|handle|profile|person|target|guy)\s+|'
        r'(?:this|that|the|a|an|my|his|her|their)\s+|'
        r'(?:user(?:name)?|account|handle|profile|person|target|guy)\s+'
        r')',
        flags=re.IGNORECASE
    )
    while True:
        prev = cleaned
        cleaned = descriptor_pattern.sub('', cleaned).strip()
        cleaned = cleaned.strip("'\"<>:;,.?!()[]{}").strip()
        if cleaned == prev:
            break

    cleaned = re.sub(r'\s+(?:is\s+online|online|is)$', '', cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.strip("'\"<>:;,.?!()[]{}").strip()

    generic = {
        "someone", "something", "target", "person", "anyone", "user",
        "the user", "the target", "the person", "him", "her", "them",
        "me", "you", "us", "this", "that"
    }
    if cleaned.lower() in generic:
        return ""
    return cleaned


def extract_investigation_intent(cleaned_text: str, raw_text: str = "") -> Optional[Dict[str, Any]]:
    """
    Scans cleaned_text for an investigation request using INVESTIGATION_PHRASES,
    handling negative conversational uses, exclusions, mid-sentence occurrences,
    and multi-word targets.
    """
    if not cleaned_text:
        return None

    # 1. Negative conversational uses
    for pat in CONVERSATIONAL_INVESTIGATION_PATTERNS:
        if pat.search(cleaned_text):
            return None

    # 2. General exclusions
    for pat in EXCLUSION_PATTERNS:
        if pat.search(cleaned_text):
            return None

    lower = cleaned_text.lower().strip()

    # 3. Special construct A: "(who is|look up) <target> online"
    m_who = re.search(r'\b(?:who\s+is|look\s+up)\s+(.+?)\s+online(?:\s*[\?!.]*)?$', lower)
    if m_who:
        target = m_who.group(1).strip()
        raw_start = cleaned_text.lower().find(target)
        raw_target = cleaned_text[raw_start:raw_start + len(target)] if raw_start != -1 else target
        return {
            "target": _clean_target_str(raw_target),
            "is_explicit": False,
            "matched_phrase": "who is X online",
        }

    # 4. Special construct B: "check if <target> exists (online)?"
    m_exists = re.search(r'\bcheck\s+(?:if\s+)?(.+?)\s+exists(?:\s+online)?(?:\s*[\?!.]*)?$', lower)
    if m_exists:
        target = m_exists.group(1).strip()
        raw_start = cleaned_text.lower().find(target)
        raw_target = cleaned_text[raw_start:raw_start + len(target)] if raw_start != -1 else target
        return {
            "target": _clean_target_str(raw_target),
            "is_explicit": False,
            "matched_phrase": "check if X exists",
        }

    # 5. Special construct C: "has <target> been (breached|pwned|leaked)" and email breach
    m_breach = re.search(r'\b(?:has\s+)?(.+?)\s+been\s+(?:breached|pwned|leaked)(?:\s*[\?!.]*)?$', lower)
    m_breach_colon = re.search(r'\b(?:has\s+(?:this\s+)?email\s+been\s+(?:breached|pwned|leaked)\s*[:\-]?\s*)(.+?)(?:\s*[\?!.]*)?$', lower)
    m_em_breach = re.search(r'\b(?:email\s+breach(?:\s+check)?|check\s+email\s+breach)\s+(.+?)(?:\s*[\?!.]*)?$', lower)

    for m_cand, phrase_label in ((m_breach_colon, "has email been breached"), (m_breach, "has email been breached"), (m_em_breach, "email breach")):
        if m_cand:
            target = m_cand.group(1).strip()
            raw_start = cleaned_text.lower().find(target)
            raw_target = cleaned_text[raw_start:raw_start + len(target)] if raw_start != -1 else target
            return {
                "target": _clean_target_str(raw_target),
                "is_explicit": False,
                "matched_phrase": phrase_label,
            }

    # 6. Standalone / Empty investigation phrasing
    m_ambig = re.match(
        r'^(?:(?:start\s+(?:an?\s+)?(?:investigation|recon))|(?:investigate|recon|stalk|trace)(?:\s+(?:someone|something|target|person|anyone))?)$',
        lower
    )
    if m_ambig:
        return {
            "target": "",
            "is_explicit": False,
            "matched_phrase": m_ambig.group(0),
        }

    # 7. Match any phrase from INVESTIGATION_PHRASES anywhere in the sentence
    is_natural_request = False
    if raw_text:
        raw_lower = raw_text.lower()
        if any(p in raw_lower for p in [
            "can you", "could you", "would you", "i want you to", "i need you to",
            "please", "look into", "dig into", "find out who", "who is", "check if",
            "search for", "find accounts"
        ]):
            is_natural_request = True

    sorted_phrases = sorted(INVESTIGATION_PHRASES, key=lambda p: len(p), reverse=True)
    for phrase in sorted_phrases:
        pattern = r'\b' + re.escape(phrase) + r'\b'
        match = re.search(pattern, lower)
        if match:
            end_idx = match.end()
            raw_target = cleaned_text[end_idx:].strip()
            is_explicit = (not is_natural_request) and (match.start() == 0 and phrase in ("investigate", "stalk", "recon", "deep_scan", "dox", "run osint on"))
            clean_tgt = _clean_target_str(raw_target)
            return {
                "target": clean_tgt,
                "is_explicit": is_explicit and bool(clean_tgt),
                "matched_phrase": phrase,
            }

    return None


def log_intent_decision(cleaned_text: str, matched_rule: str, outcome: str) -> None:
    """
    Appends a single JSON line to data/intent_decisions.log containing ONLY
    the three allowed fields: cleaned_text, matched_rule, outcome.
    """
    try:
        log_path = Path(__file__).resolve().parent.parent / "data" / "intent_decisions.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "cleaned_text": str(cleaned_text),
            "matched_rule": str(matched_rule),
            "outcome": str(outcome)
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


class AgentRouter:
    def __init__(self):
        self.task_manager: TaskManager = get_task_manager()
        self.event_bus: EventBus = get_event_bus()

    def get_salutation(self) -> str:
        try:
            from core.jarvis_memory import JarvisMemory
            return JarvisMemory().get_salutation() or "Sir"
        except Exception:
            return "Sir"

    def is_fast_path(self, text: str, source: str = "typed") -> bool:
        """Determines if the directive is a deterministic zero-LLM fast path."""
        handled, _, _, _ = self.route_fast_path(text, source=source)
        return handled

    def route_fast_path(self, text: str, source: str = "typed") -> Tuple[bool, str, Optional[Task], str]:
        """
        Executes deterministic zero-LLM fast paths with <1ms latency.
        Includes system controls, volume, media, app launching, mute, terminal, task surface,
        salutation, creator provenance, area annotation, vision, timers, and rule storage.
        """
        text_strip = (text or "").strip()
        cleaned_text = normalize_sentence(text_strip)
        text_lower = cleaned_text.lower() if cleaned_text else text_strip.lower()
        if not text_strip:
            return False, "", None, ""

        sal = self.get_salutation()

        # ── 0A. Salutation / Honorific Directives ──
        m_sal = re.search(
            r'\b(?:call me|address me as|refer to me as|my title is|my name is)\s+([a-zA-Z0-9_\'\-]+(?:\s+[a-zA-Z0-9_\'\-]+){0,2})\b',
            text_strip,
            re.IGNORECASE
        )
        m_sal_text = None
        if m_sal:
            raw_sal = m_sal.group(1).strip()
            raw_sal = re.sub(r'\s+(?:from\s+now(?:\s+on)?|going\s+forward|please|from\s+today)$', '', raw_sal, flags=re.IGNORECASE).strip()
            disallowed = {
                "later", "back", "when", "if", "at", "on", "up", "out", "now", "soon",
                "tomorrow", "tonight", "yesterday", "here", "there", "again", "please", "maybe",
                "names", "titles", "this", "that", "something", "anything"
            }
            raw_lower = raw_sal.lower()
            if raw_lower not in disallowed and not any(raw_lower.startswith(d + " ") for d in disallowed):
                low_no_quote = raw_lower.replace("'", "")
                if low_no_quote == "maam":
                    m_sal_text = "Ma'am"
                elif low_no_quote == "mam":
                    m_sal_text = "Mam"
                elif low_no_quote in ("madam", "madame"):
                    m_sal_text = "Madam"
                elif low_no_quote == "sir":
                    m_sal_text = "Sir"
                elif any(c.isdigit() for c in raw_sal) or "_" in raw_sal:
                    m_sal_text = raw_sal
                else:
                    m_sal_text = raw_sal.title()

        if not m_sal_text:
            if any(p in text_lower for p in ["im a woman", "i am a woman", "my gender is female", "she/her"]):
                m_sal_text = "Ma'am"
            elif any(p in text_lower for p in ["default salutation", "reset salutation"]):
                m_sal_text = "Sir"

        if m_sal_text:
            try:
                from core.jarvis_memory import JarvisMemory
                mem = JarvisMemory()
                mem.set_salutation(m_sal_text)
            except Exception as e:
                print(f"[AgentRouter] Error saving salutation: {e}")
            self.event_bus.emit("set_operator_salutation", {"salutation": m_sal_text})
            ack = f"Understood, {m_sal_text}. Your preferred address has been set; all protocol registers, speech models, and telemetry have been updated."
            return True, ack, None, "set_operator_salutation"

        # ── 0B. Creator Provenance & Architecture Identity ──────────
        m_creator = re.search(
            r'\b(?:who\s+(?:built|created|made|designed|developed|programmed|coded|invented|wrote)\s+(?:you|u|jarvis)|who\s+is\s+your\s+(?:creator|maker|developer|author|architect|engineer))\b',
            text_lower
        )
        if m_creator:
            sal = self.get_salutation()
            ack = f"I was engineered and deployed by Project Hellhound, created by l4zz3rj0d. I operate as your personal tactical intelligence officer, {sal}."
            return True, ack, None, "creator_provenance"

        # ── 0. Mode Switch Commands ─────────────────────
        if any(kw in text_lower for kw in ["osint mode", "war room", "recon mode", "tactical mode", "war mode", "warm mode", "warm room", "engage osint", "engage war"]):
            self.event_bus.emit("set_app_mode", {"mode": "osint"})
            return True, f"Tactical intelligence and target reconnaissance mode engaged, {sal}.", None, "mode_switch_osint"

        if any(kw in text_lower for kw in ["partner mode", "assistant mode", "casual mode", "companion mode", "executive mode"]):
            self.event_bus.emit("set_app_mode", {"mode": "partner"})
            return True, f"Returning to primary executive mode, {sal}. Systems standing by.", None, "mode_switch_partner"

        active_task = self.task_manager.get_active_task()

        # ── 1. Task Surface Follow-Up Commands ────────
        target_task = active_task
        if not target_task:
            tasks = self.task_manager.list_tasks()
            if tasks:
                target_task = tasks[-1]

        # A. Minimize command
        if any(w in text_lower for w in ["minimize", "minimise", "hide panel", "hide window", "hide surface", "dismiss", "put it away", "minimize panel", "minimize window", "minimize surface", "minimize task", "minimize that window"]):
            if target_task:
                self.task_manager.minimize_task(target_task.task_id)
                return True, "Minimized the panel, Sir.", target_task, "followup_minimize"
            return True, "No active task surface to minimize.", None, "followup_minimize"

        # B. Expand/Maximize command
        if any(w in text_lower for w in ["expand", "maximize", "show panel", "show window", "restore panel", "restore window", "bring it up", "open panel", "restore", "restore surface", "expand surface", "restore task", "maximize window", "bring that up"]):
            if target_task:
                self.task_manager.expand_task(target_task.task_id)
                return True, "Expanded the task surface.", target_task, "followup_expand"
            return True, "No active task surface to expand.", None, "followup_expand"

        # C. Close/Dismiss command
        if any(w in text_lower for w in ["close panel", "close window", "close task", "dismiss panel", "dismiss window", "dismiss task", "close that", "dismiss that", "close that window"]):
            if target_task:
                self.task_manager.close_task(target_task.task_id)
                return True, "Closed the task surface.", None, "followup_close"
            return True, "No active task surface to close.", None, "followup_close"

        # D. Select Item / Open Result ("open the second one", "show result 1", "open the first result")
        m_item = re.search(r'(?:open|show|play|view|select|click)\s+(?:the\s+)?(?:result\s+|video\s+|item\s+|link\s+|number\s+)?(\d+|first|second|third|fourth|fifth)(?:\s+(?:result|video|item|link|one))?', text_lower)
        if m_item:
            if active_task and active_task.findings:
                raw_idx = m_item.group(1)
                idx_map = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
                idx_num = idx_map.get(raw_idx, int(raw_idx) if raw_idx.isdigit() else 1) - 1

                finding = self.task_manager.select_task_item(active_task.task_id, idx_num)
                if finding:
                    surf_task = self.task_manager.create_task(
                        type_=TaskType.BROWSER_SURF.value,
                        title=f"Browser: {finding.title[:35]}",
                        data={"url": finding.url, "parent_task_id": active_task.task_id, "title": finding.title}
                    )
                    self.task_manager.add_finding(surf_task.task_id, finding)
                    self.task_manager.complete_task(surf_task.task_id, f"Loaded internal surface: {finding.title}")
                    return True, f"Opening {finding.title} inside the internal browser surface.", surf_task, "followup_select"
            return True, "No active search results to select from.", None, "followup_select"

        # ── 2.5 OSINT Investigation Fast-Path ─────────────
        inv_intent = extract_investigation_intent(cleaned_text, raw_text=text_strip)
        if inv_intent is not None:
            target_cand = inv_intent["target"]
            is_explicit = inv_intent["is_explicit"]
            matched_phrase = inv_intent["matched_phrase"]

            target_sanitized = target_cand
            target_type = "username"

            from core.input_parser import parse, is_classifiable_target
            from core.config import get_config
            always_confirm = bool(get_config("ALWAYS_CONFIRM_INVESTIGATIONS", False))

            if source != "voice":
                # Typed commands require a classifiable target
                if not target_cand or not is_classifiable_target(target_cand):
                    clarification_msg = f"Which target username, email, domain, or IP shall we investigate, {sal}?"
                    log_intent_decision(cleaned_text, f"investigation:{matched_phrase}", "clarification")
                    return True, clarification_msg, None, "clarification"

            if target_cand and is_classifiable_target(target_cand):
                parsed = parse(target_cand)
                if parsed.target_type in ("username", "name"):
                    target_sanitized = target_cand
                else:
                    target_sanitized = parsed.value
                target_type = parsed.target_type

            should_open_brief = (source == "voice") or (not is_explicit) or always_confirm or (not target_sanitized)
            outcome = "brief_modal" if should_open_brief else "immediate_run"

            rule_name = f"investigation:{matched_phrase}"
            log_intent_decision(cleaned_text, rule_name, outcome)

            task = self.task_manager.create_task(
                type_=TaskType.OSINT_INVESTIGATE.value,
                title=f"OSINT: {target_sanitized or 'Brief'}",
                data={
                    "target": target_sanitized,
                    "target_type": target_type,
                    "query": text_strip,
                    "cleaned_query": cleaned_text,
                    "is_explicit": is_explicit,
                    "should_open_brief": should_open_brief,
                    "outcome": outcome,
                }
            )
            ack = f"Initiating OSINT reconnaissance on {target_sanitized}, {sal}." if target_sanitized else f"Opening investigation brief, {sal}."
            return True, ack, task, "osint_investigate"

        # ── 3. Autonomous Terminal / System Commands ──────
        cmd_candidate = None
        m_cmd_explicit = re.search(r'^(?:run\s+command|execute\s+command|terminal|shell|bash)\s*[:\-]?\s*(.+)$', text_strip, re.IGNORECASE)
        m_backtick = re.search(r'^(?:run|exec|execute)?\s*`([^`]+)`$', text_strip, re.IGNORECASE)

        cli_tools = [
            "dig", "nmap", "curl", "subfinder", "nuclei", "httpx", "ping", "whois",
            "traceroute", "df", "free", "ps", "ls", "cat", "python", "python3",
            "bash", "ip", "netstat", "ss", "uptime", "uname", "grep", "which",
            "find", "head", "tail", "wc"
        ]
        tools_regex = r'^(?:run|exec|execute)?\s*(' + '|'.join(cli_tools) + r')\b(.*)$'
        m_tool = re.search(tools_regex, text_strip, re.IGNORECASE)

        if m_backtick:
            cmd_candidate = m_backtick.group(1).strip()
        elif m_cmd_explicit:
            cmd_candidate = m_cmd_explicit.group(1).strip().strip('`')
        elif m_tool and not any(kw in text_lower for kw in ["search", "youtube", "investigate", "target", "profile", "account"]):
            tool_name = m_tool.group(1).strip().lower()
            rest = m_tool.group(2).strip()
            cmd_candidate = f"{tool_name} {rest}".strip()

        if cmd_candidate:
            task = self.task_manager.create_task(
                type_=TaskType.TERMINAL_COMMAND.value,
                title=f"Terminal: {cmd_candidate[:30]}",
                data={"command": cmd_candidate}
            )
            return True, f"Executing `{cmd_candidate}` on your system.", task, "terminal_command"

        # ── 4.7 Voice Mute / Unmute Agency Directives ──
        is_voice_unmute = any(p in text_lower for p in [
            "unmute yourself", "can you unmute yourself", "could you unmute yourself",
            "unmute jarvis", "unmute your voice", "unmute the voice", "unmute mic",
            "unmute microphone", "resume listening", "start listening"
        ])
        is_voice_mute = not is_voice_unmute and any(p in text_lower for p in [
            "mute yourself", "can you mute yourself", "could you mute yourself",
            "mute jarvis", "mute your voice", "mute the voice", "mute mic",
            "mute microphone", "be quiet jarvis", "be quiet", "stop talking",
            "shut up jarvis", "shut up", "silence yourself"
        ])
        if is_voice_mute or is_voice_unmute:
            action_type = "voice_mute" if is_voice_mute else "voice_unmute"
            task = self.task_manager.create_task(
                type_=TaskType.SYSTEM_CONTROL.value,
                title=f"Voice Control: {'Mute' if is_voice_mute else 'Unmute'}",
                data={"action_type": action_type, "command": text_strip}
            )
            msg = f"Muting voice system, {sal}." if is_voice_mute else f"Restoring voice system, {sal}."
            return True, msg, task, "system_control"

        # ── 4.8 System Agency Controls (Volume, Media, Lock, Process Kill, Clipboard) ──
        if any(kw in text_lower for kw in [
            "volume up", "volume down", "mute", "unmute", "set volume", "turn up the volume",
            "turn down the volume", "pause music", "resume music", "play music", "next track",
            "previous track", "stop music", "lock screen", "lock workstation", "lock computer",
            "clipboard", "top process", "highest cpu", "highest memory", "what's eating memory",
            "whats eating memory", "kill process", "terminate process"
        ]):
            action_type = "volume"
            if any(w in text_lower for w in ["pause", "resume", "play music", "track", "stop music"]):
                action_type = "media"
            elif any(w in text_lower for w in ["lock"]):
                action_type = "lock"
            elif any(w in text_lower for w in ["clipboard"]):
                action_type = "clipboard"
            elif any(w in text_lower for w in ["process", "cpu", "memory"]):
                action_type = "process"

            task = self.task_manager.create_task(
                type_=TaskType.SYSTEM_CONTROL.value,
                title=f"System Control: {action_type.title()}",
                data={"action_type": action_type, "command": text_strip}
            )
            return True, f"Executing {action_type} system directive, {sal}.", task, "system_control"

        # ── 5. System Action (App Launch) ────────────────
        m_app = re.search(r'^(?:open|launch|run|start)\s+(?:application|app|program)?\s*([a-zA-Z0-9_\-\s]+)$', text_lower)
        if m_app:
            app_name = m_app.group(1).strip()
            excluded = ["google", "youtube", "dialog", "target", "investigation", "case", "node", "first", "second", "third", "fourth", "fifth", "1", "2", "3", "4", "5", "panel", "surface", "chip"] + cli_tools
            known_apps = {"browser", "chrome", "firefox", "terminal", "calculator", "calc", "files", "code", "vscode", "editor", "gedit", "vlc", "spotify", "slack", "discord", "settings"}
            is_valid_app = (app_name in known_apps) or bool(shutil.which(app_name)) or bool(shutil.which(app_name.replace(" ", "-")))
            if is_valid_app and app_name not in excluded and not app_name.startswith("the ") and not any(w in app_name for w in ["result", "video", "item", "link", "number", "second", "third", "fourth", "fifth"]):
                task = self.task_manager.create_task(
                    type_=TaskType.SYSTEM_ACTION.value,
                    title=f"System Action: Launch {app_name.upper()}",
                    data={"app_name": app_name}
                )
                return True, f"Launching {app_name} on your system.", task, "system_action"

        # ── 4.8 Memory Storage / Rule Definition ─────────
        is_query = bool(
            "?" in text_strip
            or re.search(r'^(?:what|how|why|when|where|who|which|is|are|can|could|do|does|explain|tell\s+me)\b', text_lower)
            or any(kw in text_lower for kw in [
                "do you remember", "what did we", "what did i", "what do you mean",
                "what does", "recall our", "recall the", "i mean", "meaning of", "the meaning",
                "how to", "how do", "how we", "can you", "could you"
            ])
        )
        if not is_query:
            store_match = re.search(
                r'^(?:(?:hey\s+)?jarvis,?\s*)?(?:(?:always\s+)?remember\s+(?:this\s+rule|this\s+log|this|that)|save\s+(?:this\s+)?(?:rule|preference)|store\s+(?:this\s+)?(?:rule|preference|in\s+memory)|keep\s+in\s+mind\s+that|from\s+now\s+on\s+(?:always|never|remember)|.+?\b(?:doesn\'?t\s+mean.+?it\s+says\s+that|means\s+that\b))\b',
                text_lower
            )
            if store_match:
                rule_text = text_strip
                rule_text = re.sub(r'^(?:j\.?a\.?r\.?v\.?i\.?s\.?,?\s*|please\s+|hey\s+jarvis,?\s*)', '', rule_text, flags=re.IGNORECASE).strip()
                task = self.task_manager.create_task(
                    type_="memory_store",
                    title=f"Store Memory: {rule_text[:35]}",
                    data={"rule": rule_text}
                )
                return True, f"I have committed that rule to persistent memory, {sal}.", task, "memory_store"

        # ── 4.9 Desktop Vision & OCR ──
        if any(kw in text_lower for kw in [
            "look at my screen", "see my screen", "check my screen", "read my screen",
            "what's on my screen", "what is on my screen", "what am i looking at",
            "what am i pointing at", "explain this error", "read this window",
            "read this code", "look at this", "ocr this", "extract text from screen",
            "inspect screen", "read pointer", "look at this code", "look at this error"
        ]):
            task = self.task_manager.create_task(
                type_=TaskType.VISION_INSPECT.value,
                title="Desktop Vision & Cursor Inspection",
                data={"query": text_strip}
            )
            return True, f"Deploying Stark Vision Eye and analyzing text under your cursor, {sal}.", task, "vision_inspect"

        # ── 4.10 Ambient Sentinel Telemetry ─────────────
        if any(kw in text_lower for kw in [
            "quiet mode", "enable quiet mode", "disable quiet mode",
            "sentinel status", "guardian status", "check vitals", "vitals check"
        ]):
            task = self.task_manager.create_task(
                type_=TaskType.AMBIENT_CONFIG.value,
                title="Ambient Sentinel Guardian",
                data={"query": text_strip}
            )
            return True, f"Accessing Ambient Sentinel configuration and telemetry, {sal}.", task, "ambient_config"

        # ── 4.11 Engineering Lab Script Authoring ───────
        if any(kw in text_lower for kw in [
            "create script", "write script", "write a python script",
            "create a python script", "engineering script", "write a script"
        ]):
            task = self.task_manager.create_task(
                type_=TaskType.ENGINEERING_SCRIPT.value,
                title="Stark Engineering Lab",
                data={"query": text_strip}
            )
            return True, f"Initializing Stark Engineering Lab sandbox, {sal}.", task, "engineering_script"

        # ── 4.12 Proactive Reminders & Timers ───────────
        if any(kw in text_lower for kw in [
            "set a timer", "set timer", "start a timer", "start timer",
            "countdown", "remind me", "set a reminder", "set reminder",
            "list reminders", "show reminders", "my reminders", "what are my reminders",
            "list timers", "show timers", "my timers", "what are my timers",
            "active timers", "active reminders", "any timers", "any reminders",
            "cancel timer", "cancel reminder", "clear timers", "stop timer"
        ]) or re.search(r'^(?:timer|reminder)\b', text_lower):
            task = self.task_manager.create_task(
                type_=TaskType.REMINDER_TIMER.value,
                title="Proactive Reminder & Timer",
                data={"query": text_strip}
            )
            return True, f"Configuring tactical reminder protocol, {sal}.", task, "reminder_timer"

        # ── 6.5 Case Cross-Verification ──────────────────
        if any(kw in text_lower for kw in ["cross verify", "cross-verify", "verify case", "correlate case", "cross check case", "cross-check case", "check cross correlation"]):
            task = self.task_manager.create_task(
                type_="cross_verify_case",
                title="Case Cross-Verification",
                data={"query": text_strip}
            )
            return True, f"Cross-verifying active case telemetry against all intelligence records, {sal}.", task, "cross_verify_case"

        # ── 7.6 Area Annotation (AI Ability) ─────────────
        if any(kw in text_lower for kw in [
            "annotate this area", "annotate area", "mark this area", "highlight this area",
            "annotate sector", "draw boundary", "defense zone", "tactical perimeter",
            "no-fly zone", "no fly zone", "exclusion zone", "mark area", "mark a zone",
            "mark zone", "perimeter zone"
        ]) or (("annotate" in text_lower or "mark" in text_lower) and any(z in text_lower for z in ["zone", "perimeter", "sector", "boundary"])):
            m_sec = re.search(r'(?:annotate|mark|highlight)\s+(?:this\s+area|area|sector)\s*(?:as|called|named)?\s*(.*)', text_strip, flags=re.IGNORECASE)
            sec_name = m_sec.group(1).strip() if m_sec else ""
            m_rad = re.search(r'(\d+)\s*(?:km|kilo)', text_lower)
            radius = float(m_rad.group(1)) if m_rad else 30.0
            classification = "NO-FLY ZONE" if ("no fly" in text_lower or "no-fly" in text_lower) else ("DEFENSE ZONE" if "defense" in text_lower else "TACTICAL SECTOR")
            if not sec_name:
                sec_name = classification

            task = self.task_manager.create_task(
                type_="annotate_area",
                title=f"Annotate: {sec_name[:25]}",
                data={"sector_name": sec_name, "radius_km": radius, "classification": classification, "query": text_strip}
            )
            self.event_bus.emit("annotate_area", {
                "sector_name": sec_name,
                "radius_km": radius,
                "classification": classification,
                "use_camera_center": True
            })
            return True, f"Illuminating tactical perimeter and annotating {sec_name} on World Telemetry, {sal}.", task, "annotate_area"

        return False, "", None, ""

    def route_input(self, text: str) -> Tuple[bool, str, Optional[Task], str]:
        """
        Routes incoming user query.
        Zero-LLM fast paths execute instantly (<1ms).
        Goal-driven queries are classified and decomposed via unified tool selection.
        """
        # 1. Fast paths always execute instantly with zero LLM latency
        handled, ack, task, cat = self.route_fast_path(text)
        if handled:
            return handled, ack, task, cat

        # 2. Unified goal-driven classification: model dynamically selects tools from registry
        from core.jarvis_reasoning_loop import JarvisCognitiveLoop
        steps = JarvisCognitiveLoop().analyze_goal(text)
        if not steps:
            return False, "", None, ""

        sal = self.get_salutation()
        text_strip = (text or "").strip()
        cleaned_text = normalize_sentence(text_strip)

        # Check for ask_location clarification first
        ask_loc = next((s for s in steps if s.get("action") == "ask_location"), None)
        if ask_loc:
            phrase = ask_loc.get("progress_phrase")
            if not phrase:
                topic = ask_loc.get("topic", "telemetry")
                if "traffic" in topic:
                    phrase = f"Which city or sector would you like live traffic telemetry for, {sal}?"
                else:
                    phrase = f"Which city or region would you like atmospheric telemetry for, {sal}?"
            return True, phrase, None, "clarification"

        # Determine non-nav steps
        non_nav_steps = [s for s in steps if s.get("action") != "nav"]
        if len(non_nav_steps) >= 2 or (steps and steps[0].get("action") in ("annotate", "tactical_goal")):
            task = self.task_manager.create_task(
                type_=TaskType.TACTICAL_GOAL.value,
                title=f"Goal: {text_strip[:35]}",
                data={"original_prompt": text_strip, "steps": steps}
            )
            return True, f"Engaging autonomous multi-step reasoning protocol, {sal}.", task, "tactical_goal"

        primary_step = non_nav_steps[0] if non_nav_steps else steps[0]
        action = primary_step.get("action")

        if action == "search":
            q = primary_step.get("query", text_strip)
            task = self.task_manager.create_task(
                type_=TaskType.GOOGLE_SEARCH.value,
                title=f"Google Search: {q}",
                data={"query": q}
            )
            log_intent_decision(cleaned_text or text_strip, "google_search", "google_search")
            return True, f"Searching Google for '{q}'.", task, "google_search"

        elif action == "diagnostics":
            task = self.task_manager.create_task(
                type_=TaskType.SYSTEM_DIAGNOSTIC.value,
                title="System Diagnostic & Telemetry",
                data={"query": text_strip}
            )
            return True, f"Running full hardware and system diagnostics, {sal}.", task, "system_diagnostic"

        elif action == "top_processes":
            task = self.task_manager.create_task(
                type_=TaskType.SYSTEM_CONTROL.value,
                title="System Control: Process",
                data={"action_type": "process", "command": text_strip}
            )
            return True, f"Executing process system directive, {sal}.", task, "system_control"

        elif action == "briefing":
            task = self.task_manager.create_task(
                type_=TaskType.SITUATIONAL_BRIEFING.value,
                title="Executive Situational Briefing",
                data={"query": text_strip}
            )
            return True, f"Compiling executive situational briefing, {sal}.", task, "situational_briefing"

        elif action == "git_intel":
            task = self.task_manager.create_task(
                type_=TaskType.GIT_INTEL.value,
                title="Git Repository Telemetry",
                data={"query": text_strip}
            )
            return True, f"Auditing repository telemetry and version control status, {sal}.", task, "git_intel"

        elif action == "memory_recall":
            q = primary_step.get("query", text_strip)
            task = self.task_manager.create_task(
                type_=TaskType.MEMORY_RECALL.value,
                title=f"Memory Recall: {q[:30]}",
                data={"query": q}
            )
            return True, f"Scanning memory logs for related context, {sal}.", task, "memory_recall"

        elif action == "flights":
            task = self.task_manager.create_task(
                type_=TaskType.FLIGHT_INTEL.value,
                title="Tactical Airspace Radar",
                data={"query": text_strip}
            )
            return True, "Locking onto global military airspace and ADS-B radar feeds.", task, "flight_intel"

        elif action == "satellites":
            sat_name = str(primary_step.get("query", "ISS")).upper()
            task = self.task_manager.create_task(
                type_=TaskType.SATELLITE_TRACK.value,
                title=f"Orbital Tracking: {sat_name}",
                data={"query": sat_name}
            )
            return True, f"Acquiring real-time orbital telemetry for {sat_name}, {sal}.", task, "satellite_track"

        elif action == "conflicts":
            task = self.task_manager.create_task(
                type_=TaskType.CONFLICT_INTEL.value,
                title="Global Conflict & Warzone Intelligence",
                data={"query": text_strip}
            )
            return True, f"Synthesizing active warzone telemetry and frontline geometry, {sal}.", task, "conflict_intel"

        elif action == "directions":
            task = self.task_manager.create_task(
                type_=TaskType.DIRECTIONS.value,
                title="Valhalla Street Navigation",
                data={"query": text_strip}
            )
            return True, f"Computing precision turn-by-turn road route and maneuvers, {sal}.", task, "directions"

        elif action == "osint_investigate":
            tgt = str(primary_step.get("target") or text_strip).strip().strip("'\"<>:;,?")
            from core.input_parser import parse, is_classifiable_target
            if not is_classifiable_target(tgt):
                return True, f"Which target username, email, domain, or IP shall we investigate, {sal}?", None, "clarification"
            parsed = parse(tgt)
            target_sanitized = parsed.value
            task = self.task_manager.create_task(
                type_=TaskType.OSINT_INVESTIGATE.value,
                title=f"OSINT: {target_sanitized}",
                data={"target": target_sanitized, "target_type": parsed.target_type, "query": text_strip, "is_explicit": False}
            )
            return True, f"Initiating OSINT target reconnaissance on {target_sanitized}, {sal}.", task, "osint_investigate"

        elif action == "cyber_recon":
            tgt = primary_step.get("target", "target")
            task = self.task_manager.create_task(
                type_=TaskType.CYBER_RECON.value,
                title=f"Cyber RECON: {tgt}",
                data={"target": tgt, "query": text_strip}
            )
            return True, f"Initiating OSINT cyber intelligence sweep on {tgt}, {sal}.", task, "cyber_recon"

        elif action == "live_news":
            task = self.task_manager.create_task(
                type_=TaskType.LIVE_NEWS.value,
                title="Global SIGINT News Broadcast",
                data={"query": text_strip}
            )
            return True, f"Accessing 24/7 global SIGINT broadcast network, {sal}.", task, "live_news"

        elif action == "weather":
            loc_clean = primary_step.get("location", "").strip()
            task = self.task_manager.create_task(
                type_=TaskType.WEATHER_INTEL.value,
                title=f"Weather Telemetry: {loc_clean.title()}",
                data={"location": loc_clean, "query": text_strip}
            )
            return True, f"Scanning atmospheric telemetry for {loc_clean.title()}, {sal}.", task, "weather_intel"

        elif action == "traffic":
            loc_clean = primary_step.get("location", "").strip()
            task = self.task_manager.create_task(
                type_=TaskType.TRAFFIC_INTEL.value,
                title=f"Traffic Telemetry: {loc_clean.title()}",
                data={"location": loc_clean, "query": text_strip}
            )
            return True, f"Querying live traffic telemetry and GIS nodes for {loc_clean.title()}, {sal}.", task, "traffic_intel"


        elif action == "ground_intel":
            loc_clean = primary_step.get("location", "").strip() or "Coimbatore"
            task = self.task_manager.create_task(
                type_=TaskType.TACTICAL_GOAL.value,
                title=f"Ground Intel: {loc_clean.title()}",
                data={"original_prompt": text_strip, "steps": steps}
            )
            return True, f"Extracting georeferenced open-source media and video footage for {loc_clean.title()}, {sal}.", task, "tactical_goal"

        elif action == "vessels":
            loc_clean = primary_step.get("location", "").strip()
            task = self.task_manager.create_task(
                type_=TaskType.TACTICAL_GOAL.value,
                title=f"Maritime AIS Fleet{': ' + loc_clean.title() if loc_clean else ''}",
                data={"original_prompt": text_strip, "steps": steps}
            )
            return True, f"Scanning maritime AIS transponders and naval vessel corridors, {sal}.", task, "tactical_goal"

        elif action == "nav":
            loc_clean = primary_step.get("location", "").strip()
            task = self.task_manager.create_task(
                type_=TaskType.TACTICAL_GOAL.value,
                title=f"Navigation: {loc_clean.title()}",
                data={"original_prompt": text_strip, "steps": steps}
            )
            return True, f"Navigating orbital telemetry to {loc_clean.title()}, {sal}.", task, "tactical_goal"

        return False, "", None, ""

