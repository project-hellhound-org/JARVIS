import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import asyncio
import logging
import re
import httpx
from core.target_model import Entity, Target

logger = logging.getLogger(__name__)


async def run(target: Target, domain: str, on_find=None) -> None:
    """Check Wayback Machine CDX API for historical snapshots."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(
                "https://web.archive.org/cdx/search/cdx",
                params={
                    "url": f"*.{domain}",
                    "output": "json",
                    "fl": "original,timestamp,statuscode",
                    "collapse": "urlkey",
                    "limit": "20",
                    "filter": "statuscode:200",
                }
            )
            if r.status_code != 200:
                note = f"Wayback CDX query failed: HTTP {r.status_code}"
                logger.warning(f"[wayback] {note}")
                target.notes.append(note)
                target.log("wayback_error", {"domain": domain, "status": r.status_code})
                return

            try:
                rows = r.json()
            except Exception as json_err:
                note = f"Wayback CDX returned invalid JSON: {json_err}"
                logger.warning(f"[wayback] {note}")
                target.notes.append(note)
                target.log("wayback_error", {"domain": domain, "error": str(json_err)})
                return

            if not isinstance(rows, list) or len(rows) <= 1:
                return

            # First row is header
            for row in rows[1:]:
                if len(row) >= 2:
                    original_url = row[0]
                    timestamp = row[1]
                    year = timestamp[:4]

                    target.log("wayback_snapshot", {
                        "url": original_url,
                        "year": year,
                        "timestamp": timestamp,
                    })

                    # Extract unique subdomains
                    subdomain_match = re.match(
                        rf"https?://([^/]+\.{re.escape(domain)})", original_url
                    )
                    if subdomain_match:
                        subdomain = subdomain_match.group(1)
                        entity = Entity(
                            entity_type="domain",
                            value=subdomain,
                            sources=["wayback"],
                            confidence=0.7,
                            platform="Wayback Machine",
                            metadata={"first_seen": year},
                        )
                        if target.add_entity(entity) and on_find:
                            await on_find(entity)

    except Exception as e:
        note = f"Wayback check failed: {e}"
        logger.warning(f"[wayback] {note}")
        target.notes.append(note)
        target.log("wayback_error", {"domain": domain, "error": str(e)})
