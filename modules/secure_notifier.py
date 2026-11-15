"""
J.A.R.V.I.S. Secure Multi-Channel Notifier & Zero-Leakage Dispatcher.

Features:
1. Automated PII & Credential Sanitization:
   - Strips API keys, Bearer tokens, JWT tokens, AWS/GitHub/Groq/NVIDIA credentials.
   - Masks private IPv4 subnets (10.x, 192.168.x, 172.16-31.x, 127.0.0.1) and user home directories.
   - Redacts raw secrets, passwords, and emails.
2. Desktop Notifications:
   - Dispatches native Linux notifications via `notify-send` with urgency levels and J.A.R.V.I.S. branding.
3. External SMS & Webhook Alerts:
   - Dispatches critical operational briefs via Twilio SMS or custom Webhook.
   - Enforces strict rate-limiting (default 1 external alert per hour) to prevent spam and operator fatigue.
"""

import os
import re
import time
import shutil
import subprocess
import urllib.request
import urllib.parse
import json
from pathlib import Path
from typing import Dict, Any, Optional

# ── Zero-Leakage Sanitization Rules ──────────────────────────────

_CREDENTIAL_PATTERNS = [
    # API keys with standard prefixes (sk-..., gsk_..., nvapi-...)
    re.compile(r'\b(?:sk|gsk|nvapi)[-_][A-Za-z0-9_-]{16,}\b'),
    # GitHub personal access tokens
    re.compile(r'\bgh[pousr]_[A-Za-z0-9_]{16,}\b'),
    # AWS access keys
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    # JWT tokens
    re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b'),
    # Generic Bearer tokens
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9_.-]{16,}\b'),
    # Secrets / Passwords in key-value format
    re.compile(r'(?i)\b(?:password|passwd|secret|api[_-]?key|token|auth[_-]?token)\s*[:=]\s*["\']?([^"\'\s]{6,})["\']?'),
]

_PRIVATE_IPV4_PATTERNS = [
    re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'),
    re.compile(r'\b(?:192\.168\.\d{1,3}\.\d{1,3})\b'),
    re.compile(r'\b(?:172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'),
    re.compile(r'\b(?:127\.0\.0\.1)\b'),
]

_HOME_PATH_PATTERN = re.compile(r'/home/[a-zA-Z0-9_.-]+')
_EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b')


def sanitize_text(text: str) -> str:
    """
    Sanitize text to guarantee zero credential, token, or PII leakage.
    Replaces sensitive tokens and private identifiers with safe redacted placeholders.
    """
    if not text:
        return ""

    sanitized = text

    # Redact explicit credential matches
    for pat in _CREDENTIAL_PATTERNS:
        sanitized = pat.sub('[REDACTED_CREDENTIAL]', sanitized)

    # Redact private IPv4 addresses
    for pat in _PRIVATE_IPV4_PATTERNS:
        sanitized = pat.sub('[REDACTED_IP]', sanitized)

    # Mask local user directory paths
    sanitized = _HOME_PATH_PATTERN.sub('~', sanitized)

    # Mask email addresses
    def _mask_email(m):
        full = m.group(0)
        parts = full.split('@')
        user = parts[0]
        domain = parts[1] if len(parts) > 1 else ''
        masked_user = (user[0] + '***') if user else '***'
        return f"{masked_user}@{domain}"

    sanitized = _EMAIL_PATTERN.sub(_mask_email, sanitized)

    return sanitized


