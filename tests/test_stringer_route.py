"""GET /stringer/forage/mismatch — signed, read-only, and a summary not the report."""

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from webapp import api, store

KEY = "route-test-key"
HOST = "prowl-api.example"
PATH = "/stringer/forage/mismatch"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "forage.db"))
    monkeypatch.setenv(api.STRINGER_KEY_ENV, KEY)
    return TestClient(api.app)


def _headers(
    scheme: str = "https",
    key: str = KEY,
    site: str | None = "acj.example",
    forwarded_proto: str | None = None,
) -> dict:
    """Sign ``scheme://HOST/PATH``; ``forwarded_proto`` is what the proxy says."""
    digest = "sha-256=" + base64.b64encode(hashlib.sha256(b"").digest()).decode()
    base = f"@method: GET\n@target-uri: {scheme}://{HOST}{PATH}\ncontent-digest: {digest}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
    h = {
        "host": HOST,
        "x-forwarded-proto": forwarded_proto or scheme,
        "x-forwarded-host": HOST,
        "signature-input": 'stringer=(@method @target-uri content-digest);keyid="site-key-v1";alg="hmac-sha256";created=1',
        "signature": f"stringer=:{sig}:",
        "content-digest": digest,
    }
    if site is not None:
        h[api.SITE_HEADER] = site
    return h


FULL_RESULT = {
    "site": "https://acj.example",
    "priority": ["opinion", "news"],
    "sitemap_summary": [
        {"section": "news", "url_count": 100, "avg_depth": 1.0},
        {"section": "opinion", "url_count": 20, "avg_depth": 3.0},
        {"section": "archive", "url_count": 500, "avg_depth": 1.0},
    ],
    "depth_summary": [
        {"section": "news", "avg_depth": 1.0},
        {"section": "opinion", "avg_depth": 3.0},
        {"section": "archive", "avg_depth": 1.0},
    ],
    "mismatch": [{"section": "poisoned", "problem": "whatever the runner said"}],
    "robots": {"sitemaps_declared": [], "disallowed_patterns": []},
    "robots_issues": [{"level": "warning", "message": "No sitemap declared"}],
    "cc": [{"section": "news", "cc_url_count": 3}],
    "competitors": [],
    "briefing": "# Bot Crawl Briefing",
}


def test_unsigned_request_is_refused(client):
    assert client.get(PATH, headers={"host": HOST}).status_code == 401


def test_wrong_key_is_refused(client):
    assert client.get(PATH, headers=_headers(key="wrong")).status_code == 401


def test_unset_key_refuses_a_correctly_signed_request(client, monkeypatch):
    monkeypatch.delenv(api.STRINGER_KEY_ENV)
    assert client.get(PATH, headers=_headers()).status_code == 401


def test_verifies_the_addressed_url_not_the_bound_one(client):
    """Behind TLS termination the caller signs https:// and this process is
    reached over http://. The forwarded scheme is what the caller addressed,
    so an https signature passes -- and one that disagrees with the forwarded
    scheme does not. The production asymmetry: https-signed 200, http-signed 401."""
    assert client.get(PATH, headers=_headers(scheme="https", forwarded_proto="https")).status_code == 200
    assert client.get(PATH, headers=_headers(scheme="http", forwarded_proto="https")).status_code == 401


def test_without_forwarded_headers_the_bound_address_is_what_is_verified(client):
    """No proxy in front (a self-hoster on a bare port): the TestClient binds
    http://testserver, so an https signature for another host cannot pass."""
    h = _headers(scheme="https")
    for k in ("x-forwarded-proto", "x-forwarded-host", "host"):
        del h[k]
    assert client.get(PATH, headers=h).status_code == 401


def test_missing_site_header_is_a_400_not_a_guess(client):
    r = client.get(PATH, headers=_headers(site=None))
    assert r.status_code == 400


def test_no_analysis_yet_is_a_200_with_a_state(client):
    """The hub turns any non-2xx into a red 'error' card. Nothing stored is not
    an error, it is an instruction: go run Forage."""
    r = client.get(PATH, headers=_headers())
    assert r.status_code == 200
    assert r.json() == {"state": "no_analysis", "site": "acj.example", "mismatch": []}


def test_returns_the_latest_stored_run_as_a_summary(client):
    store.save_result("https://acj.example", FULL_RESULT)
    body = client.get(PATH, headers=_headers()).json()
    assert body["state"] == "ok"
    assert body["site"] == "https://acj.example"
    assert body["analysed_at"]
    assert body["robots_issues"] == FULL_RESULT["robots_issues"]
    sections = {m["section"]: m["problem"] for m in body["mismatch"]}
    assert "news" in sections and "opinion" in sections


def test_mismatch_is_recomputed_under_the_default_priority(client):
    """Anyone can run Forage on any site with any priority list, and that run
    becomes 'latest'. The hub view must not inherit a stranger's ranking."""
    store.save_result("https://acj.example", FULL_RESULT)
    body = client.get(PATH, headers=_headers()).json()
    assert body["priority"] == api.DEFAULT_PRIORITY
    sections = {m["section"]: m for m in body["mismatch"]}
    assert "poisoned" not in sections
    # Under the default order news is #1 and shallow: fine. opinion is #2 and
    # three clicks deep: flagged.
    assert sections["news"]["problem"] == "OK"
    assert sections["opinion"]["problem"] == "High priority, deep URL"


def test_full_result_is_absent(client):
    """Asserted rather than trusted: the stored result carries the whole crawl."""
    store.save_result("https://acj.example", FULL_RESULT)
    body = client.get(PATH, headers=_headers()).json()
    for heavy in ("sitemap_summary", "depth_summary", "cc", "briefing", "competitors", "robots"):
        assert heavy not in body, heavy


def test_store_unavailable_is_a_500_without_the_path(client, monkeypatch, tmp_path):
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "gone" / "forage.db"))
    r = client.get(PATH, headers=_headers())
    assert r.status_code == 500
    assert "gone" not in r.text


def test_health_reports_the_store(client, monkeypatch, tmp_path):
    assert client.get("/api/health").json() == {"ok": True, "db": "ok"}
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "gone" / "forage.db"))
    assert client.get("/api/health").json() == {"ok": True, "db": "missing"}


def test_finished_job_is_persisted(client, monkeypatch):
    """The reason the store exists: a result must outlive the JOBS dict."""
    monkeypatch.setattr(api, "_build_result", lambda req, set_step: dict(FULL_RESULT))
    api.JOBS.clear()
    api.JOBS["j1"] = {"status": "running", "step": "", "result": None, "error": None, "created_at": 0}
    api._run_job("j1", api.AnalyseRequest(url="https://acj.example"))
    assert api.JOBS["j1"]["status"] == "done"
    assert store.latest_result("acj.example")["result"]["site"] == "https://acj.example"
