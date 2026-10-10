import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import asyncio
import logging
import shutil
from typing import List, Callable, Any, Awaitable
import httpx
from core.target_model import Entity, Breach, Target
from core.tool_locator import find_tool, build_subprocess_env
import core.config

logger = logging.getLogger(__name__)


async def _check_breachdirectory(target: Target, email: str, on_find=None) -> List[Breach]:
    """Check BreachDirectory API via RapidAPI (requires RAPIDAPI_KEY)."""
    api_key = core.config.get_config("RAPIDAPI_KEY")
    if not api_key:
        note = "BreachDirectory skipped: no key configured"
        logger.warning(f"[email_recon] {note}")
        target.notes.append(note)
        target.log("tool_skipped", {"tool": "breachdirectory", "reason": "no key configured"})
        return []

    breaches: List[Breach] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://breachdirectory.p.rapidapi.com/?func=auto&term={email}",
                headers={
                    "X-RapidAPI-Key": api_key,
                    "X-RapidAPI-Host": "breachdirectory.p.rapidapi.com",
                }
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("found"):
                    for item in data.get("result", []):
                        breach = Breach(
                            name=item.get("sources", ["Unknown"])[0],
                            date=item.get("last_breach", "Unknown"),
                            exposed_fields=item.get("fields", []),
                            source="breachdirectory",
                        )
                        breaches.append(breach)
                        if target.add_breach(breach) and on_find:
                            await on_find(breach)
            else:
                note = f"BreachDirectory failed: HTTP {r.status_code}"
                logger.warning(f"[email_recon] {note}")
                target.notes.append(note)
    except Exception as e:
        note = f"BreachDirectory error: {e}"
        logger.warning(f"[email_recon] {note}")
        target.notes.append(note)
    return breaches


async def _check_holehe(target: Target, email: str, on_find=None) -> None:
    """Run holehe CLI to check email on 120+ sites."""
    holehe_bin = find_tool("holehe")
    if not holehe_bin:
        note = "holehe not found on PATH or in ~/.local/bin, skipped"
        logger.warning(f"[email_recon] {note}")
        target.notes.append(note)
        target.log("tool_skipped", {"tool": "holehe", "reason": "not installed"})
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            holehe_bin, email, "--only-used", "--no-color",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=build_subprocess_env(),
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode(errors="ignore").splitlines():
            line_str = line.strip()
            # Ignore holehe summary/legend line e.g. "[+] Email used, [-] Email not used, [x] Rate limit"
            if "[+]" in line_str and "email used" not in line_str.lower() and "rate limit" not in line_str.lower():
                # Extract site name
                parts = line_str.split("[+]")[-1].strip().split()
                if parts:
                    site = parts[0]
                    entity = Entity(
                        entity_type="email",
                        value=email,
                        sources=["holehe"],
                        confidence=0.85,
                        platform=site,
                        metadata={"registered": True},
                    )
                    if target.add_entity(entity) and on_find:
                        await on_find(entity)

                    # Follow-up profile enrichment if holehe detects Gravatar registration
                    if "gravatar" in site.lower():
                        try:
                            import modules.gravatar_profile as gravatar_profile
                            await gravatar_profile.enrich(target, email, on_find)
                        except Exception as e:
                            logger.warning(f"[email_recon] Gravatar profile enrichment error: {e}")
    except FileNotFoundError:
        note = "holehe not found on PATH or in ~/.local/bin, skipped"
        logger.warning(f"[email_recon] {note}")
        target.notes.append(note)
    except Exception as e:
        note = f"holehe check failed: {e}"
        logger.warning(f"[email_recon] {note}")
        target.notes.append(note)


async def _check_gravatar(target: Target, email: str, on_find=None) -> None:
    """Check if email has a Gravatar — fetches public profile, name, location, links, avatar, screenshot."""
    try:
        import modules.gravatar_profile as gravatar_profile
        await gravatar_profile.enrich(target, email, on_find)
    except Exception as e:
        logger.warning(f"[email_recon] Gravatar check error: {e}")


# Modular registry of breach provider functions (all share the same signature: (target, email, on_find) -> List[Breach])
BREACH_PROVIDERS: List[Callable[[Target, str, Any], Awaitable[List[Breach]]]] = [
    _check_breachdirectory,
]


async def run(target: Target, email: str, on_find=None) -> None:
    await asyncio.gather(
        *(provider(target, email, on_find) for provider in BREACH_PROVIDERS),
        _check_holehe(target, email, on_find),
        _check_gravatar(target, email, on_find),
    )

    # Run multi-technique email intelligence enrichment (Gravatar, Libravatar, Google Maps dork)
    try:
        import modules.email_intel as email_intel
        await email_intel.enrich(target, email, on_find)
    except Exception as e:
        logger.warning(f"[email_recon] Email intel enrichment error: {e}")
