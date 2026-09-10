import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))


import asyncio
import httpx
import re
from core.target_model import Entity, Target

def _check_identity_in_fragment(query: str, fragment: str) -> bool:
    if not fragment:
        return False
    
    query_lower = query.lower()
    fragment_lower = fragment.lower()
    
    if query_lower not in fragment_lower:
        return False
        
    identity_markers = [
        "author:",
        "committer:",
        "signed-off-by:",
        "co-authored-by:",
        "git config",
        "user.name",
        "user.email",
        "created by",
        "written by"
    ]
    
    for marker in identity_markers:
        if marker in fragment_lower:
            marker_idx = fragment_lower.find(marker)
            query_idx = fragment_lower.find(query_lower)
            if abs(marker_idx - query_idx) < 60:
                return True
                
    return False


def _extract_email_from_fragment(query: str, fragment: str) -> str:
    pattern = re.compile(
        r'([a-zA-Z0-9._%+-]*' + re.escape(query) + r'[a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        re.IGNORECASE
    )
    match = pattern.search(fragment)
    if match:
        return match.group(1).strip().lower()
    return ""


def _assess_match_strength(query: str, original_query: str, identity_field: str) -> tuple:
    """
    Determine if a query match against an identity field is strong or weak.
    Returns (is_strong: bool, confidence: float).
    """
    if not identity_field:
        return False, 0.15

    field_lower = identity_field.lower().strip()
    original_lower = original_query.lower().strip()
    query_lower = query.lower().strip()

    def normalize(s):
        return re.sub(r'[\s._\-]+', '', s.lower())

    field_norm = normalize(field_lower)
    original_norm = normalize(original_lower)

    # Full original query (all tokens combined) appears in field
    if original_norm in field_norm:
        return True, 0.85

    # All tokens present in field (any order)
    original_tokens = original_lower.split()
    if len(original_tokens) > 1 and all(tok in field_lower for tok in original_tokens):
        return True, 0.80

    # Single-word target — query IS the full original
    if query_lower == original_lower:
        return True, 0.85

    # Partial match — only one token of multi-word query
    if len(query_lower) < 6:
        return False, 0.10
    return False, 0.15


def _check_org_affiliation(target: Target, repo_owner: str) -> bool:
    """Check if repo_owner matches any org/username the target is already linked to."""
    if not repo_owner:
        return False
    owner_lower = repo_owner.lower()
    for entity in target.entities:
        if entity.confidence < 0.5:
            continue
        # Check username entities on GitHub
        if entity.entity_type == "username" and entity.platform == "GitHub":
            if entity.value.lower() == owner_lower:
                return True
        # Check profile links
        if entity.entity_type == "profile_link":
            if f"github.com/{owner_lower}" in entity.value.lower():
                return True
        # Check metadata username field
        if entity.metadata.get("username", "").lower() == owner_lower:
            return True
    return False


