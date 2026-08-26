"""Rate-limit key tests — the key must be something the client cannot choose.

No FastAPI import: webapp/client_ip.py is deliberately stdlib-only so this runs
without the API extras installed.
"""

import pytest

from webapp.client_ip import TRUSTED_HEADER_ENV, client_ip, trusted_header

FLY = {TRUSTED_HEADER_ENV: "fly-client-ip"}
NGINX = {TRUSTED_HEADER_ENV: "x-forwarded-for"}


# --- default posture: trust nothing off the wire -------------------------------

@pytest.mark.parametrize("header", ["x-forwarded-for", "fly-client-ip", "x-real-ip"])
def test_no_header_is_trusted_by_default(header):
    """The regression this module exists for: a spoofed header must not key."""
    assert client_ip({header: "1.2.3.4"}, "9.9.9.9", {}) == "9.9.9.9"


def test_spoofing_cannot_mint_fresh_buckets():
    """Rotating a header per request must not change the key."""
    keys = {
        client_ip({"x-forwarded-for": f"203.0.113.{n}"}, "9.9.9.9", {})
        for n in range(1, 20)
    }
    assert keys == {"9.9.9.9"}


def test_empty_env_value_means_no_trust():
    assert trusted_header({TRUSTED_HEADER_ENV: "   "}) is None
    assert client_ip({"fly-client-ip": "1.2.3.4"}, "9.9.9.9", {TRUSTED_HEADER_ENV: ""}) == "9.9.9.9"


# --- opted-in proxy header -----------------------------------------------------

def test_configured_header_is_used():
    assert client_ip({"fly-client-ip": "1.2.3.4"}, "9.9.9.9", FLY) == "1.2.3.4"


def test_header_lookup_is_case_insensitive():
    assert client_ip({"Fly-Client-IP": "1.2.3.4"}, "9.9.9.9", FLY) == "1.2.3.4"


def test_only_the_configured_header_is_read():
    """Configuring one header must not quietly re-trust the others."""
    headers = {"x-forwarded-for": "1.2.3.4", "fly-client-ip": "5.6.7.8"}
    assert client_ip(headers, "9.9.9.9", FLY) == "5.6.7.8"
    assert client_ip(headers, "9.9.9.9", NGINX) == "1.2.3.4"


def test_leftmost_entry_of_a_list():
    assert client_ip({"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, "9.9.9.9", NGINX) == "1.2.3.4"


def test_ipv6_is_accepted():
    assert client_ip({"fly-client-ip": "2001:db8::1"}, "9.9.9.9", FLY) == "2001:db8::1"


# --- guards on the opted-in path -----------------------------------------------

@pytest.mark.parametrize("value", ["", "   ", "not-an-ip", "1.2.3.4.5", "<script>", "999.1.1.1"])
def test_unparseable_header_falls_back_to_peer(value):
    """An edge that failed to set the header leaves it forgeable again."""
    assert client_ip({"fly-client-ip": value}, "9.9.9.9", FLY) == "9.9.9.9"


def test_missing_header_falls_back_to_peer():
    assert client_ip({}, "9.9.9.9", FLY) == "9.9.9.9"


def test_no_peer_and_no_header_is_a_constant_not_a_crash():
    assert client_ip({}, None, {}) == "unknown"
