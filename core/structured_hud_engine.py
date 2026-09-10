# core/structured_hud_engine.py
"""
Structured HUD & JSON Data Engine for J.A.R.V.I.S. (Action Panel Integration).
Provides standardized JSON output schema, search cache management, sentiment/breaking tagging,
priority ranking, error handling fallbacks, and live frontend payload feeds.
"""

import os
import json
import time
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

BASE_DIR = Path(__file__).parent.parent.resolve()
CACHE_FILE = BASE_DIR / "data" / "search_cache.json"
SCHEMA_STATE_FILE = BASE_DIR / "data" / "structured_hud_active.json"


class SearchCacheManager:
    """Cache manager for web search and audit queries to save bandwidth and prevent redundant API hits."""
    def __init__(self, ttl_seconds: int = 1800):
        self.ttl_seconds = ttl_seconds
        os.makedirs(CACHE_FILE.parent, exist_ok=True)
        self._ensure_cache_file()

    def _ensure_cache_file(self):
        if not CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({}, f)
            except Exception:
                pass

    def _get_key(self, query: str) -> str:
        return hashlib.md5(query.strip().lower().encode("utf-8")).hexdigest()

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        key = self._get_key(query)
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
                entry = cache.get(key)
                if entry:
                    if time.time() - entry.get("timestamp_ts", 0) < self.ttl_seconds:
                        entry["cached"] = True
                        return entry
        except Exception:
            pass
        return None

    def set(self, query: str, data: Dict[str, Any]):
        key = self._get_key(query)
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

        data["timestamp_ts"] = time.time()
        cache[key] = data
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"[search_cache] Error saving cache entry: {e}")

    def clear(self) -> str:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)
            return "Search cache cleared successfully."
        except Exception as e:
            return f"Failed to clear cache: {e}"


class StructuredHUDEngine:
    """
    Central J.A.R.V.I.S. structured data generator and payload router for J.A.R.V.I.S. interface panels.
    """
    def __init__(self):
        self.cache = SearchCacheManager()
        self.json_mode_enabled = True

    def toggle_json_mode(self, enabled: Optional[bool] = None) -> bool:
        if enabled is not None:
            self.json_mode_enabled = enabled
        else:
            self.json_mode_enabled = not self.json_mode_enabled
        return self.json_mode_enabled

    def analyze_sentiment(self, text: str) -> str:
        """Categorize sentiment of finding snippet."""
        text_lower = text.lower()
        neg_words = ["crisis", "war", "crash", "drop", "death", "error", "fail", "pissed", "meltdown", "panic", "crime", "illegal", "attack"]
        pos_words = ["boost", "success", "hero", "record", "launch", "breakthrough", "upgraded", "victory", "solid", "win"]
        
        neg_score = sum(1 for w in neg_words if w in text_lower)
        pos_score = sum(1 for w in pos_words if w in text_lower)

        if neg_score > pos_score:
            return "negative"
        elif pos_score > neg_score:
            return "positive"
        return "neutral"

    def is_breaking_news(self, text: str) -> bool:
        """Determine if a snippet represents breaking urgent intel."""
        text_lower = text.lower()
        breaking_kw = ["breaking", "urgent", "just in", "live coverage", "alert", "developing", "whistleblower", "flash"]
        return any(kw in text_lower for kw in breaking_kw)

    def rate_source_reliability(self, domain_or_url: str) -> str:
        """Evaluate source domain reliability score."""
        domain_lower = domain_or_url.lower()
        high_rel = ["bbc", "cnn", "ndtv", "reuters", "bloomberg", "apnews", "nytimes", "github", "google", "weather"]
        med_rel = ["reddit", "twitter", "x.com", "medium", "news24", "timesofindia"]
        
        if any(d in domain_lower for d in high_rel):
            return "HIGH"
        elif any(d in domain_lower for d in med_rel):
            return "MEDIUM"
        return "STANDARD"

    def build_structured_payload(
        self,
        topic: str,
        action_type: str,
        findings_raw: List[Dict[str, Any]],
        spoken_tl_dr: str,
        error_msg: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build a standardized, consistent J.A.R.V.I.S. JSON schema payload.
        """
        if error_msg:
            return {
                "status": "ERROR",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "topic": topic,
                "action_type": action_type,
                "error_message": error_msg,
                "findings_count": 0,
                "findings": [],
                "spoken_tl_dr": f"Failed to fetch data for '{topic}': {error_msg}"
            }

        formatted_findings = []
        for item in findings_raw:
            headline = item.get("title") or item.get("headline") or "Untitled Record"
            summary = item.get("snippet") or item.get("summary") or ""
            url = item.get("url") or item.get("link") or "N/A"
            
            sentiment = self.analyze_sentiment(headline + " " + summary)
            is_breaking = self.is_breaking_news(headline + " " + summary) or item.get("is_breaking", False)
            reliability = self.rate_source_reliability(url + " " + headline)

            # Priority rank calculation (Higher score = higher priority)
            priority_score = 50
            if is_breaking:
                priority_score += 40
            if sentiment == "negative":
                priority_score += 15
            if reliability == "HIGH":
                priority_score += 10

            formatted_findings.append({
                "headline": headline,
                "summary": summary,
                "url": url,
                "sentiment": sentiment,
                "is_breaking": is_breaking,
                "source_reliability": reliability,
                "priority_score": priority_score,
                "key_points": [s.strip() for s in re.split(r'[;.!]', summary) if len(s.strip()) > 10][:3]
            })

        # Priority queue sorting: sort findings by priority_score descending
        formatted_findings.sort(key=lambda x: x["priority_score"], reverse=True)

        payload = {
            "status": "SUCCESS",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "topic": topic,
            "action_type": action_type,
            "json_mode_enabled": self.json_mode_enabled,
            "findings_count": len(formatted_findings),
            "findings": formatted_findings,
            "spoken_tl_dr": spoken_tl_dr,
            "has_breaking_news": any(f["is_breaking"] for f in formatted_findings),
            "dominant_sentiment": self._calc_dominant_sentiment(formatted_findings)
        }

        # Cache valid web searches
        if action_type == "SEARCH" and formatted_findings:
            self.cache.set(topic, payload)

        # Save to disk for frontend polling / inspection
        try:
            with open(SCHEMA_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"[structured_hud] Error saving schema state: {e}")

        return payload

    def _calc_dominant_sentiment(self, findings: List[Dict[str, Any]]) -> str:
        if not findings:
            return "neutral"
        sentiments = [f["sentiment"] for f in findings]
        return max(set(sentiments), key=sentiments.count)
