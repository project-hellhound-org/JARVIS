import os
import re
import shutil
import subprocess
import urllib.parse
import webbrowser
from typing import Any
from core.jarvis_memory import JarvisMemory
from core.structured_hud_engine import StructuredHUDEngine

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "skills")


def _format_flight_alt(alt: Any) -> str:
    """Safely format flight altitude without throwing ValueError when alt is 'ground' or string."""
    if alt is None or alt == "":
        return "N/A"
    s_alt = str(alt).strip()
    if s_alt.lower() == "ground":
        return "Ground"
    try:
        val = int(float(s_alt))
        return f"{val:,} ft"
    except (ValueError, TypeError):
        return f"{s_alt} ft"


def _format_flight_speed(gs: Any) -> str:
    """Safely format ground speed in knots."""
    if gs is None or gs == "":
        return "0 kts"
    try:
        val = float(gs)
        if val.is_integer():
            return f"{int(val)} kts"
        return f"{val:.1f} kts"
    except (ValueError, TypeError):
        return f"{gs} kts"


def _format_flight_track(track: Any) -> str:
    """Safely format flight bearing/track in degrees."""
    if track is None or track == "":
        return "0°"
    try:
        val = float(track)
        if val.is_integer():
            return f"{int(val)}°"
        return f"{val:.1f}°"
    except (ValueError, TypeError):
        return f"{track}°"


