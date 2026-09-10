# Manages full conversation history & persistent cross-session recall
import json
import re
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).parent.parent / "data" / "session_history.json"


class SessionMemory:
    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or HISTORY_FILE
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.history: List[Dict] = []
        self.past_sessions: List[Dict] = []
        self.started_at = datetime.now().isoformat()
        self._load_from_disk()

    def _load_from_disk(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.past_sessions = data.get("past_sessions", [])
                    current = data.get("current_session", {})
                    if current and current.get("messages"):
                        self.history = current.get("messages", [])
            except Exception as e:
                print(f"[SessionMemory] Warning loading persistent history: {e}")
                self.history = []
                self.past_sessions = []

    def _save_to_disk(self):
        try:
            payload = {
                "updated_at": datetime.now().isoformat(),
                "current_session": {
                    "started_at": self.started_at,
                    "messages": self.history[-30:]
                },
                "past_sessions": self.past_sessions[-10:]
            }
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"[SessionMemory] Error saving persistent history: {e}")

    def add(self, role: str, content: str, target: Optional[str] = None):
        """Append a message. role = 'user' | 'jarvis'"""
        if role == "jarvis" and content:
            content = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', content, flags=re.IGNORECASE).strip()
        entry = {
            "role": role,
            "content": content,
            "at": datetime.now().isoformat(),
        }
        if target:
            entry["target"] = target
        self.history.append(entry)
        self._save_to_disk()

    def last_n(self, n: int = 12) -> List[Dict]:
        return self.history[-n:]

    def clear(self):
        if self.history:
            self.past_sessions.append({
                "started_at": self.started_at,
                "ended_at": datetime.now().isoformat(),
                "messages": self.history[-10:]
            })
        self.history = []
        self.started_at = datetime.now().isoformat()
        self._save_to_disk()

    def to_text(self, n: int = 12) -> str:
        lines = []
        for m in self.last_n(n):
            role_label = "User" if m.get("role") == "user" else "J.A.R.V.I.S."
            clean_content = re.sub(r'^(?:ASSISTANT|AI)\s*:\s*', '', m.get("content", ""), flags=re.IGNORECASE).strip()
            lines.append(f"{role_label}: {clean_content}")
        return "\n".join(lines)

    def get_last_session_summary(self) -> Optional[str]:
        """Return a human-friendly string summarizing the previous session's user topics/targets."""
        source_msgs = []
        if self.past_sessions:
            source_msgs = self.past_sessions[-1].get("messages", [])
        elif len(self.history) > 2:
            source_msgs = self.history[:-2]

        user_queries = [m["content"] for m in source_msgs if m.get("role") == "user" and len(m.get("content", "")) > 3]
        if user_queries:
            recent_topics = ", ".join([f"'{q}'" for q in user_queries[-3:]])
            return f"Recent previous topic/queries: {recent_topics}"
        return None

    def get_cross_session_context(self) -> str:
        """Build context summary for prompt injection."""
        summary = self.get_last_session_summary()
        if summary:
            return f"[CROSS-SESSION MEMORY RECALL]: {summary}"
        return ""