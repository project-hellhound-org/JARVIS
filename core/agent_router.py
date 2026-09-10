import re
from typing import Tuple, Dict, Any, Optional
from core.task import Task, TaskType, TaskFinding
from core.task_manager import get_task_manager, TaskManager
from core.event_bus import get_event_bus, EventBus

class AgentRouter:
    def __init__(self):
        self.task_manager: TaskManager = get_task_manager()
        self.event_bus: EventBus = get_event_bus()

    def route_input(self, text: str) -> Tuple[bool, str, Optional[Task], str]:
        """
        Routes incoming user voice/text query.
        Returns tuple:
            (handled: bool, spoken_ack: str, task: Optional[Task], action_category: str)
        """
        text_strip = text.strip()
        text_lower = text_strip.lower()
        if not text_lower:
            return False, "", None, ""

        # ── 0. Check for Mode Switch Commands ─────────────────────
        if any(kw in text_lower for kw in ["osint mode", "war room", "recon mode", "tactical mode", "war mode", "warm mode", "warm room", "engage osint", "engage war"]):
            self.event_bus.emit("set_app_mode", {"mode": "osint"})
            return True, "War room mode engaged, partner. Reconnaissance and target tracking active.", None, "mode_switch_osint"

        if any(kw in text_lower for kw in ["partner mode", "assistant mode", "casual mode", "companion mode"]):
            self.event_bus.emit("set_app_mode", {"mode": "partner"})
            return True, "Switched back to partner mode, buddy. Keeping things clean and quiet.", None, "mode_switch_partner"

        active_task = self.task_manager.get_active_task()

        # ── 1. Check for Conversational Follow-Up Commands ────────
        target_task = active_task
        if not target_task:
            tasks = self.task_manager.list_tasks()
            if tasks:
                target_task = tasks[-1]

        # A. Minimize command
        if any(w in text_lower for w in ["minimize", "minimize panel", "minimize window", "hide panel", "hide window", "minimize that", "hide that", "put it down", "minimize that window"]):
            if target_task:
                self.task_manager.minimize_task(target_task.task_id)
                return True, "Minimized the panel, buddy.", target_task, "followup_minimize"

        # B. Expand/Maximize command
        if any(w in text_lower for w in ["expand", "maximize", "show panel", "show window", "restore panel", "restore window", "bring it up", "open panel", "restore", "restore surface", "expand surface", "restore task", "maximize window", "bring that up"]):
            if target_task:
                self.task_manager.expand_task(target_task.task_id)
                return True, "Expanded the task surface.", target_task, "followup_expand"

        # C. Close/Dismiss command
        if any(w in text_lower for w in ["close panel", "close window", "close task", "dismiss panel", "dismiss window", "dismiss task", "close that", "dismiss that", "close that window"]):
            if target_task:
                self.task_manager.close_task(target_task.task_id)
                return True, "Closed the task surface.", None, "followup_close"

        # D. Select Item / Open Result ("open the second one", "show result 1")
        m_item = re.search(r'(?:open|show|play|view|select|click)\s+(?:the\s+)?(?:result\s+|video\s+|item\s+|link\s+|number\s+)?(\d+|first|second|third|fourth|fifth)', text_lower)
        if m_item and active_task and active_task.findings:
            raw_idx = m_item.group(1)
            idx_map = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
            idx_num = idx_map.get(raw_idx, int(raw_idx) if raw_idx.isdigit() else 1) - 1
            
            finding = self.task_manager.select_task_item(active_task.task_id, idx_num)
            if finding:
                # Upgrade task to Internal Browser Surface for watching/viewing
                surf_task = self.task_manager.create_task(
                    type_=TaskType.BROWSER_SURF.value,
                    title=f"Browser: {finding.title[:35]}",
                    data={"url": finding.url, "parent_task_id": active_task.task_id, "title": finding.title}
                )
                self.task_manager.add_finding(surf_task.task_id, finding)
                self.task_manager.complete_task(surf_task.task_id, f"Loaded internal surface: {finding.title}")
                return True, f"Opening {finding.title} inside the internal browser surface.", surf_task, "followup_select"

        # ── 2. Check for YouTube Search ────────────────────────────
        if "youtube" in text_lower or "watch" in text_lower or "video search" in text_lower:
            m_yt = re.search(r'(?:search\s+youtube\s+for|youtube\s+search|youtube|find\s+videos?\s+on|watch)\s+(?:for\s+)?(.+)', text_lower)
            query = m_yt.group(1).strip() if m_yt else text_strip
            query = re.sub(r'^(?:do\s+a|can\s+you|please|search|for|about)\s+', '', query, flags=re.IGNORECASE).strip()
            if not query:
                query = text_strip

            task = self.task_manager.create_task(
                type_=TaskType.YOUTUBE_SEARCH.value,
                title=f"YouTube Search: {query}",
                data={"query": query}
            )
            return True, f"Searching YouTube for '{query}', buddy.", task, "youtube_search"

        # ── 3. Check for Autonomous Terminal / System Command ──────
        # Handles explicit commands, backticks, or direct CLI binary execution (dig, nmap, curl, etc.)
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

        # ── 4. Check for Google / Web Search ───────────────────────
        if any(kw in text_lower for kw in ["google", "search", "look up", "latest news", "find out"]):
            if not any(t_kw in text_lower for t_kw in ["search target", "search case", "target investigation"]):
                m_g = re.search(r'(?:google\s+search|search\s+google|search|look\s+up|find\s+out|find)\s+(?:for\s+|about\s+|on\s+)?(.+)', text_lower)
                query = m_g.group(1).strip() if m_g else text_strip
                query = re.sub(r'^(?:do\s+a|can\s+you|please|search|for|about)\s+', '', query, flags=re.IGNORECASE).strip()
                if not query:
                    query = text_strip

                task = self.task_manager.create_task(
                    type_=TaskType.GOOGLE_SEARCH.value,
                    title=f"Google Search: {query}",
                    data={"query": query}
                )
                return True, f"Searching Google for '{query}'.", task, "google_search"

        # ── 5. Check for System Action (App Launch) ────────────────
        m_app = re.search(r'^(?:open|launch|run|start)\s+(?:application|app|program)?\s*([a-zA-Z0-9_\-\s]+)$', text_lower)
        if m_app:
            app_name = m_app.group(1).strip()
            excluded = ["google", "youtube", "dialog", "target", "investigation", "case", "node", "first", "second", "third", "fourth", "fifth", "1", "2", "3", "4", "5", "panel", "surface", "chip"] + cli_tools
            if app_name not in excluded and not app_name.startswith("the ") and not any(w in app_name for w in ["result", "video", "item", "link", "number", "second", "third", "fourth", "fifth"]):
                task = self.task_manager.create_task(
                    type_=TaskType.SYSTEM_ACTION.value,
                    title=f"System Action: Launch {app_name.upper()}",
                    data={"app_name": app_name}
                )
                return True, f"Launching {app_name} on your system.", task, "system_action"

        # ── 5. Check for Memory Recall Query ───────────────────────
        if any(kw in text_lower for kw in ["remember", "yesterday", "previous conversation", "what did we say", "what did we discuss"]):
            task = self.task_manager.create_task(
                type_=TaskType.MEMORY_RECALL.value,
                title=f"Memory Recall: {text_strip[:30]}",
                data={"query": text_strip}
            )
            return True, "Scanning memory logs for related context.", task, "memory_recall"

        # ── 6. Check for Investigation Recon ─────────────────────
        if any(kw in text_lower for kw in ["investigate", "scan target", "recon case", "analyze target"]):
            m_target = re.search(r'(?:investigate|scan|recon|analyze)\s+(?:target\s+|domain\s+)?([a-zA-Z0-9\.\-_]+)', text_lower)
            target = m_target.group(1).strip() if m_target else "target"
            task = self.task_manager.create_task(
                type_=TaskType.INVESTIGATION.value,
                title=f"Investigation: {target}",
                data={"target": target}
            )
            return True, f"Initiating investigation pipeline for target '{target}'.", task, "investigation"

        # ── 7. Check for Flight Radar / Military Airspace ──────────
        if any(kw in text_lower for kw in [
            "military flight", "military aircraft", "flight radar", "airspace",
            "tracking flight", "flight trace", "radar sweep", "god's view flight",
            "gods view flight", "military radar", "air traffic", "track aircraft", "planes overhead"
        ]):
            task = self.task_manager.create_task(
                type_=TaskType.FLIGHT_INTEL.value,
                title="Tactical Airspace Radar",
                data={"query": text_strip}
            )
            return True, "Locking onto global military airspace and ADS-B radar feeds.", task, "flight_intel"

        # ── 8. Check for Live Weather Telemetry ────────────────────
        if any(kw in text_lower for kw in [
            "weather", "temperature", "forecast", "how's the weather", "how is the weather",
            "current weather", "is it raining", "what's the weather"
        ]):
            loc = ""
            m_loc = re.search(r'(?:weather|forecast|temperature)\s+(?:in|for|at)\s+([a-zA-Z\s,]+)', text_lower)
            if m_loc:
                loc = m_loc.group(1).strip()
            task = self.task_manager.create_task(
                type_=TaskType.WEATHER_INTEL.value,
                title=f"Weather Telemetry: {loc.title() if loc else 'Local Sector'}",
                data={"location": loc, "query": text_strip}
            )
            ack = f"Scanning atmospheric telemetry for {loc.title()}, partner." if loc else "Pulling live atmospheric telemetry for our sector."
            return True, ack, task, "weather_intel"

        # ── 8B. Check for Live Traffic & GIS Map Telemetry ────────
        if any(kw in text_lower for kw in [
            "traffic", "traffic situation", "traffic condition", "traffic update",
            "traffic in", "road condition", "congestion", "map of", "show map", "gis map", "street traffic"
        ]):
            loc = ""
            m_loc = re.search(r'(?:traffic\s+(?:situation|condition|update|status)?\s+(?:in|for|at|around)?|map\s+(?:of|for|around)|where\s+is)\s+([a-zA-Z\s,]+)', text_lower)
            if m_loc:
                loc = m_loc.group(1).strip()
            if not loc:
                m_in = re.search(r'\bin\s+([a-zA-Z\s]+)$', text_lower)
                if m_in:
                    loc = m_in.group(1).strip()
            loc_clean = re.sub(r'^(?:the|a)\s+', '', loc, flags=re.I).strip()
            # Clean trailing question marks or punctuation
            loc_clean = re.sub(r'[?!.,]+$', '', loc_clean).strip()
            display_loc = loc_clean.title() if loc_clean else "Local Sector"
            task = self.task_manager.create_task(
                type_=TaskType.TRAFFIC_INTEL.value,
                title=f"Traffic Telemetry: {display_loc}",
                data={"location": loc_clean, "query": text_strip}
            )
            ack = f"Querying live traffic telemetry and GIS nodes for {display_loc}, Sir."
            return True, ack, task, "traffic_intel"

        # ── 9. Check for CCTV Surveillance Feeds ──────────────────
        if any(kw in text_lower for kw in ["cctv", "traffic cam", "security camera", "public camera", "surveillance camera"]):
            city = "shinjuku" if any(w in text_lower for w in ["tokyo", "shinjuku", "japan"]) else "austin"
            task = self.task_manager.create_task(
                type_=TaskType.BROWSER_SURF.value,
                title=f"CCTV Surveillance: {city.title()}",
                data={"cctv": True, "city": city}
            )
            return True, f"Connecting to live public CCTV surveillance feeds for {city.title()}.", task, "cctv_intel"

        return False, "", None, ""

