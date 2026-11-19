"""POST /stringer/forage/operator-run: the hub starts an operator run, signed."""

import base64
import hashlib
import hmac
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from webapp import api

KEY = "route-test-key"
HOST = "prowl-api.example"
PATH = "/stringer/forage/operator-run"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(api.STRINGER_KEY_ENV, KEY)
    monkeypatch.delenv(api.STRINGER_KEY_ID_ENV, raising=False)
    monkeypatch.delenv(api.OPERATOR_COOLDOWN_ENV, raising=False)
    monkeypatch.setenv("FETCH_HOST_HEADERS", json.dumps({"news.example": {"X-Site-Key": "k"}}))
    monkeypatch.setattr(api, "_operator_runs", {})
    monkeypatch.setattr(api, "_operator_state", {"last_start": None, "in_flight": False})
    monkeypatch.setattr(api.limiter, "enabled", False)
    return TestClient(api.app)


def _sign(method: str, path: str, body: bytes = b"", v2: bool = True) -> dict:
    digest = "sha-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()
    base = f"@method: {method}\n@target-uri: https://{HOST}{path}\ncontent-digest: {digest}"
    h = {"host": HOST, "x-forwarded-proto": "https", "x-forwarded-host": HOST, "content-digest": digest}
    if v2:
        params = (f'(@method @target-uri content-digest);keyid="site-key-v1";'
                  f'alg="hmac-sha256";created={int(time.time())}')
        base += f"\n@signature-params: {params}"
        h["signature-input"] = f"stringer-v2={params}"
        label = "stringer-v2"
    else:
        h["signature-input"] = 'stringer=(@method @target-uri content-digest);keyid="site-key-v1";alg="hmac-sha256";created=1'
        label = "stringer"
    sig = base64.b64encode(hmac.new(KEY.encode(), base.encode(), hashlib.sha256).digest()).decode()
    h["signature"] = f"{label}=:{sig}:"
    return h


def _post(client, url: str, signed: bool = True, v2: bool = True):
    body = json.dumps({"url": url}).encode()
    headers = {"content-type": "application/json"}
    if signed:
        headers.update(_sign("POST", PATH, body, v2=v2))
    return client.post(PATH, content=body, headers=headers)


def _status(client, run_id: str, signed: bool = True):
    path = f"{PATH}/{run_id}"
    return client.get(path, headers=_sign("GET", path) if signed else {})


def _finished(client, run_id: str) -> dict:
    for _ in range(200):
        st = _status(client, run_id).json()
        if st["state"] != "running":
            return st
        time.sleep(0.01)
    raise AssertionError("run never finished")


def _runner(monkeypatch, fn):
    monkeypatch.setattr(api, "_operator_run_site", fn)


def test_unsigned_is_refused_and_runs_nothing(client, monkeypatch):
    ran = []
    _runner(monkeypatch, lambda url: ran.append(url))
    assert (_post(client, "https://news.example/", signed=False).status_code, ran) == (401, [])


def test_a_signature_without_signed_created_is_refused(client, monkeypatch):
    ran = []
    _runner(monkeypatch, lambda url: ran.append(url))
    assert (_post(client, "https://news.example/", v2=False).status_code, ran) == (401, [])


def test_a_host_with_no_door_is_refused(client, monkeypatch):
    ran = []
    _runner(monkeypatch, lambda url: ran.append(url))
    assert (_post(client, "https://elsewhere.example/").status_code, ran) == (400, [])


def test_a_signed_run_on_a_configured_host_reports_stored(client, monkeypatch):
    seen = []
    _runner(monkeypatch, lambda url: seen.append(url) or "stored https://news.example/: 3 sitemap sections")
    res = _post(client, "https://news.example/")
    st = _finished(client, res.json()["id"])
    assert (res.status_code, seen, st["state"], st["message"]) == (
        202, ["https://news.example/"], "stored", "stored https://news.example/: 3 sitemap sections")


def test_a_failed_run_reports_why(client, monkeypatch):
    def boom(url):
        raise RuntimeError("failed: spider timed out")
    _runner(monkeypatch, boom)
    st = _finished(client, _post(client, "https://news.example/").json()["id"])
    assert (st["state"], st["message"]) == ("failed", "failed: spider timed out")


def test_one_run_at_a_time(client, monkeypatch):
    release = threading.Event()
    _runner(monkeypatch, lambda url: release.wait(5) and "stored")
    _post(client, "https://news.example/")
    second = _post(client, "https://news.example/")
    release.set()
    assert second.status_code == 409


def test_a_second_run_inside_ten_minutes_waits(client, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(api, "_operator_clock", lambda: clock["t"])
    _runner(monkeypatch, lambda url: "stored")
    _finished(client, _post(client, "https://news.example/").json()["id"])
    clock["t"] = 9 * 60
    early = _post(client, "https://news.example/")
    clock["t"] = 10 * 60
    later = _post(client, "https://news.example/")
    assert (early.status_code, early.headers.get("retry-after"), later.status_code) == (429, "60", 202)


def test_status_needs_a_signature_and_a_known_id(client, monkeypatch):
    _runner(monkeypatch, lambda url: "stored")
    run_id = _post(client, "https://news.example/").json()["id"]
    assert (_status(client, run_id, signed=False).status_code, _status(client, "nope").status_code) == (401, 404)


def test_the_runner_is_the_operator_command_and_raises_on_its_failure(monkeypatch):
    from webapp import operator_run

    def fake_main(argv, say):
        say("crawling")
        say(f"failed: nothing at {argv[0]}")
        return 1
    monkeypatch.setattr(operator_run, "main", fake_main)
    with pytest.raises(RuntimeError, match="failed: nothing at https://news.example/"):
        api._operator_run_site("https://news.example/")