async def _emit_identity_cluster(target: Target, author_data: dict, source_url: str, on_find, sources: list) -> None:
    """
    author_data: dict containing 'login' (str), 'name' (str), 'email' (str)
    source_url: str (the URL of the commit/file/profile)
    sources: list of str (e.g. ['github_commits'] or ['github'])
    """
    try:
        from modules.verify import verify_hit
    except ImportError:
        verify_hit = None

    login = author_data.get("login", "")
    name = author_data.get("name", "")
    email = author_data.get("email", "")
    location = author_data.get("location", "")
    bio = author_data.get("bio", "")

    # Clean name if it's identical to email or login
    if name and (name.lower() == login.lower() or (email and name.lower() == email.lower())):
        name = ""

    # Guardrail: Only emit the username/profile-link entities when a real GitHub login is resolvable
    if not login:
        if email and "@" in email and "noreply" not in email:
            metadata = {
                "name": name,
                "url": source_url
            }
            if location:
                metadata["location"] = location
            if bio:
                metadata["bio"] = bio
            entity = Entity(
                entity_type="email",
                value=email.lower(),
                sources=sources,
                confidence=0.85,
                platform="GitHub",
                metadata=metadata
            )
            if verify_hit:
                entity = await verify_hit(entity, case_slug=target.case_slug)
            if target.add_entity(entity) and on_find:
                await on_find(entity)
        return

    profile_url = f"https://github.com/{login}"

    relations = {}
    if name:
        relations["name"] = name
    if email and "@" in email and "noreply" not in email:
        relations["email"] = email.lower()
    relations["username"] = login
    relations["profile_link"] = profile_url

    # 1. Profile Link entity (Verify first to capture screenshot)
    profile_relations = {k: v for k, v in relations.items() if k != "profile_link"}
    profile_metadata = {
        "name": name,
        "username": login,
        "email": email.lower() if (email and "@" in email and "noreply" not in email) else "",
        "url": profile_url,
        "source_url": source_url,
        "related_to": profile_relations
    }
    if location:
        profile_metadata["location"] = location
    if bio:
        profile_metadata["bio"] = bio

    profile_entity = Entity(
        entity_type="profile_link",
        value=profile_url,
        sources=sources,
        confidence=0.85,
        platform="GitHub",
        metadata=profile_metadata
    )
    if verify_hit:
        profile_entity = await verify_hit(profile_entity, case_slug=target.case_slug)

    # Extract screenshot path if captured
    screenshot_path = profile_entity.metadata.get("screenshot_path")

    # Add profile entity
    if target.add_entity(profile_entity) and on_find:
        await on_find(profile_entity)

    # 2. Email entity
    if email and "@" in email and "noreply" not in email:
        email_relations = {k: v for k, v in relations.items() if k != "email"}
        email_metadata = {
            "name": name,
            "username": login,
            "url": profile_url,
            "source_url": source_url,
            "related_to": email_relations
        }
        if screenshot_path:
            email_metadata["screenshot_path"] = screenshot_path
        if location:
            email_metadata["location"] = location
        if bio:
            email_metadata["bio"] = bio

        email_entity = Entity(
            entity_type="email",
            value=email.lower(),
            sources=sources,
            confidence=0.85,
            platform="GitHub",
            metadata=email_metadata
        )
        if verify_hit:
            email_entity = await verify_hit(email_entity, case_slug=None)
        if target.add_entity(email_entity) and on_find:
            await on_find(email_entity)

    # 3. Name entity
    if name:
        name_relations = {k: v for k, v in relations.items() if k != "name"}
        name_metadata = {
            "username": login,
            "email": email.lower() if (email and "@" in email and "noreply" not in email) else "",
            "url": profile_url,
            "source_url": source_url,
            "related_to": name_relations
        }
        if screenshot_path:
            name_metadata["screenshot_path"] = screenshot_path
        if location:
            name_metadata["location"] = location
        if bio:
            name_metadata["bio"] = bio

        name_entity = Entity(
            entity_type="name",
            value=name,
            sources=sources,
            confidence=0.80,
            platform="GitHub",
            metadata=name_metadata
        )
        if verify_hit:
            name_entity = await verify_hit(name_entity, case_slug=None)
        if target.add_entity(name_entity) and on_find:
            await on_find(name_entity)

    # 4. Username entity
    username_relations = {k: v for k, v in relations.items() if k != "username"}
    username_metadata = {
        "name": name,
        "email": email.lower() if (email and "@" in email and "noreply" not in email) else "",
        "url": profile_url,
        "source_url": source_url,
        "related_to": username_relations
    }
    if screenshot_path:
        username_metadata["screenshot_path"] = screenshot_path
    if location:
        username_metadata["location"] = location
    if bio:
        username_metadata["bio"] = bio

    username_entity = Entity(
        entity_type="username",
        value=login,
        sources=sources,
        confidence=0.85,
        platform="GitHub",
        metadata=username_metadata
    )
    if verify_hit:
        username_entity = await verify_hit(username_entity, case_slug=None)
    if target.add_entity(username_entity) and on_find:
        await on_find(username_entity)


async def run(target: Target, query: str, query_type: str = "user", on_find=None, original_query: str = "") -> None:
    """
    query_type: 'user' | 'email' | 'keyword'
    original_query: the full original search term (e.g. full name) for strength assessment.
                    Defaults to query when not provided.
    """
    if not original_query:
        original_query = query
    results = await asyncio.gather(
        _search_commits(target, query, on_find, original_query),
        _search_code(target, query, on_find, original_query),
        _get_user_profile(target, query, on_find, original_query),
        _parse_profile_readme(target, query, on_find, original_query),
    )
    
    commits_found, code_found, profile_status, _ = results
    
    if profile_status == 404 and not commits_found and not code_found:
        try:
            import modules.dork_fallback as dork_fallback
            count, _ = await dork_fallback.dork(target, query, site_filter="github.com", entity_type="domain", on_find=on_find, module_name="github_recon")
            try:
                from core.orchestrator import log_tool_fallback
                log_tool_fallback(target, "github_recon", count)
            except Exception:
                pass
        except Exception:
            pass


