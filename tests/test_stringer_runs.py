"""Hub-owned runs through the API: start, poll, list, quota, scoping."""

import base64
import hashlib
import hmac
import json
import socket
import uuid

import pytest
from fastapi.testclient import TestClient

from webapp import api, store

KEY = "runs-test-key"
HOST = "prowl-api.example"
OWNER = "acj.example"

_REAL_GETADDRINFO = socket.getaddrinfo


def _fake_getaddrinfo(host, *args, **kwargs):
    """``*.example`` is IANA-reserved (RFC 2606) and never resolves via real DNS,
    anywhere -- but these tests must post a *.example analysis target (public-repo
    rule: placeholders only). Resolve it to a known-public literal IP instead, so
    ``validate_public_url``'s real SSRF logic still runs, just without depending
    on live DNS for a domain that can never have any."""
    if isinstance(host, str) and host.endswith(".example"):
        return _REAL_GETADDRINFO("93.184.216.34", *args, **kwargs)
    return _REAL_GETADDRINFO(host, *args, **kwargs)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "forage.db"))
    monkeypatch.setenv(api.STRINGER_KEY_ENV, KEY)
    # Never crawl in tests.
    monkeypatch.setattr(api, "_build_result", lambda req, set_step: {"site": req.url, "sitemap_summary": [], "depth_summary": [], "robots_issues": []})
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    return TestClient(api.app)


def _signed(method: str, path: str, body: str = "", key: str = KEY, owner: str | None = OWNER) -> dict:
    digest = "sha-256=" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    base = f"@method: {method}\n@target-uri: https://{HOST}{path}\ncontent-digest: {digest}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
    h = {
        "host": HOST, "x-forwarded-proto": "https", "x-forwarded-host": HOST,
        "signature-input": 'stringer=(@method @target-uri content-digest);keyid="site-key-v1";alg="hmac-sha256";created=1',
        "signature": f"stringer=:{sig}:", "content-digest": digest, "content-type": "application/json",
    }
    if owner is not None:
        h[api.SITE_HEADER] = owner
    return h


def _post(client, body: dict, **kw):
    raw = json.dumps(body)
    return client.post("/stringer/forage/analyse", content=raw, headers=_signed("POST", "/stringer/forage/analyse", raw, **kw))


def test_owned_job_writes_outcome_to_the_row(client):
    rid = str(uuid.uuid4())
    store.start_run(OWNER, rid, "https://site.example", "x")
    api._run_job(None, api.AnalyseRequest(url="https://site.example"), owned=(OWNER, rid))
    got = store.get_run(OWNER, rid)
    assert got["status"] == "done" and got["result"]["site"] == "https://site.example"


def test_owned_job_failure_is_recorded_not_raised(client, monkeypatch):
    def boom(req, set_step):
        raise RuntimeError("fetch failed")
    monkeypatch.setattr(api, "_build_result", boom)
    rid = str(uuid.uuid4())
    store.start_run(OWNER, rid, "https://site.example", "x")
    api._run_job(None, api.AnalyseRequest(url="https://site.example"), owned=(OWNER, rid))
    assert store.get_run(OWNER, rid)["error"] == "fetch failed"


def test_startup_reaps_interrupted_runs(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "forage.db"))
    stale = datetime.now(timezone.utc) - timedelta(minutes=20)
    store.start_run(OWNER, str(uuid.uuid4()), "https://s.example", "x", now=stale)
    with TestClient(api.app):
        pass  # lifespan startup runs here
    assert store.list_runs(OWNER)[0]["error"] == "interrupted"


def test_analyse_starts_a_run_for_the_owner(client):
    rid = str(uuid.uuid4())
    r = _post(client, {"url": "https://site.example", "run_id": rid, "by": "A Student"})
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == rid and r.json()["status"] in ("running", "done")
    assert store.get_run(OWNER, rid)["by"] == "A Student"


def test_analyse_unsigned_is_401(client):
    raw = json.dumps({"url": "https://site.example", "run_id": str(uuid.uuid4())})
    assert client.post("/stringer/forage/analyse", content=raw, headers={"host": HOST}).status_code == 401


def test_analyse_wrong_key_is_401(client):
    assert _post(client, {"url": "https://site.example", "run_id": str(uuid.uuid4())}, key="nope").status_code == 401


