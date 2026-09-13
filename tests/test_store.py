"""Stored results — the part of Forage that has to outlive a restart."""

import json

import pytest

from webapp import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "forage.db"
    monkeypatch.setenv(store.DB_PATH_ENV, str(path))
    return path


def test_latest_returns_none_when_nothing_stored(db):
    assert store.latest_result("acj.example") is None


def test_save_then_latest_round_trips(db):
    store.save_result("https://acj.example/", {"site": "https://acj.example/", "n": 1})
    row = store.latest_result("acj.example")
    assert row["result"] == {"site": "https://acj.example/", "n": 1}
    assert row["analysed_at"].endswith("+00:00")


def test_latest_is_the_most_recent_run(db):
    store.save_result("https://acj.example", {"n": 1})
    store.save_result("https://acj.example", {"n": 2})
    assert store.latest_result("acj.example")["result"]["n"] == 2


def test_host_lookup_is_case_and_scheme_insensitive(db):
    store.save_result("HTTPS://ACJ.Example/path", {"n": 1})
    assert store.latest_result("acj.example")["result"]["n"] == 1
    assert store.latest_result("https://acj.example/")["result"]["n"] == 1


def test_result_survives_a_new_connection(db):
    """A restart is a new process with the same file; the row must still be there."""
    store.save_result("https://acj.example", {"n": 1})
    con = store._connect()
    con.close()
    assert store.latest_result("acj.example")["result"]["n"] == 1


def test_missing_directory_is_refused_not_silently_elsewhere(tmp_path, monkeypatch):
    """A volume that did not mount must not become an ephemeral file that looks fine
    until the machine restarts."""
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "no-such-dir" / "forage.db"))
    assert store.check() == "missing"
    with pytest.raises(store.StoreUnavailable):
        store.save_result("https://acj.example", {"n": 1})


def test_check_reports_ok_for_a_writable_path(db):
    assert store.check() == "ok"


def test_stored_result_is_json_not_pickle(db):
    store.save_result("https://acj.example", {"n": 1})
    con = store._connect()
    raw = con.execute("SELECT result_json FROM results").fetchone()[0]
    con.close()
    assert json.loads(raw) == {"n": 1}
