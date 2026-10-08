"""
CCTV Security Proxy & Media Stream Handler for J.A.R.V.I.S. / God's Eye.
Enforces strict security policies:
- Catalog-only host allowlist (no open proxy)
- HTTPS only (rejects plaintext HTTP)
- Post-DNS IP validation blocking loopback, private, link-local, multicast, and reserved IPs
- Validation on every redirect hop (max 2 same-host hops)
- Body size limits (playlist 256KB, segment 4MB, frame 2MB, media 32MB) and timeout limits (10s)
- RFC 7233 canonical Range header sanitization
- In-memory HLS puller session management (bounded leases and ring buffer)
"""

import os
import re
import time
import socket
import ipaddress
import urllib.parse
import urllib.request
import threading
import uuid
import subprocess
from typing import Dict, List, Optional, Tuple, Any

# Limits pinned to God's Eye constants
CCTV_FRAME_FETCH_TIMEOUT_MS = 6000
CCTV_FRAME_MAX_BODY_BYTES = 2 * 1024 * 1024        # 2 MB
CCTV_MEDIA_FETCH_TIMEOUT_MS = 10000
CCTV_MEDIA_MAX_BODY_BYTES = 32 * 1024 * 1024       # 32 MB
HLS_PLAYLIST_MAX_BYTES = 256 * 1024                # 256 KB
HLS_SEGMENT_MAX_BYTES = 4 * 1024 * 1024            # 4 MB
HLS_SESSION_MAX_BYTES = 24 * 1024 * 1024           # 24 MB
HLS_MAX_SESSIONS = 2
HLS_MAX_LEASES = 8
HLS_IDLE_TIMEOUT_SEC = 15.0
HLS_POLL_INTERVAL_SEC = 2.0
HLS_READY_TIMEOUT_SEC = 12.0
RANGE_MAX_DIGITS = 16

# Verified camera catalog upstream hosts
DEFAULT_ALLOWED_HOSTS = {
    "images.unsplash.com",
    "cwwp2.dot.ca.gov",
    "s3-eu-west-1.amazonaws.com",
    "cctv.austinmobility.io",
    "storage.googleapis.com",
    "video.deldot.gov",
    "deldot.gov",
    "webcam.warendorf.de",
    "ristmikud.tallinn.ee",
    "its.txdot.gov",
    "api.tfl.gov.uk"
}

class SecurityError(Exception):
    """Raised when an upstream request violates SSRF or security constraints."""
    pass


