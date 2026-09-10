# modules/inbox_intel.py
"""
Read-Only Inbox & Messaging Scanner Module for J.A.R.V.I.S..
Scans inbox for urgent messages (flight delays, boss panic texts, "WE NEED TO TALK", critical alerts)
and provides instant J.A.R.V.I.S. TL;DR summaries.
"""

import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any

INBOX_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "inbox_data.json")

URGENT_KEYWORDS = [
    "flight delay", "canceled", "cancelled", "boss", "we need to talk",
    "urgent", "critical", "emergency", "server down", "asap", "deadline", "security alert"
]


class InboxIntelManager:
    def __init__(self, filepath: str = INBOX_FILE):
        self.filepath = filepath
        self._ensure_storage()

    def _ensure_storage(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            initial_inbox = [
                {
                    "id": "msg_101",
                    "sender": "Boss <vought_boss@company.com>",
                    "subject": "WE NEED TO TALK - Q3 Review",
                    "snippet": "Sir, urgent update. We need to talk about the budget deployment ASAP before the 4 PM sync.",
                    "date": now_str,
                    "unread": True,
                    "urgent": True
                },
                {
                    "id": "msg_102",
                    "sender": "Delta Air Lines <alerts@delta.com>",
                    "subject": "Flight Delay Notification: FL 842 to NYC",
                    "snippet": "Your flight FL 842 has been delayed by 1 hour and 45 minutes due to severe weather.",
                    "date": now_str,
                    "unread": True,
                    "urgent": True
                },
                {
                    "id": "msg_103",
                    "sender": "GitHub Security <no-reply@github.com>",
                    "subject": "Security Alert: Secret token detected in commit",
                    "snippet": "We detected an exposed API token in repository jarvis-core. Immediate revocation recommended.",
                    "date": now_str,
                    "unread": True,
                    "urgent": True
                },
                {
                    "id": "msg_104",
                    "sender": "Coffee Club <newsletter@coffeeroasters.io>",
                    "subject": "Your weekly espresso roast choice is ready",
                    "snippet": "Discover our latest Ethiopian roast blend available now in stores.",
                    "date": now_str,
                    "unread": False,
                    "urgent": False
                }
            ]
            self._save(initial_inbox)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, messages: List[Dict[str, Any]]):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2)
        except Exception as e:
            print(f"[inbox_intel] Error saving inbox: {e}")

    def scan_urgent(self) -> List[Dict[str, Any]]:
        """Scan inbox for urgent or unread messages matching critical keywords."""
        messages = self._load()
        urgent_msgs = []
        for m in messages:
            if m.get("urgent"):
                urgent_msgs.append(m)
                continue
            subj_body = f"{m.get('subject', '')} {m.get('snippet', '')}".lower()
            if any(kw in subj_body for kw in URGENT_KEYWORDS):
                m["urgent"] = True
                urgent_msgs.append(m)
        return urgent_msgs

    def search_inbox(self, query: str) -> List[Dict[str, Any]]:
        messages = self._load()
        q_lower = query.lower()
        results = []
        for m in messages:
            if (q_lower in m.get("subject", "").lower() or
                q_lower in m.get("snippet", "").lower() or
                q_lower in m.get("sender", "").lower()):
                results.append(m)
        return results

    def add_mock_email(self, sender: str, subject: str, snippet: str, urgent: bool = False):
        messages = self._load()
        msg_id = f"msg_{int(time.time())}"
        new_msg = {
            "id": msg_id,
            "sender": sender,
            "subject": subject,
            "snippet": snippet,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "unread": True,
            "urgent": urgent or any(kw in (subject + " " + snippet).lower() for kw in URGENT_KEYWORDS)
        }
        messages.insert(0, new_msg)
        self._save(messages)

    def get_tldr_summary(self) -> str:
        """Generate a sharp, profane J.A.R.V.I.S. TL;DR of urgent inbox items."""
        urgent = self.scan_urgent()
        if not urgent:
            return "Inbox is clear of emergency shit right now, partner. No panic texts or flight delays."

        lines = [f"Listen up: you got {len(urgent)} urgent inbox item(s) that need your eyes:"]
        for m in urgent[:3]:
            sender_name = m.get("sender", "Unknown").split("<")[0].strip()
            subj = m.get("subject", "")
            snip = m.get("snippet", "")
            if "WE NEED TO TALK" in subj.upper():
                lines.append(f"• Boss panic warning from {sender_name}: '{subj}'. TL;DR: Drop what you're doing, boss wants a word.")
            elif "FLIGHT" in subj.upper() or "DELAY" in subj.upper():
                lines.append(f"• Travel Alert: {subj}. TL;DR: {snip}")
            elif "SECURITY" in subj.upper() or "SECRET" in subj.upper():
                lines.append(f"• Security Alert: {subj}. TL;DR: {snip}")
            else:
                lines.append(f"• Urgent from {sender_name}: '{subj}' — {snip[:80]}...")

        lines.append("Review 'em before someone starts yellin'.")
        return "\n".join(lines)