async def _get_user_profile(target, username, on_find, original_query=""):
    """Pull public GitHub profile — reveals email, name, bio."""
    try:
        from modules.verify import verify_hit
    except ImportError:
        verify_hit = None

    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "JARVIS-OSINT"}
        ) as client:
            r = await client.get(f"https://api.github.com/users/{username}")
            if r.status_code == 200:
                data = r.json()
                profile_url = f"https://github.com/{username}"

                # Assess match strength against original query
                if not original_query:
                    original_query = username
                identity_fields = [
                    username,
                    data.get("name", "") or "",
                    data.get("email", "") or "",
                    data.get("bio", "") or "",
                ]
                best_strong, best_conf = False, 0.10
                for f in identity_fields:
                    if f:
                        strong, conf = _assess_match_strength(username, original_query, f)
                        if strong and conf > best_conf:
                            best_strong, best_conf = True, conf

                if not best_strong:
                    # Weak match — profile exists but doesn't match the full original query
                    entity = Entity(
                        entity_type="mention",
                        value=profile_url,
                        sources=["github"],
                        confidence=best_conf,
                        platform="GitHub",
                        metadata={
                            "note": f"profile '{username}' exists but is a weak match for '{original_query}'",
                            "url": profile_url
                        }
                    )
                    try:
                        from modules.verify import verify_hit as _vh
                        entity = await _vh(entity, case_slug=target.case_slug)
                    except ImportError:
                        pass
                    if target.add_entity(entity) and on_find:
                        await on_find(entity)
                    return True

                author_data = {
                    "login": username,
                    "name": data.get("name", ""),
                    "email": data.get("email", ""),
                    "location": data.get("location", ""),
                    "bio": data.get("bio", "")
                }
                await _emit_identity_cluster(target, author_data, profile_url, on_find, sources=["github"])

                # Location
                if data.get("location"):
                    target.log("github_location", {
                        "username": username,
                        "location": data["location"]
                    })

                # Blog/website
                if data.get("blog"):
                    blog_url = data["blog"]
                    if not blog_url.startswith("http"):
                        blog_url = "http://" + blog_url
                    entity = Entity(
                        entity_type="domain",
                        value=data["blog"],
                        sources=["github"],
                        confidence=0.75,
                        platform="GitHub",
                        metadata={"username": username, "url": blog_url}
                    )
                    if verify_hit:
                        entity = await verify_hit(entity, case_slug=target.case_slug)
                    if target.add_entity(entity) and on_find:
                        await on_find(entity)
                return True
            elif r.status_code == 404:
                return 404
    except Exception:
        pass
    return False


async def _search_commits(target, query, on_find, original_query=""):
    """Search commit history for email leaks."""
    found_any = False
    try:
        from modules.verify import verify_hit
    except ImportError:
        verify_hit = None

    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={
                "User-Agent": "JARVIS-OSINT",
                "Accept": "application/vnd.github.cloak-preview, application/vnd.github.v3.text-match+json"
            }
        ) as client:
            r = await client.get(
                f"https://api.github.com/search/commits?q={query}&per_page=5"
            )
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items:
                    found_any = True
                for item in items:
                    commit = item.get("commit", {})
                    author = commit.get("author", {})
                    email = author.get("email", "")
                    name = author.get("name", "")

                    # Check why the match occurred
                    author_login = item.get("author", {}).get("login", "") if item.get("author") else ""
                    committer = commit.get("committer", {})
                    committer_email = committer.get("email", "")
                    committer_name = committer.get("name", "")
                    committer_login = item.get("committer", {}).get("login", "") if item.get("committer") else ""

                    query_lower = query.lower()
                    is_identity = False

                    # Check direct identity fields
                    if query_lower in author_login.lower():
                        is_identity = True
                    elif query_lower in committer_login.lower():
                        is_identity = True
                    elif query_lower in name.lower():
                        is_identity = True
                    elif query_lower in committer_name.lower():
                        is_identity = True
                    elif email and (query_lower in email.split("@")[0].lower() or query_lower in email.lower()):
                        is_identity = True
                    elif committer_email and (query_lower in committer_email.split("@")[0].lower() or query_lower in committer_email.lower()):
                        is_identity = True

                    # Check text matches for identity indicators
                    text_matches = item.get("text_matches", [])
                    if not is_identity and text_matches:
                        for match in text_matches:
                            fragment = match.get("fragment", "")
                            if _check_identity_in_fragment(query, fragment):
                                is_identity = True
                                break

                    commit_url = item.get("html_url", "")
                    if is_identity:
                        # Assess match strength before emitting at high confidence
                        matched_field = (author_login or committer_login or
                                         name or committer_name or
                                         email or committer_email)
                        if not original_query:
                            original_query = query
                        is_strong, match_confidence = _assess_match_strength(
                            query, original_query, matched_field)

                        if is_strong:
                            # Case (a) - genuinely strong identity match
                            author_data = {
                                "login": author_login or committer_login,
                                "name": name or committer_name,
                                "email": email or committer_email
                            }
                            await _emit_identity_cluster(target, author_data, commit_url, on_find, sources=["github_commits"])
                        else:
                            # Partial token match — not sufficient for identity attribution
                            entity = Entity(
                                entity_type="mention",
                                value=commit_url,
                                sources=["github_commits"],
                                confidence=match_confidence,
                                platform="GitHub",
                                metadata={
                                    "note": f"partial name fragment '{query}' matched in identity field — not sufficient for identity attribution",
                                    "repo": item.get("repository", {}).get("full_name", ""),
                                    "commit_url": commit_url,
                                    "url": commit_url
                                }
                            )
                            if verify_hit:
                                entity = await verify_hit(entity, case_slug=target.case_slug)
                            if target.add_entity(entity) and on_find:
                                await on_find(entity)
                    else:
                        # Case (b) - low confidence mention
                        repo_fullname = item.get("repository", {}).get("full_name", "")
                        repo_owner = repo_fullname.split("/")[0] if "/" in repo_fullname else ""
                        is_affiliated = _check_org_affiliation(target, repo_owner)

                        if is_affiliated:
                            mention_note = "repo belongs to an organization the target is affiliated with — possible collaborator or team context"
                            mention_confidence = 0.35
                        else:
                            mention_note = "target string appears as content, not as author identity — likely unrelated"
                            mention_confidence = 0.2

                        entity = Entity(
                            entity_type="mention",
                            value=commit_url,
                            sources=["github_commits"],
                            confidence=mention_confidence,
                            platform="GitHub",
                            metadata={
                                "note": mention_note,
                                "repo": repo_fullname,
                                "commit_url": commit_url,
                                "url": commit_url
                            }
                        )
                        if verify_hit:
                            entity = await verify_hit(entity, case_slug=target.case_slug)
                        if target.add_entity(entity) and on_find:
                            await on_find(entity)
    except Exception:
        pass
    return found_any


