"""SSRF guard tests — literal IPs only, so no DNS/network is required."""

import pytest

from webapp.ssrf import UnsafeURLError, validate_public_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://127.0.0.1/",
    "http://localhost/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://100.64.0.1/",            # CGNAT
    "http://[::1]/",                 # IPv6 loopback
    "http://0.0.0.0/",
    "https://[::ffff:127.0.0.1]/",   # IPv4-mapped IPv6 loopback
    "file:///etc/passwd",            # non-http scheme
    "ftp://example.com/",            # non-http scheme
    "http:///nohost",                # missing host
])
def test_blocks_unsafe(url):
    with pytest.raises(UnsafeURLError):
        validate_public_url(url)


@pytest.mark.parametrize("url", [
    "http://93.184.216.34/",   # public literal IPv4
    "https://1.1.1.1/",
    "http://8.8.8.8/",
])
def test_allows_public(url):
    assert validate_public_url(url) == url