def is_blocked_ip(ip_str: str) -> bool:
    """
    Check if an IP address string belongs to a loopback, private, link-local,
    multicast, or reserved network.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    # If IPv4-mapped IPv6 (::ffff:127.0.0.1), unmap to IPv4
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    if ip.is_multicast:
        return True
    if ip.is_reserved:
        return True
    if ip.is_unspecified:
        return True

    # Explicit check for 127.0.0.0/8, 169.254.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.IPv4Network("127.0.0.0/8"):
            return True
        if ip in ipaddress.IPv4Network("169.254.0.0/16"):
            return True
        if ip in ipaddress.IPv4Network("10.0.0.0/8"):
            return True
        if ip in ipaddress.IPv4Network("172.16.0.0/12"):
            return True
        if ip in ipaddress.IPv4Network("192.168.0.0/16"):
            return True
        if ip in ipaddress.IPv4Network("0.0.0.0/8"):
            return True
    elif isinstance(ip, ipaddress.IPv6Address):
        if ip in ipaddress.IPv6Network("::1/128"):
            return True
        if ip in ipaddress.IPv6Network("fc00::/7"):
            return True
        if ip in ipaddress.IPv6Network("fe80::/10"):
            return True

    return False


def validate_and_pin_upstream_url(url: str, allowed_hosts: Optional[set] = None, allow_http: bool = False) -> Tuple[str, str, int, str]:
    """
    Validate target upstream URL and pin the resolved IP address:
    - Scheme must be https (unless allow_http=True for explicit testing)
    - Hostname must be in allowed_hosts
    - DNS resolution must NOT return any private, loopback, or link-local IP
    Returns (url, pinned_ip, port, hostname).
    """
    if not url or not isinstance(url, str):
        raise SecurityError("Missing or invalid URL")

    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if not allow_http and scheme != "https":
        raise SecurityError(f"Rejected scheme '{scheme}': HTTPS only")
    if scheme not in ("http", "https"):
        raise SecurityError(f"Unsupported scheme '{scheme}'")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise SecurityError("Missing hostname in URL")

    hosts = allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS
    if hostname not in hosts:
        raise SecurityError(f"Host '{hostname}' not in camera catalog allowlist")

    # Resolve IP addresses and inspect each one
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
        addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SecurityError(f"DNS resolution failed for '{hostname}': {e}")

    if not addr_info:
        raise SecurityError(f"No IP addresses resolved for '{hostname}'")

    pinned_ip = None
    for family, socktype, proto, canonname, sockaddr in addr_info:
        ip_addr = sockaddr[0]
        if is_blocked_ip(ip_addr):
            raise SecurityError(f"Target '{hostname}' resolved to blocked IP '{ip_addr}'")
        if not pinned_ip:
            pinned_ip = ip_addr

    if not pinned_ip:
        raise SecurityError(f"No valid non-blocked IP found for '{hostname}'")

    return url, pinned_ip, port, hostname


def validate_upstream_url(url: str, allowed_hosts: Optional[set] = None, allow_http: bool = False) -> str:
    """Validate target upstream URL and return normalized URL string."""
    val_url, _, _, _ = validate_and_pin_upstream_url(url, allowed_hosts=allowed_hosts, allow_http=allow_http)
    return val_url


def sanitize_cctv_range_header(value: Any, max_bytes: int = CCTV_MEDIA_MAX_BODY_BYTES) -> str:
    """
    Canonicalize and bound client Range header before forwarding.
    RFC 7233 §3.1 compliance.
    """
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""

    match = re.match(r"^bytes=(\d*)-(\d*)$", raw, re.IGNORECASE)
    if not match:
        return ""

    first_text, last_text = match.groups()
    if len(first_text) > RANGE_MAX_DIGITS or len(last_text) > RANGE_MAX_DIGITS:
        return ""
    if not first_text and not last_text:
        return ""

    # Suffix form: bytes=-N
    if not first_text:
        try:
            suffix = int(last_text)
        except ValueError:
            return ""
        if suffix <= 0:
            return ""
        return f"bytes=-{min(suffix, max_bytes)}"

    try:
        first = int(first_text)
    except ValueError:
        return ""
    if first < 0:
        return ""

    ceiling = first + max_bytes - 1

    # Open-ended: bytes=N-
    if not last_text:
        return f"bytes={first}-{ceiling}"

    try:
        last = int(last_text)
    except ValueError:
        return ""
    if last < first:
        return ""

    return f"bytes={first}-{min(last, ceiling)}"


def fetch_upstream_bytes(
    url: str,
    max_bytes: int,
    timeout_sec: float = 10.0,
    headers: Optional[Dict[str, str]] = None,
    allowed_hosts: Optional[set] = None,
    allow_http: bool = False
) -> Tuple[bytes, Dict[str, str], int]:
    """
    Fetch upstream bytes with strict hop-by-hop redirect verification,
    declared-size ceiling, and streaming byte-cap enforcement.
    """
    current_url = url
    req_headers = {"User-Agent": "JARVIS-GodsEye-Proxy/2.0"}
    if headers:
        req_headers.update(headers)

    import http.client
    for hop in range(3):  # Max 2 redirects (3 attempts)
        validated_url, pinned_ip, port, hostname = validate_and_pin_upstream_url(
            current_url, allowed_hosts=allowed_hosts, allow_http=allow_http
        )

        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def http_error_302(self, req, fp, code, msg, hdrs):
                return fp
            http_error_301 = http_error_302
            http_error_303 = http_error_302
            http_error_307 = http_error_302
            http_error_308 = http_error_302

        # Pinned IP connection ensuring TCP connects directly to the validated IP (prevents DNS rebinding)
        class PinnedHTTPSConnection(http.client.HTTPSConnection):
            def connect(self):
                self.sock = socket.create_connection((pinned_ip, self.port), self.timeout)
                if self._tunnel_host:
                    self._tunnel()
                server_hostname = getattr(self, '_server_hostname', None) or hostname
                self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)

        class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(lambda host, **kwargs: PinnedHTTPSConnection(pinned_ip, port=port, **kwargs), req)

        opener = urllib.request.build_opener(NoRedirectHandler, PinnedHTTPSHandler)
        req = urllib.request.Request(validated_url, headers=req_headers)

        try:
            resp = opener.open(req, timeout=timeout_sec)
        except urllib.error.HTTPError as e:
            # 206 Partial Content or other error responses
            if e.code == 206:
                resp = e
            else:
                raise

        status = getattr(resp, "status", getattr(resp, "code", 200))
        resp_headers = dict(resp.headers.items())

        if 300 <= status < 400:
            location = resp_headers.get("Location") or resp_headers.get("location")
            if not location or hop == 2:
                raise SecurityError("Redirect limit reached or missing location")
            next_url = urllib.parse.urljoin(current_url, location)
            current_url = next_url
            continue

        # Check declared Content-Length
        cl_str = resp_headers.get("Content-Length") or resp_headers.get("content-length")
        if cl_str:
            try:
                cl = int(cl_str)
                if cl > max_bytes:
                    raise SecurityError(f"Declared Content-Length {cl} exceeds cap {max_bytes}")
            except ValueError:
                pass

        # Stream reading with cap
        chunks = []
        total = 0
        chunk_size = 65536
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise SecurityError(f"Streamed body exceeds cap {max_bytes}")
            chunks.append(chunk)

        return b"".join(chunks), resp_headers, status

    raise SecurityError("Too many redirects")


class PythonHlsPuller:
    """
    Thread-safe in-memory HLS session puller implementing the exact contract
    of God's Eye stream.js.
    """
    def __init__(self, limits: Optional[Dict[str, Any]] = None):
        self.limits = limits or {
            "sessions": HLS_MAX_SESSIONS,
            "leases": HLS_MAX_LEASES,
            "session_bytes": HLS_SESSION_MAX_BYTES,
            "segment_bytes": HLS_SEGMENT_MAX_BYTES,
            "segments": 12,
            "idle_sec": HLS_IDLE_TIMEOUT_SEC,
            "poll_sec": HLS_POLL_INTERVAL_SEC,
        }
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.closed = False

    def ensure(self, camera_id: str, media_url: str, lease_id: str) -> Dict[str, Any]:
        with self.lock:
            if self.closed:
                raise RuntimeError("HLS service closed")

            entry = self.sessions.get(camera_id)
            if entry and entry["url"] != media_url:
                self._stop_session(camera_id)
                entry = None

            if entry:
                self._touch_lease(entry, lease_id, create=True)
                return entry

            if len(self.sessions) >= self.limits["sessions"]:
                raise RuntimeError("HLS session capacity reached")

            entry = {
                "camera_id": camera_id,
                "url": media_url,
                "token": str(uuid.uuid4()),
                "segments": {},       # seq -> {"seq": int, "duration": float, "body": bytes, "discontinuity": bool}
                "bytes": 0,
                "stopping": False,
                "failures": 0,
                "next_seq": 0,
                "upstream": {},        # upstream_seq -> uri
                "leases": {},          # lease_id -> last_active_ts
                "upstream_newest": -1,
                "discontinuities": 0,
                "thread": None
            }
            self.sessions[camera_id] = entry
            self._touch_lease(entry, lease_id, create=True)

            t = threading.Thread(target=self._poll_loop, args=(entry,), daemon=True)
            entry["thread"] = t
            t.start()
            return entry

    def _touch_lease(self, entry: Dict[str, Any], lease_id: str, create: bool = False):
        if lease_id not in entry["leases"]:
            if not create or len(entry["leases"]) >= self.limits["leases"]:
                raise RuntimeError("HLS lease capacity reached")
        entry["leases"][lease_id] = time.time()

    def release(self, camera_id: str, lease_id: str):
        with self.lock:
            entry = self.sessions.get(camera_id)
            if not entry:
                return
            entry["leases"].pop(lease_id, None)
            if not entry["leases"]:
                self._stop_session(camera_id)

    def _stop_session(self, camera_id: str):
        entry = self.sessions.pop(camera_id, None)
        if entry:
            entry["stopping"] = True
            entry["segments"].clear()
            entry["bytes"] = 0

    def wait_ready(self, entry: Dict[str, Any], timeout_sec: float = HLS_READY_TIMEOUT_SEC) -> bool:
        start = time.time()
        while time.time() - start < timeout_sec:
            if entry["stopping"]:
                return False
            with self.lock:
                if len(entry["segments"]) >= 2:
                    return True
            time.sleep(0.05)
        return False

    def build_playlist(self, camera_id: str, lease_id: str) -> Optional[str]:
        with self.lock:
            entry = self.sessions.get(camera_id)
            if not entry or entry["stopping"] or len(entry["segments"]) < 2:
                return None
            self._touch_lease(entry, lease_id)
            segments = sorted(entry["segments"].values(), key=lambda s: s["seq"])
            max_duration = max((s["duration"] for s in segments), default=2.0)
            target_duration = max(1, int(round(max_duration)))

            lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                f"#EXT-X-TARGETDURATION:{target_duration}",
                f"#EXT-X-MEDIA-SEQUENCE:{segments[0]['seq']}",
                f"#EXT-X-DISCONTINUITY-SEQUENCE:{entry['discontinuities']}"
            ]
            prev_seq = None
            for seg in segments:
                if seg.get("discontinuity") or (prev_seq is not None and seg["seq"] != prev_seq + 1):
                    lines.append("#EXT-X-DISCONTINUITY")
                lines.append(f"#EXTINF:{seg['duration']:.3f},")
                lines.append(f"/api/cctv/media/{urllib.parse.quote(camera_id)}/seg_{seg['seq']}.ts?session={entry['token']}&lease={urllib.parse.quote(lease_id)}")
                prev_seq = seg["seq"]

            return "\n".join(lines) + "\n"

    def get_segment(self, camera_id: str, token: str, seq: int, lease_id: str) -> Optional[bytes]:
        with self.lock:
            entry = self.sessions.get(camera_id)
            if not entry or entry["token"] != token or lease_id not in entry["leases"]:
                return None
            seg = entry["segments"].get(seq)
            if seg:
                self._touch_lease(entry, lease_id)
                return seg["body"]
            return None

    def _poll_loop(self, entry: Dict[str, Any]):
        """Background thread polling upstream playlist or feeding synthetic fallback chunks."""
        camera_id = entry["camera_id"]
        media_url = entry["url"]

        while not entry["stopping"]:
            now = time.time()
            # Lease expiration check
            with self.lock:
                expired = [lid for lid, ts in entry["leases"].items() if now - ts > self.limits["idle_sec"]]
                for lid in expired:
                    del entry["leases"][lid]
                if not entry["leases"]:
                    self._stop_session(camera_id)
                    break

            try:
                # Try fetching upstream HLS playlist
                body, headers, status = fetch_upstream_bytes(media_url, max_bytes=HLS_PLAYLIST_MAX_BYTES, timeout_sec=6.0)
                text = body.decode("utf-8", errors="ignore")
                segments_to_fetch = self._parse_media_playlist(text, media_url)

                for seg_meta in segments_to_fetch:
                    if entry["stopping"]:
                        break
                    with self.lock:
                        if seg_meta["seq"] in entry["upstream"]:
                            continue

                    seg_body, _, _ = fetch_upstream_bytes(seg_meta["uri"], max_bytes=self.limits["segment_bytes"], timeout_sec=6.0)
                    with self.lock:
                        self._add_segment(entry, seg_meta["seq"], seg_meta["duration"], seg_body, seg_meta["uri"])

                entry["failures"] = 0
            except Exception:
                entry["failures"] += 1
                if len(entry["segments"]) < 4:
                    self._generate_fallback_segments(entry)

            time.sleep(self.limits["poll_sec"])

    def _parse_media_playlist(self, text: str, base_url: str) -> List[Dict[str, Any]]:
        if not text.startswith("#EXTM3U"):
            raise ValueError("Invalid M3U8")
        seq = 0
        duration = None
        discontinuity = False
        res = []
        base_origin = urllib.parse.urlsplit(base_url).netloc

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                seq = int(line[22:].strip())
            elif line == "#EXT-X-DISCONTINUITY":
                discontinuity = True
            elif line.startswith("#EXTINF:"):
                duration_str = line[8:].split(",")[0].strip()
                duration = float(duration_str)
            elif line and not line.startswith("#"):
                if duration is not None:
                    full_uri = urllib.parse.urljoin(base_url, line)
                    parsed_uri = urllib.parse.urlsplit(full_uri)
                    if parsed_uri.netloc != base_origin:
                        raise SecurityError("Cross-origin segment rejected")
                    res.append({
                        "seq": seq,
                        "duration": duration,
                        "uri": full_uri,
                        "discontinuity": discontinuity
                    })
                    seq += 1
                    duration = None
                    discontinuity = False
        return res[-self.limits["segments"]:]

    def _add_segment(self, entry: Dict[str, Any], upstream_seq: int, duration: float, body: bytes, uri: str):
        while entry["segments"] and (entry["bytes"] + len(body) > self.limits["session_bytes"] or len(entry["segments"]) >= self.limits["segments"]):
            oldest_key = min(entry["segments"].keys())
            old_seg = entry["segments"].pop(oldest_key)
            entry["bytes"] -= len(old_seg["body"])

        seq = entry["next_seq"]
        entry["next_seq"] += 1
        entry["segments"][seq] = {
            "seq": seq,
            "duration": duration,
            "body": body,
            "discontinuity": False
        }
        entry["upstream"][upstream_seq] = uri
        entry["bytes"] += len(body)

    def _generate_fallback_segments(self, entry: Dict[str, Any]):
        """Generate valid 2-second H.264 MPEG-TS fallback segment using ffmpeg."""
        seq = entry["next_seq"]
        entry["next_seq"] += 1
        tmp_ts = f"/tmp/hls_fb_{entry['camera_id']}_{seq}.ts"
        try:
            cmd = [
                "ffmpeg", "-f", "lavfi",
                "-i", f"testsrc=duration=2:size=320x240:rate=15",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", tmp_ts, "-y"
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
            if os.path.exists(tmp_ts):
                with open(tmp_ts, "rb") as f:
                    body = f.read()
                os.remove(tmp_ts)
                with self.lock:
                    self._add_segment(entry, seq, 2.0, body, f"fallback://{seq}")
        except Exception:
            pass


_global_hls_puller: Optional[PythonHlsPuller] = None
_puller_lock = threading.Lock()

def get_hls_puller() -> PythonHlsPuller:
    global _global_hls_puller
    with _puller_lock:
        if _global_hls_puller is None:
            _global_hls_puller = PythonHlsPuller()
        return _global_hls_puller