async def _search_code(target, query, on_find, original_query=""):
    """Search GitHub code for mentions of email/username."""
    found_any = False
    try:
        from modules.verify import verify_hit
    except ImportError:
        verify_hit = None

    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={
                "User-Agent": "JARVIS-OSINT",
                "Accept": "application/vnd.github.v3.text-match+json"
            }
        ) as client:
            r = await client.get(
                f"https://api.github.com/search/code?q={query}&per_page=3"
            )
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items:
                    found_any = True
                for item in items:
                    # Log mention for timeline logs
                    html_url = item.get("html_url", "")
                    repo_info = item.get("repository", {})
                    repo_fullname = repo_info.get("full_name", "")
                    file_name = item.get("name", "")
                    
                    target.log("github_code_mention", {
                        "query": query,
                        "repo": repo_fullname,
                        "file": file_name,
                        "url": html_url
                    })

                    # Check why the match occurred
                    owner_login = repo_info.get("owner", {}).get("login", "") if repo_info else ""
                    repo_name = repo_info.get("name", "") if repo_info else ""
                    file_path = item.get("path", "")
                    
                    query_lower = query.lower()
                    is_identity = False

                    if query_lower in owner_login.lower():
                        is_identity = True
                    elif query_lower in repo_name.lower():
                        is_identity = True
                    elif query_lower in file_name.lower():
                        is_identity = True
                    elif query_lower in file_path.lower():
                        is_identity = True

                    text_matches = item.get("text_matches", [])
                    extracted_email = ""
                    if text_matches:
                        for match in text_matches:
                            fragment = match.get("fragment", "")
                            if not is_identity and _check_identity_in_fragment(query, fragment):
                                is_identity = True
                            
                            # Check if we can extract an email containing the query
                            email_match = _extract_email_from_fragment(query, fragment)
                            if email_match:
                                extracted_email = email_match

                    if is_identity:
                        # Assess match strength before emitting at high confidence
                        matched_field = owner_login or repo_name or file_name
                        if not original_query:
                            original_query = query
                        is_strong, match_confidence = _assess_match_strength(
                            query, original_query, matched_field)

                        if is_strong:
                            # Case (a) - genuinely strong identity match
                            author_data = {
                                "login": owner_login,
                                "name": "",
                                "email": extracted_email
                            }
                            await _emit_identity_cluster(target, author_data, html_url, on_find, sources=["github"])
                        else:
                            # Partial token match — not sufficient for identity attribution
                            entity = Entity(
                                entity_type="mention",
                                value=html_url,
                                sources=["github_code"],
                                confidence=match_confidence,
                                platform="GitHub",
                                metadata={
                                    "note": f"partial name fragment '{query}' matched in identity field — not sufficient for identity attribution",
                                    "repo": repo_fullname,
                                    "file": file_name,
                                    "url": html_url
                                }
                            )
                            if verify_hit:
                                entity = await verify_hit(entity, case_slug=target.case_slug)
                            if target.add_entity(entity) and on_find:
                                await on_find(entity)
                    else:
                        # Case (b) - low confidence mention
                        code_repo_owner = repo_fullname.split("/")[0] if "/" in repo_fullname else ""
                        is_affiliated = _check_org_affiliation(target, code_repo_owner)

                        if is_affiliated:
                            mention_note = "repo belongs to an organization the target is affiliated with — possible collaborator or team context"
                            mention_confidence = 0.35
                        else:
                            mention_note = "target string appears as content, not as author identity — likely unrelated"
                            mention_confidence = 0.2

                        entity = Entity(
                            entity_type="mention",
                            value=html_url,
                            sources=["github_code"],
                            confidence=mention_confidence,
                            platform="GitHub",
                            metadata={
                                "note": mention_note,
                                "repo": repo_fullname,
                                "file": file_name,
                                "url": html_url
                            }
                        )
                        if verify_hit:
                            entity = await verify_hit(entity, case_slug=target.case_slug)
                        if target.add_entity(entity) and on_find:
                            await on_find(entity)
    except Exception:
        pass
    return found_any


