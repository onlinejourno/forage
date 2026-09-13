"""Stringer signature verification — the gate on the hub's read-only endpoint.

No FastAPI import: webapp/stringer_auth.py is stdlib-only so this runs without
the API extras installed, and so a self-hoster can audit it in isolation.
"""

import base64
import hashlib
import hmac

from webapp.stringer_auth import target_uri_for, verify_stringer_signature

KEY = "test-site-key"
URL = "https://prowl-api.example/stringer/forage/mismatch"


def _digest(body: str) -> str:
    return "sha-256=" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()


def _sign(method: str, url: str, body: str = "", key: str = KEY) -> dict:
    digest = _digest(body)
    base = f"@method: {method.upper()}\n@target-uri: {url}\ncontent-digest: {digest}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
    return {
        "signature-input": 'stringer=(@method @target-uri content-digest);keyid="site-key-v1";alg="hmac-sha256";created=1',
        "signature": f"stringer=:{sig}:",
        "content-digest": digest,
    }


def test_valid_signature_is_accepted():
    assert verify_stringer_signature("GET", URL, "", _sign("GET", URL), KEY)


def test_unset_key_refuses_everyone():
    """An empty key must not admit whoever signs with the empty string."""
    assert not verify_stringer_signature("GET", URL, "", _sign("GET", URL, key=""), "")


def test_wrong_key_is_refused():
    assert not verify_stringer_signature("GET", URL, "", _sign("GET", URL, key="other"), KEY)


def test_signature_is_bound_to_the_target_uri():
    """A signature valid for one URL cannot be replayed against another."""
    headers = _sign("GET", "https://prowl-api.example/stringer/forage/other")
    assert not verify_stringer_signature("GET", URL, "", headers, KEY)


def test_tampered_digest_is_refused():
    headers = _sign("GET", URL)
    headers["content-digest"] = _digest("tampered")
    assert not verify_stringer_signature("GET", URL, "", headers, KEY)


def test_missing_headers_are_refused():
    for dropped in ("signature-input", "signature", "content-digest"):
        headers = _sign("GET", URL)
        del headers[dropped]
        assert not verify_stringer_signature("GET", URL, "", headers, KEY), dropped


def test_garbage_signature_does_not_raise():
    headers = _sign("GET", URL)
    headers["signature"] = "stringer=:not base64!!:"
    assert not verify_stringer_signature("GET", URL, "", headers, KEY)


def test_target_uri_prefers_the_forwarded_address():
    """Behind TLS termination the process is reached over http while the caller
    signed https. Verify the URL the caller ADDRESSED."""
    uri = target_uri_for(
        {"x-forwarded-proto": "https", "x-forwarded-host": "prowl-api.example", "host": "0.0.0.0:8080"},
        "http",
        "0.0.0.0:8080",
        "/stringer/forage/mismatch",
    )
    assert uri == URL


def test_target_uri_falls_back_to_the_bound_address():
    uri = target_uri_for({"host": "localhost:8080"}, "http", "localhost:8080", "/x")
    assert uri == "http://localhost:8080/x"
