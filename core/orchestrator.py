import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import asyncio
from core.input_parser import parse
from core.target_model import Target, Entity
from core.case_brief import CaseBrief, InvestigationPlan


def _reorder_modules(default_order: list, plan: InvestigationPlan = None) -> list:
    """Reorder module names based on investigation plan. Returns new order."""
    if not plan or not plan.priority_order:
        return default_order
    
    # Start with prioritized modules (in plan order), filtered to valid defaults
    ordered = [m for m in plan.priority_order if m in default_order]
    # Add remaining modules not in priority list
    remaining = [m for m in default_order if m not in ordered]
    # Move skip_candidates to the very end
    if plan.skip_candidates:
        skipped = [m for m in remaining if m in plan.skip_candidates]
        remaining = [m for m in remaining if m not in plan.skip_candidates]
        remaining.extend(skipped)
    ordered.extend(remaining)
    return ordered


import modules.social_enum as social
import modules.domain_intel as domain
import modules.email_recon as email_recon
import modules.paste_search as paste_search
import modules.github_recon as github_recon
import modules.ip_recon as ip_recon
import modules.wayback as wayback


class Orchestrator:
    def __init__(self, on_status=None, on_find=None, on_done=None, lessons_store=None):
        self.on_status = on_status
        self.on_find = on_find
        self.on_done = on_done
        self.lessons_store = lessons_store

    async def investigate(self, raw_input: str, brief: CaseBrief = None, plan: InvestigationPlan = None) -> Target:
        parsed = parse(raw_input)
        target = Target(primary=parsed.value, target_type=parsed.target_type)

        if plan and plan.reasoning:
            target.log("investigation_plan_created", {
                "reasoning": plan.reasoning,
                "priority_order": plan.priority_order,
                "skip_candidates": plan.skip_candidates
            })
            await self._status(f"Strategy: {plan.reasoning}")

        await self._status(f"Parsed as {parsed.target_type}: {parsed.value}")

        if parsed.target_type == "email":
            await self._email_pipeline(target, parsed, plan=plan)
        elif parsed.target_type == "username":
            await self._username_pipeline(target, parsed, plan=plan)
        elif parsed.target_type == "domain":
            await self._domain_pipeline(target, parsed, plan=plan)
        elif parsed.target_type == "ip":
            await self._ip_pipeline(target, parsed, plan=plan)
        elif parsed.target_type == "name":
            await self._name_pipeline(target, parsed, plan=plan)

        # ── Brief-driven pivots (additive, never replaces baseline) ──
        hints_source = None
        if plan and plan.extra_hints:
            hints_source = plan
        elif brief and brief.hints:
            hints_source = brief

        if hints_source:
            await self._brief_driven_pivot(target, parsed, hints_source)

        # ── Identity-driven pivots (auto-pivot on verified name) ──
        await self._identity_driven_pivot(target)

        # ── Discovered domain/email pivots ──
        await self._pivot_on_discovered_entities(target)

        # ── Correlation Engine ──
        try:
            from core.correlation import correlate
            await correlate(target)
        except Exception:
            pass

        # ── Geocoding & EXIF Pass ──
        try:
            await self._geocode_pass(target)
        except Exception as e:
            print(f"[orchestrator] Error running geocode pass: {e}")

        target.compute_risk()
        target.save()

        if self.on_done:
            await self.on_done(target)

        return target

    # Backward-compatible alias — older scripts/configs may still call .stalk(...)
    stalk = investigate

    async def _email_pipeline(self, target, parsed, plan=None):
        local = parsed.metadata["local"]
        dom = parsed.metadata["domain"]
        generic = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com"}

        candidates = list(set([
            local,
            local.replace(".", "_"),
            local.replace(".", ""),
            local.split(".")[0] if "." in local else local,
        ]))

        await self._status(f"⚡ Deploying autonomous OSINT swarm for email: {parsed.value}...")
        swarm_tasks = [
            email_recon.run(target, parsed.value, self._make_find_cb(target)),
            paste_search.run(target, parsed.value, self._make_find_cb(target)),
        ]
        for uname in candidates[:2]:
            swarm_tasks.append(social.run(target, uname, self._make_find_cb(target), lessons_store=self.lessons_store))
            swarm_tasks.append(github_recon.run(target, uname, on_find=self._make_find_cb(target)))
        if dom not in generic:
            swarm_tasks.append(domain.run(target, dom, self._make_find_cb(target)))
            swarm_tasks.append(wayback.run(target, dom, self._make_find_cb(target)))

        await asyncio.gather(*swarm_tasks, return_exceptions=True)

    async def _username_pipeline(self, target, parsed, plan=None):
        await self._status(f"⚡ Deploying autonomous reconnaissance swarm for: {parsed.value}...")
        swarm_tasks = [
            social.run(target, parsed.value, self._make_find_cb(target), lessons_store=self.lessons_store),
            github_recon.run(target, parsed.value, on_find=self._make_find_cb(target)),
            paste_search.run(target, parsed.value, self._make_find_cb(target)),
            wayback.run(target, parsed.value, self._make_find_cb(target)),
        ]
        await asyncio.gather(*swarm_tasks, return_exceptions=True)

        await self._status("Dorking Google for username...")
        import modules.dork_fallback as dork_fallback
        count, status = await dork_fallback.dork(
            target=target,
            query=parsed.value,
            site_filter="",
            entity_type="username",
            on_find=self._make_find_cb(target),
            module_name="username_dork"
        )
        if status == "blocked":
            await self._status("Google search rate-limited / blocked by CAPTCHA.")
        elif status == "empty":
            await self._status("Google search completed: no profiles found.")
        else:
            await self._status(f"Google search found {count} profiles.")

    async def _domain_pipeline(self, target, parsed, plan=None):
        await self._status(f"⚡ Deploying autonomous domain reconnaissance swarm for: {parsed.value}...")
        swarm_tasks = [
            domain.run(target, parsed.value, self._make_find_cb(target)),
            wayback.run(target, parsed.value, self._make_find_cb(target)),
            paste_search.run(target, parsed.value, self._make_find_cb(target)),
        ]
        await asyncio.gather(*swarm_tasks, return_exceptions=True)

    async def _ip_pipeline(self, target, parsed, plan=None):
        default_order = ["ip_recon"]
        ordered = _reorder_modules(default_order, plan)

        for module in ordered:
            if module == "ip_recon":
                await self._status("Geolocating IP...")
                await ip_recon.run(target, parsed.value, self._make_find_cb(target))

    async def _name_pipeline(self, target, parsed, plan=None):
        full_name = parsed.value          # e.g. "sree danush"
        first = parsed.metadata["first"]
        last = parsed.metadata["last"]
        candidates = list(set([
            f"{first}{last}",
            f"{first}.{last}",
            f"{first}_{last}",
            f"{first[0]}{last}",
            first,
        ]))

        default_order = ["github_recon", "social_enum", "paste_search"]
        ordered = _reorder_modules(default_order, plan)

        for module in ordered:
            if module == "github_recon":
                # Full-name GitHub search first (primary, high-specificity pass)
                await self._status(f"Running GitHub deep recon for full name '{full_name}'...")
                await github_recon.run(target, full_name, original_query=full_name,
                                       on_find=self._make_find_cb(target))

                await self._status(f"Trying name username variants on GitHub...")
                for uname in candidates:
                    await github_recon.run(target, uname, original_query=full_name,
                                           on_find=self._make_find_cb(target))
            elif module == "social_enum":
                await self._status(f"Trying {len(candidates)} username variants across 300+ platforms...")
                for uname in candidates:
                    await social.run(target, uname, self._make_find_cb(target),
                                     lessons_store=self.lessons_store)
            elif module == "paste_search":
                await self._status("Searching paste sites...")
                await paste_search.run(target, parsed.value, self._make_find_cb(target))

    async def _brief_driven_pivot(self, target, parsed, brief_or_plan):
        """
        Run additional recon based on structured hints from the case brief or plan.
        This is purely additive — the baseline pipeline has already completed.
        """
        hints = getattr(brief_or_plan, "extra_hints", None) or getattr(brief_or_plan, "hints", None) or {}
        username = str(parsed.value or "")


        # ── Suspect old GitHub repos with leaked source ───────────
        if hints.get("suspect_old_github"):
            await self._status("Brief pivot: searching GitHub for old repos/leaked source...")
            # Build keyword query from hints
            parts = [username]
            employer = hints.get("employer")
            if employer:
                parts.append(str(employer))
            keywords = hints.get("keywords")
            if isinstance(keywords, list):
                parts.extend(str(kw) for kw in keywords[:3])
            
            # Defensively str-cast + filter(None, ...) before join
            parts_cleaned = [str(p).strip() for p in parts if p is not None]
            parts_cleaned = list(filter(None, parts_cleaned))
            keyword_query = " ".join(parts_cleaned)

            if keyword_query:
                await github_recon.run(
                    target, keyword_query,
                    query_type="keyword",
                    on_find=self._make_find_cb(target),
                )

        # ── Employer-based additional social pass ─────────────────
        employer = hints.get("employer")
        if employer:
            employer_str = str(employer).strip()
            if employer_str:
                await self._status(f"Brief pivot: trying username + employer '{employer_str}'...")
                # Defensively str-cast + filter(None, ...) before join
                parts_cleaned = [str(p).strip() for p in [username, employer_str] if p is not None]
                parts_cleaned = list(filter(None, parts_cleaned))
                combined_query = " ".join(parts_cleaned)

                if combined_query:
                    await social.run(
                        target, combined_query, self._make_find_cb(target),
                        lessons_store=self.lessons_store,
                    )
                    # Also try GitHub with employer context
                    await github_recon.run(
                        target, combined_query,
                        query_type="keyword",
                        on_find=self._make_find_cb(target),
                    )

        # ── Additional usernames from brief ───────────────────────
        additional_usernames = hints.get("additional_usernames")
        if isinstance(additional_usernames, list):
            for extra_uname in additional_usernames[:5]:
                extra_uname_str = str(extra_uname).strip()
                if extra_uname_str:
                    await self._status(f"Brief pivot: checking additional username '{extra_uname_str}'...")
                    await social.run(
                        target, extra_uname_str, self._make_find_cb(target),
                        lessons_store=self.lessons_store,
                    )
                    await github_recon.run(
                        target, extra_uname_str,
                        on_find=self._make_find_cb(target),
                    )

        # ── Real name pass ────────────────────────────────────────
        real_name = hints.get("real_name")
        if real_name and parsed.target_type != "name":
            real_name_str = str(real_name).strip()
            if real_name_str:
                await self._status(f"Brief pivot: searching for real name '{real_name_str}'...")
                await paste_search.run(target, real_name_str, self._make_find_cb(target))
                await github_recon.run(
                    target, real_name_str,
                    query_type="keyword",
                    on_find=self._make_find_cb(target),
                )

    async def _identity_driven_pivot(self, target: Target):
        """
        Pivot on any verified name entity discovered during the run, prioritizing real high-confidence
        discovered username entities over the raw input target/email.
        """
        import modules.dork_fallback as dork_fallback

        # Find verified name-type entities
        verified_names = [
            e for e in target.entities 
            if e.entity_type == "name" and e.metadata.get("verified")
        ]

        # Look for high-confidence discovered username entities (excluding emails and raw target primary)
        candidate_usernames = [
            e for e in target.entities
            if e.entity_type == "username"
            and (e.metadata.get("verified") or (e.confidence or 0.5) >= 0.8)
            and e.value.strip()
            and e.value.strip().lower() != target.primary.strip().lower()
            and "@" not in e.value
        ]

        pivot_username = None
        if candidate_usernames:
            # Sort by verified state, confidence, and length (longer unique handles take precedence)
            candidate_usernames.sort(
                key=lambda x: (
                    1 if x.metadata.get("verified") else 0,
                    x.confidence or 0.5,
                    len(x.value.strip())
                ),
                reverse=True
            )
            pivot_username = candidate_usernames[0].value.strip()
        else:
            # Fall back to target.primary if not an email, or handle prefix if email
            if "@" in target.primary:
                pivot_username = target.primary.split("@")[0].strip()
            else:
                pivot_username = target.primary.strip()

        for name_entity in verified_names:
            real_name = name_entity.value.strip()
            if not real_name:
                continue

            # Check if we have already pivoted on this name
            already_pivoted = False
            for event in target.timeline:
                if event.get("event") == "identity_pivot_triggered":
                    if event.get("data", {}).get("name") == real_name:
                        already_pivoted = True
                        break
            
            if already_pivoted:
                continue

            await self._status(f"Identity pivot: pivoting on verified name '{real_name}' and username '{pivot_username}'")
            target.log("identity_pivot_triggered", {
                "name": real_name,
                "username": pivot_username
            })

            cb = self._make_find_cb(target)

            # Pass 1: Discovered username alone (if available and distinct from real_name)
            if pivot_username and pivot_username.lower() != real_name.lower():
                await self._status(f"Identity pivot: searching username '{pivot_username}'...")
                count_uname, _ = await dork_fallback.dork(
                    target=target,
                    query=pivot_username,
                    site_filter="",
                    entity_type="domain",
                    on_find=cb,
                    module_name=f"identity_pivot_username_{pivot_username}"
                )
                if count_uname > 0:
                    log_tool_fallback(target, f"identity_pivot_username_{pivot_username}", count_uname)

            # Pass 2: Plain name alone
            await self._status(f"Identity pivot: searching real name '{real_name}'...")
            count_plain, _ = await dork_fallback.dork(
                target=target,
                query=real_name,
                site_filter="",
                entity_type="domain",
                on_find=cb,
                module_name=f"identity_pivot_{real_name}"
            )
            if count_plain > 0:
                log_tool_fallback(target, f"identity_pivot_{real_name}", count_plain)

            # Pass 3: Combined name + username dork
            if pivot_username:
                combined_query = f"{real_name} {pivot_username}"
            else:
                combined_query = real_name

            if combined_query.lower() != real_name.lower() and combined_query.lower() != pivot_username.lower():
                await self._status(f"Identity pivot: searching combined '{combined_query}'...")
                count_combined, _ = await dork_fallback.dork(
                    target=target,
                    query=combined_query,
                    site_filter="",
                    entity_type="domain",
                    on_find=cb,
                    module_name=f"identity_pivot_combined_{real_name}"
                )
                if count_combined > 0:
                    log_tool_fallback(target, f"identity_pivot_combined_{real_name}", count_combined)

    def _make_find_cb(self, target: Target):
        async def _cb(entity: Entity):
            # Asynchronously trigger evidence screenshot capture for verified URL entities
            url = (entity.metadata or {}).get("url") or (entity.value if str(entity.value).startswith("http") else None)
            if url and (entity.metadata or {}).get("verified") is not False:
                try:
                    from modules.evidence import capture
                    case_slug = target.primary.replace("@", "_").replace(".", "_")
                    asyncio.create_task(capture(url, case_slug, entity.entity_id))
                except Exception as e:
                    print(f"[orchestrator] Evidence capture trigger error: {e}")

            if self.on_find:
                await self.on_find(entity, target)
        return _cb

    async def _status(self, msg: str):
        if self.on_status:
            await self.on_status(msg)

    async def _pivot_on_discovered_entities(self, target: Target):
        """
        Pivot on any discovered domains/emails to run domain_intel/wayback or breach checks.
        """
        max_depth = 3
        cb = self._make_find_cb(target)

        for depth in range(max_depth):
            pivoted_any = False

            # Find all domain entities
            domains = [e for e in target.entities if e.entity_type == "domain"]
            for dom_entity in domains:
                dom = dom_entity.value.strip().lower()
                if not dom:
                    continue

                # Skip common generic/social/infrastructure domains to avoid useless pivots
                generic_social = {
                    "github.com", "linkedin.com", "twitter.com", "x.com", "instagram.com",
                    "facebook.com", "medium.com", "gmail.com", "yahoo.com", "outlook.com",
                    "hotmail.com", "t.me", "telegram.org", "reddit.com", "youtube.com"
                }
                if dom in generic_social:
                    continue

                # Check if already pivoted
                already_pivoted = False
                for event in target.timeline:
                    if event.get("event") == "domain_pivot_triggered" and event.get("data", {}).get("domain") == dom:
                        already_pivoted = True
                        break

                if already_pivoted:
                    continue

                await self._status(f"Discovered domain pivot: checking {dom}...")
                target.log("domain_pivot_triggered", {"domain": dom})
                pivoted_any = True

                await domain.run(target, dom, cb)
                await wayback.run(target, dom, cb)

            # Find all email entities
            emails = [e for e in target.entities if e.entity_type == "email"]
            for email_entity in emails:
                email = email_entity.value.strip().lower()
                if not email:
                    continue

                # Check if already pivoted
                already_pivoted = False
                for event in target.timeline:
                    if event.get("event") == "email_pivot_triggered" and event.get("data", {}).get("email") == email:
                        already_pivoted = True
                        break

                if already_pivoted:
                    continue

                await self._status(f"Discovered email pivot: breach lookup for {email}...")
                target.log("email_pivot_triggered", {"email": email})
                pivoted_any = True

                await email_recon.run(target, email, cb)

            # Find all IP entities
            ips = [e for e in target.entities if e.entity_type == "ip"]
            for ip_entity in ips:
                ip = ip_entity.value.strip()
                if not ip:
                    continue

                # Check if already pivoted
                already_pivoted = False
                for event in target.timeline:
                    if event.get("event") == "ip_pivot_triggered" and event.get("data", {}).get("ip") == ip:
                        already_pivoted = True
                        break

                if already_pivoted:
                    continue

                await self._status(f"Discovered IP pivot: recon for {ip}...")
                target.log("ip_pivot_triggered", {"ip": ip})
                pivoted_any = True

                await ip_recon.run(target, ip, cb)

            # If no new pivots were triggered, we are done
            if not pivoted_any:
                break

    async def _geocode_pass(self, target: Target):
        """
        Geocodes any plain-text location strings found on entities and extracts GPS from screenshots.
        Logs a geo_pass_complete timeline event.
        """
        try:
            from modules.geocode import geocode
            from modules.exif_geo import extract_gps
        except ImportError:
            return

        geocoded_count = 0
        exif_count = 0

        # Geocode entities
        for entity in target.entities:
            # Check bio locations
            loc = entity.metadata.get("location")
            if loc and not entity.metadata.get("geocoded"):
                res = geocode(loc)
                if res:
                    entity.metadata["geocoded"] = res
                    geocoded_count += 1

            # Check EXIF GPS locations from captured screenshots
            shot_path = entity.metadata.get("screenshot_path")
            if shot_path and not entity.metadata.get("exif_location"):
                full_path = Path(__file__).parent.parent / shot_path
                if full_path.exists():
                    res = extract_gps(str(full_path))
                    if res:
                        entity.metadata["exif_location"] = res
                        exif_count += 1

        if geocoded_count > 0 or exif_count > 0:
            target.log("geo_pass_complete", {
                "geocoded_locations": geocoded_count,
                "exif_gps_extracted": exif_count
            })


def log_tool_fallback(target, module: str, count: int):
    """
    Log a tool_fallback_triggered timeline event when dorking fires.
    """
    target.log("tool_fallback_triggered", {
        "module": module,
        "found": count
    })