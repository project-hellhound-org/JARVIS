import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import asyncio
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path as P
from core.target_model import Entity, Target
from core.tool_locator import find_tool, build_subprocess_env
from modules.verify import verify_hit

logger = logging.getLogger(__name__)


def sanitize_username(username: str) -> str:
    """Sanitize username allowing only letters, digits, dot, underscore, hyphen."""
    if not username:
        return ""
    return re.sub(r"[^a-zA-Z0-9._-]", "", str(username))


async def run(target: Target, username: str, on_find=None, lessons_store=None) -> None:
    clean_user = sanitize_username(username)
    if not clean_user:
        note = f"Username '{username}' contains no valid characters, skipped"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)
        return

    initial_count = len([e for e in target.entities if e.entity_type == "username" and e.value == clean_user])

    await _run_sherlock(target, clean_user, on_find, lessons_store)
    await _run_maigret(target, clean_user, on_find, lessons_store)

    post_count = len([e for e in target.entities if e.entity_type == "username" and e.value == clean_user])
    if post_count == initial_count:
        try:
            import modules.dork_fallback as dork_fallback
            count, _ = await dork_fallback.dork(target, clean_user, site_filter="", entity_type="username", on_find=on_find, module_name="social_enum")
            try:
                from core.orchestrator import log_tool_fallback
                log_tool_fallback(target, "social_enum", count)
            except Exception:
                pass
        except Exception:
            pass

    await _run_instagram_probe(target, clean_user, on_find, lessons_store)


async def _run_sherlock(target, username, on_find, lessons_store=None):
    sherlock_bin = find_tool("sherlock")
    if not sherlock_bin:
        note = "sherlock not found on PATH or in ~/.local/bin, skipped"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)
        target.log("tool_skipped", {"tool": "sherlock", "reason": "not installed"})
        return

    clean_user = sanitize_username(username)
    if not clean_user:
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            sherlock_bin, clean_user,
            "--print-found", "--no-color", "--no-txt",
            "--timeout", "5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=build_subprocess_env(),
        )
        try:
            # 120s timeout with 5s per-request timeout ensures full scan completes
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            note = "sherlock execution timed out"
            logger.warning(f"[social_enum] {note}")
            target.notes.append(note)
            return

        for line in stdout.decode(errors="ignore").splitlines():
            if line.startswith("[+]"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    platform = parts[0].replace("[+]", "").strip()
                    url = parts[1].strip()
                    entity = Entity(
                        entity_type="username",
                        value=clean_user,
                        sources=["sherlock"],
                        confidence=0.85,
                        platform=platform,
                        metadata={"url": url},
                    )
                    # Verify before trusting
                    case_slug = target.case_slug
                    entity = await verify_hit(entity, lessons_store=lessons_store, case_slug=case_slug)
                    if target.add_entity(entity) and on_find:
                        await on_find(entity)
    except FileNotFoundError:
        note = "sherlock not found on PATH or in ~/.local/bin, skipped"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)
    except Exception as e:
        note = f"sherlock check failed: {e}"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)


async def _run_maigret(target, username, on_find, lessons_store=None):
    maigret_bin = find_tool("maigret")
    if not maigret_bin:
        note = "maigret not found on PATH or in ~/.local/bin, skipped"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)
        target.log("tool_skipped", {"tool": "maigret", "reason": "not installed"})
        return

    clean_user = sanitize_username(username)
    if not clean_user:
        return

    tmp_dir = tempfile.TemporaryDirectory()
    try:
        proc = await asyncio.create_subprocess_exec(
            maigret_bin, clean_user,
            "--folderoutput", tmp_dir.name,
            "--json", "simple",
            "--timeout", "5",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=build_subprocess_env(),
        )
        try:
            # 120s timeout ensures full top-500 scan completes smoothly
            await asyncio.wait_for(proc.communicate(), timeout=120.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            note = "maigret execution timed out"
            logger.warning(f"[social_enum] {note}")
            target.notes.append(note)
            return

        json_candidates = list(P(tmp_dir.name).glob("*.json"))
        expected_file = P(tmp_dir.name) / f"maigret_{clean_user}.json"
        if expected_file.exists() and expected_file not in json_candidates:
            json_candidates.append(expected_file)

        for out_file in json_candidates:
            try:
                data = json.loads(out_file.read_text(encoding="utf-8"))
                for site, info in data.items():
                    if not isinstance(info, dict):
                        continue
                    status_val = info.get("status")
                    is_claimed = False
                    claim_url = ""
                    if isinstance(status_val, dict):
                        is_claimed = (status_val.get("status") == "Claimed")
                        claim_url = status_val.get("url") or info.get("url_user", "") or info.get("url", "")
                    elif status_val == "Claimed":
                        is_claimed = True
                        claim_url = info.get("url_user") or info.get("url", "")

                    if is_claimed:
                        entity = Entity(
                            entity_type="username",
                            value=clean_user,
                            sources=["maigret"],
                            confidence=0.9,
                            platform=site,
                            metadata={"url": claim_url},
                        )
                        # Verify before trusting
                        case_slug = target.case_slug
                        entity = await verify_hit(entity, lessons_store=lessons_store, case_slug=case_slug)
                        if target.add_entity(entity) and on_find:
                            await on_find(entity)
            except Exception as json_err:
                logger.warning(f"[social_enum] Error parsing maigret output: {json_err}")
    except FileNotFoundError:
        note = "maigret not found on PATH or in ~/.local/bin, skipped"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)
    except Exception as e:
        note = f"maigret check failed: {e}"
        logger.warning(f"[social_enum] {note}")
        target.notes.append(note)
    finally:
        try:
            tmp_dir.cleanup()
        except Exception:
            pass


async def _run_instagram_probe(target, username, on_find, lessons_store=None):
    """
    Standalone Instagram check via headless probe.
    Always runs regardless of Sherlock/Maigret results — neither tool
    reliably covers Instagram.
    """
    try:
        from modules.headless_probe import probe as headless_probe
    except ImportError:
        return

    try:
        url = f"https://www.instagram.com/{username}/"
        slug = target.case_slug
        entity_id = f"username_{username}_instagram"
        evidence_dir = P(__file__).parent.parent / "cases" / slug / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(evidence_dir / f"{entity_id}.png")

        result = await headless_probe(url, "instagram", screenshot_path=screenshot_path)

        if result.get("exists"):
            entity = Entity(
                entity_type="username",
                value=username,
                sources=["headless_probe"],
                confidence=0.8,
                platform="Instagram",
                metadata={
                    "url": url,
                    "verified": True,
                    "page_title": result.get("title", ""),
                    "og_image": result.get("og_image"),
                    "description": result.get("og_description"),
                    "screenshot_path": f"cases/{slug}/evidence/{entity_id}.png",
                },
            )
            # Check lessons store for prior warnings
            if lessons_store:
                try:
                    warning = lessons_store.has_platform_warning("Instagram")
                    if warning:
                        entity.confidence *= 0.5
                        entity.metadata["lesson_warning"] = warning
                except Exception:
                    pass

            if target.add_entity(entity) and on_find:
                await on_find(entity)
    except Exception:
        pass
