"""Hub-owned runs: started, stepped, finished, listed, counted, reaped."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from webapp import store

OWNER = "acj.example"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "forage.db"))


def _rid():
    return str(uuid.uuid4())


def test_start_then_get(db):
    rid = _rid()
    row = store.start_run(OWNER, rid, "https://site.example", "A Student")
    assert row["status"] == "running" and row["result"] is None
    got = store.get_run(OWNER, rid)
    assert got["site"] == "https://site.example" and got["by"] == "A Student"
    assert got["started_at"].endswith("+00:00")


def test_duplicate_run_id_for_same_owner_is_refused(db):
    rid = _rid()
    store.start_run(OWNER, rid, "https://site.example", "x")
    with pytest.raises(store.DuplicateRun):
        store.start_run(OWNER, rid, "https://other.example", "x")


def test_runs_are_owner_scoped(db):
    rid = _rid()
    store.start_run(OWNER, rid, "https://site.example", "x")
    assert store.get_run("other.example", rid) is None
    assert store.list_runs("other.example") == []


def test_step_and_finish_done(db):
    rid = _rid()
    store.start_run(OWNER, rid, "https://site.example", "x")
    store.set_run_step(OWNER, rid, "Spidering…")
    assert store.get_run(OWNER, rid)["step"] == "Spidering…"
    store.finish_run(OWNER, rid, {"site": "https://site.example", "n": 1}, None)
    got = store.get_run(OWNER, rid)
    assert got["status"] == "done" and got["result"] == {"site": "https://site.example", "n": 1}
    assert got["finished_at"]


def test_finish_error(db):
    rid = _rid()
    store.start_run(OWNER, rid, "https://site.example", "x")
    store.finish_run(OWNER, rid, None, "boom")
    got = store.get_run(OWNER, rid)
    assert got["status"] == "error" and got["error"] == "boom" and got["result"] is None


def test_list_runs_newest_first_without_results(db):
    for i in range(3):
        store.start_run(OWNER, _rid(), f"https://s{i}.example", "x")
    rows = store.list_runs(OWNER)
    assert [r["site"] for r in rows] == ["https://s2.example", "https://s1.example", "https://s0.example"]
    assert all("result" not in r for r in rows)


def test_list_runs_is_capped(db):
    for _ in range(55):
        store.start_run(OWNER, _rid(), "https://s.example", "x")
    assert len(store.list_runs(OWNER)) == 50


def test_start_run_with_limit_refuses_the_nth_plus_one(db):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    store.start_run(OWNER, _rid(), "https://s.example", "x", now=now, limit=2)
    store.start_run(OWNER, _rid(), "https://s.example", "x", now=now, limit=2)
    with pytest.raises(store.QuotaExceeded):
        store.start_run(OWNER, _rid(), "https://s.example", "x", now=now, limit=2)
    # Another owner has its own quota.
    store.start_run("other.example", _rid(), "https://s.example", "x", now=now, limit=2)
    # A run from yesterday does not count against today's quota.
    store.start_run(OWNER, _rid(), "https://s.example", "x", now=now - timedelta(days=1))
    assert store.runs_today(OWNER, now=now) == 2


def test_start_run_without_limit_never_refuses(db):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    for _ in range(5):
        store.start_run(OWNER, _rid(), "https://s.example", "x", now=now)
    assert store.runs_today(OWNER, now=now) == 5


def test_runs_today_counts_only_today_utc(db):
    now = datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)
    store.start_run(OWNER, _rid(), "https://s.example", "x", now=now)
    store.start_run(OWNER, _rid(), "https://s.example", "x", now=now - timedelta(days=1))
    assert store.runs_today(OWNER, now=now) == 1
    assert store.runs_today(OWNER, now=now + timedelta(hours=1)) == 0


def test_reap_marks_stale_running_rows_interrupted(db):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    old, fresh = _rid(), _rid()
    store.start_run(OWNER, old, "https://s.example", "x", now=now - timedelta(minutes=16))
    store.start_run(OWNER, fresh, "https://s.example", "x", now=now - timedelta(minutes=1))
    assert store.reap_interrupted(now=now) == 1
    assert store.get_run(OWNER, old)["error"] == "interrupted"
    assert store.get_run(OWNER, fresh)["status"] == "running"


def test_dashboard_results_share_the_table_and_latest_reads_only_done(db):
    store.save_result("https://acj.example", {"n": 1})
    store.start_run("acj.example", _rid(), "https://acj.example", "x")  # running, newer
    assert store.latest_result("acj.example")["result"] == {"n": 1}


def test_existing_database_with_several_rows_upgrades_in_place(tmp_path, monkeypatch):
    """Pre-migration DB with 2 legacy rows must upgrade without error; latest_result and
    start_run must both work; legacy rows must not appear in list_runs."""
    import sqlite3

    monkeypatch.setenv(store.DB_PATH_ENV, str(tmp_path / "forage.db"))
    path = tmp_path / "forage.db"

    # Create a pre-migration database with just the original schema
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE results ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "host TEXT NOT NULL, "
        "site TEXT NOT NULL, "
        "analysed_at TEXT NOT NULL, "
        "result_json TEXT NOT NULL"
        ")"
    )
    con.execute(
        "INSERT INTO results (host, site, analysed_at, result_json) VALUES (?, ?, ?, ?)",
        ("acj.example", "https://acj.example", "2026-09-14T10:00:00+00:00", '{"n": 1}'),
    )
    con.execute(
        "INSERT INTO results (host, site, analysed_at, result_json) VALUES (?, ?, ?, ?)",
        ("acj.example", "https://acj.example", "2026-09-14T11:00:00+00:00", '{"n": 2}'),
    )
    con.commit()
    con.close()

    # Now call store functions which trigger _connect() and the migration
    latest = store.latest_result("acj.example")
    assert latest["result"]["n"] == 2  # The newer row

    # Create a new run; this verifies the unique index works
    new_rid = _rid()
    store.start_run("acj.example", new_rid, "https://acj.example", "test")

    # list_runs should only show hub runs (owner != ''); legacy rows have owner=''
    runs = store.list_runs("acj.example")
    assert len(runs) == 1
    assert runs[0]["run_id"] == new_rid


def test_runs_failed_recent_counts_errors_only(db):
    import sqlite3

    a, b, c = _rid(), _rid(), _rid()
    store.start_run(OWNER, a, "https://s.example", "x")
    store.start_run(OWNER, b, "https://s.example", "x")
    store.start_run(OWNER, c, "https://s.example", "x")
    store.finish_run(OWNER, a, None, "boom")
    store.finish_run(OWNER, b, {"n": 1}, None)
    store.finish_run(OWNER, c, None, "old error")

    # Manually set c's finished_at to 48 hours ago
    old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    con = sqlite3.connect(store.db_path())
    try:
        con.execute("UPDATE results SET finished_at = ? WHERE run_id = ?", (old_time, c))
        con.commit()
    finally:
        con.close()

    assert store.runs_failed_recent(hours=24) == 1
    assert store.runs_failed_recent(hours=72) == 2
