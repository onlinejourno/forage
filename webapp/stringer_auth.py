"""Stringer HMAC-SHA256 signature verification.

Written here rather than imported, deliberately: Forage is MIT and must stay
installable and auditable on its own, without pulling in anything from a
private repository. Stdlib only. The wire profile is small and frozen, and
Tare (also MIT) carries the same forty lines in JavaScript — what keeps the
implementations honest is that they all sign the same base string, and that a
real call is made between them before any of this is called done.

    Signature-Input: stringer=(@method @target-uri content-digest);keyid="<id>";alg="hmac-sha256";created=<ts>
    Signature:       stringer=:<base64-hmac>:
    Content-Digest:  sha-256=<base64-sha256-of-body>

signed over:

    @method: <METHOD>
    @target-uri: <FULL URL>
    content-digest: <CONTENT-DIGEST>

Signed ``created``. That base does not cover
``created``, so a captured request replays until the key is revoked. Current
Stringer clients send a second member beside the first:

    Signature-Input: stringer=<params>, stringer-v2=<params>
    Signature:       stringer=:<v1>:, stringer-v2=:<v2>:

whose base is the three lines above plus ``@signature-params: <params>``, so
keyid and created are signed. A request naming ``stringer-v2`` is judged by it
alone, never falling back to the first member, and refused outside MAX_AGE /
MAX_SKEW. A request without it (older clients) is still accepted while sites
upgrade, unless STRINGER_REQUIRE_SIGNED_CREATED=1.
"""

import base64
import binascii
import hashlib
import hmac
import os
import re
import time
from collections.abc import Mapping

# Oldest a v2 request may be, and how far ahead of our clock its ``created``
# may sit, in seconds. The client signs afresh on every attempt.
MAX_AGE = 300
MAX_SKEW = 60

# The v2 member's parameters exactly as the clients write them. Strict on
# purpose: this text is signed verbatim, so anything else is refused.
_V2_INPUT = re.compile(
    r'(?:^|,\s*)stringer-v2=(\(@method @target-uri content-digest\);'
    r'keyid="([^"]*)";alg="hmac-sha256";created=(\d{1,12}))\s*(?:,|$)'
)
_V2_NAMED = re.compile(r"(?:^|,\s*)stringer-v2=")


def target_uri_for(headers: Mapping[str, str], scheme: str, netloc: str, path_qs: str) -> str:
    """The URL the caller addressed, which is what it signed.

    ``scheme`` and ``netloc`` describe how this process was reached. A
    TLS-terminating proxy reaches it over http:// while the caller signed
    https://, so verifying against that rejects every correctly signed request
    -- and the resulting 401 is indistinguishable from a wrong key.

    The forwarded headers are caller-supplied and that buys an attacker
    nothing: whichever URL they name still has to carry a valid HMAC under the
    site key.
    """
    get = _getter(headers)
    host = get("x-forwarded-host") or get("host") or netloc
    proto = get("x-forwarded-proto") or scheme or "https"
    return f"{proto}://{host}{path_qs}"


def verify_stringer_signature(
    method: str,
    target_uri: str,
    body: bytes | str,
    headers: Mapping[str, str],
    key: str,
    now: float | None = None,
    keyid: str | None = None,
) -> bool:
    """True only for a request that carries a valid signature under ``key``.

    Fails closed on every missing piece, and on an empty key: an unset
    FORAGE_STRINGER_KEY must refuse everyone rather than admit whoever signs
    with the empty string. ``keyid``, when given, is the only keyid a v2
    signature may name.
    """
    if not key:
        return False

    get = _getter(headers)
    signature_input = get("signature-input")
    signature = get("signature")
    content_digest = get("content-digest")
    if not signature_input or not signature or not content_digest:
        return False

    raw = body.encode() if isinstance(body, str) else (body or b"")
    expected_digest = "sha-256=" + base64.b64encode(hashlib.sha256(raw).digest()).decode()
    if not hmac.compare_digest(content_digest, expected_digest):
        return False

    base = "\n".join([
        f"@method: {method.upper()}",
        f"@target-uri: {target_uri}",
        f"content-digest: {content_digest}",
    ])

    if _V2_NAMED.search(signature_input):
        params = _V2_INPUT.search(signature_input)
        provided = _parse(signature, "stringer-v2")
        if not params or provided is None:
            return False
        if not _same(key, f"{base}\n@signature-params: {params.group(1)}", provided):
            return False
        if keyid is not None and params.group(2) != keyid:
            return False
        age = (time.time() if now is None else now) - int(params.group(3))
        return -MAX_SKEW <= age <= MAX_AGE

    if os.environ.get("STRINGER_REQUIRE_SIGNED_CREATED") == "1":
        return False
    provided = _parse(signature, "stringer")
    return provided is not None and _same(key, base, provided)


def _same(key: str, base: str, provided: str) -> bool:
    expected = hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()
    try:
        given = base64.b64decode(provided, validate=True)
    except (binascii.Error, ValueError):
        return False
    return hmac.compare_digest(expected, given)


def _getter(headers: Mapping[str, str]):
    lowered = {str(k).lower(): v for k, v in headers.items()}
    return lambda name: lowered.get(name.lower())


def _parse(header: str, label: str) -> str | None:
    m = re.search(rf"(?:^|,\s*){re.escape(label)}=:([^:]+):", str(header))
    return m.group(1) if m else None
