"""Per-host headers are a door, and only an operator run holds it open.

FETCH_HOST_HEADERS can carry a secret that lets Forage past a site's block.
The public analyse route runs any site for anyone and hands back the result,
so if it sent those headers a stranger could read a locked site through our
door. Only `python -m webapp.operator_run` opens it.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from webapp import api, operator_run, ssrf

DOOR = {"X-Door": "s-1"}


@pytest.fixture
def door_env(monkeypatch):
    monkeypatch.setenv("FETCH_HOST_HEADERS", json.dumps({"news.example": DOOR}))


def test_closed_by_default_even_when_configured(door_env):
    assert ssrf.host_headers("https://news.example/a") == {}


def test_open_inside_an_operator_run_and_closed_after(door_env):
    with ssrf.operator_door():
        assert ssrf.host_headers("https://news.example/a") == DOOR
    assert ssrf.host_headers("https://news.example/a") == {}


def test_a_worker_thread_does_not_inherit_the_open_door(door_env):
    # The public route runs jobs on a pool; a pool thread never starts with
    # the operator's context.
    with ssrf.operator_door(), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(ssrf.host_headers, "https://news.example/a").result() == {}


def test_public_analyse_job_never_sends_the_door(door_env, monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_run_site", lambda url, step: seen.append(ssrf.host_headers(url)) or _empty_site())
    monkeypatch.setattr(api.store, "save_result", lambda site, result: None)
    api.JOBS["j1"] = {"status": "running"}
    api._run_job("j1", api.AnalyseRequest(url="https://news.example"))
    assert seen == [{}]


def test_operator_run_opens_the_door_and_stores_the_result(door_env, monkeypatch):
    seen, saved = [], []
    monkeypatch.setattr(api, "_run_site", lambda url, step: seen.append(ssrf.host_headers(url)) or _empty_site())
    monkeypatch.setattr(api.store, "save_result", lambda site, result: saved.append(site))
    monkeypatch.setattr(operator_run, "validate_public_url", lambda u: u)
    assert operator_run.main(["news.example"]) == 0
    assert seen == [DOOR]
    assert saved == ["https://news.example"]
    assert ssrf.host_headers("https://news.example/a") == {}


def test_operator_run_refuses_a_non_public_url(monkeypatch):
    monkeypatch.setattr(api.store, "save_result", lambda site, result: pytest.fail("stored"))
    assert operator_run.main(["http://127.0.0.1"]) == 2


def _empty_site():
    import pandas as pd

    return {
        "sitemap_summary": pd.DataFrame(),
        "depth_summary": pd.DataFrame(),
        "robots": {},
        "robots_issues": [],
        "cc": pd.DataFrame(),
    }
