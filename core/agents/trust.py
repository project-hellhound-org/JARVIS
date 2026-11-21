# core/agents/trust.py
"""
Untrusted-content handling for the tool loop.

Two defences, used together:

* Fencing    -- anything that came from outside the operator (an email body, a
                subject line, an invite description, a file's text) reaches the
                model only inside a fence whose markers carry a per-run random
                nonce. Text inside cannot close the fence early, because it
                cannot guess the nonce.
* Provenance -- every tool result is recorded as trusted (the operator's own
                words, our own lookups) or untrusted (fenced content). Before a
                tool runs, the arguments it declares as *grounded* (recipients,
                for example) are checked: a value that appears in untrusted text
                but nowhere in trusted text was put there by the content, not by
                the operator, and the call is refused.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Iterable, List, Optional

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-']{2,}")
# Words too common to tell us anything about where a value came from.
_COMMON = {
    "the", "and", "for", "you", "your", "with", "from", "this", "that", "have", "will", "can", "are", "was",
    "not", "but", "all", "any", "our", "out", "about", "please", "thanks", "team", "mail", "email", "com",
    "org", "net", "www", "http", "https", "reply", "dear", "regards", "best",
}


def new_nonce() -> str:
    return secrets.token_hex(4)


def fence(text: Any, source: str, nonce: str) -> str:
    """Wrap untrusted text so the model treats it as data.

    Any marker-looking text inside is neutralised, so content can neither close
    this fence nor open a fake 'trusted' section.
    """
    body = str(text if text is not None else "")
    body = re.sub(r"<<<|>>>", "‹‹", body)
    return (f"<<<UNTRUSTED_DATA nonce={nonce} source={source}>>>\n{body}\n"
            f"<<<END_UNTRUSTED_DATA nonce={nonce}>>>")


def _tokens(value: Any) -> List[str]:
    """Identifying tokens in a value: addresses and URLs whole, plus distinctive words."""
    text = " ".join(_flatten(value))
    out = [m.lower() for m in _EMAIL_RE.findall(text)] + [m.lower() for m in _URL_RE.findall(text)]
    stripped = _URL_RE.sub(" ", _EMAIL_RE.sub(" ", text))
    out += [w.lower().strip("'.") for w in _WORD_RE.findall(stripped) if w.lower().strip("'.") not in _COMMON]
    return [t for t in out if t]


def _flatten(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _flatten(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _flatten(v)
    else:
        yield str(value)


class Provenance:
    """What the model has seen this run, split by whether it can be trusted."""

    def __init__(self, trusted: Iterable[str] = ()):
        self._trusted: List[str] = [str(t).lower() for t in trusted if t]
        self._untrusted: List[str] = []

    def add_trusted(self, text: Any) -> None:
        for chunk in _flatten(text):
            if chunk:
                self._trusted.append(chunk.lower())

    def add_untrusted(self, text: Any) -> None:
        for chunk in _flatten(text):
            if chunk:
                self._untrusted.append(chunk.lower())

    @property
    def has_untrusted(self) -> bool:
        return bool(self._untrusted)

    def trusted_mentions(self, literal: str) -> bool:
        """Did trusted text contain this exact string (e.g. a file path the operator typed)?"""
        lit = str(literal or "").lower().strip()
        return bool(lit) and any(lit in t for t in self._trusted)

    def ungrounded(self, value: Any, strict: bool = False) -> Optional[str]:
        """Something in ``value`` that only untrusted content supplied, else None.

        Checked per item (one recipient, one request): an email address or link
        must itself appear in trusted text; a plain name is fine when any of its
        words came from trusted text ("Pepper Potts" after the operator said
        "Pepper"). ``strict`` (for free-text requests handed to other agents):
        no distinctive word may come only from untrusted text.
        """
        if not self._untrusted or value in (None, "", [], ()):
            return None
        trusted = "\n".join(self._trusted)
        untrusted = "\n".join(self._untrusted)
        if strict:
            for tok in _tokens(value):
                if tok in untrusted and tok not in trusted:
                    return tok
            return None
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            text = " ".join(_flatten(item))
            hard = [m.lower() for m in _EMAIL_RE.findall(text)] + [m.lower() for m in _URL_RE.findall(text)]
            for tok in hard:
                if tok not in trusted:
                    return tok
            words = [t for t in _tokens(_URL_RE.sub(" ", _EMAIL_RE.sub(" ", text)))]
            if words and not any(w in trusted for w in words):
                tainted = next((w for w in words if w in untrusted), None)
                if tainted:
                    return tainted
        return None
