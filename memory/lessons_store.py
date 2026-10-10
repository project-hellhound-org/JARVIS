# memory/lessons_store.py
"""
Persistent lesson store for false-positive memory.
Uses ChromaDB + sentence-transformers for semantic search when available,
falls back to a simple JSON file store otherwise.
"""
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)
_chroma_init_warning_logged = False

LESSONS_DIR = Path(__file__).parent.parent / "cases" / ".lessons"


class LessonsStore:
    """
    Stores lessons learned from false-positive reports.
    Two backends:
      1. ChromaDB + sentence-transformers (semantic RAG)
      2. JSON flat-file fallback (keyword match)
    """

    def __init__(self):
        self._chroma = None
        self._collection = None
        self._available = False
        LESSONS_DIR.mkdir(parents=True, exist_ok=True)
        self._json_path = LESSONS_DIR / "lessons.json"
        self._lessons = self._load_json()
        self._init_chroma()

    # ── ChromaDB bootstrap ────────────────────────────────────
    def _init_chroma(self):
        global _chroma_init_warning_logged
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(LESSONS_DIR / "chroma"))
            self._collection = client.get_or_create_collection(
                name="lessons",
                metadata={"hnsw:space": "cosine"},
            )
            self._chroma = client
            self._available = True
        except Exception as e:
            # ChromaDB or sentence-transformers not installed — use JSON fallback
            if not _chroma_init_warning_logged:
                logger.warning(f"[lessons_store] ChromaDB initialization failed ({e}), falling back to JSON store.")
                _chroma_init_warning_logged = True
            self._available = False

    # ── JSON fallback I/O ─────────────────────────────────────
    def _load_json(self) -> list:
        if self._json_path.exists():
            try:
                return json.loads(self._json_path.read_text())
            except Exception:
                return []
        return []

    def _save_json(self):
        self._json_path.write_text(json.dumps(self._lessons, indent=2))

    # ── Public API ────────────────────────────────────────────
    def add_lesson(
        self,
        trigger: str,
        lesson: str,
        platform: str,
        context: str = "general",
    ) -> bool:
        """
        Store a lesson. Updates existing lesson in place if trigger and platform match,
        or appends a new lesson. Returns True on success.
        """
        clean_trigger = (trigger or "").strip()
        clean_platform = (platform or "general").strip().lower()
        now_iso = datetime.utcnow().isoformat()

        # Update in place if matching record exists
        existing_idx = -1
        for idx, existing in enumerate(self._lessons):
            if (
                existing.get("trigger", "").strip().lower() == clean_trigger.lower()
                and existing.get("platform", "").strip().lower() == clean_platform
            ):
                existing_idx = idx
                break

        if existing_idx >= 0:
            self._lessons[existing_idx]["lesson"] = lesson
            self._lessons[existing_idx]["context"] = context
            self._lessons[existing_idx]["timestamp"] = now_iso
            self._save_json()
            return True

        entry = {
            "trigger": clean_trigger,
            "lesson": lesson,
            "platform": clean_platform,
            "context": context,
            "timestamp": now_iso,
        }

        # Always persist to JSON regardless of Chroma availability
        self._lessons.append(entry)
        self._save_json()

        # Also add to ChromaDB if available
        if self._available and self._collection is not None:
            try:
                doc_id = f"{platform.lower()}_{len(self._lessons)}"
                self._collection.add(
                    documents=[f"{trigger} | {lesson}"],
                    metadatas=[{
                        "platform": platform.lower(),
                        "context": context,
                    }],
                    ids=[doc_id],
                )
            except Exception:
                pass

        return True

    def has_platform_warning(self, platform: str) -> str | None:
        """
        Check if there's a stored lesson warning about a platform.
        Returns the lesson text if found, None otherwise.
        """
        platform_lower = platform.lower()

        # Try ChromaDB semantic search first
        if self._available and self._collection is not None:
            try:
                results = self._collection.query(
                    query_texts=[f"{platform} false positive unreliable"],
                    n_results=1,
                    where={"platform": platform_lower},
                )
                if results and results["documents"] and results["documents"][0]:
                    return results["documents"][0][0]
            except Exception:
                pass

        # JSON fallback — simple keyword match
        for entry in reversed(self._lessons):
            if entry.get("platform", "").lower() == platform_lower:
                return entry.get("lesson", "")

        return None

    def get_lessons_for_context(self, context: str = "general") -> list:
        """Return all lessons matching a given context."""
        return [
            entry for entry in self._lessons
            if entry.get("context", "general") == context
        ]
