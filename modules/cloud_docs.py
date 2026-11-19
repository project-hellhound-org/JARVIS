# modules/cloud_docs.py
"""
Local Workspace & Document Intelligence Engine for J.A.R.V.I.S..

Searches real filesystem paths for user files, folders, reports, documentation,
and general documents/media so JARVIS can find things to read aloud, summarise,
or attach to an outgoing email. Scope is portable: it is built at runtime from
whatever this machine actually has (home folders + every mounted/attached
drive), not hardcoded to one operator's layout, so the same code works on
anyone's installation.
"""

import os
import re
import string
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).parent.parent.resolve()

SUPPORTED_EXTENSIONS = {
    # text / code / data
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".html", ".sh",
    ".csv", ".toml", ".rst", ".conf",
    # documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".rtf", ".odt", ".ods", ".odp",
    # images
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic",
    # archives / media (so "attach the recording" etc. can resolve too)
    ".zip", ".rar", ".7z", ".mp3", ".mp4", ".mov", ".wav",
}

# Directories that are never worth walking into: huge, never contain a user's
# own documents, and (for OS/program directories on a mounted Windows drive)
# routinely slow or permission-denied on every entry.
IGNORED_DIRS = {
    ".git", "__pycache__", "node_modules", ".pytest_cache",
    ".venv", "venv", "dist", "build", ".egg-info", ".cache",
    "$recycle.bin", "system volume information",
    "windows", "program files", "program files (x86)", "programdata",
    "appdata", "msocache", "$windows.~bt", "$windows.~ws",
}
_IGNORED_DIRS_LOWER = {d.lower() for d in IGNORED_DIRS}

# Filler words that shouldn't count as a filename-matching token.
_STOPWORDS = {
    "the", "a", "an", "my", "our", "file", "files", "folder", "folders",
    "doc", "docs", "find", "search", "look", "read", "show", "for", "in",
    "on", "of", "and", "to", "attach", "mail", "email", "send", "this",
}


def _detect_drive_roots(mount_bases: Optional[List[Path]] = None) -> List[Path]:
    """Every drive/volume this machine actually has, so the search covers the
    whole system on ANY install -- not just the drive JARVIS happens to live
    on. Works whether JARVIS runs under WSL (Windows drives bind-mounted at
    /mnt/<letter>), native Linux (removable media under /media or /mnt), or
    native Windows (real drive letters). ``mount_bases`` is overridable for
    testing; production always scans the real /mnt and /media."""
    roots: List[Path] = []

    for mount_base in (mount_bases if mount_bases is not None else [Path("/mnt"), Path("/media")]):
        if mount_base.is_dir():
            try:
                for entry in sorted(mount_base.iterdir()):
                    if entry.is_dir():
                        roots.append(entry)
            except Exception:
                pass

    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            if drive.exists():
                roots.append(drive)

    return roots


_SKIP_PROFILE_NAMES = {"public", "default", "default user", "all users", "defaultappuser"}
_PROFILE_SUBFOLDERS = ("Desktop", "Downloads", "Documents", "Pictures")


def _windows_profile_roots(users_dirs: Optional[List[Path]] = None) -> List[Path]:
    """Under WSL, Path.home() is the Linux home (e.g. /home/<user>) -- a
    separate, usually near-empty folder from the real Windows profile the
    operator actually sees in File Explorer and saves files to
    (C:/Users/<name>/Downloads etc, bind-mounted at
    /mnt/c/Users/<name> under WSL). Without this, a search only ever reaches
    those real files via a slow, un-prioritized walk of the whole C: drive.
    Surfacing the real profile folders directly -- for every real user
    profile found, not a hardcoded name -- means they get searched first,
    fast, on anyone's machine. ``users_dirs`` is overridable for testing."""
    roots: List[Path] = []
    for users_dir in (users_dirs if users_dirs is not None else [Path("/mnt/c/Users"), Path("C:/Users")]):
        if not users_dir.is_dir():
            continue
        try:
            for profile in sorted(users_dir.iterdir()):
                if not profile.is_dir() or profile.name.lower() in _SKIP_PROFILE_NAMES:
                    continue
                for sub in _PROFILE_SUBFOLDERS:
                    candidate = profile / sub
                    if candidate.is_dir():
                        roots.append(candidate)
        except Exception:
            pass
    return roots


