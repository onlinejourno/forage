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
"""

import base64
import binascii
import hashlib
import hmac
import re
from collections.abc import Mapping


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
    method: str, target_uri: str, body: bytes | str, headers: Mapping[str, str], key: str
) -> bool:
    """True only for a request that carries a valid signature under ``key``.

    Fails closed on every missing piece, and on an empty key: an unset
    FORAGE_STRINGER_KEY must refuse everyone rather than admit whoever signs
    with the empty string.
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

    provided = _parse(signature, "stringer")
    if provided is None:
        return False

    base = "\n".join([
        f"@method: {method.upper()}",
        f"@target-uri: {target_uri}",
        f"content-digest: {content_digest}",
    ])
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
    m = re.search(rf"{label}=:([^:]+):", str(header))
    return m.group(1) if m else None
