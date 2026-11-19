# modules/oauth_connector.py
"""
Shared OAuth2 connector for JARVIS account integrations (Gmail first; Calendar
reuses the same connector by adding its scope).

* ``authorize()``  -- OAuth2 authorization-code flow with PKCE: opens the system
  browser on the provider's consent screen, catches the redirect on a short-lived
  127.0.0.1 listener, exchanges the code for tokens, stores them encrypted.
* ``get_client()`` -- returns a ready google-api-python-client service. Expired
  access tokens are refreshed with the stored refresh token (no new consent) and
  re-saved; the underlying google-auth Credentials also refresh themselves.
* Tokens live ONLY encrypted at rest (Fernet), one file per account:
  ``data/oauth_tokens/<provider>/<email>.enc``. The Fernet key is derived
  (PBKDF2-HMAC-SHA256) from a random per-install secret plus machine identity.

Secrets policy: nothing in this module prints, logs or returns-in-repr an access
token, refresh token or client secret. Errors carry provider error *codes* only.
"""

from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import json
import os
import platform
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TOKEN_ROOT = ROOT / "data" / "oauth_tokens"

GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

# One shared Google connector covers Gmail and Calendar. Accounts connected before
# a scope was added must reconnect once (get_client(require_scopes=...) detects it).
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    GOOGLE_CALENDAR_SCOPE,
]
GOOGLE_GMAIL_SCOPES = GOOGLE_SCOPES  # backwards-compatible name

PROVIDERS: Dict[str, Dict[str, str]] = {
    "google": {
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "revoke_uri": "https://oauth2.googleapis.com/revoke",
        "userinfo_uri": "https://openidconnect.googleapis.com/v1/userinfo",
    },
    # "microsoft": stretch goal (msal) -- not implemented yet.
}

_REFRESH_MARGIN_S = 60
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class OAuthError(Exception):
    """OAuth failure. Message never contains token or secret material."""


class NotConnectedError(OAuthError):
    """No stored account for this provider (user hasn't connected one yet)."""


class MissingScopeError(OAuthError):
    """The stored account was authorised without a scope this caller needs; reconnect to grant it."""


class TokenSet(dict):
    """Token dict whose repr/str never reveal secret values (safe if accidentally printed)."""

    _SECRET_KEYS = {"access_token", "refresh_token", "id_token", "client_secret"}

    def __repr__(self) -> str:
        shown = {k: ("***" if k in self._SECRET_KEYS and v else v) for k, v in self.items()}
        return f"TokenSet({shown})"

    __str__ = __repr__