async def _parse_profile_readme(target, username, on_find, original_query=""):
    """Fetch and parse raw GitHub profile README for social/blog links."""
    # Skip README scraping if username is a weak match for original query
    if original_query and original_query.lower() != username.lower():
        is_strong, _ = _assess_match_strength(username, original_query, username)
        if not is_strong:
            return False
    try:
        from modules.verify import verify_hit
    except ImportError:
        verify_hit = None

    import re
    import urllib.parse

    # 1. Fetch README from main or master
    url_main = f"https://raw.githubusercontent.com/{username}/{username}/main/README.md"
    url_master = f"https://raw.githubusercontent.com/{username}/{username}/master/README.md"

    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "JARVIS-OSINT"}
        ) as client:
            r = await client.get(url_main)
            if r.status_code != 200:
                r = await client.get(url_master)
            
            if r.status_code == 200:
                content = r.text
                
                # Regex to extract all URLs
                raw_urls = re.findall(r'https?://[^\s\)\"\'>\]\<\`\)]+', content)
                seen_urls = set()

                # Second pass: extract markdown link targets [![img](badge)](real_url)
                # This captures URLs like medium.com/@handle wrapped around shields.io badges
                markdown_link_urls = re.findall(r'\]\((https?://[^\s\)]+)\)', content)
                raw_urls = list(set(raw_urls + markdown_link_urls))

                
                for url in raw_urls:
                    # Clean trailing punctuation
                    url = url.rstrip('.,;')
                    low_url = url.lower()
                    
                    # Exclude utility/badge domains
                    if any(x in low_url for x in ["shields.io", "githubusercontent.com", "github.com/buttons", "gitpod.io"]):
                        continue
                    
                    # Skip the user's main profile/repo link to avoid duplicate main target representation
                    if low_url == f"https://github.com/{username.lower()}" or low_url == f"http://github.com/{username.lower()}":
                        continue
                        
                    # Skip image files
                    if any(low_url.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"]):
                        continue
                    
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    
                    # Classify URL by domain
                    parsed_url = urllib.parse.urlparse(url)
                    domain_name = parsed_url.netloc.lower()
                    
                    entity_type = "domain"
                    platform = None
                    
                    if "linkedin.com" in domain_name:
                        entity_type = "social"
                        platform = "LinkedIn"
                    elif "medium.com" in domain_name:
                        entity_type = "social"
                        platform = "Medium"
                    elif "tryhackme.com" in domain_name:
                        entity_type = "social"
                        platform = "TryHackMe"
                    else:
                        entity_type = "domain"
                        platform = domain_name.replace("www.", "").capitalize()
                    
                    # Since this is direct self-declared README info, set sources=["github"] so verify_hit trusts it instantly
                    entity = Entity(
                        entity_type=entity_type,
                        value=url,
                        sources=["github"],
                        confidence=0.85,
                        platform=platform,
                        metadata={"username": username, "url": url}
                    )
                    
                    if verify_hit:
                        entity = await verify_hit(entity, case_slug=target.case_slug)
                    if target.add_entity(entity) and on_find:
                        await on_find(entity)
                return True
    except Exception:
        pass
    return False