def _default_search_roots() -> List[Path]:
    home = Path.home()
    roots = [
        WORKSPACE_ROOT,
        WORKSPACE_ROOT.parent,
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        home / "Pictures",
    ]
    # Real Windows profile folders (if any) are searched before the slow,
    # unprioritized whole-drive walk below -- that's where files actually
    # saved through the Windows UI (browser downloads, etc.) really live.
    roots.extend(_windows_profile_roots())
    roots.extend(_detect_drive_roots())

    seen = set()
    deduped: List[Path] = []
    for root in roots:
        try:
            key = str(root.resolve())
        except Exception:
            key = str(root)
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


SEARCH_ROOTS = _default_search_roots()


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[\s_\-.]+", text) if t]


def _score_name(name_lower: str, tokens: List[str], q_clean: str) -> int:
    """Whole-token matching only -- substring matching previously let a
    stray word like "in" "match" inside "install.sh"."""
    score = 0
    if len(q_clean) >= 3 and q_clean in name_lower:
        score += 50
    name_tokens = set(_tokenize(name_lower))
    token_hits = sum(1 for t in tokens if t in name_tokens)
    if token_hits:
        score += token_hits * 15
    return score


def _build_record(name: str, full_path: str, root: Path, is_dir: bool) -> Dict[str, Any]:
    rel_source = str(root.name) or str(root)
    mtime_str = "Unknown"
    size_kb = 0.0
    try:
        stat = os.stat(full_path)
        mtime_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
        if not is_dir:
            size_kb = round(stat.st_size / 1024, 1)
    except Exception:
        pass

    if is_dir:
        file_type = "FOLDER"
    else:
        ext = os.path.splitext(name)[1].lower()
        file_type = ext.lstrip(".").upper() or "TEXT"

    return {
        "id": "",  # filled in by the caller once the final ranking is known
        "filename": name,
        "path": full_path,
        "source": rel_source,
        "file_type": file_type,
        "size_kb": size_kb,
        "modified": mtime_str,
        "title": name.replace("_", " ").replace("-", " ").title(),
        "is_dir": is_dir,
    }


