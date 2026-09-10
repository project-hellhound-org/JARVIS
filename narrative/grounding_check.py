"""
narrative/grounding_check.py — Anti-Hallucination & Grounding Audit for J.A.R.V.I.S. Monologues.
"""
import re
from typing import Tuple, List

KNOWN_PLATFORMS = [
    "wordpress", "github", "twitter", "x.com", "x", "instagram", "facebook", 
    "linkedin", "gravatar", "reddit", "medium", "tumblr", "pinterest", 
    "disqus", "google maps", "google", "haveibeenpwned", "pwned", "keybase", "gitlab", "bitbucket"
]

VISUAL_DESCRIPTION_PATTERNS = [
    r"\b(woman|man|girl|guy|person)\s+with\b",
    r"\b(smiling|smile|laughing|frowning)\b",
    r"\b(wearing|glasses|shirt|hat|jacket|hoodie|beard|moustache)\b",
    r"\b(photo|avatar|picture|screenshot|image)\s+(shows|depicts|features|displays|contains|reveals)\b",
    r"\b(visual\s+details?|appearance\s+of\s+the\s+avatar)\b",
    r"\b(blue|red|green|black|white|dark|bright)\s+(background|hair|eyes|clothing)\b",
]

def verify_grounding(monologue_text: str, target) -> Tuple[str, List[str]]:
    """
    Cross-checks monologue narrative text against target's verified facts.
    Returns (cleaned_monologue_text, list_of_warnings).
    """
    if not monologue_text:
        return monologue_text, []

    warnings = []

    # 1. Extract platforms present in target
    target_platforms = set()
    for e in getattr(target, "entities", []):
        if getattr(e, "platform", None):
            target_platforms.add(e.platform.lower())
        for s in getattr(e, "sources", []):
            target_platforms.add(s.lower())
        if hasattr(e, "metadata") and isinstance(e.metadata, dict):
            for k in ("platform", "service", "source"):
                if e.metadata.get(k):
                    target_platforms.add(str(e.metadata[k]).lower())

    for breach in getattr(target, "breaches", []):
        if getattr(breach, "name", None):
            target_platforms.add(breach.name.lower())

    text_lower = monologue_text.lower()

    # Check for platform mentions not present in target
    for plat in KNOWN_PLATFORMS:
        pattern = r"\b" + re.escape(plat) + r"\b"
        if re.search(pattern, text_lower):
            matched = any(plat in tp or tp in plat for tp in target_platforms)
            if not matched:
                warnings.append(f"Monologue referenced '{plat.capitalize()}', but no {plat.capitalize()} data exists for target.")

    # 2. Check for visual descriptions of images (Ollama/Gemma is text-only without vision)
    for pattern in VISUAL_DESCRIPTION_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            matched_phrase = match.group(0)
            warnings.append(f"Described visual image details ('{matched_phrase}') without vision analysis capability.")
            break

    # 3. Append disclaimer note if warnings found
    final_text = monologue_text
    if warnings:
        disclaimer = (
            "\n\n[Note: Grounding Audit flagged potential speculation: "
            + "; ".join(warnings) + " — treat as speculation.]"
        )
        final_text += disclaimer

    return final_text, warnings
