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

import contextvars
import ipaddress
import json
import os
import socket
from contextlib import contextmanager
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


_HOST_HEADERS_ENV = "FETCH_HOST_HEADERS"

# The headers can be a secret that lets Forage past a site's block. The public
# analyse route runs any site for anyone and returns the result, so it must
# never send them: only an operator run (webapp.operator_run) opens the door.
# A context variable, not a global: a pool thread starts with the default
# context, so a public job can never inherit an operator's open door.
_door_open: contextvars.ContextVar[bool] = contextvars.ContextVar("forage_door_open", default=False)


@contextmanager
def operator_door():
    """Send FETCH_HOST_HEADERS for the duration of an operator run."""
    token = _door_open.set(True)
    try:
        yield
    finally:
        _door_open.reset(token)


def host_headers(url: str) -> dict:
    """Extra request headers for ``url``'s host, from ``FETCH_HOST_HEADERS``.

    A site can sit behind a login (for example Cloudflare Access) and give
    Forage a service token. The setting is JSON mapping a host to the headers
    it needs; the host and its subdomains match. Read on every call, never at
    import. A broken setting raises: silently sending nothing would look
    exactly like the site blocking us. Empty unless inside operator_door().
    """
    if not _door_open.get():
        return {}
    raw = os.environ.get(_HOST_HEADERS_ENV, "").strip()
    if not raw:
        return {}
    try:
        table = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{_HOST_HEADERS_ENV} is not valid JSON") from exc
    if not isinstance(table, dict):
        raise ValueError(f"{_HOST_HEADERS_ENV} must map hosts to headers")  # noqa: TRY004 - one error type for every bad setting
    host = (urlparse(url).hostname or "").lower()
    for key, extra in table.items():
        if not isinstance(extra, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in extra.items()
        ):
            raise ValueError(f"{_HOST_HEADERS_ENV}: headers for {key!r} must be strings")
        key = str(key).lower()
        if host == key or host.endswith("." + key):
            return dict(extra)
    return {}


def safe_get(url: str, *, max_redirects: int = 5, **kwargs) -> requests.Response:
    """``requests.get`` with SSRF validation on the initial URL and every hop.

    Redirects are followed manually so each ``Location`` is re-validated — a
    public host that 30x-redirects to an internal address is rejected.
    """
    kwargs["allow_redirects"] = False
    current = url
    for _ in range(max_redirects + 1):
        validate_public_url(current)
        # Per hop: a host's token never follows a redirect to another host.
        hop_kwargs = {**kwargs, "headers": {**(kwargs.get("headers") or {}), **host_headers(current)}}
        resp = requests.get(current, **hop_kwargs)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise UnsafeURLError("too many redirects")