class CloudDocumentManager:
    """
    On-device file and folder search and document intelligence manager.
    """

    def __init__(self, search_roots: Optional[List[Path]] = None):
        self.roots = [p for p in (search_roots or SEARCH_ROOTS) if p.exists()]

    def search_documents(self, query: str, limit: int = 5, time_budget_s: float = 10.0) -> List[Dict[str, Any]]:
        """
        Searches the local filesystem for matching files AND folders.

        Runs two passes:
          1. A fast, non-recursive scan of the immediate contents of every
             search root. This guarantees a project folder sitting right at
             the top of a drive (e.g. F:\\beast) is found immediately,
             regardless of walk order or how large other drives are deeper
             down -- a full recursive walk of C: can easily burn the whole
             time budget before ever reaching F:.
          2. A full recursive walk (subject to the time budget below), so
             files and nested folders deeper inside a tree are still
             reachable, just not relied on alone for the common case of a
             folder sitting at the top of a drive.
        """
        q_clean = query.strip().lower()
        if not q_clean:
            return []

        tokens = [t for t in _tokenize(q_clean) if t not in _STOPWORDS]
        if not tokens:
            tokens = _tokenize(q_clean) or [q_clean]

        matches: List[Tuple[int, Dict[str, Any]]] = []
        visited_paths = set()

        def consider(name: str, full_path: str, root: Path, is_dir: bool) -> None:
            if full_path in visited_paths:
                return
            if not is_dir:
                ext = os.path.splitext(name)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS and ext != "":
                    return
            visited_paths.add(full_path)
            score = _score_name(name.lower(), tokens, q_clean)
            if score > 0:
                matches.append((score, _build_record(name, full_path, root, is_dir)))

        # ── Phase 1: fast top-level scan of every root ──────────────
        for root in self.roots:
            if len(matches) >= 30:
                break
            try:
                with os.scandir(root) as it:
                    for entry in it:
                        name_lower = entry.name.lower()
                        if name_lower in _IGNORED_DIRS_LOWER or entry.name.startswith("."):
                            continue
                        try:
                            is_dir = entry.is_dir()
                        except OSError:
                            continue
                        consider(entry.name, entry.path, root, is_dir)
            except Exception:
                pass

        # ── Phase 2: full recursive walk, time-budgeted ─────────────
        deadline = time.monotonic() + time_budget_s
        timed_out = False
        if len(matches) < 30:
            for root in self.roots:
                if timed_out:
                    break
                try:
                    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                        if time.monotonic() > deadline:
                            timed_out = True
                            break

                        dirnames[:] = [
                            d for d in dirnames
                            if d.lower() not in _IGNORED_DIRS_LOWER and not d.startswith(".")
                        ]

                        for dname in dirnames:
                            consider(dname, os.path.join(dirpath, dname), root, True)
                        for fname in filenames:
                            consider(fname, os.path.join(dirpath, fname), root, False)

                        if len(matches) >= 30:
                            break
                    if len(matches) >= 30:
                        break
                except Exception as e:
                    print(f"[cloud_docs] Walk error in {root}: {e}")

        # Sort highest score first
        matches.sort(key=lambda x: x[0], reverse=True)
        top_results = [m[1] for m in matches[:limit]]
        for i, doc in enumerate(top_results, start=1):
            doc["id"] = f"doc_{i}"
            doc["summary"], doc["key_points"] = self._extract_file_preview(doc["path"], doc.get("is_dir", False))

        return top_results

    def _extract_file_preview(self, filepath: str, is_dir: bool = False) -> Tuple[str, List[str]]:
        """Reads genuine text excerpts from matching files, or describes a
        matching folder (never tries to read a folder's "content" as text)."""
        if is_dir:
            try:
                entries = list(Path(filepath).iterdir())
                return (f"Folder containing {len(entries)} item{'s' if len(entries) != 1 else ''}.",
                        ["This is a folder, not a file."])
            except Exception:
                return "Folder located.", ["This is a folder, not a file."]

        try:
            p = Path(filepath)
            if not p.exists() or p.stat().st_size > 2 * 1024 * 1024:  # limit to 2MB
                return "File content available on disk.", ["Binary or large file."]

            ext = p.suffix.lower()
            if ext in {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".sh", ".toml", ".rst"}:
                text = p.read_text(encoding="utf-8", errors="ignore")
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                line_count = len(lines)

                summary = f"File contains {line_count} lines of code/text. Modified {time.strftime('%Y-%m-%d', time.localtime(p.stat().st_mtime))}."
                key_points = []
                marker_prefixes = ("#", "//", "/*", chr(34) * 3, "'''", "import", "class", "def")
                for line in lines[:8]:
                    if line.startswith(marker_prefixes):
                        clean_line = re.sub(r"^[#/*" + chr(34) + "']+\\s*", "", line).strip()
                        if clean_line and len(clean_line) > 5 and clean_line not in key_points:
                            key_points.append(clean_line[:90])
                    if len(key_points) >= 3:
                        break

                if not key_points and lines:
                    key_points = [lines[0][:90]]

                return summary, key_points
        except Exception:
            pass

        return "Local system document located and verified.", ["Standard local storage."]

    def get_document_summary(self, query: str) -> str:
        """Find a document/folder and format a key-points summary in J.A.R.V.I.S. voice."""
        results = self.search_documents(query, limit=3)
        if not results:
            return f"I searched your local workspace and repositories, Sir, but found no documents or folders matching '{query}'."

        best = results[0]
        points_str = "; ".join(best.get("key_points", []))
        summary_text = best.get("summary", "")
        noun = "folder" if best.get("is_dir") else f"'{best['filename']}' ({best['file_type']}, {best['size_kb']} KB)"

        return (
            f"I located the {noun} in your {best['source']} directory, Sir. "
            f"{summary_text} "
            f"Key excerpts: {points_str}" if points_str else f"I located the {noun} in your {best['source']} directory, Sir. {summary_text}"
        )
