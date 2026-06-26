"""SSRF guard — only fetch public, http(s) URLs.

Every outbound request in this tool acts on a *user-supplied* host (sitemap,
robots.txt, shallow spider). Without a guard, a request for
``http://169.254.169.254/`` or ``http://10.0.0.5/`` turns the server into a
proxy into its own network. This module validates the scheme and resolves
*every* address the hostname maps to, rejecting any private / loopback /
link-local / reserved / CGNAT target — on the initial URL and on every
redirect hop.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class UnsafeURLError(ValueError):
    """Raised when a URL is not a safe, public http(s) target."""


def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable address — treat as unsafe
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # unwrap ::ffff:127.0.0.1 etc.
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        or ip in _CGNAT
    )


def validate_public_url(raw: str) -> str:
    """Return ``raw`` unchanged if it is a safe public http(s) target.

    Resolves *all* addresses the host maps to, so a split-horizon or
    multi-record host cannot slip one private answer through.
    Raises :class:`UnsafeURLError` otherwise.
    """
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"only http/https allowed, got {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host {host!r}") from exc
    for info in infos:
        if _ip_blocked(info[4][0]):
            raise UnsafeURLError(f"host {host!r} resolves to a non-public address")
    return raw


def safe_get(url: str, *, max_redirects: int = 5, **kwargs) -> requests.Response:
    """``requests.get`` with SSRF validation on the initial URL and every hop.

    Redirects are followed manually so each ``Location`` is re-validated — a
    public host that 30x-redirects to an internal address is rejected.
    """
    kwargs["allow_redirects"] = False
    current = url
    for _ in range(max_redirects + 1):
        validate_public_url(current)
        resp = requests.get(current, **kwargs)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise UnsafeURLError("too many redirects")
