import re
import shutil
from typing import Tuple, Dict, Any, Optional
from core.task import Task, TaskType, TaskFinding
from core.task_manager import get_task_manager, TaskManager
from core.event_bus import get_event_bus, EventBus

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

    def is_fast_path(self, text: str) -> bool:
        """Determines if the directive is a deterministic zero-LLM fast path."""
        handled, _, _, _ = self.route_fast_path(text)
        return handled

    def route_fast_path(self, text: str) -> Tuple[bool, str, Optional[Task], str]:
        """
        Executes deterministic zero-LLM fast paths with <1ms latency.
        Includes system controls, volume, media, app launching, mute, terminal, task surface,
        salutation, creator provenance, area annotation, vision, timers, and rule storage.
        """
        text_strip = (text or "").strip()
        text_lower = text_strip.lower()
        if not text_lower:
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
        elif m_tool and not any(kw in text_lower for kw in ["search", "youtube", "investigate", "target"]):
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

        # Check for ask_location clarification first
        ask_loc = next((s for s in steps if s.get("action") == "ask_location"), None)
        if ask_loc:
            phrase = ask_loc.get("progress_phrase")
            if not phrase:
                topic = ask_loc.get("topic", "telemetry")
                if "traffic" in topic:
                    phrase = f"Which city or sector would you like live traffic telemetry for, {sal}?"
                elif "optical" in topic or "cctv" in topic:
                    phrase = f"Which city would you like optical surveillance feeds for, {sal}?"
                else:
                    phrase = f"Which city or region would you like atmospheric telemetry for, {sal}?"
            return True, phrase, None, "clarification"

        # Determine non-nav steps
        non_nav_steps = [s for s in steps if s.get("action") != "nav"]
        if len(non_nav_steps) >= 2 or (steps and steps[0].get("action") in ("annotate", "cockpit", "tactical_goal")):
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

        elif action == "cctv":
            city_clean = primary_step.get("location", "").strip()
            task = self.task_manager.create_task(
                type_=TaskType.BROWSER_SURF.value,
                title=f"CCTV Surveillance: {city_clean.title()}",
                data={"cctv": True, "city": city_clean.lower()}
            )
            return True, f"Connecting to live public CCTV surveillance feeds for {city_clean.title()}, {sal}.", task, "cctv_intel"

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

