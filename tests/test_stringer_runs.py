"""Hub-owned runs through the API: start, poll, list, quota, scoping."""

import base64
import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from webapp import api, store

KEY = "runs-test-key"
HOST = "prowl-api.example"
OWNER = "acj.example"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "forage.db"))
    monkeypatch.setenv(api.STRINGER_KEY_ENV, KEY)
    # Never crawl in tests.
    monkeypatch.setattr(api, "_build_result", lambda req, set_step: {"site": req.url, "sitemap_summary": [], "depth_summary": [], "robots_issues": []})
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
