
import pytest
import socket
from unittest.mock import patch, MagicMock
from modules.cctv_security_proxy import (
    is_blocked_ip,
    validate_upstream_url,
    sanitize_cctv_range_header,
    fetch_upstream_bytes,
    PythonHlsPuller,
    SecurityError,
    DEFAULT_ALLOWED_HOSTS,
    CCTV_MEDIA_MAX_BODY_BYTES
)

def test_is_blocked_ip():
    assert is_blocked_ip('127.0.0.1') is True
    assert is_blocked_ip('127.255.0.1') is True
    assert is_blocked_ip('::1') is True
    assert is_blocked_ip('::ffff:127.0.0.1') is True
    assert is_blocked_ip('10.0.0.1') is True
    assert is_blocked_ip('172.16.0.1') is True
    assert is_blocked_ip('192.168.1.1') is True
    assert is_blocked_ip('169.254.169.254') is True
    assert is_blocked_ip('fe80::1') is True
    assert is_blocked_ip('fc00::1') is True
    assert is_blocked_ip('224.0.0.1') is True
    assert is_blocked_ip('ff02::1') is True
    assert is_blocked_ip('0.0.0.0') is True
    assert is_blocked_ip('garbage') is True
    # Public IPs should not be blocked
    assert is_blocked_ip('8.8.8.8') is False
    assert is_blocked_ip('1.1.1.1') is False

def test_reject_non_https():
    with pytest.raises(SecurityError, match='HTTPS only'):
        validate_upstream_url('http://storage.googleapis.com/sample.mp4')
    with pytest.raises(SecurityError, match='HTTPS only'):
        validate_upstream_url('ftp://storage.googleapis.com/sample.mp4')
    with pytest.raises(SecurityError, match='HTTPS only'):
        validate_upstream_url('file:///etc/passwd')

def test_reject_non_allowlisted_host():
    with pytest.raises(SecurityError, match='not in camera catalog allowlist'):
        validate_upstream_url('https://evil-attacker.com/stream.m3u8')
    with pytest.raises(SecurityError, match='not in camera catalog allowlist'):
        validate_upstream_url('https://google.com/malicious')

def test_reject_loopback_dns_resolution():
    with patch('socket.getaddrinfo') as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with pytest.raises(SecurityError, match='resolved to blocked IP'):
            validate_upstream_url('https://storage.googleapis.com/test.mp4')

def test_reject_private_dns_resolution():
    for private_ip in ['10.1.2.3', '192.168.0.100', '172.20.0.5']:
        with patch('socket.getaddrinfo') as mock_dns:
            mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (private_ip, 443))]
            with pytest.raises(SecurityError, match='resolved to blocked IP'):
                validate_upstream_url('https://storage.googleapis.com/test.mp4')

def test_reject_aws_metadata_dns_resolution():
    with patch('socket.getaddrinfo') as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 443))]
        with pytest.raises(SecurityError, match='resolved to blocked IP'):
            validate_upstream_url('https://storage.googleapis.com/latest/meta-data')

def test_reject_ipv6_loopback_dns_resolution():
    with patch('socket.getaddrinfo') as mock_dns:
        mock_dns.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 443, 0, 0))]
        with pytest.raises(SecurityError, match='resolved to blocked IP'):
            validate_upstream_url('https://storage.googleapis.com/test.mp4')

def test_reject_redirect_to_blocked_ip():
    class FakeRedirectResp:
        status = 302
        headers = {'Location': 'https://storage.googleapis.com/redirected'}
        def read(self, *args): return b''

    with patch('urllib.request.build_opener') as mock_opener,          patch('socket.getaddrinfo') as mock_dns:
        # First hop resolves to public, redirect resolves to loopback
        mock_dns.side_effect = [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        ]
        opener_inst = MagicMock()
        opener_inst.open.return_value = FakeRedirectResp()
        mock_opener.return_value = opener_inst

        with pytest.raises(SecurityError, match='resolved to blocked IP'):
            fetch_upstream_bytes('https://storage.googleapis.com/start', max_bytes=1024)

