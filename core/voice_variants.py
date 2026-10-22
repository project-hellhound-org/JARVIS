"""
Voice variant suggestion engine for J.A.R.V.I.S. Investigation Brief.

Provides deterministic, pure-function suggestions for voice-transcribed identifiers
(and typed inputs on demand) to resolve common voice STT ambiguities such as:
- Spoken symbols ("dot", "underscore", "dash", "hyphen", "at", "plus")
- Number words to digits ("one two three" -> 123, "nineteen eighty four" -> 1984)
- Repeated words ("double seven" -> 77, "triple zero" -> 000)
- Ambiguous "oh" / "zero" readings ("oh seven" -> 07 and o7)
- Common hacker handle homophones and leetspeak ("lead" -> l33t, leet, 1337)
- Spelled-out and NATO phonetic letters ("alpha bravo" -> ab)
- Spoken "capital x" -> X
- Joined variants with separators (_ or .)

All suggestions are strictly validated against character and length constraints
(limit 64 chars) matching parse_brief_identifiers, de-duplicated, and capped at 8.
Suggestions are NEVER applied automatically.
"""

import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Explicit homophone & leetspeak dictionary for common hacker handle patterns.
# Centralized, small, and explicitly documented for easy future extension.
# Format: { root_word: [(replacement, human_readable_reason), ...] }
# ---------------------------------------------------------------------------
LEET_HOMOPHONES: Dict[str, List[Tuple[str, str]]] = {
    "lead": [
        ("l33t", "leet variant of 'lead'"),
        ("leet", "homophone of 'lead'"),
        ("1337", "1337 variant of 'lead'"),
    ],
    "leed": [
        ("l33t", "leet variant of 'leed'"),
        ("leet", "homophone of 'leed'"),
        ("1337", "1337 variant of 'leed'"),
    ],
    "lede": [
        ("l33t", "leet variant of 'lede'"),
        ("leet", "homophone of 'lede'"),
        ("1337", "1337 variant of 'lede'"),
    ],
    "let": [
        ("l33t", "leet variant of 'let'"),
        ("leet", "homophone of 'let'"),
        ("1337", "1337 variant of 'let'"),
    ],
    "lit": [
        ("l33t", "leet variant of 'lit'"),
        ("leet", "homophone of 'lit'"),
        ("1337", "1337 variant of 'lit'"),
    ],
    "elite": [
        ("1337", "1337 variant of 'elite'"),
        ("leet", "homophone of 'elite'"),
        ("l33t", "leet variant of 'elite'"),
    ],
}

# ---------------------------------------------------------------------------
# Phonetic alphabet & number lookup dictionaries
# ---------------------------------------------------------------------------
NATO_PHONETIC: Dict[str, str] = {
    "alpha": "a", "alfa": "a",
    "bravo": "b",
    "charlie": "c",
    "delta": "d",
    "echo": "e",
    "foxtrot": "f",
    "golf": "g",
    "hotel": "h",
    "india": "i",
    "juliet": "j", "juliett": "j",
    "kilo": "k",
    "lima": "l",
    "mike": "m",
    "november": "n",
    "oscar": "o",
    "papa": "p",
    "quebec": "q",
    "romeo": "r",
    "sierra": "s",
    "tango": "t",
    "uniform": "u",
    "victor": "v",
    "whiskey": "w", "whisky": "w",
    "xray": "x", "x-ray": "x",
    "yankee": "y",
    "zulu": "z",
}

SINGLE_DIGITS: Dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

TEENS: Dict[str, int] = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}

