import os
import json
import time

MEMORY_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jarvis_memory.json")

class JarvisMemory:
    def __init__(self, filepath=MEMORY_FILE_PATH):
        self.filepath = filepath
        self._ensure_file()

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        default_skills = [
            {
                "name": "open_app",
                "description": "Launch desktop applications (browser, terminal, editor, etc.)",
                "trigger": "open app"
            },
            {
                "name": "google_search",
                "description": "Search Google in default browser with structured snippet extraction",
                "trigger": "google search"
            },
            {
                "name": "calendar_intel",
                "description": "Check schedule, reminders, double-bookings, auto-reschedule",
                "trigger": "calendar"
            },
            {
                "name": "inbox_intel",
                "description": "Read-only urgent inbox scan and TL;DR summaries",
                "trigger": "inbox scan"
            },
            {
                "name": "maps_nav",
                "description": "Live navigation, rerouting around traffic, 24hr taco spot search",
                "trigger": "find tacos"
            },
            {
                "name": "cloud_docs",
                "description": "Search Google Drive/Dropbox files and read key points",
                "trigger": "find document"
            },
            {
                "name": "smart_home",
                "description": "Control lights, thermostat, front door lock, arrival macro",
                "trigger": "I'm home"
            },
            {
                "name": "self_upgrade",
                "description": "Inspect own code files, mistake audit, versioned rollback, auto-retrain",
                "trigger": "audit code"
            },
            {
                "name": "soldierboy_action_hud",
                "description": "Soldier Boy-style holographic action HUD, multi-card findings grid, breaking news badges, sentiment color coding, and raw JSON schema toggle",
                "trigger": "open panel"
            }
        ]

        data = None
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None

        if not data:
            data = {
                "user_speech_patterns": [
                    "User prefers concise, witty, energetic, swearing voice responses.",
                    "The user/operator is NOT Dean. Refer to the user as 'bruh', 'buddy', or 'partner'.",
                    "STT mishears 'Soldier' as 'joke', 'joh', 'zho', 'jarvis', 'chow', 'show', 'suraj'."
                ],
                "failures_and_lessons": [
                    "Speech recognition of target usernames is error-prone; trigger target dialog modal when intent is 'investigate' without clean target."
                ],
                "learned_skills": default_skills,
                "history_log": []
            }
        else:
            # Sync default skills so all 9 partner capabilities are active
            existing = data.setdefault("learned_skills", [])
            existing_names = {s.get("name") for s in existing}
            for ds in default_skills:
                if ds["name"] not in existing_names:
                    existing.append(ds)

        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_memory(self):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[SoldierBoyMemory] Error loading memory: {e}")
            return {}

    def log_speech_pattern(self, note: str):
        mem = self.load_memory()
        patterns = mem.setdefault("user_speech_patterns", [])
        if note not in patterns:
            patterns.append(note)
            self._save(mem)

    def log_failure_lesson(self, failure_and_lesson: str):
        mem = self.load_memory()
        lessons = mem.setdefault("failures_and_lessons", [])
        if failure_and_lesson not in lessons:
            lessons.append(failure_and_lesson)
            self._save(mem)

    def register_learned_skill(self, skill_name: str, description: str, trigger: str, code_snippet: str = ""):
        mem = self.load_memory()
        skills = mem.setdefault("learned_skills", [])
        for s in skills:
            if s.get("name") == skill_name:
                s["description"] = description
                s["trigger"] = trigger
                if code_snippet:
                    s["code"] = code_snippet
                self._save(mem)
                return
        skills.append({
            "name": skill_name,
            "description": description,
            "trigger": trigger,
            "code": code_snippet,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self._save(mem)

    def get_memory_summary_for_prompt(self):
        mem = self.load_memory()
        patterns = "\n- ".join(mem.get("user_speech_patterns", [])[-4:])
        lessons = "\n- ".join(mem.get("failures_and_lessons", [])[-4:])
        skills = ", ".join([s["name"] for s in mem.get("learned_skills", [])])
        return f"""PERSISTENT MEMORY & LEARNED SKILLS:
- User Speech Habits:\n- {patterns}
- Learned Lessons & Fixes:\n- {lessons}
- Available Learned Skills: {skills} (open_app, google_search, calendar_intel, inbox_intel, maps_nav, cloud_docs, smart_home, self_upgrade, soldierboy_action_hud)
- Holographic Panel Capability: Wired and active. When user asks to open the action panel or view search/audit findings, open_soldierboy_panel and soldierboy_structured_json_feed render dynamic multi-card overlays in the Action HUD."""

    def _save(self, data):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[JarvisMemory] Error saving memory: {e}")


# Backward-compatible alias
SoldierBoyMemory = JarvisMemory

