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


# --- Signed `created` ---------------------------------------------------
# The `stringer` member does not sign `created`, so a captured request replays
# until the key is revoked. A `stringer-v2` member signs it too.

V2_PARAMS = '(@method @target-uri content-digest);keyid="{keyid}";alg="hmac-sha256";created={created}'


def _mac(text: str, key: str = KEY) -> str:
    return base64.b64encode(hmac.new(key.encode(), text.encode(), hashlib.sha256).digest()).decode()


def sign_v2(method, url, created, body="", key=KEY, keyid="site-key-v1", signed_created=None):
    """Both members, as current clients send them. ``signed_created`` signs one
    ``created`` while presenting another: a forgery."""
    digest = _digest(body)
    base = f"@method: {method.upper()}\n@target-uri: {url}\ncontent-digest: {digest}"
    params = V2_PARAMS.format(keyid=keyid, created=created)
    signed = V2_PARAMS.format(keyid=keyid, created=created if signed_created is None else signed_created)
    return {
        "signature-input": f"stringer={params}, stringer-v2={params}",
        "signature": f"stringer=:{_mac(base, key)}:, stringer-v2=:{_mac(base + chr(10) + '@signature-params: ' + signed, key)}:",
        "content-digest": digest,
    }


NOW = 1_790_000_000


def test_a_fresh_v2_request_is_accepted():
    assert verify_stringer_signature("GET", URL, "", sign_v2("GET", URL, NOW), KEY, now=NOW)


def test_a_v2_request_with_an_empty_keyid_is_accepted():
    headers = sign_v2("GET", URL, NOW, keyid="")
    assert verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW)


def test_a_replay_older_than_the_window_is_refused():
    headers = sign_v2("GET", URL, NOW - 301)
    assert not verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW)


def test_a_created_too_far_ahead_is_refused():
    headers = sign_v2("GET", URL, NOW + 61)
    assert not verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW)


def test_a_forged_created_is_refused():
    headers = sign_v2("GET", URL, NOW, signed_created=NOW - 3600)
    assert not verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW)


def test_a_broken_v2_member_never_falls_back_to_v1():
    """Each of these still carries a valid ``stringer`` member."""
    stale = sign_v2("GET", URL, NOW - 3600)
    bad_mac = sign_v2("GET", URL, NOW)
    bad_mac["signature"] = bad_mac["signature"].split(", stringer-v2=")[0] + ", stringer-v2=:AAAA:"
    bad_params = sign_v2("GET", URL, NOW)
    bad_params["signature-input"] = bad_params["signature-input"].replace(
        "stringer-v2=(@method", "stringer-v2=(@method @authority"
    )
    for headers in (stale, bad_mac, bad_params):
        assert not verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW)



def test_a_v1_only_request_is_accepted_during_the_rollout(monkeypatch):
    monkeypatch.delenv("STRINGER_REQUIRE_SIGNED_CREATED", raising=False)
    assert verify_stringer_signature("GET", URL, "", _sign("GET", URL), KEY)


def test_a_v1_only_request_is_refused_once_signed_created_is_required(monkeypatch):
    monkeypatch.setenv("STRINGER_REQUIRE_SIGNED_CREATED", "1")
    assert not verify_stringer_signature("GET", URL, "", _sign("GET", URL), KEY)
    assert verify_stringer_signature("GET", URL, "", sign_v2("GET", URL, NOW), KEY, now=NOW)


def test_the_shared_signing_vector_verifies():
    """Shared Stringer signing vector: "POST with JSON body to localhost"."""
    headers = {
        "signature-input": 'stringer=(@method @target-uri content-digest);keyid="site-key-v1";alg="hmac-sha256";created=1790000000, stringer-v2=(@method @target-uri content-digest);keyid="site-key-v1";alg="hmac-sha256";created=1790000000',
        "signature": "stringer=:kAsO15TpYQ69HHoHEg6w/ESPRSgEOpI44KEG5+zDWqc=:, stringer-v2=:RcizfEsfOlBSFaXhyQ87l18QaMONfRZM7XOhqRcuAto=:",
        "content-digest": "sha-256=y56/TdC/n9uyVHikUirHX4dI4WVCnpQ9I89YAV3HLuo=",
    }
    body = '{"claim_key":"health-check","title":"Test"}'
    assert verify_stringer_signature(
        "POST", "http://localhost:8000/brief/", body, headers, "test-key-16-bytes!", now=1790000000
    )


def test_a_v2_keyid_matching_the_configured_one_is_accepted():
    headers = sign_v2("GET", URL, NOW)
    assert verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW, keyid="site-key-v1")


def test_a_v2_keyid_other_than_the_configured_one_is_refused():
    for presented in ("other", ""):
        headers = sign_v2("GET", URL, NOW, keyid=presented)
        assert not verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW, keyid="site-key-v1")


def test_an_empty_keyid_is_accepted_when_configured_empty():
    headers = sign_v2("GET", URL, NOW, keyid="")
    assert verify_stringer_signature("GET", URL, "", headers, KEY, now=NOW, keyid="")