def test_analyse_tampered_body_is_401(client):
    raw = json.dumps({"url": "https://site.example", "run_id": str(uuid.uuid4())})
    h = _signed("POST", "/stringer/forage/analyse", raw)
    r = client.post("/stringer/forage/analyse", content=raw.replace("site.example", "evil.example"), headers=h)
    assert r.status_code == 401


def test_analyse_without_owner_header_is_400(client):
    assert _post(client, {"url": "https://site.example", "run_id": str(uuid.uuid4())}, owner=None).status_code == 400


def test_analyse_run_id_must_be_a_uuid(client):
    r = _post(client, {"url": "https://site.example", "run_id": "not-a-uuid"})
    assert r.status_code == 400 and "run_id" in r.text


def test_analyse_is_idempotent_on_run_id(client):
    rid = str(uuid.uuid4())
    a = _post(client, {"url": "https://site.example", "run_id": rid})
    b = _post(client, {"url": "https://other.example", "run_id": rid})
    assert a.status_code == 200 and b.status_code == 200
    assert b.json()["site"] == "https://site.example", "the second POST returned the existing run and started nothing"
    assert store.runs_today(OWNER) == 1


def test_analyse_refuses_private_targets(client):
    r = _post(client, {"url": "http://127.0.0.1/", "run_id": str(uuid.uuid4())})
    assert r.status_code == 400 and "refused" in r.text
    assert store.runs_today(OWNER) == 0, "a refused URL does not spend quota"


def test_analyse_quota_is_per_owner_per_day(client, monkeypatch):
    monkeypatch.setenv(api.HUB_RUNS_PER_DAY_ENV, "2")
    for _ in range(2):
        assert _post(client, {"url": "https://site.example", "run_id": str(uuid.uuid4())}).status_code == 200
    r = _post(client, {"url": "https://site.example", "run_id": str(uuid.uuid4())})
    assert r.status_code == 429
    assert r.json()["state"] == "quota" and r.json()["resets_at"].endswith("T00:00:00+00:00")
    # Another owner is unaffected.
    assert _post(client, {"url": "https://site.example", "run_id": str(uuid.uuid4())}, owner="other.example").status_code == 200


def test_quota_default_is_twenty(client, monkeypatch):
    monkeypatch.delenv(api.HUB_RUNS_PER_DAY_ENV, raising=False)
    assert api._runs_per_day() == 20


def _get(client, path, **kw):
    return client.get(path, headers=_signed("GET", path, **kw))


def test_run_is_readable_by_its_owner_only(client):
    rid = str(uuid.uuid4())
    _post(client, {"url": "https://site.example", "run_id": rid})
    mine = _get(client, f"/stringer/forage/run/{rid}")
    assert mine.status_code == 200 and mine.json()["run_id"] == rid
    theirs = _get(client, f"/stringer/forage/run/{rid}", owner="other.example")
    assert theirs.status_code == 404


def test_done_run_carries_its_result(client):
    rid = str(uuid.uuid4())
    store.start_run(OWNER, rid, "https://site.example", "x")
    store.finish_run(OWNER, rid, {"site": "https://site.example", "mismatch": []}, None)
    body = _get(client, f"/stringer/forage/run/{rid}").json()
    assert body["status"] == "done" and body["result"]["mismatch"] == []


def test_running_run_has_no_result_key(client):
    rid = str(uuid.uuid4())
    store.start_run(OWNER, rid, "https://site.example", "x")
    assert "result" not in _get(client, f"/stringer/forage/run/{rid}").json()


def test_runs_lists_the_owners_runs_with_quota(client, monkeypatch):
    monkeypatch.setenv(api.HUB_RUNS_PER_DAY_ENV, "5")
    rid0 = str(uuid.uuid4())
    store.start_run(OWNER, rid0, "https://s0.example", "x")
    # Finish the first run so it has a result in the list
    store.finish_run(OWNER, rid0, {"mismatch": []}, None)
    rid1 = str(uuid.uuid4())
    store.start_run(OWNER, rid1, "https://s1.example", "x")
    store.start_run("other.example", str(uuid.uuid4()), "https://o.example", "x")
    body = _get(client, "/stringer/forage/runs").json()
    assert [r["site"] for r in body["runs"]] == ["https://s1.example", "https://s0.example"]
    assert body["used_today"] == 2 and body["limit"] == 5 and body["resets_at"]
    # Verify no result key in any list entry, even the finished run
    assert all("result" not in r for r in body["runs"])


def test_run_id_path_must_be_a_uuid(client):
    assert _get(client, "/stringer/forage/run/not-a-uuid").status_code == 400