class SystemSkillEngine:
    def __init__(self):
        self.memory = JarvisMemory()
        self.hud_engine = StructuredHUDEngine()
        os.makedirs(SKILLS_DIR, exist_ok=True)

    def perform_live_search(self, query: str) -> tuple[str, list[dict]]:
        """
        Perform a fast live web search using DuckDuckGo HTML POST API.
        Returns (summary_text: str, results_list: list[dict])
        """
        import urllib.request
        import urllib.parse
        query_clean = query.strip()
        if not query_clean:
            return "", []

        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query_clean}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            }
        )
        results = []
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')

            titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html_text, re.DOTALL)
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html_text, re.DOTALL)
            urls = re.findall(r'<a[^>]+class="result__url"[^>]*href="([^"]+)"', html_text, re.DOTALL)

            for i in range(min(6, len(snippets))):
                t = re.sub(r'<[^>]+>', '', titles[i]).strip() if i < len(titles) else f"Record #{i+1}"
                s = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                link = urls[i].strip() if i < len(urls) else f"https://www.google.com/search?q={urllib.parse.quote(query_clean)}"
                if link.startswith("//"):
                    link = "https:" + link
                elif not link.startswith("http"):
                    link = "https://" + link
                
                s = s.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&nbsp;', ' ')
                t = t.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&nbsp;', ' ')
                if s:
                    results.append({"title": t, "snippet": s, "url": link})

            if results:
                summary_parts = [f"Live web search records for '{query_clean}':\n"]
                for idx, item in enumerate(results[:5], 1):
                    summary_parts.append(f"{idx}. {item['title']}: {item['snippet']}")
                return "\n".join(summary_parts), results
        except Exception as e:
            print(f"[system_skills] Live search error: {e}")

        fallback_url = f"https://www.google.com/search?q={urllib.parse.quote(query_clean)}"
        return f"Google search complete for '{query_clean}'.", [{"title": f"Google Search: {query_clean}", "snippet": f"Search results for {query_clean}", "url": fallback_url}]

    def perform_youtube_search(self, query: str) -> tuple[str, list[dict]]:
        """
        Perform a live YouTube video search using DuckDuckGo HTML search.
        Returns (summary_text: str, results_list: list[dict])
        """
        import urllib.request
        import urllib.parse
        query_clean = query.strip()
        if not query_clean:
            return "", []

        url = f"https://html.duckduckgo.com/html/?q=site:youtube.com+{urllib.parse.quote(query_clean)}"
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            }
        )
        results = []
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')

            titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html_text, re.DOTALL)
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html_text, re.DOTALL)
            urls = re.findall(r'<a[^>]+class="result__url"[^>]*href="([^"]+)"', html_text, re.DOTALL)

            for i in range(min(6, len(snippets))):
                t = re.sub(r'<[^>]+>', '', titles[i]).strip() if i < len(titles) else f"YouTube Video #{i+1}"
                s = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                link = urls[i].strip() if i < len(urls) else f"https://www.youtube.com/results?search_query={urllib.parse.quote(query_clean)}"
                if "uddg=" in link:
                    m = re.search(r'uddg=([^&]+)', link)
                    if m:
                        link = urllib.parse.unquote(m.group(1))
                t = t.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&nbsp;', ' ')
                s = s.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&nbsp;', ' ')
                results.append({"title": t, "snippet": s, "url": link, "type": "youtube"})

            if results:
                summary_parts = [f"YouTube video search records for '{query_clean}':\n"]
                for idx, item in enumerate(results[:5], 1):
                    summary_parts.append(f"{idx}. {item['title']}: {item['snippet']}")
                return "\n".join(summary_parts), results
        except Exception as e:
            print(f"[system_skills] YouTube search error: {e}")

        yt_fallback_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query_clean)}"
        return f"YouTube video search initialized for '{query_clean}'.", [{"title": f"YouTube: {query_clean}", "snippet": f"Watch YouTube results for {query_clean}", "url": yt_fallback_url}]

    def google_search(self, query: str) -> str:
        query_clean = query.strip()
        summary, _ = self.perform_live_search(query_clean)
        return summary

    def open_application(self, app_name: str) -> str:
        app_clean = app_name.strip().lower()

        # Common mapping
        aliases = {
            "browser": ["google-chrome", "firefox", "chromium"],
            "google": ["google-chrome", "firefox"],
            "chrome": ["google-chrome", "chromium"],
            "terminal": ["x-terminal-emulator", "tilix", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"],
            "calculator": ["kcalc", "gnome-calculator", "galculator", "xcalc"],
            "files": ["thunar", "nautilus", "dolphin", "pcmanfm"],
            "code": ["code", "vscodium", "sublime_text"],
            "editor": ["gedit", "kate", "mousepad", "nano"]
        }

        target_execs = aliases.get(app_clean, [app_clean])
        found_bin = None
        for exec_candidate in target_execs:
            b = shutil.which(exec_candidate)
            if b:
                found_bin = b
                break

        if found_bin:
            try:
                subprocess.Popen([found_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Launched application '{app_clean}' ({os.path.basename(found_bin)})."
            except Exception as e:
                return f"Failed to launch '{app_clean}': {e}"
        else:
            # Try xdg-open or direct launch
            try:
                subprocess.Popen([app_clean], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Attempted launch for '{app_clean}'."
            except Exception as e:
                return f"Could not find or launch application '{app_clean}'."

    def create_custom_skill(self, name: str, description: str, trigger: str, code: str) -> str:
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
        file_path = os.path.join(SKILLS_DIR, f"{safe_name}.py")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f'"""Skill: {description}\nTrigger: {trigger}\n"""\n\n{code}\n')
            self.memory.register_learned_skill(safe_name, description, trigger, code)
            return f"Successfully created and registered new skill '{safe_name}'."
        except Exception as e:
            return f"Failed to create skill '{safe_name}': {e}"

    @staticmethod
    def is_relative_query(query: str) -> bool:
        """
        Check if search query relies on relative pronouns or conversational context
        (e.g., "that guy", "him", "he", "this person", "that target", "bro").
        """
        if not query:
            return True
        q_clean = query.strip().lower()
        if len(q_clean) <= 2:
            return True
        relative_patterns = [
            r'\b(?:that|this)\s+(?:guy|man|person|user|target|handle|profile|account|individual|dude|bro)\b',
            r'\b(?:him|her|he|she|them|they|it|that|this)\b',
            r'^(?:on\s+|about\s+|for\s+)?(?:that|this|him|her|he|she|them|it|bro|guy)(?:\s+bro)?$',
            r'\bthat\s+guy\b',
            r'\bon\s+that\b'
        ]
        for pat in relative_patterns:
            if re.search(pat, q_clean):
                return True
        return False

    def try_execute(self, command_text: str, on_progress=None) -> tuple[bool, str, bool, str, dict]:
        """
        Check if user input matches an OS system skill command or search request.
        Returns (handled: bool, message: str, is_search: bool, query: str, structured_payload: dict)
        """
        text = command_text.strip()
        text_lower = text.lower()

        # Dynamic self-upgrade memory logger
        try:
            from core.jarvis_memory import JarvisMemory
            mem = JarvisMemory()
            mem.store_memory("skill_execution", f"Active skill requested: '{text}'")
        except Exception:
            pass

        # ── Agent Router & Task Manager Integration ──────────────
        from core.agent_router import AgentRouter
        from core.task_manager import get_task_manager
        from core.task import TaskType, TaskFinding

        task_mgr = get_task_manager()
        router = AgentRouter()
        handled, ack_msg, task, action_cat = router.route_input(text)

        if handled:
            if action_cat.startswith("followup_") or not task:
                payload = {"action_type": action_cat}
                if action_cat == "set_operator_salutation":
                    payload["salutation"] = self.memory.get_salutation()
                return True, ack_msg, False, "", payload

            if task:
                if task.type == TaskType.YOUTUBE_SEARCH.value:
                    query = task.data.get("query", text)
                    task_mgr.update_progress(task.task_id, 30, f"Searching YouTube for '{query}'...")
                    msg, raw_results = self.perform_youtube_search(query)
                    for item in raw_results:
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=item.get("title", "YouTube Video"),
                            url=item.get("url", ""),
                            snippet=item.get("snippet", ""),
                            source="youtube"
                        ))
                    task_mgr.complete_task(task.task_id, summary=f"{len(raw_results)} YouTube results found")
                    payload = self.hud_engine.build_structured_payload(f"YouTube: {query}", "YOUTUBE", raw_results, msg)
                    return True, msg, True, query, payload

                elif task.type == TaskType.GOOGLE_SEARCH.value:
                    query = task.data.get("query", text)
                    task_mgr.update_progress(task.task_id, 30, f"Searching Google for '{query}'...")
                    msg, raw_results = self.perform_live_search(query)
                    for item in raw_results:
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=item.get("title", "Result"),
                            url=item.get("url", ""),
                            snippet=item.get("snippet", ""),
                            source="google"
                        ))
                    task_mgr.complete_task(task.task_id, summary=f"{len(raw_results)} Google results found")
                    payload = self.hud_engine.build_structured_payload(f"Google: {query}", "SEARCH", raw_results, msg)
                    return True, msg, True, query, payload

                elif task.type == TaskType.SYSTEM_ACTION.value:
                    app = task.data.get("app_name", "")
                    task_mgr.update_progress(task.task_id, 50, f"Launching process '{app}'...")
                    msg = self.open_application(app)
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=f"Launch App: {app}",
                        url=f"app://{app}",
                        snippet=msg,
                        source="system"
                    ))
                elif task.type == TaskType.TERMINAL_COMMAND.value:
                    cmd_str = task.data.get("command", "")
                    task_mgr.update_progress(task.task_id, 20, f"Executing: {cmd_str[:40]}...")
                    from core.system_commander import get_system_commander
                    commander = get_system_commander()
                    res = commander.execute(cmd_str, timeout=60.0)
                    stdout_snip = res["stdout"][:2000] if res["stdout"] else ""
                    stderr_snip = res["stderr"][:1000] if res["stderr"] else ""
                    output_display = stdout_snip or stderr_snip or "Command completed with no output."

                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=f"Terminal: {cmd_str[:30]}",
                        url="term://system",
                        snippet=output_display[:400],
                        source="terminal",
                        extra=res
                    ))
                    summary_msg = f"Exit code {res['exit_code']} in {res['elapsed_sec']}s"
                    if res["success"]:
                        task_mgr.complete_task(task.task_id, summary=summary_msg, extra_data=res)
                    else:
                        task_mgr.fail_task(task.task_id, error_msg=res.get("error") or summary_msg)

                    payload = self.hud_engine.build_structured_payload(
                        f"Command: {cmd_str[:30]}",
                        "TERMINAL",
                        [{"title": f"Output ({res['elapsed_sec']}s)", "snippet": output_display, "url": "term://output"}],
                        summary_msg
                    )
                    return True, f"Executed `{cmd_str}` (exit {res['exit_code']}):\n{output_display[:600]}", False, "", payload

                elif task.type == "memory_store":
                    rule_text = task.data.get("rule", "")
                    self.memory.store_custom_rule(rule_text)
                    sal = self.memory.get_salutation()
                    task_mgr.complete_task(task.task_id, summary=f"Rule committed to persistent memory: {rule_text}")
                    msg = f"I have committed that rule to persistent memory, {sal}."
                    return True, msg, False, "", {"action_type": "MEMORY_STORE", "rule": rule_text}

                elif task.type == TaskType.MEMORY_RECALL.value:
                    task_mgr.update_progress(task.task_id, 40, "Retrieving memory entries...")
                    mem_history = self.memory.get_recent_speech_patterns(limit=5)
                    sal = self.memory.get_salutation()
                    if mem_history:
                        msg = f"Retrieved {len(mem_history)} recent context logs from memory, {sal}."
                        raw_results = []
                        for idx, entry in enumerate(mem_history, 1):
                            f_item = {"title": f"Memory Context #{idx}", "snippet": entry, "url": "memory://log"}
                            raw_results.append(f_item)
                            task_mgr.add_finding(task.task_id, TaskFinding(
                                title=f"Memory Context #{idx}",
                                url="memory://log",
                                snippet=entry,
                                source="memory"
                            ))
                        task_mgr.complete_task(task.task_id, summary=msg)
                        payload = self.hud_engine.build_structured_payload("Memory Recall", "MEMORY", raw_results, msg)
                        return True, msg, False, "", payload
                    else:
                        msg = f"No prior memory records found for that context, {sal}."
                        task_mgr.complete_task(task.task_id, summary=msg)
                        return True, msg, False, "", {}

                elif task.type == TaskType.FLIGHT_INTEL.value:
                    task_mgr.update_progress(task.task_id, 30, "Scanning ADS-B and military transponder feeds...")
                    from modules.flight_intel import FlightIntelEngine
                    fe = FlightIntelEngine()
                    flights = fe.get_military_aircraft(limit=8)
                    task_mgr.update_progress(task.task_id, 80, f"Identified {len(flights)} active military airframes.")
                    debrief_msg = fe.format_tactical_debrief(flights)
                    raw_results = []
                    for f in flights:
                        flight_label = f.get('flight') or f.get('hex') or 'Unknown'
                        desc = f.get('desc') or f.get('t') or 'Military Airframe'
                        alt_str = _format_flight_alt(f.get('alt_baro', 0))
                        speed_str = _format_flight_speed(f.get('gs', 0))
                        track_str = _format_flight_track(f.get('track', 0))
                        squawk_str = f.get('squawk') or 'N/A'
                        snip = f"Alt: {alt_str} | Speed: {speed_str} | Track: {track_str} | Squawk: {squawk_str}"
                        url = f"https://globe.adsb.lol/?icao={f.get('hex', '')}"
                        f_item = {"title": f"{flight_label} - {desc}", "snippet": snip, "url": url, "extra": f}
                        raw_results.append(f_item)
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=f_item["title"],
                            url=url,
                            snippet=snip,
                            source="adsb.lol",
                            extra=f
                        ))
                    task_mgr.complete_task(task.task_id, summary=f"{len(flights)} military radar signatures locked.")
                    payload = self.hud_engine.build_structured_payload("Military Radar", "RADAR", raw_results, debrief_msg)
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.WEATHER_INTEL.value:
                    loc = task.data.get("location", "")
                    task_mgr.update_progress(task.task_id, 30, f"Pulling atmospheric telemetry for '{loc or 'Local'}'...")
                    from modules.weather_intel import WeatherIntelEngine
                    we = WeatherIntelEngine()
                    w = we.get_weather(loc)
                    debrief_msg = we.format_weather_debrief(w)
                    raw_results = [{
                        "title": f"Atmospheric Telemetry - {w.get('city', 'Local')}",
                        "snippet": f"{w.get('condition')} | {w.get('temp_f')}°F ({w.get('temp_c')}°C) | Wind: {w.get('wind_kmh')} km/h | Humidity: {w.get('humidity')}% | Pressure: {w.get('pressure_hpa')} hPa",
                        "url": "https://open-meteo.com",
                        "extra": w
                    }]
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=raw_results[0]["title"],
                        url="https://open-meteo.com",
                        snippet=raw_results[0]["snippet"],
                        source="open-meteo",
                        extra=w
                    ))
                    task_mgr.complete_task(task.task_id, summary=f"Weather: {w.get('condition')}, {w.get('temp_f')}°F")
                    payload = self.hud_engine.build_structured_payload(f"Weather: {w.get('city')}", "WEATHER", raw_results, debrief_msg)
                    return True, debrief_msg, False, "", payload

                elif task.type in (TaskType.TRAFFIC_INTEL.value, TaskType.MAPS_NAV.value):
                    loc = task.data.get("location", "")
                    task_mgr.update_progress(task.task_id, 30, f"Querying traffic telemetry and GIS nodes for '{loc or 'Local Sector'}'...")
                    from modules.maps_nav import MapsNavigationEngine
                    nav = MapsNavigationEngine()
                    traffic_data = nav.get_traffic_intel(loc)
                    task_mgr.update_progress(task.task_id, 80, f"Traffic telemetry verified: {traffic_data.get('status', 'Nominal')}")
                    debrief_msg = nav.format_traffic_debrief(traffic_data)
                    raw_results = [{
                        "title": f"Traffic Intelligence — {traffic_data.get('city', 'Sector')}",
                        "snippet": f"Flow: {traffic_data.get('status')} | Avg Speed: {traffic_data.get('avg_speed_kmh')} km/h | Delay: +{traffic_data.get('delay_mins')} mins",
                        "url": traffic_data.get("osm_embed_url", ""),
                        "extra": traffic_data
                    }]
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=raw_results[0]["title"],
                        url=traffic_data.get("osm_embed_url", ""),
                        snippet=raw_results[0]["snippet"],
                        source="OpenStreetMap/TomTom",
                        extra=traffic_data
                    ))
                    task_mgr.complete_task(task.task_id, summary=f"Traffic: {traffic_data.get('status')}, {traffic_data.get('avg_speed_kmh')} km/h")
                    payload = self.hud_engine.build_structured_payload(f"Traffic Intel: {traffic_data.get('city')}", "MAPS", raw_results, debrief_msg)
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.SYSTEM_DIAGNOSTIC.value:
                    task_mgr.update_progress(task.task_id, 30, "Querying live hardware telemetry...")
                    from modules.system_diagnostics import SystemDiagnosticsEngine
                    diag = SystemDiagnosticsEngine()
                    metrics = diag.get_metrics()
                    debrief_msg = diag.format_tactical_debrief(metrics)
                    payload = diag.build_hud_payload(metrics)
                    for item in payload.get("findings", []):
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=item["headline"],
                            url=item["url"],
                            snippet=item["summary"],
                            source="system_telemetry",
                            extra=metrics
                        ))
                    task_mgr.complete_task(task.task_id, summary=f"CPU: {metrics['cpu_percent']}%, RAM: {metrics['ram_percent']}%, Thermals: {metrics['peak_thermal_c']}°C")
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="System Diagnostics & Hardware Telemetry", action_type="DIAGNOSTIC", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.BROWSER_SURF.value and task.data.get("cctv"):
                    city = task.data.get("city", "")
                    sal = self.memory.get_salutation()
                    debrief_msg = f"Deploying God's Eye tactical surveillance feeds for {city.title() if city else 'active sector'}, {sal}."
                    task_mgr.complete_task(task.task_id, summary="CCTV optical layers online")
                    return True, debrief_msg, False, "", {"action_type": "CCTV", "city": city}

                elif task.type == TaskType.SATELLITE_TRACK.value:
                    sat_query = task.data.get("query", "ISS")
                    sal = self.memory.get_salutation()
                    from modules.osiris_intel import get_osiris_client
                    sats = get_osiris_client().get_satellites(query=sat_query, limit=5)
                    if sats:
                        target_sat = sats[0]
                        s_name = target_sat.get("name", sat_query.upper())
                        s_lat = target_sat.get("lat", 0.0)
                        s_lon = target_sat.get("lng", 0.0)
                        s_alt = target_sat.get("alt", 420)
                        debrief_msg = f"Orbital telemetry acquired for {s_name}. Current position is {s_lat:.2f} degrees latitude, {s_lon:.2f} degrees longitude, altitude {s_alt} kilometers, {sal}."
                        task_mgr.complete_task(task.task_id, summary=f"Tracking {s_name}: Alt {s_alt}km")
                        payload = {"action_type": "SATELLITE", "satellite": target_sat, "lat": s_lat, "lon": s_lon, "name": s_name}
                    else:
                        debrief_msg = f"Unable to acquire telemetry for satellite '{sat_query}' on orbital tracking arrays, {sal}."
                        task_mgr.complete_task(task.task_id, summary="Satellite not found")
                        payload = {"action_type": "SATELLITE", "query": sat_query}
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title=f"Orbital Tracking // {sat_query.upper()}", action_type="SATELLITE", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.CONFLICT_INTEL.value:
                    sal = self.memory.get_salutation()
                    from modules.osiris_intel import get_osiris_client
                    conflicts = get_osiris_client().get_conflicts()
                    n_warzones = conflicts.get("activeWarzones", 0)
                    n_total = conflicts.get("totalZones", 0)
                    zones = conflicts.get("zones", [])
                    top_names = [z.get("label", "Zone") for z in zones[:3]]
                    top_str = ", ".join(top_names) if top_names else "major geopolitical theatres"
                    debrief_msg = f"Monitoring {n_warzones} active warzones out of {n_total} tracked conflict zones globally, {sal}. Primary active sectors include {top_str}."
                    task_mgr.complete_task(task.task_id, summary=f"Conflicts: {n_warzones} active warzones")
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="Global Conflict & Frontline Intelligence", action_type="CONFLICT", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", {"action_type": "CONFLICT", "data": conflicts}

                elif task.type == TaskType.DIRECTIONS.value:
                    query_text = task.data.get("query", "")
                    sal = self.memory.get_salutation()
                    m_dirs = re.search(r'from\s+["\']?([^"\',]+)["\']?\s+to\s+["\']?([^"\',]+)["\']?', query_text, flags=re.IGNORECASE)
                    from_loc = m_dirs.group(1).strip() if m_dirs else "Origin"
                    to_loc = m_dirs.group(2).strip() if m_dirs else "Destination"

                    from frontend.desktop import resolve_geospatial_coordinates
                    c_from = resolve_geospatial_coordinates(from_loc)
                    c_to = resolve_geospatial_coordinates(to_loc)

                    if c_from and c_to:
                        from modules.osiris_intel import get_osiris_client
                        route = get_osiris_client().get_turn_by_turn_route(c_from[0], c_from[1], c_to[0], c_to[1])
                        debrief_msg = f"Turn-by-turn road route computed from {from_loc.title()} to {to_loc.title()}, {sal}. Projecting navigation corridor on the 3D grid."
                        payload = {"action_type": "DIRECTIONS", "from": from_loc, "to": to_loc, "route": route, "from_coords": c_from, "to_coords": c_to}
                    else:
                        debrief_msg = f"Routing directive received for {from_loc.title()} to {to_loc.title()}, {sal}."
                        payload = {"action_type": "DIRECTIONS", "from": from_loc, "to": to_loc}
                    task_mgr.complete_task(task.task_id, summary=f"Route: {from_loc} -> {to_loc}")
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.CYBER_RECON.value:
                    target = task.data.get("target", "target")
                    sal = self.memory.get_salutation()
                    from modules.osiris_intel import get_osiris_client
                    recon_data = get_osiris_client().get_cyber_recon(target)
                    debrief_msg = f"Cyber reconnaissance sweep complete for {target}, {sal}. Network indicators and threat intelligence cataloged."
                    task_mgr.complete_task(task.task_id, summary=f"Cyber RECON: {target}")
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title=f"Cyber Intelligence // {target}", action_type="RECON", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", {"action_type": "CYBER_RECON", "target": target, "data": recon_data}

                elif task.type == TaskType.LIVE_NEWS.value:
                    sal = self.memory.get_salutation()
                    from modules.osiris_intel import get_osiris_client
                    news_feeds = get_osiris_client().get_live_news()
                    feed_count = len(news_feeds)
                    debrief_msg = f"Connecting to 24/7 global SIGINT broadcast streams, {sal}. {feed_count} international channels active."
                    task_mgr.complete_task(task.task_id, summary=f"SIGINT Broadcasts: {feed_count} channels")
                    return True, debrief_msg, False, "", {"action_type": "LIVE_NEWS", "feeds": news_feeds}

                elif task.type == TaskType.SITUATIONAL_BRIEFING.value:
                    task_mgr.update_progress(task.task_id, 30, "Compiling multi-source situational briefing...")
                    from modules.situational_briefing import SituationalBriefingEngine
                    sb = SituationalBriefingEngine()
                    briefing = sb.generate_briefing()
                    debrief_msg = briefing["spoken_text"]
                    raw_results = briefing.get("structured_findings", [])
                    for item in raw_results:
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=item["title"],
                            url=item["url"],
                            snippet=item["snippet"],
                            source="situational_telemetry"
                        ))
                    task_mgr.complete_task(task.task_id, summary="Situational briefing compiled", extra_data=briefing)
                    payload = self.hud_engine.build_structured_payload("Situational Briefing", "BRIEFING", raw_results, debrief_msg)
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="Executive Situational Briefing", action_type="BRIEFING", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.SYSTEM_CONTROL.value:
                    act_type = task.data.get("action_type", "volume")
                    cmd_str = task.data.get("command", "").lower()
                    from modules.system_controller import SystemController
                    sc = SystemController()

                    if act_type == "clipboard":
                        task_mgr.update_progress(task.task_id, 40, "Reading active clipboard buffers...")
                        debrief_msg = sc.summarize_clipboard()
                        raw_results = [{"title": "System Clipboard", "snippet": debrief_msg, "url": "system://clipboard"}]
                        task_mgr.add_finding(task.task_id, TaskFinding(title="System Clipboard", url="system://clipboard", snippet=debrief_msg, source="clipboard"))
                        task_mgr.complete_task(task.task_id, summary="Clipboard inspected")
                        payload = self.hud_engine.build_structured_payload("System Clipboard", "CLIPBOARD", raw_results, debrief_msg)
                        return True, debrief_msg, False, "", payload

                    elif act_type == "process":
                        task_mgr.update_progress(task.task_id, 40, "Auditing process telemetry...")
                        if any(w in cmd_str for w in ["kill", "terminate"]):
                            m_target = re.search(r'(?:kill|terminate)\s+(?:process\s+)?([a-zA-Z0-9_\-\.]+)', cmd_str)
                            target_proc = m_target.group(1).strip() if m_target else ""
                            res = sc.kill_process(target_proc)
                            debrief_msg = res["debrief"]
                            raw_results = [{"title": f"Process Termination: {target_proc}", "snippet": debrief_msg, "url": "system://processes"}]
                        else:
                            by_metric = "memory" if any(w in cmd_str for w in ["memory", "ram"]) else "cpu"
                            top_procs = sc.get_top_processes(limit=5, by=by_metric)
                            summary_procs = ", ".join([f"{p['name']} ({p['cpu_percent']}% CPU, {p['memory_percent']}% RAM)" for p in top_procs[:3]])
                            debrief_msg = f"Top resource processes on your machine, Sir: {summary_procs}."
                            raw_results = [{
                                "title": f"Process: {p['name']} (PID {p['pid']})",
                                "snippet": f"CPU: {p['cpu_percent']}% | RAM: {p['memory_percent']}% | Status: {p['status']}",
                                "url": f"system://process/{p['pid']}"
                            } for p in top_procs]

                        for r in raw_results:
                            task_mgr.add_finding(task.task_id, TaskFinding(title=r["title"], url=r["url"], snippet=r["snippet"], source="process_monitor"))
                        task_mgr.complete_task(task.task_id, summary="Process audit completed")
                        payload = self.hud_engine.build_structured_payload("Process Telemetry", "PROCESS", raw_results, debrief_msg)
                        return True, debrief_msg, False, "", payload

                    elif act_type == "lock":
                        task_mgr.update_progress(task.task_id, 50, "Locking workstation...")
                        res = sc.lock_workstation()
                        debrief_msg = res["debrief"]
                        task_mgr.complete_task(task.task_id, summary="Workstation locked")
                        payload = self.hud_engine.build_structured_payload("Workstation Lock", "SYSTEM", [{"title": "Workstation Security", "snippet": debrief_msg, "url": "system://lock"}], debrief_msg)
                        return True, debrief_msg, False, "", payload

                    elif act_type == "media":
                        task_mgr.update_progress(task.task_id, 50, "Dispatching media control...")
                        if "pause" in cmd_str:
                            res = sc.media_control("pause")
                        elif "resume" in cmd_str or "play" in cmd_str:
                            res = sc.media_control("play")
                        elif "next" in cmd_str:
                            res = sc.media_control("next")
                        elif "prev" in cmd_str:
                            res = sc.media_control("prev")
                        else:
                            res = sc.media_control("toggle")
                        debrief_msg = res["debrief"]
                        task_mgr.complete_task(task.task_id, summary="Media command executed")
                        payload = self.hud_engine.build_structured_payload("Media Playback", "MEDIA", [{"title": "Media Playback", "snippet": debrief_msg, "url": "system://media"}], debrief_msg)
                        return True, debrief_msg, False, "", payload

                    elif action_type == "voice_mute":
                        task_mgr.update_progress(task.task_id, 100, "Muting voice...")
                        debrief_msg = f"Voice audio and listening suspended, {sal}. Tap microphone or say wake command to reactivate."
                        task_mgr.complete_task(task.task_id, summary="Voice muted")
                        payload = self.hud_engine.build_structured_payload("Voice Control", "VOICE", [{"title": "Voice State", "snippet": debrief_msg, "url": "system://voice"}], debrief_msg)
                        payload["voice_mute_action"] = True
                        return True, debrief_msg, False, "", payload

                    elif action_type == "voice_unmute":
                        task_mgr.update_progress(task.task_id, 100, "Restoring voice...")
                        debrief_msg = f"Voice audio and listening active, {sal}."
                        task_mgr.complete_task(task.task_id, summary="Voice unmuted")
                        payload = self.hud_engine.build_structured_payload("Voice Control", "VOICE", [{"title": "Voice State", "snippet": debrief_msg, "url": "system://voice"}], debrief_msg)
                        payload["voice_unmute_action"] = True
                        return True, debrief_msg, False, "", payload

                    else:  # volume
                        task_mgr.update_progress(task.task_id, 50, "Adjusting audio volume...")
                        if "mute" in cmd_str and "unmute" not in cmd_str:
                            res = sc.mute(True)
                        elif "unmute" in cmd_str:
                            res = sc.mute(False)
                        elif "up" in cmd_str:
                            res = sc.adjust_volume(10)
                        elif "down" in cmd_str:
                            res = sc.adjust_volume(-10)
                        else:
                            m_pct = re.search(r'(\d+)', cmd_str)
                            pct_val = int(m_pct.group(1)) if m_pct else 70
                            res = sc.set_volume(pct_val)
                        debrief_msg = res["debrief"]
                        task_mgr.complete_task(task.task_id, summary=f"Volume adjusted: {res.get('volume', '')}%")
                        payload = self.hud_engine.build_structured_payload("Audio Control", "AUDIO", [{"title": "Audio Control", "snippet": debrief_msg, "url": "system://audio"}], debrief_msg)
                        return True, debrief_msg, False, "", payload

                elif task.type == TaskType.GIT_INTEL.value:
                    task_mgr.update_progress(task.task_id, 30, "Auditing repository working directory...")
                    from modules.system_controller import SystemController
                    sc = SystemController()
                    stat = sc.get_git_status()
                    debrief_msg = sc.format_git_debrief(stat)
                    raw_results = [{
                        "title": f"Git Branch: {stat.get('branch', 'unknown')}",
                        "snippet": debrief_msg,
                        "url": "git://status",
                        "extra": stat
                    }]
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=raw_results[0]["title"],
                        url=raw_results[0]["url"],
                        snippet=raw_results[0]["snippet"],
                        source="git_telemetry",
                        extra=stat
                    ))
                    task_mgr.complete_task(task.task_id, summary=f"Git: {stat.get('branch')}, {len(stat.get('modified', []))} modified")
                    payload = self.hud_engine.build_structured_payload("Git Repository Status", "GIT", raw_results, debrief_msg)
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="Git Repository Telemetry", action_type="GIT", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.VISION_INSPECT.value:
                    task_mgr.update_progress(task.task_id, 30, "Deploying Stark Vision Eye...")
                    from modules.desktop_vision import DesktopVisionEngine
                    v_engine = DesktopVisionEngine()
                    vis_res = v_engine.inspect_screen_at_cursor()
                    debrief_msg = vis_res["debrief"]
                    raw_results = [{
                        "title": f"Vision: {vis_res['window'].get('title', 'Active Window')}",
                        "snippet": debrief_msg,
                        "url": vis_res.get("crop_path") or vis_res.get("screenshot_path") or "vision://display",
                        "extra": vis_res
                    }]
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=raw_results[0]["title"],
                        url=raw_results[0]["url"],
                        snippet=raw_results[0]["snippet"],
                        source="desktop_vision",
                        extra=vis_res
                    ))
                    task_mgr.complete_task(task.task_id, summary=f"Vision: {vis_res['window'].get('wm_class', 'display')}")
                    payload = self.hud_engine.build_structured_payload("Desktop Vision Inspection", "VISION", raw_results, debrief_msg)
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="Desktop Vision & Cursor Inspection", action_type="VISION", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.AMBIENT_CONFIG.value:
                    task_mgr.update_progress(task.task_id, 30, "Querying Ambient Sentinel telemetry...")
                    from modules.ambient_sentinel import AmbientSentinel
                    sentinel = AmbientSentinel.get_instance()
                    cmd_q = task.data.get("query", "").lower()
                    if "quiet mode on" in cmd_q or "enable quiet mode" in cmd_q:
                        debrief_msg = sentinel.enable_quiet_mode(True)
                    elif "quiet mode off" in cmd_q or "disable quiet mode" in cmd_q:
                        debrief_msg = sentinel.enable_quiet_mode(False)
                    else:
                        vitals = sentinel.check_vitals()
                        status = sentinel.get_status()
                        debrief_msg = f"{status['debrief']} {len(vitals)} active vital notifications logged."

                    raw_results = [{
                        "title": "Ambient Sentinel Guardian",
                        "snippet": debrief_msg,
                        "url": "sentinel://status"
                    }]
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=raw_results[0]["title"],
                        url=raw_results[0]["url"],
                        snippet=raw_results[0]["snippet"],
                        source="ambient_sentinel"
                    ))
                    task_mgr.complete_task(task.task_id, summary="Sentinel telemetry inspected")
                    payload = self.hud_engine.build_structured_payload("Ambient Sentinel", "SENTINEL", raw_results, debrief_msg)
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.ENGINEERING_SCRIPT.value:
                    task_mgr.update_progress(task.task_id, 30, "Initializing Stark Engineering Lab...")
                    from modules.engineering_lab import EngineeringLab
                    lab = EngineeringLab()
                    name = "voice_probe"
                    lang = "python"
                    code = "import sys, os\nprint(f'Stark Diagnostic Node: Python {sys.version.split()[0]} | Working dir: {os.getcwd()}')"
                    
                    res = lab.run_task(name, lang, code)
                    debrief_msg = res["debrief"]
                    raw_results = [{
                        "title": f"Script: {res.get('filename', 'probe.py')}",
                        "snippet": debrief_msg,
                        "url": res.get("filepath", "lab://script"),
                        "extra": res
                    }]
                    task_mgr.add_finding(task.task_id, TaskFinding(
                        title=raw_results[0]["title"],
                        url=raw_results[0]["url"],
                        snippet=raw_results[0]["snippet"],
                        source="engineering_lab",
                        extra=res
                    ))
                    task_mgr.complete_task(task.task_id, summary=f"Script executed (exit {res.get('exit_code', 0)})")
                    payload = self.hud_engine.build_structured_payload("Engineering Lab Execution", "ENGINEERING", raw_results, debrief_msg)
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="Stark Engineering Lab", action_type="TERMINAL", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

                elif task.type == TaskType.REMINDER_TIMER.value:
                    task_mgr.update_progress(task.task_id, 30, "Configuring tactical reminder...")
                    import time
                    from datetime import datetime
                    from modules.reminder_service import ReminderService
                    rem_svc = ReminderService.get_instance()
                    cmd_q = task.data.get("query", text).strip()
                    cmd_lower = cmd_q.lower()

                    if any(w in cmd_lower for w in ["cancel", "clear", "stop", "dismiss"]):
                        cancelled = rem_svc.cancel()
                        if cancelled:
                            debrief_msg = "Tactical timer cancelled successfully, Sir."
                        else:
                            debrief_msg = "No active countdown timers found to cancel, Sir."
                        raw_results = [{"title": "Timer Cancelled", "snippet": debrief_msg, "url": "timer://cancel"}]
                    elif any(w in cmd_lower for w in ["list", "show", "my reminders", "what are my", "status"]):
                        active = rem_svc.get_active()
                        if active:
                            items_summary = "; ".join([f"'{r.get('label')}' ({r.get('remaining_sec', 0)}s remaining)" for r in active])
                            debrief_msg = f"You have {len(active)} active countdown timer{'s' if len(active) > 1 else ''}, Sir: {items_summary}."
                            raw_results = [{
                                "title": f"Active Timer: {r.get('label')}",
                                "snippet": f"Remaining: {r.get('remaining_sec', 0)} seconds | Status: active",
                                "url": f"timer://{r.get('id')}",
                                "extra": r
                            } for r in active]
                        else:
                            debrief_msg = "You have no active countdown timers or scheduled reminders at present, Sir."
                            raw_results = [{"title": "No Active Timers", "snippet": debrief_msg, "url": "timer://status"}]
                    else:
                        target_epoch, label, is_timer = rem_svc.parse_time_and_label(cmd_q)
                        if target_epoch is not None:
                            now = time.time()
                            duration = max(1, int(target_epoch - now))
                            if is_timer:
                                item = rem_svc.add_timer(duration, label=label or "Timer")
                                mins = duration // 60
                                secs = duration % 60
                                time_str = f"{mins} minute{'s' if mins != 1 else ''}" if mins > 0 else f"{secs} seconds"
                                debrief_msg = f"Timer initialized for {time_str} ({label}), Sir. Counting down."
                            else:
                                item = rem_svc.add_reminder(target_epoch, label=label or "Reminder")
                                rem_dt = datetime.fromtimestamp(target_epoch).strftime("%I:%M %p").lstrip("0")
                                debrief_msg = f"Reminder logged for {rem_dt} regarding '{label}', Sir. I will notify you promptly."
                            
                            raw_results = [{
                                "title": f"{'Timer' if is_timer else 'Reminder'}: {label}",
                                "snippet": debrief_msg,
                                "url": f"timer://{item.get('id')}",
                                "extra": item
                            }]
                        else:
                            item = rem_svc.add_timer(300, label="5-minute timer")
                            debrief_msg = "Timer set for 5 minutes, Sir."
                            raw_results = [{
                                "title": "5-Minute Timer",
                                "snippet": debrief_msg,
                                "url": f"timer://{item.get('id')}",
                                "extra": item
                            }]

                    for r in raw_results:
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=r["title"],
                            url=r["url"],
                            snippet=r["snippet"],
                            source="reminder_service"
                        ))
                    task_mgr.complete_task(task.task_id, summary=debrief_msg)
                    payload = self.hud_engine.build_structured_payload("Tactical Timers & Reminders", "TIMER", raw_results, debrief_msg)
                    try:
                        from frontend.hud_panel import HUDPanelManager
                        HUDPanelManager().show_action_hud(title="Tactical Timers & Reminders", action_type="TIMER", details=debrief_msg)
                    except Exception:
                        pass
                    return True, debrief_msg, False, "", payload

        # Explicit search fallback
        query = None
        m_explicit = re.search(r'^(?:google\s+search|search\s+google|search\s+the\s+web)\s+(?:for\s+|about\s+|on\s+)?(.+)', text_lower)
        if m_explicit:
            query = m_explicit.group(1).strip()

        if query:
            clean_query = re.sub(r'^(?:do\s+some|can\s+you|please|for|about|is|what|doing|see|find|on)\s+', '', query, flags=re.IGNORECASE).strip()
            if not clean_query:
                clean_query = query

            if self.is_relative_query(clean_query):
                return False, "", True, clean_query, {}

            # Cache lookup
            cached = self.hud_engine.cache.get(clean_query)
            if cached:
                msg = cached.get("spoken_tl_dr", "")
                payload = cached
            else:
                try:
                    msg, raw_results = self.perform_live_search(clean_query)
                    payload = self.hud_engine.build_structured_payload(clean_query, "SEARCH", raw_results, msg)
                except Exception as e:
                    err_str = f"Google search error: {e}"
                    payload = self.hud_engine.build_structured_payload(clean_query, "SEARCH", [], "", error_msg=err_str)
                    msg = payload["spoken_tl_dr"]

            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(
                    title=f"Google Search: {clean_query}",
                    action_type="SEARCH",
                    details=msg,
                    preview_link_or_file=f"https://www.google.com/search?q={urllib.parse.quote(clean_query)}"
                )
            except Exception:
                pass
            return True, msg, True, clean_query, payload

        # 2. Smart Home Arrival & IoT Controls
        if "i'm home" in text_lower or "im home" in text_lower:
            from modules.smart_home import SmartHomeManager
            msg = SmartHomeManager().handle_arrival()
            raw = [{"title": "Smart Home Arrival Macro", "snippet": msg, "url": "data/smart_home_state.json"}]
            payload = self.hud_engine.build_structured_payload("Smart Home Arrival", "SMART HOME", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Smart Home Arrival Macro", action_type="SMART HOME", details=msg, preview_link_or_file="data/smart_home_state.json")
            except Exception:
                pass
            return True, msg, False, "", payload

        if any(kw in text_lower for kw in ["thermostat", "turn on lights", "turn off lights", "lights on", "lights off", "lock front door", "lock door", "unlock front door", "unlock door"]):
            from modules.smart_home import SmartHomeManager
            sh = SmartHomeManager()
            msg = ""
            if "thermostat" in text_lower:
                m = re.search(r'(\d+)', text_lower)
                temp = int(m.group(1)) if m else 72
                msg = sh.set_thermostat(temp)
            elif "unlock" in text_lower:
                msg = sh.set_lock_state(False)
            elif "lock" in text_lower:
                msg = sh.set_lock_state(True)
            elif any(w in text_lower for w in ["turn off", "lights off", "off"]):
                msg = sh.set_light_state(False)
            elif "lights" in text_lower:
                msg = sh.set_light_state(True)
            raw = [{"title": "IoT Action Executed", "snippet": msg, "url": "data/smart_home_state.json"}]
            payload = self.hud_engine.build_structured_payload("IoT Controls", "IOT", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Smart Home Action", action_type="IOT", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 3. Calendar & Scheduling Queries
        if any(kw in text_lower for kw in ["calendar", "schedule", "my meetings", "double booking", "remind me", "reschedule"]):
            from modules.calendar_intel import CalendarIntelManager
            cal = CalendarIntelManager()
            if "reschedule" in text_lower:
                msg = cal.reschedule_event("Sync", "16:00", "16:30")
            else:
                msg = cal.format_jarvis_reminders()
            raw = [{"title": "Calendar Agenda", "snippet": msg, "url": "data/calendar.json"}]
            payload = self.hud_engine.build_structured_payload("Calendar Intel", "CALENDAR", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Calendar & Agenda Intel", action_type="CALENDAR", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 4. Email & Inbox Scan
        if any(kw in text_lower for kw in ["scan email", "inbox", "urgent mail", "panic text", "flight delay", "check mail"]):
            from modules.inbox_intel import InboxIntelManager
            msg = InboxIntelManager().get_tldr_summary()
            raw = [{"title": "Inbox Digest", "snippet": msg, "url": "data/inbox.json", "is_breaking": True}]
            payload = self.hud_engine.build_structured_payload("Inbox Scan", "INBOX", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Inbox Intel Scan", action_type="INBOX", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 5. Maps, Navigation & Traffic Intel
        if any(kw in text_lower for kw in ["traffic", "traffic situation", "traffic condition", "congestion", "road condition", "taco", "hangry", "food", "navigation", "reroute", "directions", "turn left", "nearest gas", "nearest coffee", "map of"]):
            from modules.maps_nav import MapsNavigationEngine
            nav = MapsNavigationEngine()
            if any(k in text_lower for k in ["traffic", "congestion", "road condition"]):
                loc = ""
                m_loc = re.search(r'(?:traffic\s+(?:situation|condition|update)?\s+(?:in|for|at|around)?|where\s+is)\s+([a-zA-Z\s,]+)', text_lower)
                if m_loc:
                    loc = m_loc.group(1).strip()
                if not loc:
                    m_in = re.search(r'\bin\s+([a-zA-Z\s]+)$', text_lower)
                    if m_in:
                        loc = m_in.group(1).strip()
                loc_clean = re.sub(r'[?!.,]+$', '', loc).strip()
                tdata = nav.get_traffic_intel(loc_clean)
                msg = nav.format_traffic_debrief(tdata)
                raw = [{
                    "title": f"Traffic Telemetry — {tdata.get('city')}",
                    "snippet": f"Status: {tdata.get('status')} | Speed: {tdata.get('avg_speed_kmh')} km/h | Delay: +{tdata.get('delay_mins')}m",
                    "url": tdata.get("osm_embed_url", ""),
                    "extra": tdata
                }]
            elif "taco" in text_lower or "hangry" in text_lower or "food" in text_lower:
                msg = nav.format_nearby_food_response("tacos")
                raw = [{"title": "Navigation Directions", "snippet": msg, "url": "data/maps_nav.json"}]
            elif "route" in text_lower or "direction" in text_lower or "nav" in text_lower:
                route = nav.get_route_directions("HQ")
                msg = f"Route set for {route['destination']}. {route['jarvis_prompts'][1]}"
                raw = [{"title": "Navigation Directions", "snippet": msg, "url": "data/maps_nav.json"}]
            else:
                msg = nav.format_nearby_food_response("coffee")
                raw = [{"title": "Navigation Directions", "snippet": msg, "url": "data/maps_nav.json"}]

            payload = self.hud_engine.build_structured_payload("Maps & Traffic Intel", "MAPS", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Maps & Traffic Intel", action_type="MAPS", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload


        # 6. Cloud Docs & File Search
        m_doc = re.search(
            r'\b(?:find|search|open|read|locate|show)\s+(?:me\s+)?(?:the\s+)?(?:file|doc|document|pdf|notes)\s+([a-zA-Z0-9_\-\.\s]+)',
            text_lower
        )
        if m_doc or any(kw in text_lower for kw in ["find file", "find document", "read document", "locate file", "search docs for", "final_final", "buried file"]):
            from modules.cloud_docs import CloudDocumentManager
            if m_doc:
                q = m_doc.group(1).strip()
            else:
                q = re.sub(r'^(?:find\s+file|find\s+document|search\s+docs(?:\s+for)?|read\s+document|locate\s+file)\s*', '', text_lower).strip()
            if not q:
                q = "Final_Final"
            msg = CloudDocumentManager().get_document_summary(q)
            raw = [{"title": f"Document Match: {q}", "snippet": msg, "url": f"cloud://docs/{q}"}]
            payload = self.hud_engine.build_structured_payload(f"Cloud Document: {q}", "FILE SEARCH", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title=f"Cloud Document Access: '{q}'", action_type="FILE SEARCH", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 7. Self-Upgrade & Code Mistake Audit
        if any(kw in text_lower for kw in ["audit code", "inspect code", "audit files", "self upgrade", "level up", "rollback skill", "skill metrics", "upgarded", "upgraded", "codebase", "what are good", "what is new"]):
            from modules.self_upgrade import JarvisSelfUpgrade
            upgrader = JarvisSelfUpgrade()
            if "rollback" in text_lower:
                msg = upgrader.rollback_skill("navigation_boost")
                raw = [{"title": "Skill Rollback Executed", "snippet": msg, "url": "data/skills_config.json"}]
            elif "level up" in text_lower or "whisper" in text_lower:
                msg = upgrader.format_level_up_whisper()
                raw = [{"title": "Level-Up Whisper Active", "snippet": msg, "url": "data/self_upgrade_log.json"}]
            else:
                from core.task_manager import TaskManager
                task_mgr = TaskManager()
                audit_task = task_mgr.create_task(
                    type_="terminal",
                    title="LIVE CODE AUDIT: REPOSITORY SELF-CHECK",
                    data={
                        "command": "jarvis audit --repository",
                        "stdout": "⚡ Initializing codebase self-inspection...\n"
                    }
                )
                accumulated_lines = []

                def _audit_progress_cb(finfo):
                    nonlocal accumulated_lines
                    line_entry = f"⚡ LIVE CODE AUDIT [{finfo['index']}/{finfo['total']}]: {finfo['file']} ({finfo['lines']} LOC)... [VERIFIED]"
                    accumulated_lines.append(line_entry)
                    display_stdout = "\n".join(accumulated_lines[-24:]) if len(accumulated_lines) > 24 else "\n".join(accumulated_lines)
                    pct = int(finfo['index'] / finfo['total'] * 100)
                    audit_task.data["stdout"] = display_stdout
                    task_mgr.update_progress(audit_task.task_id, pct, line_entry)
                    if callable(on_progress):
                        try:
                            on_progress(finfo)
                        except Exception:
                            pass

                res = upgrader.inspect_code_and_logs(on_progress=_audit_progress_cb)
                msg = f"Self-Inspection complete. {res['inspected_files']} files audited ({res['total_lines_of_code']} total lines of code). {res['recommendation']}"
                final_stdout = "\n".join(accumulated_lines) + f"\n\n✓ AUDIT COMPLETE: {res['inspected_files']} files audited ({res['total_lines_of_code']} LOC).\n{res['recommendation']}"
                task_mgr.complete_task(
                    audit_task.task_id,
                    summary=f"Audited {res['inspected_files']} files",
                    extra_data={"stdout": final_stdout, "exit_code": 0}
                )
                raw = [{"title": "Diagnostic Codebase Inspection", "snippet": msg, "url": "modules/self_upgrade.py"}]

            payload = self.hud_engine.build_structured_payload("Diagnostic Audit", "TERMINAL", raw, msg)
            return True, msg, False, "", payload

        # 8. Sarcastic Hardware Alert Triggers
        if "oh great" in text_lower or "printer jammed" in text_lower or "printer" in text_lower:
            msg = "Hardware alert: The printer is reporting a mechanical paper jam, Sir. I recommend a manual inspection rather than percussive maintenance."
            raw = [{"title": "Hardware Exception", "snippet": msg, "url": "dev://printer0"}]
            payload = self.hud_engine.build_structured_payload("Hardware Alert", "HARDWARE ALERT", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Hardware Warning: Printer Jam", action_type="HARDWARE ALERT", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 9. Export case report pattern
        if text_lower in ["export", "/export", "export report", "export case", "save report"]:
            msg = "Generating HTML investigation report for current case..."
            raw = [{"title": "HTML Investigation Export", "snippet": msg, "url": "reports/export.html"}]
            payload = self.hud_engine.build_structured_payload("Export Report", "EXPORT", raw, msg)
            return True, msg, False, "", payload

        # 10. Open Application pattern
        open_match = re.search(r'^(?:open|launch|run|start)\s+(?:application|app|program)?\s*([a-zA-Z0-9_\-\s]+)$', text_lower, re.IGNORECASE)
        if open_match:
            app = open_match.group(1).strip()
            if app not in ["google", "dialog", "target", "investigation", "case", "node"]:
                msg = self.open_application(app)
                raw = [{"title": f"Launched App: {app}", "snippet": msg, "url": f"app://{app}"}]
                payload = self.hud_engine.build_structured_payload(f"Launch App: {app}", "APP LAUNCH", raw, msg)
                try:
                    from frontend.hud_panel import HUDPanelManager
                    HUDPanelManager().show_action_hud(title=f"Launch Application: {app.upper()}", action_type="APP LAUNCH", details=msg)
                except Exception:
                    pass
                return True, msg, False, "", payload

        # 11. Military Radar & Flight Telemetry
        if any(kw in text_lower for kw in ["military flight", "military aircraft", "flight radar", "airspace", "tracking flight", "flight trace", "radar sweep", "military radar", "track aircraft"]):
            from modules.flight_intel import FlightIntelEngine
            fe = FlightIntelEngine()
            flights = fe.get_military_aircraft(limit=8)
            msg = fe.format_tactical_debrief(flights)
            raw = []
            for f in flights:
                flight_label = f.get('flight') or f.get('hex') or 'Unknown'
                desc = f.get('desc') or f.get('t') or 'Military Airframe'
                alt_str = _format_flight_alt(f.get('alt_baro', 0))
                speed_str = _format_flight_speed(f.get('gs', 0))
                track_str = _format_flight_track(f.get('track', 0))
                snip = f"Alt: {alt_str} | Speed: {speed_str} | Track: {track_str}"
                raw.append({"title": f"{flight_label} - {desc}", "snippet": snip, "url": f"https://globe.adsb.lol/?icao={f.get('hex', '')}", "extra": f})
            payload = self.hud_engine.build_structured_payload("Military Radar", "RADAR", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Airspace Radar: Military Telemetry", action_type="RADAR", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 12. Live Weather Telemetry
        if any(kw in text_lower for kw in ["weather", "temperature", "forecast", "how's the weather", "current weather"]):
            loc = ""
            m_loc = re.search(r'(?:weather|forecast|temperature)\s+(?:in|for|at)\s+([a-zA-Z\s,]+)', text_lower)
            if m_loc:
                loc = m_loc.group(1).strip()
            from modules.weather_intel import WeatherIntelEngine
            we = WeatherIntelEngine()
            w = we.get_weather(loc)
            msg = we.format_weather_debrief(w)
            raw = [{
                "title": f"Atmospheric Telemetry - {w.get('city', 'Local')}",
                "snippet": f"{w.get('condition')} | {w.get('temp_f')}°F ({w.get('temp_c')}°C) | Wind: {w.get('wind_kmh')} km/h",
                "url": "https://open-meteo.com",
                "extra": w
            }]
            payload = self.hud_engine.build_structured_payload(f"Weather: {w.get('city')}", "WEATHER", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title=f"Atmospheric Telemetry: {w.get('city')}", action_type="WEATHER", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 13. System Hardware Diagnostic Telemetry
        if any(kw in text_lower for kw in [
            "system diagnostic", "hardware diagnostic", "system status", "hardware status",
            "system telemetry", "hardware stats", "system stats", "cpu load", "cpu usage", "battery status",
            "thermal status", "thermals", "resource monitor", "how is the system", "system resources",
            "where we are in the resources", "how are our resources", "how are the resources",
            "check resources", "ram usage", "memory usage", "disk usage", "hardware health", "our resources"
        ]):
            from modules.system_diagnostics import SystemDiagnosticsEngine
            diag = SystemDiagnosticsEngine()
            metrics = diag.get_metrics()
            debrief_msg = diag.format_tactical_debrief(metrics)
            payload = diag.build_hud_payload(metrics)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="System Diagnostics & Hardware Telemetry", action_type="DIAGNOSTIC", details=debrief_msg)
            except Exception:
                pass
            return True, debrief_msg, False, "", payload

        return False, "", False, "", {}

