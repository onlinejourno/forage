"""Per-host request headers from FETCH_HOST_HEADERS.

A site can sit behind a login (for example Cloudflare Access) and give Forage
a service token. The token goes only to that site's host, worked out again on
every redirect hop, so a redirect elsewhere never carries it.
"""
import json

import pytest
import requests

from webapp import ssrf

TOKEN = {"CF-Access-Client-Id": "id-1", "CF-Access-Client-Secret": "s-1"}


def _env(monkeypatch, table):
    monkeypatch.setenv("FETCH_HOST_HEADERS", json.dumps(table))


def test_no_setting_means_no_extra_headers(monkeypatch):
    monkeypatch.delenv("FETCH_HOST_HEADERS", raising=False)
    assert ssrf.host_headers("https://news.example/a") == {}


def test_host_and_subdomains_match_lookalikes_do_not(monkeypatch):
    _env(monkeypatch, {"news.example": TOKEN})
    assert ssrf.host_headers("https://news.example/a") == TOKEN
    assert ssrf.host_headers("https://WWW.news.example/a") == TOKEN
    assert ssrf.host_headers("https://evilnews.example/a") == {}
    assert ssrf.host_headers("https://news.example.attacker.test/a") == {}


def test_a_broken_setting_fails_loudly(monkeypatch):
    monkeypatch.setenv("FETCH_HOST_HEADERS", "{not json")
    with pytest.raises(ValueError, match="FETCH_HOST_HEADERS"):
        ssrf.host_headers("https://news.example/a")
    _env(monkeypatch, {"news.example": ["x"]})
    with pytest.raises(ValueError, match="FETCH_HOST_HEADERS"):
        ssrf.host_headers("https://news.example/a")


class _Resp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None


def test_the_token_does_not_follow_a_redirect_to_another_host(monkeypatch):
    _env(monkeypatch, {"news.example": TOKEN})
    seen = []

    def fake_get(url, headers=None, **kw):
        seen.append((url, dict(headers or {})))
        if "news.example" in url:
            return _Resp(302, "https://elsewhere.example/b")
        return _Resp(200)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(ssrf, "validate_public_url", lambda u: u)
    ssrf.safe_get("https://news.example/a", headers={"User-Agent": "UA"})
    assert seen[0][1] == {"User-Agent": "UA", **TOKEN}
    assert seen[1] == ("https://elsewhere.example/b", {"User-Agent": "UA"})
