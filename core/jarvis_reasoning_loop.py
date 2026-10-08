"""
J.A.R.V.I.S. Autonomous Tactical Reasoning Engine ("JARVIS-Level Thinking").

Orchestrates multi-step cognitive plans, dynamically inspects and activates tactical tools
(God's Eye 3D navigation, live ADS-B radar,
traffic vectors, terminal diagnostics, atmospheric telemetry, web intelligence),
and streams real-time spoken progress phrases ("I am checking X... and I found Y")
to the voice pipeline until the goal is fully accomplished.
"""

import os
import re
import time
import json
import threading
from typing import Dict, List, Any, Optional, Callable, Tuple

from core.event_bus import get_event_bus, EventBus
from core.task_manager import get_task_manager, TaskManager
from core.task import Task, TaskType, TaskFinding


class JarvisCognitiveLoop:
    """
    Multi-Step Autonomous Tactical Cognitive Agent for J.A.R.V.I.S.
    """

    def __init__(self, voice_engine=None, event_bus: Optional[EventBus] = None):
        self.voice = voice_engine
        self.bus = event_bus or get_event_bus()
        self.task_manager: TaskManager = get_task_manager()
        self._max_steps = 5

    def get_salutation(self) -> str:
        try:
            from core.jarvis_memory import JarvisMemory
            return JarvisMemory().get_salutation() or "Sir"
        except Exception:
            return "Sir"

    # ── 1. Dynamic Tool / Capability Registry ────────────────────────

    def tool_gods_eye_nav(self, location_name: str, zoom_altitude: Optional[float] = None) -> Dict[str, Any]:
        """Navigates the 3D planetary Earth globe to the specified city or coordinates."""
        from frontend.desktop import resolve_geospatial_coordinates
        clean_loc = (location_name or "").strip().lower()
        if not clean_loc or clean_loc in ("random", "a random place", "random place", "somewhere", "anywhere") or "random" in clean_loc:
            import random
            RANDOM_SPOTS = [
                (11.0168, 76.9558, "Coimbatore, India"),
                (35.6762, 139.6503, "Tokyo, Japan"),
                (48.8566, 2.3522, "Paris, France"),
                (51.5074, -0.1278, "London, United Kingdom"),
                (40.7128, -74.0060, "New York City, USA"),
                (37.7749, -122.4194, "San Francisco, USA"),
                (-33.8688, 151.2093, "Sydney, Australia"),
                (12.9716, 77.5946, "Bengaluru, India"),
                (25.2048, 55.2708, "Dubai, UAE"),
                (41.9028, 12.4964, "Rome, Italy"),
                (-22.9068, -43.1729, "Rio de Janeiro, Brazil"),
                (64.1466, -21.9426, "Reykjavik, Iceland"),
            ]
            res = random.choice(RANDOM_SPOTS)
        else:
            res = resolve_geospatial_coordinates(location_name)
        if not res:
            return {"success": False, "error": f"Coordinates unresolvable for '{location_name}'"}
        lat, lon, matched_name = res
        payload = {"lat": lat, "lon": lon, "label": matched_name}
        if zoom_altitude:
            payload["altitude"] = zoom_altitude
        self.bus.emit("glide_to_location", payload)
        self.bus.emit("jarvis_play_sfx", {"effect": "target_lock"})
        return {"success": True, "lat": lat, "lon": lon, "label": matched_name}

    def tool_tactical_layer(self, layer: str, state: bool = True) -> Dict[str, Any]:
        """Toggles God's Eye tactical HUD overlay layers (traffic, flights, weather, space, etc.)."""
        valid_layers = {"traffic", "flights", "satellites", "seismic", "wildfires", "weather", "space", "radio"}
        clean_layer = layer.lower().strip()
        if clean_layer not in valid_layers:
            alias_map = {
                "flight": "flights", "radar": "flights", "plane": "flights", "airspace": "flights",
                "road": "traffic", "roads": "traffic", "flow": "traffic",
                "satellite": "space", "orbit": "space", "iss": "space",
                "earthquake": "seismic", "quake": "seismic",
                "fire": "wildfires", "fires": "wildfires", "firms": "wildfires",
                "clouds": "weather", "radar_rain": "weather"
            }
            clean_layer = alias_map.get(clean_layer, clean_layer)

        self.bus.emit("toggle_tactical_layer", {"layer": clean_layer, "state": state})
        return {"success": True, "layer": clean_layer, "state": state}

    def tool_annotate_area(self, location_or_target: str = "", sector_name: str = "", radius_km: float = 30.0, classification: str = "DEFENSE ZONE") -> Dict[str, Any]:
        """
        AI ability to designate, annotate, and project tactical perimeters/sectors on 3D World Telemetry.
        Resolves coordinates if location is specified, or marks current optical vantage.
        """
        from modules.maps_nav import MapsNavigationEngine
        nav = MapsNavigationEngine()
        lat, lon = None, None
        clean_loc = (location_or_target or "").strip()

        is_viewport = not clean_loc or any(ind in clean_loc.lower() for ind in (
            "here", "this area", "current area", "current view", "this sector", "viewport",
            "this place", "which i am seeing", "which im seeing", "where i am looking",
            "where im looking", "where we are looking", "what i am seeing", "that i am seeing"
        ))

        if not is_viewport:
            geo = nav.geocode(clean_loc)
            if geo:
                lat = geo.get("lat")
                lon = geo.get("lon")
                clean_loc = geo.get("name", clean_loc)

        if not sector_name:
            if not is_viewport and clean_loc:
                sector_name = f"{clean_loc.upper()} {classification}"
            else:
                sector_name = f"TACTICAL {classification}"

        payload = {
            "lat": lat,
            "lon": lon,
            "radius_km": radius_km,
            "sector_name": sector_name,
            "classification": classification,
            "use_camera_center": (lat is None or lon is None)
        }
        self.bus.emit("glide_to_telemetry", {})
        self.bus.emit("annotate_area", payload)
        self.bus.emit("jarvis_play_sfx", {"effect": "target_lock"})

        loc_str = f"at {lat:.2f}°, {lon:.2f}°" if lat is not None else "around current optical vantage"
        return {
            "success": True,
            "sector_name": sector_name,
            "radius_km": radius_km,
            "classification": classification,
            "lat": lat,
            "lon": lon,
            "debrief": f"Tactical perimeter illuminated for {sector_name} ({radius_km:.0f} km radius) {loc_str}."
        }

    def tool_traffic_query(self, location_name: str) -> Dict[str, Any]:
        """Queries live road traffic telemetry, average speeds, and GIS flow vectors."""
        from modules.maps_nav import MapsNavigationEngine
        nav = MapsNavigationEngine()
        traffic = nav.get_traffic_intel(location_name)
        if traffic and "lat" in traffic:
            self.tool_tactical_layer("traffic", True)
        return {"success": bool(traffic and "lat" in traffic), "traffic": traffic}

    def tool_flight_radar(self, query: str = "", military_only: bool = True) -> Dict[str, Any]:
        """Queries live ADS-B radar transponders and military airframes."""
        from modules.flight_intel import FlightIntelEngine
        fe = FlightIntelEngine()
        flights = fe.get_military_aircraft(limit=8)
        self.tool_tactical_layer("flights", True)
        self.bus.emit("glide_to_telemetry", {})
        debrief = fe.format_tactical_debrief(flights)
        return {"success": True, "count": len(flights), "flights": flights[:5], "debrief": debrief}

    def tool_weather_query(self, location_name: str) -> Dict[str, Any]:
        """Pulls live atmospheric telemetry and weather radar."""
        from modules.weather_intel import WeatherIntelEngine
        we = WeatherIntelEngine()
        w = we.get_weather(location_name)
        if not w:
            return {"success": False, "weather": {}, "debrief": f"Atmospheric readings for '{location_name}' could not be resolved."}
        debrief = we.format_weather_debrief(w)
        return {"success": True, "weather": w, "debrief": debrief}

    def tool_terminal_exec(self, command: str) -> Dict[str, Any]:
        """Executes authorized system/CLI command."""
        from core.system_commander import get_system_commander
        commander = get_system_commander()
        res = commander.execute(command, timeout=40.0)
        return {"success": res["success"], "stdout": res.get("stdout", "")[:1000], "exit_code": res.get("exit_code", 0)}

    def tool_web_search(self, query: str) -> Dict[str, Any]:
        """Performs real-time web search."""
        from core.system_skills import SystemSkillEngine
        skills = SystemSkillEngine()
        intel_summary, raw_results = skills.perform_live_search(query)
        return {"success": bool(raw_results), "summary": intel_summary[:1200], "results_count": len(raw_results)}

    def tool_diagnostics(self) -> Dict[str, Any]:
        """Runs live hardware diagnostics."""
        from modules.system_diagnostics import SystemDiagnosticsEngine
        diag = SystemDiagnosticsEngine()
        metrics = diag.get_metrics()
        debrief = diag.format_tactical_debrief(metrics)
        return {"success": True, "metrics": metrics, "debrief": debrief}

    def tool_top_processes(self, limit: int = 5, by: str = "cpu") -> Dict[str, Any]:
        """Inspects top active processes."""
        from modules.system_controller import SystemController
        sc = SystemController()
        procs = sc.get_top_processes(limit=limit, by=by)
        summary = ", ".join([f"{p['name']} ({p['cpu_percent']}% CPU, {p['memory_percent']}% RAM)" for p in procs[:3]])
        return {"success": True, "processes": procs, "debrief": f"Top resource processes: {summary}."}

    def tool_git_intel(self) -> Dict[str, Any]:
        """Inspects Git repository and version control telemetry."""
        from modules.system_controller import SystemController
        sc = SystemController()
        stat = sc.get_git_status()
        debrief = sc.format_git_debrief(stat)
        return {"success": stat.get("is_git", False), "status": stat, "debrief": debrief}

    def tool_situational_briefing(self, location: str = "") -> Dict[str, Any]:
        """Synthesizes comprehensive situational briefing."""
        from modules.situational_briefing import SituationalBriefingEngine
        sb = SituationalBriefingEngine()
        res = sb.generate_briefing(location)
        return {"success": True, "briefing": res, "debrief": res["spoken_text"]}

    def tool_clipboard(self) -> Dict[str, Any]:
        """Inspects and summarizes active system clipboard."""
        from modules.system_controller import SystemController
        sc = SystemController()
        debrief = sc.summarize_clipboard()
        return {"success": True, "debrief": debrief}

    def tool_calendar(self) -> Dict[str, Any]:
        """Queries calendar schedule and conflicts."""
        from modules.calendar_intel import CalendarIntelManager
        cal = CalendarIntelManager()
        debrief = cal.format_jarvis_reminders()
        return {"success": True, "debrief": debrief}

    def tool_inbox(self) -> Dict[str, Any]:
        """Queries unread messages and urgent inbox alerts."""
        from modules.inbox_intel import InboxIntelManager
        inbox = InboxIntelManager()
        debrief = inbox.get_tldr_summary()
        return {"success": True, "debrief": debrief}

    def tool_media_control(self, action: str) -> Dict[str, Any]:
        """Controls system media playback."""
        from modules.system_controller import SystemController
        sc = SystemController()
        return sc.media_control(action)

    def tool_volume(self, percent: int) -> Dict[str, Any]:
        """Adjusts system volume."""
        from modules.system_controller import SystemController
        sc = SystemController()
        return sc.set_volume(percent)

    def tool_osiris_satellites(self, query: str = "ISS") -> Dict[str, Any]:
        """Queries OSIRIS for real-time satellite tracking and orbital coordinates."""
        from modules.osiris_intel import get_osiris_client
        sats = get_osiris_client().get_satellites(query=query, limit=5)
        if sats:
            first = sats[0]
            self.bus.emit("glide_to_location", {
                "lat": first.get("lat", 0.0),
                "lon": first.get("lng", 0.0),
                "label": f"SATELLITE // {first.get('name', query.upper())}"
            })
            return {"success": True, "name": first.get("name"), "lat": first.get("lat"), "lng": first.get("lng"), "alt": first.get("alt")}
        return {"success": False, "query": query}

    def tool_osiris_conflicts(self) -> Dict[str, Any]:
        """Queries OSIRIS for active global conflict zones and warzone telemetry."""
        from modules.osiris_intel import get_osiris_client
        conflicts = get_osiris_client().get_conflicts()
        return {"success": True, "activeWarzones": conflicts.get("activeWarzones", 0), "totalZones": conflicts.get("totalZones", 0), "zones": conflicts.get("zones", [])}

    def tool_osiris_cyber_recon(self, target: str) -> Dict[str, Any]:
        """Executes OSINT cyber intelligence sweep via OSIRIS RECON toolkit."""
        from modules.osiris_intel import get_osiris_client
        recon = get_osiris_client().get_cyber_recon(target)
        return {"success": True, "target": target, "data": recon}

    def tool_osiris_directions(self, from_loc: str, to_loc: str) -> Dict[str, Any]:
        """Queries OSIRIS Valhalla/OSRM turn-by-turn road routing engine."""
        from frontend.desktop import resolve_geospatial_coordinates
        from modules.osiris_intel import get_osiris_client
        c1 = resolve_geospatial_coordinates(from_loc)
        c2 = resolve_geospatial_coordinates(to_loc)
        if c1 and c2:
            route = get_osiris_client().get_turn_by_turn_route(c1[0], c1[1], c2[0], c2[1])
            return {"success": True, "from": from_loc, "to": to_loc, "route": route}
        return {"success": False, "from": from_loc, "to": to_loc}

    def tool_memory_recall(self, query: str = "") -> Dict[str, Any]:
        """Scans memory logs and recent conversational context."""
        try:
            from core.jarvis_memory import JarvisMemory
            mem = JarvisMemory()
            history = mem.get_recent_speech_patterns(limit=5)
            sal = self.get_salutation()
            if history:
                return {
                    "success": True,
                    "count": len(history),
                    "entries": history,
                    "debrief": f"Retrieved {len(history)} recent context logs from memory, {sal}."
                }
            return {
                "success": False,
                "entries": [],
                "debrief": f"No prior memory records found for that context, {sal}."
            }
        except Exception as e:
            return {"success": False, "error": str(e), "debrief": "Memory retrieval unavailable."}

    def tool_live_news(self) -> Dict[str, Any]:
        """Accesses global SIGINT live news broadcasts."""
        sal = self.get_salutation()
        self.bus.emit("open_live_news", {})
        return {"success": True, "debrief": f"Accessing 24/7 global SIGINT broadcast network, {sal}."}

    def _ask_cloud(self, prompt: str, system: str, max_tokens: int = 250) -> Dict[str, Any]:
        """Try cloud LLMs via voice engine in priority order (Groq -> NVIDIA NIM -> Gemini)."""
        if self.voice and hasattr(self.voice, "_ask_cloud"):
            return self.voice._ask_cloud(prompt, system, max_tokens=max_tokens)
        try:
            from narrative.jarvis_voice import JarvisVoice
            voice = JarvisVoice()
            if hasattr(voice, "_ask_cloud"):
                return voice._ask_cloud(prompt, system, max_tokens=max_tokens)
        except Exception:
            pass
        return {"text": "", "rate_limited": False, "engine": None}

    def _ask_slm(self, prompt: str, system: str, max_tokens: int = 250, timeout: int = 15) -> Dict[str, Any]:
        if self.voice and hasattr(self.voice, "_ask_slm"):
            return self.voice._ask_slm(prompt, system, max_tokens=max_tokens, timeout=timeout)
        return {"text": "", "error": True}

    # ── 2. Intent & Plan Synthesis ────────────────────────────────────

    def analyze_goal(self, user_text: str, active_location: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Decomposes complex user prompts into a structured multi-step tactical plan using unified LLM tool selection.
        Falls back gracefully to heuristic parsing if cloud/SLM inference fails or times out.
        """
        text_strip = (user_text or "").strip()
        if not text_strip:
            return []

        sal = self.get_salutation()

        loc_context = f'\nActive Focused Location on Globe: "{active_location}" (If the user asks about "there", "here", "this area", or asks for traffic/weather/cameras without specifying a location, use this active location)' if active_location else ""
        prompt = f"""You are J.A.R.V.I.S.'s Tactical Goal Decomposition and Tool Selection Engine.
Analyze the user directive below and select the specialized tools needed to fulfill the request.
Return a JSON array of objects representing the required actions in execution order.

User Directive: "{text_strip}"{loc_context}

Available Tools:
- "nav": Pan/glide 3D planetary Earth globe to a location.
  Parameters: {{"action": "nav", "location": "<city or country name>"}}
- "traffic": Check live street traffic conditions, speeds, congestion, and road telemetry.
  Parameters: {{"action": "traffic", "location": "<city name or null>"}}
- "weather": Pull regional atmospheric radar, precipitation, temperature, and forecasts.
  Parameters: {{"action": "weather", "location": "<city name or null>"}}
- "flights": Scan global military and civilian ADS-B airspace radar transponders.
  Parameters: {{"action": "flights", "query": "<optional query/region>"}}
- "satellites": Orbital telemetry, satellite transit, ISS tracking.
  Parameters: {{"action": "satellites", "query": "<satellite name or ISS>"}}
- "conflicts": Global active warzones, frontlines, and theatre conflict telemetry.
  Parameters: {{"action": "conflicts"}}
- "cyber_recon": OSINT cyber reconnaissance, WHOIS, DNS, IP/domain scan.
  Parameters: {{"action": "cyber_recon", "target": "<domain, ip, or host>"}}
- "search": Live web search for recent news, facts, people, or real-time online queries.
  Parameters: {{"action": "search", "query": "<search terms>"}}
- "diagnostics": System hardware diagnostics, CPU load, RAM usage, thermals.
  Parameters: {{"action": "diagnostics"}}
- "top_processes": Audit active system processes and resource hogs by CPU or memory.
  Parameters: {{"action": "top_processes", "query": "<cpu or memory>"}}
- "git_intel": Inspect git repository branches, uncommitted changes, and working state.
  Parameters: {{"action": "git_intel"}}
- "briefing": Executive situational briefing aggregating weather, news, system telemetry.
  Parameters: {{"action": "briefing", "location": "<optional location>"}}
- "clipboard": Inspect clipboard buffers.
  Parameters: {{"action": "clipboard"}}
- "calendar": Agenda, upcoming meetings, schedule buffers.
  Parameters: {{"action": "calendar"}}
- "inbox": Communications buffer and unread messages.
  Parameters: {{"action": "inbox"}}
- "memory_recall": Search past conversation logs and persistent memory.
  Parameters: {{"action": "memory_recall", "query": "<search topic>"}}
- "cockpit": Engage 3D tactical cockpit chase camera on target aircraft.
  Parameters: {{"action": "cockpit", "target": "<callsign or target>"}}
- "directions": Turn-by-turn road driving route between two points.
  Parameters: {{"action": "directions", "from": "<origin>", "to": "<destination>"}}
- "live_news": Access 24/7 global SIGINT live news broadcasts.
  Parameters: {{"action": "live_news"}}
- "annotate": Mark/illuminate tactical sector perimeter or defense zone on 3D globe.
  Parameters: {{"action": "annotate", "location": "<loc>", "sector_name": "<name>", "radius_km": <float>, "classification": "<NO-FLY ZONE|SURVEILLANCE ZONE|DEFENSE ZONE>"}}
- "ground_intel": Query georeferenced open-source photos, citizen dispatches, YouTube clips, and drone footage at a specific city or sector.
  Parameters: {{"action": "ground_intel", "location": "<city name>"}}
- "vessels": Scan live maritime AIS vessel traffic, naval warships, tankers, and cargo ships in a port, strait, or sea.
  Parameters: {{"action": "vessels", "location": "<sea/port/region>", "query": "<optional ship name or type>"}}

Rules:
1. When a specific city/location is referenced for traffic or weather, prepend a "nav" action for that location first if the globe camera should focus there.
2. If weather or traffic is requested but NO location was provided at all in the prompt, set location to null.
3. For compound directives (e.g. "Navigate to Mumbai and see how traffic is flowing"), return all required actions in execution order: nav, traffic.
4. If the directive is general conversation, a question answerable without tools, or does not need these tools, return [].
5. Output ONLY a valid raw JSON array of objects. No markdown formatting, no explanations."""

        sys_prompt = "You are a precise tactical tool-selection agent. Output a raw JSON array of objects only."

        res_text = ""
        try:
            cloud = self._ask_cloud(prompt, sys_prompt, max_tokens=250)
            res_text = cloud.get("text", "")
            if not res_text:
                slm_res = self._ask_slm(prompt, sys_prompt, max_tokens=250, timeout=15)
                res_text = slm_res.get("text", "")
        except Exception:
            pass

        if res_text:
            try:
                clean_json = re.search(r'\[.*\]', res_text, re.DOTALL)
                if clean_json:
                    items = json.loads(clean_json.group(0))
                    if isinstance(items, list):
                        plan_steps = []
                        for item in items:
                            if not isinstance(item, dict) or "action" not in item:
                                continue
                            action = item.get("action")
                            # Check missing location for traffic / weather
                            if action in ("traffic", "weather") and not item.get("location"):
                                if active_location:
                                    item["location"] = active_location
                                else:
                                    topic = "live traffic telemetry" if action == "traffic" else "atmospheric telemetry"
                                    phrase = (
                                        f"Which city or sector would you like live traffic telemetry for, {sal}?"
                                        if action == "traffic"
                                        else f"Which city or region would you like atmospheric telemetry for, {sal}?"
                                    )
                                    plan_steps.append({
                                        "action": "ask_location",
                                        "topic": topic,
                                        "progress_phrase": phrase
                                    })
                                    continue

                            # Standard progress phrases
                            loc = item.get("location") or ""
                            if "progress_phrase" not in item:
                                if action == "nav":
                                    item["progress_phrase"] = f"Navigating orbital telemetry to {loc.title()}, {sal}..."
                                elif action == "traffic":
                                    item["progress_phrase"] = f"Cross-referencing live street traffic and GIS flow vectors for {loc.title()}, {sal}..."
                                elif action == "weather":
                                    item["progress_phrase"] = f"Pulling regional atmospheric radar and precipitation telemetry for {loc.title()}, {sal}..."
                                elif action == "flights":
                                    item["progress_phrase"] = f"Scanning global ADS-B military and civilian airspace transponders, {sal}..."
                                elif action == "satellites":
                                    item["progress_phrase"] = f"Acquiring real-time orbital telemetry for {str(item.get('query', 'ISS')).upper()}, {sal}..."
                                elif action == "conflicts":
                                    item["progress_phrase"] = f"Synthesizing active warzone telemetry and frontline geometry, {sal}..."
                                elif action == "cyber_recon":
                                    item["progress_phrase"] = f"Initiating OSINT cyber intelligence sweep on {item.get('target', 'target')}, {sal}..."
                                elif action == "search":
                                    item["progress_phrase"] = f"Scanning real-time web intelligence for '{item.get('query', text_strip)}'..."
                                elif action == "diagnostics":
                                    item["progress_phrase"] = f"Querying live hardware diagnostic sensors and CPU telemetry, {sal}..."
                                elif action == "top_processes":
                                    item["progress_phrase"] = f"Auditing active processes and resource allocation, {sal}..."
                                elif action == "git_intel":
                                    item["progress_phrase"] = f"Inspecting repository branch and working directory state, {sal}..."
                                elif action == "briefing":
                                    item["progress_phrase"] = f"Compiling multi-source executive situational briefing, {sal}..."
                                elif action == "clipboard":
                                    item["progress_phrase"] = f"Reading active system clipboard buffers, {sal}..."
                                elif action == "calendar":
                                    item["progress_phrase"] = f"Scanning your agenda and scheduling buffers, {sal}..."
                                elif action == "inbox":
                                    item["progress_phrase"] = f"Scanning inbox dispatches and priority communications, {sal}..."
                                elif action == "memory_recall":
                                    item["progress_phrase"] = f"Scanning memory logs for related context, {sal}..."
                                elif action == "cockpit":
                                    item["progress_phrase"] = "Acquiring kinematic lock and initializing 3D tactical cockpit chase camera..."
                                elif action == "annotate":
                                    item["progress_phrase"] = f"Illuminating tactical perimeter and annotating sector boundary on World Telemetry, {sal}..."
                                elif action == "directions":
                                    item["progress_phrase"] = f"Computing Valhalla turn-by-turn road route between {item.get('from', 'Origin')} and {item.get('to', 'Destination')}..."
                                elif action == "live_news":
                                    item["progress_phrase"] = f"Accessing 24/7 global SIGINT broadcast network, {sal}..."
                                elif action == "ground_intel":
                                    item["progress_phrase"] = f"Extracting georeferenced open-source media and video footage for {item.get('location', 'the target area')}, {sal}..."
                                elif action == "vessels":
                                    item["progress_phrase"] = f"Scanning maritime AIS transponders and naval vessel corridors, {sal}..."
                                else:
                                    item["progress_phrase"] = f"Executing operational action: {action}, {sal}..."

                            plan_steps.append(item)

                        return plan_steps
            except Exception:
                pass

        # Graceful fallback: heuristic flag and location extraction if cloud/SLM is unreachable
        return self._analyze_goal_fallback(user_text, active_location=active_location)

    def _analyze_goal_fallback(self, user_text: str, active_location: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Deterministic heuristic fallback for goal decomposition when offline or during cloud timeouts.
        """
        text_lower = user_text.lower().strip()
        sal = self.get_salutation()

        # Check explicit web search directives first
        m_explicit_search = re.search(r'^(?:google\s+search|search\s+google|search\s+the\s+web|web\s+search)\s+(?:for\s+|about\s+|on\s+)?(.+)', text_lower)
        if m_explicit_search:
            q = m_explicit_search.group(1).strip()
            q = re.sub(r'^(?:do\s+a|can\s+you|please|for|about)\s+', '', q, flags=re.IGNORECASE).strip()
            if q:
                return [{
                    "action": "search",
                    "query": q,
                    "progress_phrase": f"Scanning real-time web intelligence for '{q}'..."
                }]

        # Check explicit turn-by-turn routing directives
        has_directions = any(kw in text_lower for kw in [
            "turn by turn", "driving directions", "drive from", "driving route",
            "street route", "how do i drive from", "navigate from", "road directions"
        ]) or (("directions" in text_lower or "route" in text_lower) and (" from " in text_lower and " to " in text_lower))
        if has_directions:
            m_route = re.search(r'from\s+[\'"]?([^\'"]+?)[\'"]?\s+to\s+[\'"]?([^\'"]+?)[\'"]?(?:\s*$|\s+via|\s+avoiding)', text_lower)
            from_loc = m_route.group(1).strip() if m_route else ""
            to_loc = m_route.group(2).strip() if m_route else ""
            return [{
                "action": "directions",
                "from_loc": from_loc,
                "to_loc": to_loc,
                "progress_phrase": f"Computing precision turn-by-turn road route and maneuvers, {sal}..."
            }]

        # Check explicit OSINT cyber reconnaissance directives
        has_cyber = any(kw in text_lower for kw in [
            "cyber recon", "osint scan", "scan domain", "recon domain",
            "whois lookup", "dns lookup", "shodan scan", "ssl certs",
            "ip reputation", "cve scan", "sanctions check", "recon target"
        ]) or bool(re.search(r'\b(?:whois|dns|shodan|cve|certs)\s+(?:lookup|scan|recon)\b', text_lower))
        if has_cyber:
            m_tgt = re.search(r'(?:recon|scan|lookup|check)\s+(?:target\s+|domain\s+|ip\s+)?([a-zA-Z0-9\.\-_]+)', text_lower)
            tgt = m_tgt.group(1).strip() if m_tgt else "target"
            return [{
                "action": "cyber_recon",
                "target": tgt,
                "progress_phrase": f"Initiating OSINT cyber intelligence sweep on {tgt}, {sal}..."
            }]

        plan_steps = []

        loc_candidate = ""

        # Explicit navigation directives: "navigate to Tokyo", "glide to Paris", "fly to London", "go to Sydney"
        m_nav = re.search(r'\b(?:navigate\s+to|glide\s+to|fly\s+to|go\s+to|take\s+me\s+to|head\s+to|zoom\s+to|travel\s+to|inspect|visit)\s+([a-zA-Z0-9\s,\.\-]{2,45})', text_lower)
        if m_nav:
            raw_loc = m_nav.group(1).strip()
            cleaned_loc = re.sub(r'\b(?:and|please|now|telemetry|globe|view|satellite|map|ground|media|photos?|videos?)\b.*$', '', raw_loc).strip()
            loc_candidate = cleaned_loc.strip(' ,.?!')

        if not loc_candidate:
            m_loc = re.search(r'\b(?:in|at|for|around|over|near|towards|to)\s+([a-zA-Z0-9\s,\.\-]{2,30})', text_lower)
            if m_loc:
                raw_loc = m_loc.group(1).strip()
                cleaned_loc = re.sub(r'\b(?:and|check|see|show|find|tell|traffic|flights?|weather|how|what|lock|mark|pull|scan|annotate|designate|highlight)\b.*$', '', raw_loc).strip()
                loc_candidate = cleaned_loc.strip(' ,.?!')

        if not loc_candidate:
            m_scan = re.search(r'\b(?:scan|check|monitor|track|search)\s+([a-zA-Z0-9\s,\.\-]{2,30}?)\s+(?:airspace|radar|weather|traffic|perimeter|zone)\b', text_lower)
            if m_scan:
                loc_candidate = m_scan.group(1).strip(' ,.?!')

        if not loc_candidate:
            m_air = re.search(r'\b([a-zA-Z]{3,20})\s+(?:airspace|weather|traffic|radar)\b', text_lower)
            if m_air and m_air.group(1).lower() not in ('the', 'local', 'our', 'all', 'pull', 'scan', 'check'):
                loc_candidate = m_air.group(1).strip()

        # Check for exploratory or random place references
        if loc_candidate:
            loc_lower = loc_candidate.lower()
            if any(p in loc_lower for p in ["random place", "a random place", "somewhere", "anywhere", "a place", "some place"]):
                loc_candidate = "random"
            elif loc_lower in ("a place", "place", "random"):
                loc_candidate = "random"
        elif any(p in text_lower for p in ["random place", "a random place", "somewhere random", "to a random"]):
            loc_candidate = "random"

        # Viewport relative references check
        viewport_indicators = [
            "this place", "this area", "this location", "this spot", "current view",
            "current viewport", "current area", "current vantage", "where i am",
            "which i am seeing", "which im seeing", "that i am seeing", "that im seeing",
            "where i am seeing", "where i am looking", "where im looking",
            "where we are looking", "what i am seeing", "what im seeing",
            "here", "there", "the map", "the screen", "the globe"
        ]
        if loc_candidate and any(ind in loc_candidate.lower() for ind in viewport_indicators):
            loc_candidate = active_location or ""
        elif not loc_candidate and active_location and any(re.search(rf'\b{w}\b', text_lower) for w in ["there", "here", "this area", "this place"]):
            loc_candidate = active_location

        has_traffic = any(re.search(rf'\b{w}\b', text_lower) for w in ["traffic", "congestion", "road", "roads", "flow", "jam", "commute", "highway"]) and "air traffic" not in text_lower
        has_flight = any(w in text_lower for w in ["flight", "flights", "aircraft", "plane", "planes", "radar", "airspace", "ads-b", "adsb", "chase", "air traffic"])
        has_weather = any(w in text_lower for w in ["weather", "forecast", "rain", "temperature", "storm", "wind", "pull the weather"])
        has_cockpit = any(w in text_lower for w in ["cockpit", "chase cam", "lock on", "track plane", "lock onto", "nearest flight"])
        has_search = bool(re.search(r'\b(?:google\s+search|web\s+search|search\s+(?:the\s+web|google|online))\b', text_lower))
        has_briefing = any(w in text_lower for w in ["good morning", "briefing", "situational briefing", "status report", "morning protocol", "executive briefing", "how is the day looking", "how does the day look"]) and not any(w in text_lower for w in ["warzone", "conflict", "frontline", "cyber", "flight", "weather"])
        has_diag = any(w in text_lower for w in ["diagnostic", "system resource", "hardware stat", "cpu load", "thermals", "system status", "hardware status", "system telemetry", "resource monitor"])
        has_proc = any(w in text_lower for w in ["top process", "highest cpu", "highest memory", "what's using", "whats using", "memory hog", "cpu hog", "kill process", "terminate process", "running processes"])
        has_git = any(w in text_lower for w in ["git status", "repo status", "git branch", "uncommitted", "repository status", "git diff"])
        has_calendar = any(w in text_lower for w in ["calendar", "schedule", "my meetings", "upcoming event", "agenda", "double booking"])
        has_inbox = any(w in text_lower for w in ["scan email", "inbox", "urgent mail", "unread message", "check mail", "panic text"])
        has_clip = any(w in text_lower for w in ["clipboard", "what's on my clipboard", "whats on my clipboard", "read clipboard", "copied"])
        has_vol = any(w in text_lower for w in ["volume up", "volume down", "mute", "unmute", "set volume"])
        has_media = any(w in text_lower for w in ["pause music", "resume music", "play music", "next track", "previous track", "stop music"])
        has_memory = any(kw in text_lower for kw in [
            "do you remember", "what did we", "what did i", "recall our", "recall the",
            "what did we discuss", "did we discuss", "remind me what", "past discussion",
            "what was that", "search memory", "recall memory"
        ])
        has_news = any(kw in text_lower for kw in [
            "live news", "world news", "breaking news", "news broadcast", "sigint broadcast", "latest news"
        ])
        has_satellites = any(kw in text_lower for kw in [
            "satellite", "iss tracking", "track iss", "orbital tracking", "track satellite"
        ])
        has_conflicts = any(kw in text_lower for kw in [
            "conflict zone", "warzone", "war zones", "active conflicts", "global conflicts"
        ])
        has_annotate = any(w in text_lower for w in [
            "annotate", "mark this area", "mark area", "draw boundary", "defense zone",
            "tactical perimeter", "highlight area", "highlight sector", "draw perimeter",
            "surveillance zone", "no-fly zone", "no fly zone", "security perimeter", "annotate sector",
            "mark a", "mark perimeter", "perimeter", "30km"
        ])
        has_ground_intel = any(w in text_lower for w in [
            "ground media", "ground footage", "open source media", "photos of", "videos in", "footage of",
            "footage in", "clips in", "youtube", "citizen report", "what's happening on the ground",
            "whats happening on the ground", "ground evidence", "show videos", "show photos", "show footage",
            "stuffs", "stuff", "show me some stuffs", "show me some stuff", "show me stuffs",
            "show me footages", "show me clips", "show clips", "visual intelligence", "ground intel"
        ])
        has_vessels = any(w in text_lower for w in [
            "vessel", "vessels", "ship", "ships", "maritime", "ais", "warship", "warships",
            "carrier", "carriers", "naval", "tanker", "tankers", "cargo ship", "boat", "boats", "port traffic"
        ])

        if loc_candidate:
            plan_steps.append({
                "action": "nav",
                "location": loc_candidate,
                "progress_phrase": f"Navigating orbital telemetry to {loc_candidate.title()}, {sal}..."
            })

        if has_annotate:
            m_sec = re.search(r'(?:annotate|mark|highlight|designate)\s+(?:this\s+area|area|sector)?\s*(?:as|called|named)?\s*(.*)', user_text.strip(), flags=re.IGNORECASE)
            raw_sec = m_sec.group(1).strip() if m_sec else ""
            clean_sec = re.sub(r'\b(?:in|at|around|for|over|near)\s+(?:this\s+place|this\s+area|here|current\s+view|where\s+i|which\s+i|what\s+i|that\s+i).*$', '', raw_sec, flags=re.IGNORECASE).strip()
            clean_sec = re.sub(r'\b(?:with|at|radius|perimeter|km|miles?)\b.*$', '', clean_sec, flags=re.IGNORECASE).strip()
            clean_sec = re.sub(r'^(?:a|an|the)\s+', '', clean_sec, flags=re.IGNORECASE).strip()
            if re.match(r'^(?:\d+[\w\s]*|this\s+place.*|this\s+area.*|area|sector)?$', clean_sec, flags=re.IGNORECASE):
                clean_sec = ""
            sec_name = clean_sec or (f"{loc_candidate.title()} Sector" if loc_candidate else "Tactical Defense Zone")
            m_rad = re.search(r'(\d+(?:\.\d+)?)\s*(?:km|kilo)', text_lower)
            radius = float(m_rad.group(1)) if m_rad else 30.0
            classification = "NO-FLY ZONE" if ("no fly" in text_lower or "no-fly" in text_lower) else "SURVEILLANCE ZONE" if "surveillance" in text_lower else "DEFENSE ZONE"
            plan_steps.append({
                "action": "annotate",
                "location": loc_candidate,
                "sector_name": sec_name,
                "radius_km": radius,
                "classification": classification,
                "progress_phrase": f"Illuminating tactical perimeter and annotating sector boundary on World Telemetry, {sal}..."
            })

        if has_traffic:
            if loc_candidate:
                plan_steps.append({
                    "action": "traffic",
                    "location": loc_candidate,
                    "progress_phrase": f"Cross-referencing live street traffic and GIS flow vectors for {loc_candidate.title()}, {sal}..."
                })
            else:
                plan_steps.append({
                    "action": "ask_location",
                    "topic": "live traffic telemetry",
                    "progress_phrase": f"Which city or sector would you like live traffic telemetry for, {sal}?"
                })

        if has_weather:
            if loc_candidate:
                plan_steps.append({
                    "action": "weather",
                    "location": loc_candidate,
                    "progress_phrase": f"Pulling regional atmospheric radar and precipitation telemetry for {loc_candidate.title()}, {sal}..."
                })
            else:
                plan_steps.append({
                    "action": "ask_location",
                    "topic": "atmospheric telemetry",
                    "progress_phrase": f"Which city or region would you like atmospheric telemetry for, {sal}?"
                })

        if has_flight:
            plan_steps.append({
                "action": "flights",
                "query": loc_candidate,
                "progress_phrase": f"Scanning global ADS-B military and civilian airspace transponders, {sal}..."
            })

        if has_ground_intel:
            plan_steps.append({
                "action": "ground_intel",
                "location": loc_candidate or "Coimbatore",
                "progress_phrase": f"Extracting georeferenced open-source media and video footage for {(loc_candidate or 'Coimbatore').title()}, {sal}..."
            })

        if has_vessels:
            plan_steps.append({
                "action": "vessels",
                "location": loc_candidate or "",
                "query": user_text,
                "progress_phrase": f"Scanning maritime AIS transponders and naval vessel corridors, {sal}..."
            })

        if has_cockpit:
            plan_steps.append({
                "action": "cockpit",
                "target": loc_candidate or "",
                "progress_phrase": "Acquiring kinematic lock and initializing 3D tactical cockpit chase camera..."
            })

        if has_briefing:
            plan_steps.append({
                "action": "briefing",
                "location": loc_candidate,
                "progress_phrase": f"Compiling multi-source executive situational briefing, {sal}..."
            })

        if has_diag:
            plan_steps.append({
                "action": "diagnostics",
                "progress_phrase": f"Querying live hardware diagnostic sensors and CPU telemetry, {sal}..."
            })

        if has_proc:
            plan_steps.append({
                "action": "top_processes",
                "query": user_text,
                "progress_phrase": f"Auditing active processes and resource allocation, {sal}..."
            })

        if has_git:
            plan_steps.append({
                "action": "git_intel",
                "progress_phrase": f"Inspecting repository branch and working directory state, {sal}..."
            })

        if has_calendar:
            plan_steps.append({
                "action": "calendar",
                "progress_phrase": f"Scanning your agenda and scheduling buffers, {sal}..."
            })

        if has_inbox:
            plan_steps.append({
                "action": "inbox",
                "progress_phrase": f"Scanning inbox dispatches and priority communications, {sal}..."
            })

        if has_clip:
            plan_steps.append({
                "action": "clipboard",
                "progress_phrase": f"Reading active system clipboard buffers, {sal}..."
            })

        if has_vol or has_media:
            plan_steps.append({
                "action": "media_control",
                "command": user_text,
                "progress_phrase": f"Dispatching audio/media command to system controller, {sal}..."
            })

        if has_memory:
            plan_steps.append({
                "action": "memory_recall",
                "query": user_text,
                "progress_phrase": f"Querying long-term cognitive episodic and semantic memory banks, {sal}..."
            })

        if has_news:
            plan_steps.append({
                "action": "live_news",
                "query": user_text,
                "progress_phrase": f"Tuning into global SIGINT and news broadcasts, {sal}..."
            })

        if has_satellites:
            plan_steps.append({
                "action": "satellites",
                "query": loc_candidate or "ISS",
                "progress_phrase": f"Acquiring real-time orbital tracking and satellite ephemeris, {sal}..."
            })

        if has_conflicts:
            plan_steps.append({
                "action": "conflicts",
                "progress_phrase": f"Aggregating geopolitical and active warzone intelligence telemetry, {sal}..."
            })

        if not plan_steps and has_search:
            clean_q = re.sub(r'^(?:search|google|find|look up)\s+(?:for\s+|about\s+)?', '', text_lower).strip()
            plan_steps.append({
                "action": "search",
                "query": clean_q or user_text,
                "progress_phrase": f"Scanning real-time web intelligence for '{clean_q}'..."
            })

        return plan_steps

    # ── 3. Autonomous Execution Loop ──────────────────────────────────

    def execute_plan(
        self,
        user_text: str,
        on_progress_speak: Optional[Callable[[str], None]] = None,
        on_progress_ui: Optional[Callable[[str], None]] = None,
        active_location: Optional[str] = None,
        active_lat: Optional[float] = None,
        active_lon: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Executes an autonomous multi-step reasoning plan in a closed loop.
        Speaks intermediate progress updates in real-time as steps finish.
        Returns unified execution summary with clean spoken monologue.
        """
        steps = self.analyze_goal(user_text, active_location=active_location)
        if not steps:
            return {"handled": False, "text": "", "findings": []}

        sal = self.get_salutation()

        task = self.task_manager.create_task(
            type_=TaskType.TACTICAL_GOAL.value,
            title=f"Goal: {user_text[:35]}",
            data={"original_prompt": user_text, "steps_total": len(steps)}
        )

        observations: List[Dict[str, Any]] = []
        spoken_updates: List[str] = []

        total_steps = min(len(steps), self._max_steps)

        for idx, step in enumerate(steps[:self._max_steps]):
            action = step.get("action")
            prog_phrase = step.get("progress_phrase", "Processing tactical telemetry...")
            pct = int(((idx + 1) / total_steps) * 90)

            if on_progress_speak:
                on_progress_speak(prog_phrase)
            if on_progress_ui:
                on_progress_ui(prog_phrase)

            self.task_manager.update_progress(task.task_id, pct, prog_phrase)
            spoken_updates.append(prog_phrase)

            obs = {"step": idx + 1, "action": action}
            try:
                if action == "ask_location":
                    topic = step.get("topic", "telemetry")
                    clarification = f"Which city or region would you like {topic} for, {sal}?"
                    if on_progress_speak:
                        on_progress_speak(clarification)
                    self.task_manager.complete_task(task.task_id, {"status": "clarification", "message": clarification})
                    return {
                        "handled": True,
                        "text": clarification,
                        "spoken_text": clarification,
                        "findings": [],
                        "steps": spoken_updates
                    }

                elif action == "nav":
                    res = self.tool_gods_eye_nav(step["location"])
                    target_label = res.get('label', step['location'])
                    obs["result"] = f"Locked orbital camera onto {target_label}."
                    active_location = target_label
                    if res.get("lat") is not None and res.get("lon") is not None:
                        active_lat = res.get("lat")
                        active_lon = res.get("lon")
                    # Automatically fetch and emit open-source ground media for newly navigated place
                    try:
                        from modules.ground_intel import get_ground_intel_client
                        g_client = get_ground_intel_client()
                        media_res = g_client.get_ground_media_in_area(
                            location=target_label,
                            lat=res.get("lat"),
                            lon=res.get("lon")
                        )
                        pts = media_res.get("points") or media_res.get("media_points") or []
                        if pts:
                            want_open = bool(step.get("open_media") or any(s.get("action") == "ground_intel" for s in plan))
                            if want_open:
                                self.tool_tactical_layer("groundMedia", True)
                            self.bus.emit("control_ground_intel", {
                                "location": target_label,
                                "points": pts,
                                "open_first": want_open
                            })
                    except Exception as e:
                        pass


                elif action == "traffic":
                    res = self.tool_traffic_query(step["location"])
                    t = res.get("traffic", {})
                    obs["result"] = f"Traffic condition in {t.get('city', step['location'])} is {t.get('status', 'Nominal')} with average speed {t.get('avg_speed_kmh', 42)} km/h."
                    self.task_manager.add_finding(task.task_id, TaskFinding(
                        title=f"Traffic: {t.get('city', 'Sector')}",
                        url=t.get("osm_embed_url", ""),
                        snippet=f"Status: {t.get('status')} | Delay: +{t.get('delay_mins', 0)} min",
                        source="osm_traffic",
                        extra=t
                    ))

                elif action == "weather":
                    res = self.tool_weather_query(step["location"])
                    w = res.get("weather", {})
                    obs["result"] = f"Atmospheric readings in {w.get('city', step['location'])}: {w.get('condition')}, {w.get('temp_f')}°F ({w.get('temp_c')}°C), wind {w.get('wind_kmh')} km/h."

                elif action == "flights":
                    res = self.tool_flight_radar()
                    count = res.get("count", 0)
                    obs["result"] = f"ADS-B radar active: {count} military contacts airborne with live transponder telemetry."

                elif action == "annotate":
                    res = self.tool_annotate_area(
                        location_or_target=step.get("location", ""),
                        sector_name=step.get("sector_name", ""),
                        radius_km=step.get("radius_km", 30.0),
                        classification=step.get("classification", "DEFENSE ZONE")
                    )
                    obs["result"] = res.get("debrief", "Tactical perimeter illuminated.")
                    self.task_manager.add_finding(task.task_id, TaskFinding(
                        title=f"Tactical Sector: {res.get('sector_name')}",
                        url="#world-telemetry",
                        snippet=f"Radius: {res.get('radius_km')}km | Classification: {res.get('classification')}",
                        source="tactical_annotation",
                        extra=res
                    ))

                elif action == "cockpit":
                    self.bus.emit("control_cockpit", {"action": "enter", "target": step.get("target", "")})
                    obs["result"] = "Tactical cockpit chase camera engaged on selected target vector."

                elif action == "ground_intel":
                    loc = step.get("location") or ""
                    is_generic = not loc or str(loc).lower() in ("current", "here", "this place", "this area", "viewing right now", "now", "sector", "none", "random", "a random place")
                    target_loc = active_location if (is_generic and active_location) else (loc if not is_generic else "")
                    use_lat = active_lat if (is_generic and active_lat is not None) else None
                    use_lon = active_lon if (is_generic and active_lon is not None) else None

                    from modules.ground_intel import get_ground_intel_client
                    client = get_ground_intel_client()
                    query_loc = target_loc or (f"{use_lat:.4f},{use_lon:.4f}" if use_lat is not None and use_lon is not None else "Coimbatore")
                    media_res = client.get_ground_media_in_area(
                        lat=use_lat,
                        lon=use_lon,
                        location=query_loc,
                        radius_km=35.0,
                        limit=12
                    )
                    pts = media_res.get("media_points") or media_res.get("points") or []
                    count = len(pts)
                    loc_display = media_res.get("location") or target_loc or "Active Sector"
                    top_titles = [p.get("title", "") for p in pts[:3]]
                    self.tool_tactical_layer("groundMedia", True)
                    self.bus.emit("control_tactical_layer", {"layer": "groundMedia", "state": True, "location": loc_display})
                    self.bus.emit("control_ground_intel", {"location": loc_display, "points": pts, "open_first": True})
                    obs["result"] = f"Identified {count} geolocated open-source intelligence nodes across {loc_display}: {'; '.join(top_titles)}."
                    self.task_manager.add_finding(task.task_id, TaskFinding(
                        title=f"Ground Telemetry: {loc_display}",
                        url="#world-telemetry",
                        snippet=f"{count} media nodes verified | Top: {top_titles[0] if top_titles else 'None'}",
                        source="ground_intel",
                        extra=media_res
                    ))

                elif action == "vessels":
                    loc = step.get("location") or ""
                    q = step.get("query") or ""
                    from modules.maritime_intel import get_maritime_client
                    client = get_maritime_client()
                    v_res = client.get_vessels_in_area(limit=40)
                    v_list = v_res.get("vessels", [])
                    count = len(v_list)
                    top_names = [f"{v.get('name')} ({v.get('type')})" for v in v_list[:3]]
                    self.bus.emit("control_tactical_layer", {"layer": "vessels", "state": True})
                    obs["result"] = f"Scanning maritime AIS transponders. Tracked {count} active vessels in sector: {'; '.join(top_names)}."
                    self.task_manager.add_finding(task.task_id, TaskFinding(
                        title=f"Maritime AIS Fleet: {count} Vessels",
                        url="#world-telemetry",
                        snippet=f"Active naval & commercial contacts: {', '.join(top_names[:2])}",
                        source="maritime_intel",
                        extra=v_res
                    ))

                elif action == "satellites":
                    res = self.tool_osiris_satellites(step.get("query", "ISS"))
                    obs["result"] = f"Tracked satellite telemetry: {res.get('name', 'Object')} at {res.get('lat', 0):.2f}°, {res.get('lng', 0):.2f}°."

                elif action == "conflicts":
                    res = self.tool_osiris_conflicts()
                    obs["result"] = f"Monitored {res.get('activeWarzones', 0)} active warzones across global theatres."

                elif action == "cyber_recon":
                    res = self.tool_osiris_cyber_recon(step.get("target", "target"))
                    obs["result"] = f"Completed OSINT cyber recon sweep for {step.get('target')}."

                elif action == "directions":
                    res = self.tool_osiris_directions(step.get("from", "Origin"), step.get("to", "Destination"))
                    obs["result"] = f"Computed Valhalla turn-by-turn road route between {step.get('from')} and {step.get('to')}."

                elif action == "search":
                    res = self.tool_web_search(step["query"])
                    obs["result"] = res.get("summary", "Live search complete.")

                elif action == "diagnostics":
                    res = self.tool_diagnostics()
                    obs["result"] = res.get("debrief", "Hardware diagnostics completed.")

                elif action == "top_processes":
                    q = step.get("query", "").lower()
                    by = "memory" if any(w in q for w in ["memory", "ram"]) else "cpu"
                    res = self.tool_top_processes(limit=4, by=by)
                    obs["result"] = res.get("debrief", "Top process audit completed.")

                elif action == "git_intel":
                    res = self.tool_git_intel()
                    obs["result"] = res.get("debrief", "Git repository telemetry acquired.")

                elif action == "briefing":
                    res = self.tool_situational_briefing(step.get("location", ""))
                    obs["result"] = res.get("debrief", "Executive situational briefing generated.")

                elif action == "clipboard":
                    res = self.tool_clipboard()
                    obs["result"] = res.get("debrief", "Clipboard inspect complete.")

                elif action == "calendar":
                    res = self.tool_calendar()
                    obs["result"] = res.get("debrief", "Calendar agenda synchronized.")

                elif action == "inbox":
                    res = self.tool_inbox()
                    obs["result"] = res.get("debrief", "Communications buffer scanned.")

                elif action == "memory_recall":
                    res = self.tool_memory_recall(step.get("query", ""))
                    obs["result"] = res.get("debrief", "Memory scan completed.")

                elif action == "live_news":
                    res = self.tool_live_news()
                    obs["result"] = res.get("debrief", "SIGINT news broadcast accessed.")

                elif action == "media_control":
                    cmd_text = step.get("command", "").lower()
                    if "mute" in cmd_text:
                        res = self.tool_volume(0) if "unmute" not in cmd_text else self.tool_volume(65)
                    elif "pause" in cmd_text:
                        res = self.tool_media_control("pause")
                    elif "play" in cmd_text or "resume" in cmd_text:
                        res = self.tool_media_control("play")
                    else:
                        res = self.tool_media_control("toggle")
                    obs["result"] = res.get("debrief", "Audio command dispatched.")

            except Exception as tool_err:
                obs["result"] = f"Tool encounter: {tool_err}"

            observations.append(obs)
            time.sleep(0.3)

        summary_lines = [o.get("result", "") for o in observations if o.get("result")]
        summary_text = " ".join(summary_lines)

        final_debrief = (
            f"All operational tasks completed, {sal}. {summary_text}"
        )

        self.task_manager.complete_task(task.task_id, summary=final_debrief[:150])

        return {
            "handled": True,
            "text": final_debrief,
            "observations": observations,
            "spoken_updates": spoken_updates,
            "task_id": task.task_id,
            "active_location": active_location,
            "active_lat": active_lat,
            "active_lon": active_lon
        }