def test_reject_redirect_to_non_allowlisted_host():
    class FakeRedirectResp:
        status = 302
        headers = {'Location': 'https://unauthorized-domain.com/evil'}
        def read(self, *args): return b''

    with patch('urllib.request.build_opener') as mock_opener,          patch('socket.getaddrinfo') as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        opener_inst = MagicMock()
        opener_inst.open.return_value = FakeRedirectResp()
        mock_opener.return_value = opener_inst

        with pytest.raises(SecurityError, match='not in camera catalog allowlist'):
            fetch_upstream_bytes('https://storage.googleapis.com/start', max_bytes=1024)

def test_reject_declared_oversize_body():
    class FakeResp:
        status = 200
        headers = {'Content-Length': '100000000'}
        def read(self, *args): return b''

    with patch('urllib.request.build_opener') as mock_opener,          patch('socket.getaddrinfo') as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        opener_inst = MagicMock()
        opener_inst.open.return_value = FakeResp()
        mock_opener.return_value = opener_inst

        with pytest.raises(SecurityError, match='exceeds cap'):
            fetch_upstream_bytes('https://storage.googleapis.com/huge.bin', max_bytes=1024)

def test_reject_streamed_oversize_body():
    class FakeResp:
        status = 200
        headers = {}
        def read(self, size): return b'A' * size

    with patch('urllib.request.build_opener') as mock_opener,          patch('socket.getaddrinfo') as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        opener_inst = MagicMock()
        opener_inst.open.return_value = FakeResp()
        mock_opener.return_value = opener_inst

        with pytest.raises(SecurityError, match='Streamed body exceeds cap'):
            fetch_upstream_bytes('https://storage.googleapis.com/stream', max_bytes=1024)

def test_sanitize_range_header():
    cap = CCTV_MEDIA_MAX_BODY_BYTES
    assert sanitize_cctv_range_header("bytes=0-10\r\nX-Injected: 1") == ""
    assert sanitize_cctv_range_header("bytes=0-10\nHost: evil") == ""
    assert sanitize_cctv_range_header('bytes=-999999999999') == f'bytes=-{cap}'
    # Rejections:
    assert sanitize_cctv_range_header('bytes=0-1,2-3') == ''
    assert sanitize_cctv_range_header("bytes=0-10\r\nX-Injected: 1") == ""
    assert sanitize_cctv_range_header("bytes=0-10\nHost: evil") == ""
    assert sanitize_cctv_range_header("bytes=0-10\r\nX-Injected: 1") == ""
    assert sanitize_cctv_range_header("bytes=0-10\nHost: evil") == ""
    assert sanitize_cctv_range_header(None) == ''

def test_hls_puller_capacity_limits():
    puller = PythonHlsPuller(limits={'sessions': 1, 'leases': 2, 'session_bytes': 1024, 'segment_bytes': 256, 'segments': 2, 'idle_sec': 10, 'poll_sec': 10})
    with patch('modules.cctv_security_proxy.fetch_upstream_bytes') as mock_fetch:
        mock_fetch.return_value = (b'#EXTM3U', {}, 200)
        e1 = puller.ensure('cam1', 'https://video.deldot.gov/live/1/playlist.m3u8', 'lease1')
        assert e1 is not None
        # Same session allows up to 2 leases
        puller.ensure('cam1', 'https://video.deldot.gov/live/1/playlist.m3u8', 'lease2')
        # Third lease exceeds capacity
        with pytest.raises(RuntimeError, match='HLS lease capacity reached'):
            puller.ensure('cam1', 'https://video.deldot.gov/live/1/playlist.m3u8', 'lease3')
        # Second session exceeds session capacity
        with pytest.raises(RuntimeError, match='HLS session capacity reached'):
            puller.ensure('cam2', 'https://video.deldot.gov/live/2/playlist.m3u8', 'leaseA')
