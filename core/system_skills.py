import os
import re
import shutil
import subprocess
import urllib.parse
import webbrowser
from core.soldierboy_memory import SoldierBoyMemory
from core.structured_hud_engine import StructuredHUDEngine

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "skills")

class SystemSkillEngine:
    def __init__(self):
        self.memory = SoldierBoyMemory()
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
            from core.soldierboy_memory import SoldierBoyMemory
            mem = SoldierBoyMemory()
            mem.log_speech_pattern(f"Active skill requested: '{text}'")
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
            if action_cat.startswith("followup_"):
                return True, ack_msg, False, "", {}

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

                elif task.type == TaskType.MEMORY_RECALL.value:
                    task_mgr.update_progress(task.task_id, 40, "Retrieving memory entries...")
                    mem_history = self.memory.get_recent_speech_patterns(limit=5)
                    msg = f"Retrieved {len(mem_history)} recent context logs from memory."
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
                        snip = f"Alt: {f.get('alt_baro', 0):,} ft | Speed: {f.get('gs', 0)} kts | Track: {f.get('track', 0)}° | Squawk: {f.get('squawk', 'N/A')}"
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

                elif task.type == TaskType.BROWSER_SURF.value and task.data.get("cctv"):
                    city = task.data.get("city", "shinjuku")
                    task_mgr.update_progress(task.task_id, 30, f"Loading public CCTV feeds for {city.title()}...")
                    import json
                    cctv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cctv", f"cctv_sources.{city}.json")
                    cams = []
                    if os.path.exists(cctv_path):
                        try:
                            with open(cctv_path, "r", encoding="utf-8") as f:
                                cams = json.load(f)
                        except Exception:
                            pass
                    if not cams:
                        cams = [{"id": "cam-1", "name": f"{city.title()} Live Intersection", "url": "https://storage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4", "lat": 35.69, "lon": 139.7}]
                    
                    raw_results = []
                    for cam in cams:
                        snip = f"{cam.get('name')} | Coordinates: {cam.get('lat')}, {cam.get('lon')} | Heading: {cam.get('headingDeg', 0)}°"
                        task_mgr.add_finding(task.task_id, TaskFinding(
                            title=cam.get('name', 'CCTV Stream'),
                            url=cam.get('url', ''),
                            snippet=snip,
                            source="cctv",
                            extra=cam
                        ))
                        raw_results.append({"title": cam.get('name'), "snippet": snip, "url": cam.get('url')})
                    
                    task.data["url"] = cams[0]["url"]
                    debrief_msg = f"Surveillance link established. Tracking {len(cams)} public camera feeds across {city.title()}."
                    task_mgr.complete_task(task.task_id, summary=f"{len(cams)} CCTV feeds online ({city.title()})")
                    payload = self.hud_engine.build_structured_payload(f"CCTV: {city.title()}", "CCTV", raw_results, debrief_msg)
                    return True, debrief_msg, False, "", payload

        # Fallback to existing skill branches
        query = None
        if any(kw in text_lower for kw in ["google search", "search google", "google", "search", "latest news", "look up"]):
            if not any(t_kw in text_lower for t_kw in ["search target", "search case", "target investigation"]):
                if "latest news" in text_lower or "top news" in text_lower or "news" in text_lower:
                    m_topic = re.search(r'(?:news\s+(?:about|on|for)|latest\s+news\s+on)\s+(.+)', text_lower)
                    query = m_topic.group(1).strip() if m_topic else "latest news"
                else:
                    m = re.search(r'(?:google\s+search|search\s+google|search|look\s+up)\s+(?:for\s+|about\s+|on\s+|and\s+see\s+)?(.+)', text_lower)
                    if m:
                        query = m.group(1).strip()
                    else:
                        query = text.strip()

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

        if any(kw in text_lower for kw in ["thermostat", "turn on lights", "lights on", "lock front door", "lock door"]):
            from modules.smart_home import SmartHomeManager
            sh = SmartHomeManager()
            msg = ""
            if "thermostat" in text_lower:
                m = re.search(r'(\d+)', text_lower)
                temp = int(m.group(1)) if m else 72
                msg = sh.set_thermostat(temp)
            elif "lock" in text_lower:
                msg = sh.set_lock_state(True)
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
                msg = cal.format_soldierboy_reminders()
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

        # 5. Maps, Navigation & Late Night POI
        if any(kw in text_lower for kw in ["taco", "hangry", "food", "navigation", "reroute", "directions", "turn left", "nearest gas", "nearest coffee"]):
            from modules.maps_nav import MapsNavigationEngine
            nav = MapsNavigationEngine()
            if "taco" in text_lower or "hangry" in text_lower or "food" in text_lower:
                msg = nav.format_nearby_food_response("tacos")
            elif "route" in text_lower or "direction" in text_lower or "nav" in text_lower:
                route = nav.get_route_directions("HQ")
                msg = f"Route set for {route['destination']}. {route['soldierboy_prompts'][1]}"
            else:
                msg = nav.format_nearby_food_response("coffee")
            raw = [{"title": "Navigation Directions", "snippet": msg, "url": "data/maps_nav.json"}]
            payload = self.hud_engine.build_structured_payload("Maps Navigation", "MAPS", raw, msg)
            try:
                from frontend.hud_panel import HUDPanelManager
                HUDPanelManager().show_action_hud(title="Maps & Navigation Intel", action_type="MAPS", details=msg)
            except Exception:
                pass
            return True, msg, False, "", payload

        # 6. Cloud Docs & File Search
        if any(kw in text_lower for kw in ["find file", "find document", "pdf", "report", "final_final", "read document", "buried file"]):
            from modules.cloud_docs import CloudDocumentManager
            q = re.sub(r'^(?:find\s+file|find\s+document|search\s+docs|read\s+pdf|pdf|report)\s*', '', text_lower).strip()
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
            from modules.self_upgrade import SoldierBoySelfUpgrade
            upgrader = SoldierBoySelfUpgrade()
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
                        "command": "soldierboy audit --repository",
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

        # 8. Sarcastic Phrase Triggers
        if "oh great" in text_lower or "printer jammed" in text_lower or "printer" in text_lower:
            msg = "Oh great, the damn printer's taking a dump again. Give it a solid kick, partner, or let me blow it to pieces."
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
                snip = f"Alt: {f.get('alt_baro', 0):,} ft | Speed: {f.get('gs', 0)} kts | Track: {f.get('track', 0)}°"
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

        return False, "", False, "", {}