class SecureNotifier:
    """
    Multi-channel notification engine strictly enforcing security sanitization
    and rate limiting.
    """

    _instance = None

    @classmethod
    def get_instance(cls, config: Optional[Dict[str, Any]] = None):
        if cls._instance is None:
            cls._instance = SecureNotifier(config=config)
        elif config is not None:
            cls._instance._config = config
        return cls._instance

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or self._load_config()
        self._last_sms_time = 0.0
        self._min_sms_interval_sec = 3600.0  # Max 1 SMS per hour default
        self._notify_send_path = shutil.which("notify-send")

    def _load_config(self) -> Dict[str, Any]:
        cfg_path = Path(__file__).parent.parent / "config.yaml"
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path, "r") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

    def send_desktop_notification(
        self,
        title: str,
        message: str,
        urgency: str = "normal",
        timeout_ms: int = 6000
    ) -> bool:
        """
        Deliver a native desktop notification via Linux notify-send.
        Automatically sanitizes message and title before display.
        """
        enabled = self._config.get("desktop_notifications_enabled", True)
        if not enabled:
            return False

        if not self._notify_send_path:
            return False

        safe_title = sanitize_text(title or "J.A.R.V.I.S. Alert")
        safe_message = sanitize_text(message)

        if urgency not in ("low", "normal", "critical"):
            urgency = "normal"

        cmd = [
            self._notify_send_path,
            "-a", "J.A.R.V.I.S.",
            "-u", urgency,
            "-t", str(timeout_ms),
        ]

        # Add icon if available before positional arguments
        banner_path = Path(__file__).parent.parent / "assets" / "jarvis-banner.png"
        if banner_path.exists():
            cmd.extend(["-i", str(banner_path.resolve())])

        cmd.extend([safe_title, safe_message])

        try:
            res = subprocess.run(cmd, check=False, capture_output=True, timeout=5)
            return res.returncode == 0
        except Exception as e:
            print(f"[secure_notifier] Notice sending desktop notification: {e}")
            return False

    def send_sms_alert(
        self,
        message: str,
        to_phone: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Dispatch an SMS alert via Twilio or external Webhook with zero credential leakage
        and rate-limiting protection.
        """
        enabled = self._config.get("sms_alerts_enabled", False)
        target_phone = to_phone or self._config.get("sms_to_phone", "")

        if not (enabled or force) or not target_phone:
            return False

        now = time.time()
        if not force and (now - self._last_sms_time) < self._min_sms_interval_sec:
            remaining = int(self._min_sms_interval_sec - (now - self._last_sms_time))
            print(f"[secure_notifier] SMS rate-limit active. Next alert available in {remaining}s.")
            return False

        safe_message = sanitize_text(message)
        # Limit SMS length for safety and conciseness
        if len(safe_message) > 400:
            safe_message = safe_message[:390] + "..."

        provider = str(self._config.get("sms_provider", "twilio")).lower()
        success = False

        if provider == "twilio":
            success = self._send_twilio_sms(safe_message, target_phone)
        elif provider == "webhook" or self._config.get("notification_webhook_url"):
            success = self._send_webhook_alert(safe_message)

        if success:
            self._last_sms_time = now

        return success

    def _send_twilio_sms(self, message: str, to_phone: str) -> bool:
        sid = (self._config.get("twilio_account_sid") or os.environ.get("TWILIO_ACCOUNT_SID", "")).strip()
        token = (self._config.get("twilio_auth_token") or os.environ.get("TWILIO_AUTH_TOKEN", "")).strip()
        from_phone = (self._config.get("twilio_from_phone") or os.environ.get("TWILIO_FROM_PHONE", "")).strip()

        if not (sid and token and from_phone):
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        data = urllib.parse.urlencode({
            "To": to_phone,
            "From": from_phone,
            "Body": f"[J.A.R.V.I.S.] {message}"
        }).encode("utf-8")

        import base64
        auth_header = "Basic " + base64.b64encode(f"{sid}:{token}".encode("ascii")).decode("ascii")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", auth_header)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            # Do NOT print the exception message if it could contain authorization headers
            print("[secure_notifier] Twilio dispatch notice: Request failed.")
            return False

        return False

    def _send_webhook_alert(self, message: str) -> bool:
        webhook_url = (self._config.get("notification_webhook_url") or os.environ.get("NOTIFICATION_WEBHOOK_URL", "")).strip()
        if not webhook_url:
            return False

        payload = json.dumps({
            "source": "JARVIS",
            "timestamp": time.time(),
            "text": f"[J.A.R.V.I.S.] {message}"
        }).encode("utf-8")

        req = urllib.request.Request(webhook_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def dispatch_alert(
        self,
        title: str,
        message: str,
        urgency: str = "normal",
        external_sms: bool = False
    ) -> Dict[str, bool]:
        """
        Multi-target alert dispatcher: delivers desktop and optional SMS/webhook.
        """
        results = {
            "desktop": self.send_desktop_notification(title, message, urgency=urgency),
            "sms": False
        }

        if external_sms or urgency == "critical":
            results["sms"] = self.send_sms_alert(f"{title}: {message}")

        return results
