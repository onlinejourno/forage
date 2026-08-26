"""Which address a rate limit is keyed on.

The key must be something the CLIENT CANNOT CHOOSE. Any header a browser or a
curl call can set is attacker-supplied: one header value per request mints a
fresh rate-limit bucket, so a limit keyed on it is decorative. The previous
implementation trusted ``x-forwarded-for`` unconditionally and had exactly that
hole; galley closed the same one and wrote down why
(``editorial_optimiser/api.py`` — "one header per curl call would mint a fresh
rate-limit bucket").

Forage is self-hostable, so it cannot simply hardcode one provider's header the
way a Fly-only app can. The rule here instead:

  * By DEFAULT no header is trusted at all and the key is the socket peer
    address, which nothing off the wire can forge. Safe on a bare deploy.
  * A deployment that genuinely sits behind a proxy names that proxy's header in
    ``FORAGE_TRUSTED_CLIENT_IP_HEADER`` (Fly: ``fly-client-ip``; nginx: usually
    ``x-forwarded-for``). Only then is a header consulted.

Two guards on the opted-in path, because "trusted" is a claim about the
deployment, not about the request:

  1. The value must parse as an IP address. An edge that failed to set the
     header leaves it forgeable again; junk in it falls back to the peer rather
     than becoming a bucket key of its own.
  2. Configure only a header the edge OVERWRITES. A proxy that APPENDS (nginx
     ``$proxy_add_x_forwarded_for``) leaves the client's own value leftmost, and
     the leftmost entry is what a list is read as here.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Mapping

TRUSTED_HEADER_ENV = "FORAGE_TRUSTED_CLIENT_IP_HEADER"


def trusted_header(env: Mapping[str, str] | None = None) -> str | None:
    """The header name this deployment trusts, or None to use the peer address."""
    source = os.environ if env is None else env
    name = (source.get(TRUSTED_HEADER_ENV) or "").strip().lower()
    return name or None


def _first_ip(raw: str) -> str | None:
    """The leftmost entry of a header value, if it is a valid IP address."""
    candidate = raw.split(",")[0].strip()
    if not candidate:
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def client_ip(
    headers: Mapping[str, str],
    peer: str | None,
    env: Mapping[str, str] | None = None,
) -> str:
    """The rate-limit key for a request.

    ``headers`` may be any mapping; lookup is case-insensitive. ``peer`` is the
    socket peer address (None when the server cannot report one).
    """
    name = trusted_header(env)
    if name:
        lowered = {str(k).lower(): v for k, v in headers.items()}
        found = _first_ip(lowered.get(name, "") or "")
        if found:
            return found
    return peer or "unknown"
