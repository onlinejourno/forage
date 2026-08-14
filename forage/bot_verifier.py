"""Verify that requests claiming to be Googlebot/Bingbot actually come from
that crawler's IP space, per the official reverse-DNS + forward-confirm method.

UA strings are trivially spoofable, so any crawl-budget analysis that trusts
the User-Agent header alone will overcount real bot traffic with scrapers
impersonating Googlebot.
"""

import socket
from functools import lru_cache

import pandas as pd

REVERSE_DNS_SUFFIXES = {
    "googlebot": (".googlebot.com", ".google.com"),
    "bingbot": (".search.msn.com",),
}


@lru_cache(maxsize=10_000)
def _reverse_then_forward_confirm(ip: str, suffixes: tuple) -> bool:
    try:
        host, _, _ = socket.gethostbyaddr(ip)
    except (socket.herror, socket.gaierror):
        return False
    if not host.endswith(suffixes):
        return False
    try:
        _, _, resolved_ips = socket.gethostbyname_ex(host)
    except (socket.herror, socket.gaierror):
        return False
    return ip in resolved_ips


def verify_bot_ips(df: pd.DataFrame, bot_name_col: str = "bot_name", ip_col: str = "client") -> pd.DataFrame:
    """Add a `verified` boolean column. Unsupported bot types (gptbot, ccbot,
    etc., which don't publish a reverse-DNS scheme) are marked NaN — caller
    should treat those as "UA-only, unverifiable" rather than spoofed.
    """
    def check(row):
        suffixes = REVERSE_DNS_SUFFIXES.get(row[bot_name_col])
        if suffixes is None:
            return None
        return _reverse_then_forward_confirm(row[ip_col], suffixes)

    out = df.copy()
    out["verified"] = out.apply(check, axis=1)
    return out