TENS: Dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def validate_identifier(val: str, id_type: str) -> bool:
    """
    Validates identifier candidate value against character rules and length cap (64).
    Matches core.input_parser.parse_brief_identifiers validation constraints.
    """
    if not isinstance(val, str) or len(val) < 2 or len(val) > 64:
        return False
    if id_type == "username":
        return bool(re.match(r"^[a-zA-Z0-9_.\-]+$", val)) and not val.endswith(('.', '-', '_'))
    elif id_type == "email":
        return bool(re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", val))
    elif id_type == "name":
        clean = " ".join(val.split())
        return bool(re.match(r"^[a-zA-Z]+([\s\-][a-zA-Z]+)+$", clean))
    return False


def tokenize_spoken(text: str) -> List[str]:
    """Tokenizes spoken text, normalizing whitespace and splitting punctuation."""
    cleaned = text.strip().rstrip(".,;:!?\"'").replace("-", " - ").replace("_", " _ ").replace(".", " . ")
    res = []
    for t in cleaned.split():
        clean_tok = t.strip(".,;:!?\"'()[]{}").lower()
        if clean_tok:
            res.append(clean_tok)
    return res


def parse_tokens_to_parts(
    tokens: List[str], id_type: str, oh_as_letter: bool = False
) -> Tuple[List[str], List[str]]:
    """
    Translates a sequence of spoken tokens into identifier parts and reasons.
    Handles spoken symbols, digits, years, NATO letters, double/triple, and capitalization.
    """
    parts: List[str] = []
    reasons: List[str] = []
    i = 0
    n = len(tokens)

    while i < n:
        tok = tokens[i]

        # 1. Spoken symbols
        if tok == "dot":
            parts.append(".")
            reasons.append("spoken 'dot'")
            i += 1
            continue
        elif tok == "underscore":
            parts.append("_")
            reasons.append("spoken 'underscore'")
            i += 1
            continue
        elif tok in ("dash", "hyphen"):
            parts.append("-")
            reasons.append("spoken 'dash'")
            i += 1
            continue
        elif tok == "at" and id_type == "email":
            parts.append("@")
            reasons.append("spoken 'at'")
            i += 1
            continue
        elif tok == "plus" and id_type == "email":
            parts.append("+")
            reasons.append("spoken 'plus'")
            i += 1
            continue

        # 2. Modifiers: "capital", "double", "triple"
        if tok == "capital" and i + 1 < n:
            next_tok = tokens[i + 1]
            char = NATO_PHONETIC.get(next_tok)
            if not char and len(next_tok) == 1 and next_tok.isalpha():
                char = next_tok
            if char:
                parts.append(char.upper())
                reasons.append(f"capital '{char.upper()}'")
                i += 2
                continue
            else:
                parts.append(next_tok.capitalize())
                reasons.append("capitalized word")
                i += 2
                continue

        if tok in ("double", "triple") and i + 1 < n:
            mult = 2 if tok == "double" else 3
            next_tok = tokens[i + 1]
            val = None
            if next_tok in SINGLE_DIGITS:
                val = SINGLE_DIGITS[next_tok]
            elif next_tok in ("oh", "zero"):
                val = "o" if (oh_as_letter and next_tok == "oh") else "0"
            elif next_tok in NATO_PHONETIC:
                val = NATO_PHONETIC[next_tok]
            elif len(next_tok) == 1 and next_tok.isalnum():
                val = next_tok

            if val:
                parts.append(val * mult)
                reasons.append(f"{tok} '{val}'")
                i += 2
                continue

        # 3. Two-part years: e.g. "nineteen eighty four", "twenty twenty four", "nineteen ninety nine"
        if tok in ("nineteen", "twenty") and i + 1 < n:
            century_num = 19 if tok == "nineteen" else 20
            next_tok = tokens[i + 1]
            year_val = None
            skip = 0

            if next_tok in TENS:
                tens_val = TENS[next_tok]
                if i + 2 < n and tokens[i + 2] in SINGLE_DIGITS:
                    tens_val += int(SINGLE_DIGITS[tokens[i + 2]])
                    skip = 3
                elif i + 2 < n and tokens[i + 2] == "oh" and not oh_as_letter:
                    tens_val += 0
                    skip = 3
                else:
                    skip = 2
                year_val = f"{century_num}{tens_val:02d}"
            elif next_tok in TEENS:
                year_val = f"{century_num}{TEENS[next_tok]:02d}"
                skip = 2
            elif next_tok in ("zero", "oh") and i + 2 < n and tokens[i + 2] in SINGLE_DIGITS:
                digit = int(SINGLE_DIGITS[tokens[i + 2]])
                year_val = f"{century_num}0{digit}"
                skip = 3

            if year_val:
                parts.append(year_val)
                reasons.append(f"spoken year {year_val}")
                i += skip
                continue

        # 4. Compound tens: e.g. "ninety nine", "twenty one"
        if tok in TENS:
            tens_val = TENS[tok]
            if i + 1 < n and tokens[i + 1] in SINGLE_DIGITS:
                tens_val += int(SINGLE_DIGITS[tokens[i + 1]])
                parts.append(str(tens_val))
                reasons.append("digits spoken as words")
                i += 2
                continue
            else:
                parts.append(str(tens_val))
                reasons.append("digits spoken as words")
                i += 1
                continue

        # 5. Teens: e.g. "fourteen"
        if tok in TEENS:
            parts.append(str(TEENS[tok]))
            reasons.append("digits spoken as words")
            i += 1
            continue

        # 6. Single digits: e.g. "seven"
        if tok in SINGLE_DIGITS:
            parts.append(SINGLE_DIGITS[tok])
            reasons.append("digits spoken as words")
            i += 1
            continue

        # 7. Ambiguous "oh"
        if tok == "oh":
            if oh_as_letter:
                parts.append("o")
                reasons.append("spoken 'oh' as letter o")
            else:
                parts.append("0")
                reasons.append("spoken 'oh' as zero")
            i += 1
            continue

        # 8. NATO phonetic alphabet
        if tok in NATO_PHONETIC:
            parts.append(NATO_PHONETIC[tok])
            reasons.append(f"NATO word '{tok}'")
            i += 1
            continue

        # 9. Spelled-out letter or standard word
        parts.append(tok)
        i += 1

    return parts, reasons


def assemble_email(parts: List[str]) -> List[Tuple[str, str]]:
    """Constructs ranked email variants from translated parts."""
    raw = "".join(parts)
    results = []
    if "@" in raw:
        local, domain = raw.split("@", 1)
        local = local.strip("._-+")
        domain = domain.strip("._-")
        if local and domain and "." in domain:
            email_val = f"{local}@{domain}".lower()
            results.append((email_val, "spoken symbols to email"))
            if "." in local:
                results.append((f"{local.replace('.', '')}@{domain}".lower(), "email without dot"))
                results.append((f"{local.replace('.', '_')}@{domain}".lower(), "email with underscore"))
    return results


def assemble_name(tokens: List[str], parts: List[str]) -> List[Tuple[str, str]]:
    """Constructs name variants adhering to title-case and spelled-out rules (no leet)."""
    results = []
    clean_words = []
    for t in tokens:
        if t in ("space", "dash", "hyphen", "capital"):
            continue
        clean_words.append(t.capitalize())

    if len(clean_words) >= 2:
        results.append((" ".join(clean_words), "capitalized name"))

    nato_words = [t.capitalize() for t in tokens if t in NATO_PHONETIC]
    if len(nato_words) >= 2:
        results.append((" ".join(nato_words), "NATO phonetic name"))

    if any(t in ("dash", "hyphen") for t in tokens) and len(clean_words) >= 2:
        results.append(("-".join(clean_words), "hyphenated name"))

    return results


def suggest_identifier_variants(heard_text: str, id_type: str) -> List[Dict[str, str]]:
    """
    Pure, deterministic suggestion engine returning at most 8 ranked variants
    for a voice-transcribed or typed identifier.

    Args:
        heard_text: Spoken transcript or raw input string.
        id_type: Identifier category ('username', 'email', 'name').

    Returns:
        List of dicts: [{"value": <variant>, "reason": <explanation>}, ...] (max 8)
    """
    if not isinstance(heard_text, str) or not heard_text.strip():
        return []
    if len(heard_text) > 120:
        return []

    id_type = str(id_type or "").strip().lower()
    if id_type not in ("username", "email", "name"):
        return []

    tokens = tokenize_spoken(heard_text)
    if not tokens:
        return []

    raw_candidates: List[Tuple[str, str]] = []

    # -----------------------------------------------------------------------
    # Case: Name (capitalization and spelled-out letters only; no leetspeak)
    # -----------------------------------------------------------------------
    if id_type == "name":
        parts, _ = parse_tokens_to_parts(tokens, id_type="name")
        raw_candidates.extend(assemble_name(tokens, parts))
        out: List[Dict[str, str]] = []
        seen = set()
        for val, reason in raw_candidates:
            if val not in seen and validate_identifier(val, "name"):
                seen.add(val)
                out.append({"value": val, "reason": reason})
                if len(out) >= 8:
                    break
        return out

    # -----------------------------------------------------------------------
    # Case: Email
    # -----------------------------------------------------------------------
    if id_type == "email":
        parts_0, _ = parse_tokens_to_parts(tokens, id_type="email", oh_as_letter=False)
        raw_candidates.extend(assemble_email(parts_0))

        if "oh" in tokens:
            parts_o, _ = parse_tokens_to_parts(tokens, id_type="email", oh_as_letter=True)
            for val, r in assemble_email(parts_o):
                raw_candidates.append((val, "spoken 'oh' as letter o"))

        out = []
        seen = set()
        for val, reason in raw_candidates:
            if val not in seen and validate_identifier(val, "email"):
                seen.add(val)
                out.append({"value": val, "reason": reason})
                if len(out) >= 8:
                    break
        return out

    # -----------------------------------------------------------------------
    # Case: Username
    # -----------------------------------------------------------------------
    parts_0, reasons_0 = parse_tokens_to_parts(tokens, id_type="username", oh_as_letter=False)

    first_tok = tokens[0].lower().strip(".,;:!?\"'") if tokens else ""

    # 1. Leet & Homophone variants on leading word or root (case-insensitive & robust)
    matched_word = None
    suffix = ""
    if first_tok in LEET_HOMOPHONES:
        matched_word = first_tok
        suffix_parts = parts_0[1:] if len(parts_0) > 1 else []
        suffix = "".join(suffix_parts).strip("._-")
    else:
        for w in LEET_HOMOPHONES:
            if first_tok.startswith(w) and (not first_tok[len(w):] or first_tok[len(w):].isdigit() or first_tok[len(w)] in "_.-"):
                matched_word = w
                suffix = first_tok[len(w):].strip("._-")
                break

    if matched_word and matched_word in LEET_HOMOPHONES:
        for repl, repl_reason in LEET_HOMOPHONES[matched_word]:
            combined = f"{repl}{suffix}"
            raw_candidates.append((combined.lower(), repl_reason))

    # 2. Best guess (direct concatenation of translated parts)
    best_guess = "".join(parts_0).strip("._-")
    reason_str = ", ".join(dict.fromkeys(reasons_0)) if reasons_0 else "direct transcription"
    has_capital = any(r.startswith("capital") for r in reasons_0)
    if best_guess:
        if has_capital:
            raw_candidates.append((best_guess, reason_str))
            if best_guess.lower() != best_guess:
                raw_candidates.append((best_guess.lower(), "lowercase variant"))
        else:
            raw_candidates.append((best_guess.lower(), reason_str))

    # 3. If 'oh' was spoken, provide alternate reading with letter 'o'
    if "oh" in tokens:
        parts_o, _ = parse_tokens_to_parts(tokens, id_type="username", oh_as_letter=True)
        cand_o = "".join(parts_o).strip("._-")
        if cand_o:
            raw_candidates.append((cand_o.lower(), "spoken 'oh' as letter o"))

    # 4. Joined variants (with _, ., -)
    word_parts = [p.strip("._-") for p in parts_0 if p.strip("._-")]
    if len(word_parts) >= 2:
        raw_candidates.append(("_".join(word_parts).lower(), "joined with underscore"))
        raw_candidates.append((".".join(word_parts).lower(), "joined with dot"))
        raw_candidates.append(("-".join(word_parts).lower(), "joined with hyphen"))

    # 5. Separated leet variants (if homophone matched)
    if first_tok in LEET_HOMOPHONES:
        suffix_parts = parts_0[1:] if len(parts_0) > 1 else []
        suffix = "".join(suffix_parts).strip("._-")
        if suffix:
            for repl, repl_reason in LEET_HOMOPHONES[first_tok]:
                raw_candidates.append(
                    (f"{repl}_{suffix}".lower(), f"{repl_reason} (separated)")
                )

    # 6. Compact alphanumeric variant (if separators were in best_guess)
    if any(sep in best_guess for sep in ("_", ".", "-")):
        compact = re.sub(r"[._\-]", "", best_guess)
        if compact:
            raw_candidates.append((compact.lower(), "compact handle"))

    # 7. Raw heard text with spaces removed
    cleaned_raw = re.sub(r"\s+", "", heard_text.strip().rstrip(".,;:!?\"'"))
    if cleaned_raw:
        raw_candidates.append((cleaned_raw.lower(), "heard text without spaces"))

    # Validate against constraints, de-duplicate, and clamp to 8 suggestions
    out = []
    seen = set()
    for val, reason in raw_candidates:
        if val not in seen and validate_identifier(val, "username"):
            seen.add(val)
            out.append({"value": val, "reason": reason})
            if len(out) >= 8:
                break

    return out