# ── Encrypted token storage ────────────────────────────────────
class TokenStore:
    def __init__(self, provider: str, root: Optional[Path] = None):
        self.root = Path(root or DEFAULT_TOKEN_ROOT)
        self.dir = self.root / provider
        self._fernet: Optional[Fernet] = None

    def _machine_secret(self) -> bytes:
        """Random per-install secret (created once, owner-only where the OS allows)."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".machine_secret"
        if not path.exists():
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
        return path.read_bytes()

    def _key(self) -> Fernet:
        if self._fernet is None:
            identity = f"{_machine_identity()}|{_safe_user()}".encode()
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"jarvis-oauth-v1|" + identity,
                             iterations=200_000)
            self._fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(self._machine_secret())))
        return self._fernet

    def path_for(self, email: str) -> Path:
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise OAuthError("invalid account email")
        return self.dir / f"{email}.enc"

    def save(self, email: str, tokens: Dict[str, Any]) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(email)
        blob = self._key().encrypt(json.dumps(dict(tokens)).encode("utf-8"))
        tmp = path.with_suffix(".enc.tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        return path

    def load(self, email: str) -> TokenSet:
        path = self.path_for(email)
        if not path.exists():
            raise NotConnectedError("no stored account")
        try:
            return TokenSet(json.loads(self._key().decrypt(path.read_bytes())))
        except InvalidToken as exc:
            raise OAuthError("stored token could not be decrypted on this machine") from exc

    def delete(self, email: str) -> bool:
        path = self.path_for(email)
        if path.exists():
            path.unlink()
            return True
        return False

    def accounts(self) -> List[str]:
        if not self.dir.exists():
            return []
        return sorted(p.name[: -len(".enc")] for p in self.dir.glob("*.enc"))


def _machine_identity() -> str:
    """Stable per-install identity component for key derivation.

    Deliberately NOT uuid.getnode() (MAC address): under WSL2 the virtual network
    adapter is re-created on every WSL restart, and with multiple adapters present
    (VPN clients, VirtualBox host-only adapters, etc.) enumeration order can pick a
    *different* "MAC" each run even though it's the same machine -- silently
    changing the derived key and permanently breaking decryption of tokens saved
    under a previous run. /etc/machine-id (and its symlink target on most distros,
    /var/lib/dbus/machine-id) is generated once at OS install and persists across
    reboots and WSL restarts, so it's used first; hostname is the fallback for
    platforms where neither file exists.
    """
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
            if value:
                return value
        except Exception:
            continue
    return platform.node()


def _safe_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


# ── Loopback redirect listener ─────────────────────────────────
class RedirectListener:
    """One-shot 127.0.0.1 HTTP listener that captures ?code=&state= from the redirect."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.result: Dict[str, str] = {}
        self._done = threading.Event()
        listener = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" not in qs and "error" not in qs:
                    self.send_response(404)
                    self.end_headers()
                    return
                listener.result = {k: v[0] for k, v in qs.items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                ok = "code" in qs
                msg = "JARVIS is connected. You can close this tab." if ok else "Authorization was cancelled."
                self.wfile.write(f"<html><body style='font-family:sans-serif;background:#050607;color:#E6E6E6'>"
                                 f"<p>{msg}</p></body></html>".encode())
                listener._done.set()

            def log_message(self, *args):  # never log request lines (they contain the code)
                pass

        self._server = HTTPServer((host, port), _Handler)
        self.redirect_uri = f"http://{host}:{self._server.server_address[1]}/"
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        while not self._done.is_set():
            self._server.handle_request()

    def start(self) -> None:
        self._server.timeout = 0.5
        self._thread.start()

    def wait(self, timeout: float) -> Dict[str, str]:
        got = self._done.wait(timeout)
        self.close()
        if not got:
            raise OAuthError("timed out waiting for the consent screen")
        return self.result

    def close(self) -> None:
        self._done.set()
        try:
            self._server.server_close()
        except Exception:
            pass


# ── Connector ──────────────────────────────────────────────────
class OAuthConnector:
    def __init__(
        self,
        provider: str,
        client_id: str,
        client_secret: str,
        scopes: Optional[List[str]] = None,
        *,
        token_root: Optional[Path] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        open_browser: Callable[[str], Any] = webbrowser.open,
        listener_factory: Callable[[], Any] = RedirectListener,
        client_builder: Optional[Callable[..., Any]] = None,
        clock: Callable[[], float] = time.time,
    ):
        if provider not in PROVIDERS:
            raise ValueError(f"unsupported OAuth provider {provider!r}")
        self.provider = provider
        self.endpoints = PROVIDERS[provider]
        self.client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self.scopes = list(scopes or GOOGLE_SCOPES)
        self.store = TokenStore(provider, token_root)
        self._transport = transport
        self._open_browser = open_browser
        self._listener_factory = listener_factory
        self._client_builder = client_builder or _build_google_service
        self._clock = clock

    def __repr__(self) -> str:
        return f"OAuthConnector(provider={self.provider!r}, client_id={self.client_id!r}, client_secret='***')"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self._client_secret)

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=20.0)

    # ── Authorization-code flow ────────────────────────────────
    async def authorize(self, timeout: float = 180.0) -> TokenSet:
        """Run the consent flow and store tokens. Returns the token set (repr-masked)."""
        if not self.configured:
            raise OAuthError("OAuth client ID / secret not configured")

        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)

        listener = self._listener_factory()
        listener.start()
        try:
            params = {
                "client_id": self.client_id,
                "redirect_uri": listener.redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",  # ask for a refresh token
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
            url = f"{self.endpoints['auth_uri']}?{urllib.parse.urlencode(params)}"
            await asyncio.to_thread(self._open_browser, url)
            result = await asyncio.to_thread(listener.wait, timeout)
        finally:
            listener.close()

        if result.get("error"):
            raise OAuthError(f"consent denied ({result['error']})")
        if not secrets.compare_digest(result.get("state", ""), state):
            raise OAuthError("state mismatch on OAuth redirect")
        code = result.get("code")
        if not code:
            raise OAuthError("no authorization code returned")

        async with self._http() as http:
            resp = await http.post(self.endpoints["token_uri"], data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "redirect_uri": listener.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            })
            tok = _json_or_error(resp, "token exchange")
            email = await self._fetch_email(http, tok.get("access_token", ""))

        tokens = self._normalise(tok, email=email)
        if not tokens.get("refresh_token"):
            raise OAuthError("provider returned no refresh token")
        self.store.save(email, tokens)
        return tokens

    async def _fetch_email(self, http: httpx.AsyncClient, access_token: str) -> str:
        resp = await http.get(self.endpoints["userinfo_uri"], headers={"Authorization": f"Bearer {access_token}"})
        info = _json_or_error(resp, "userinfo")
        email = (info.get("email") or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise OAuthError("could not determine account email")
        return email

    def _normalise(self, tok: Dict[str, Any], *, email: str, previous: Optional[Dict[str, Any]] = None) -> TokenSet:
        prev = previous or {}
        expires_in = int(tok.get("expires_in") or 3600)
        return TokenSet(
            email=email,
            access_token=tok.get("access_token", ""),
            refresh_token=tok.get("refresh_token") or prev.get("refresh_token", ""),  # refresh responses omit it
            token_type=tok.get("token_type", "Bearer"),
            scopes=(tok.get("scope") or " ".join(prev.get("scopes") or self.scopes)).split(),
            expires_at=self._clock() + expires_in,
        )

    # ── Accounts / refresh ─────────────────────────────────────
    def accounts(self) -> List[str]:
        return self.store.accounts()

    def _pick_account(self, email: Optional[str]) -> str:
        if email:
            return email.strip().lower()
        accts = self.accounts()
        if not accts:
            raise NotConnectedError("no account connected")
        return accts[0]

    def is_expired(self, tokens: Dict[str, Any]) -> bool:
        return float(tokens.get("expires_at") or 0) - _REFRESH_MARGIN_S <= self._clock()

    async def refresh(self, email: Optional[str] = None) -> TokenSet:
        email = self._pick_account(email)
        tokens = self.store.load(email)
        if not tokens.get("refresh_token"):
            raise OAuthError("no refresh token stored; reconnect the account")
        async with self._http() as http:
            resp = await http.post(self.endpoints["token_uri"], data={
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            })
            tok = _json_or_error(resp, "token refresh")
        fresh = self._normalise(tok, email=email, previous=tokens)
        self.store.save(email, fresh)
        return fresh

    async def get_tokens(self, email: Optional[str] = None) -> TokenSet:
        email = self._pick_account(email)
        tokens = self.store.load(email)
        if self.is_expired(tokens):
            tokens = await self.refresh(email)
        return tokens

    async def get_client(self, email: Optional[str] = None, service: str = "gmail", version: str = "v1",
                         require_scopes: Optional[List[str]] = None):
        """Ready-to-use API client for a connected account (refreshes first if expired).

        ``require_scopes``: raise MissingScopeError (instead of failing later with a 403)
        if the account was connected before these scopes were requested.
        """
        tokens = await self.get_tokens(email)
        granted = set(tokens.get("scopes") or [])
        missing = [s for s in (require_scopes or []) if s not in granted]
        if missing:
            raise MissingScopeError("account needs reconnecting to grant: " + ", ".join(missing))
        return await asyncio.to_thread(self._client_builder, tokens, self.client_id, self._client_secret,
                                       self.endpoints["token_uri"], service, version)

    async def disconnect(self, email: Optional[str] = None) -> bool:
        """Revoke at the provider (best effort) and delete the local encrypted token."""
        email = self._pick_account(email)
        try:
            tokens = self.store.load(email)
        except NotConnectedError:
            return False
        except OAuthError:
            tokens = {}
        revoke_with = tokens.get("refresh_token") or tokens.get("access_token")
        if revoke_with:
            try:
                async with self._http() as http:
                    await http.post(self.endpoints["revoke_uri"], data={"token": revoke_with})
            except httpx.HTTPError as exc:
                print(f"[oauth] revoke request failed ({type(exc).__name__}); deleting local token anyway")
        return self.store.delete(email)


def _json_or_error(resp: httpx.Response, what: str) -> Dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.status_code >= 400:
        code = body.get("error") if isinstance(body, dict) else None
        raise OAuthError(f"{what} failed (HTTP {resp.status_code}{', ' + str(code) if code else ''})")
    return body if isinstance(body, dict) else {}


def _build_google_service(tokens: Dict[str, Any], client_id: str, client_secret: str, token_uri: str,
                          service: str, version: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=tokens.get("scopes"),
    )
    return build(service, version, credentials=creds, cache_discovery=False)


def google_connector_from_config(config: Dict[str, Any], scopes: Optional[List[str]] = None, **kw) -> OAuthConnector:
    """Build the shared Google connector from config.yaml values (install-level client)."""
    return OAuthConnector(
        "google",
        str(config.get("google_oauth_client_id") or ""),
        str(config.get("google_oauth_client_secret") or ""),
        scopes or GOOGLE_SCOPES,
        **kw,
    )
