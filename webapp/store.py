"""Stored analyses — the one thing in Forage that has to outlive a restart.

Job progress stays in memory (``api.JOBS``): it is per-request and short-lived.
A finished result is different. The hub asks "what is the latest analysis of
this site" long after the job that produced it is gone, so it is written here,
keyed by host, and read back by host.

SQLite from the stdlib, on purpose. Forage is a no-login public-data tool a
self-hoster runs from one container; asking them to provision Postgres to keep
a few JSON rows changes what the product requires. Point FORAGE_DB_PATH at a
persistent disk (a Fly volume, a bind mount) and that is the whole setup.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

DB_PATH_ENV = "FORAGE_DB_PATH"
DEFAULT_DB_PATH = Path(__file__).parent / "forage.db"


class StoreUnavailable(RuntimeError):
    """The database directory does not exist.

    Raised rather than created: on a deploy that mounts a volume, a missing
    directory means the volume did not mount, and quietly writing to the
    container's disk instead would look fine until the next restart erased it.
    """


def db_path() -> Path:
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


def check() -> str:
    """'ok' when the database can be opened, 'missing' when its directory is absent."""
    return "ok" if db_path().parent.is_dir() else "missing"


class DuplicateRun(RuntimeError):
    """This owner has already used this run_id. The caller gets the existing run."""


class QuotaExceeded(RuntimeError):
    """This owner has already started `limit` runs today. Carries the count that tripped it."""


# A run reap_interrupted marked as an interrupted-not-really-run must not still
# occupy a quota slot -- the process that was going to finish it is gone, and
# the owner shouldn't lose a run they never got a result for. Both quota COUNT
# queries (start_run's own check and runs_today, which the hub's /runs screen
# reads) must agree on this predicate, so it lives in one place.
_COUNTS_TOWARD_QUOTA = "NOT (status = 'error' AND error = 'interrupted')"


SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    host        TEXT NOT NULL,
    site        TEXT NOT NULL,
    analysed_at TEXT NOT NULL,
    result_json TEXT NOT NULL
)
"""

# Added 2026-09-14 for hub-owned runs. ALTERs are idempotent by inspection so an
# existing volume upgrades in place on first connect. After adding columns, legacy
# rows (which have owner='' from the DEFAULT) must have distinct run_ids assigned
# before the unique index can be created, else the index fails on duplicates.
RUN_COLUMNS = {
    "owner": "TEXT NOT NULL DEFAULT ''",
    "run_id": "TEXT NOT NULL DEFAULT ''",
    "run_by": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'done'",
    "step": "TEXT NOT NULL DEFAULT ''",
    "started_at": "TEXT NOT NULL DEFAULT ''",
    "finished_at": "TEXT NOT NULL DEFAULT ''",
    "error": "TEXT NOT NULL DEFAULT ''",
}


def _connect() -> sqlite3.Connection:
    path = db_path()
    if not path.parent.is_dir():
        raise StoreUnavailable(f"{DB_PATH_ENV} directory does not exist")
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(results)")}
    for name, ddl in RUN_COLUMNS.items():
        if name not in have:
            con.execute(f'ALTER TABLE results ADD COLUMN "{name}" {ddl}')
    # Legacy rows have owner='' and run_id=''; backfill distinct ids before unique index.
    con.execute("UPDATE results SET run_id = 'legacy-' || id WHERE run_id = ''")
    con.execute("CREATE INDEX IF NOT EXISTS results_host_id ON results (host, id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS results_owner_run ON results (owner, run_id)")
    con.commit()
    return con


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def host_of(site: str) -> str:
    """The lookup key: the bare host, lower-cased, whatever form the site came in."""
    s = site.strip()
    if "://" not in s:
        s = "https://" + s
    return (urlparse(s).hostname or "").lower()


def save_result(site: str, result: dict) -> None:
    """A finished dashboard run. Ownerless, done on arrival."""
    con = _connect()
    try:
        with con:
            con.execute(
                "INSERT INTO results (host, site, analysed_at, result_json, owner, run_id, status, started_at, finished_at)"
                " VALUES (?, ?, ?, ?, '', ?, 'done', ?, ?)",
                (host_of(site), site, _now_iso(), json.dumps(result), uuid.uuid4().hex, _now_iso(), _now_iso()),
            )
    finally:
        con.close()


def latest_result(site: str) -> dict | None:
    """The most recent DONE run for the site's host, or None."""
    con = _connect()
    try:
        row = con.execute(
            "SELECT site, analysed_at, result_json FROM results"
            " WHERE host = ? AND status = 'done' ORDER BY id DESC LIMIT 1",
            (host_of(site),),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {"site": row[0], "analysed_at": row[1], "result": json.loads(row[2])}


_RUN_COLS = "run_id, owner, site, run_by, status, step, started_at, finished_at, error, result_json"


def _row_to_run(row, with_result: bool = True) -> dict:
    d = {
        "run_id": row[0], "owner": row[1], "site": row[2], "by": row[3], "status": row[4],
        "step": row[5], "started_at": row[6], "finished_at": row[7] or None, "error": row[8] or None,
    }
    if with_result:
        d["result"] = json.loads(row[9]) if row[4] == "done" and row[9] else None
    return d


def start_run(
    owner: str, run_id: str, site: str, by: str, now: datetime | None = None, limit: int | None = None
) -> dict:
    con = _connect()
    try:
        if limit is None:
            try:
                with con:
                    con.execute(
                        "INSERT INTO results (host, site, analysed_at, result_json, owner, run_id, run_by, status, step, started_at)"
                        " VALUES (?, ?, ?, '{}', ?, ?, ?, 'running', 'Starting…', ?)",
                        (host_of(site), site, _now_iso(now), owner, run_id, by, _now_iso(now)),
                    )
            except sqlite3.IntegrityError as exc:
                raise DuplicateRun(run_id) from exc
        else:
            # IMMEDIATE takes the write lock before reading, so the count this
            # transaction sees and the row it inserts cannot be split by a
            # concurrent request landing between the check and the insert.
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            day = _now_iso(now)[:10]
            count = con.execute(
                "SELECT COUNT(*) FROM results WHERE owner = ? AND substr(started_at, 1, 10) = ? AND "
                + _COUNTS_TOWARD_QUOTA,
                (owner, day),
            ).fetchone()[0]
            if count >= limit:
                con.execute("ROLLBACK")
                raise QuotaExceeded(count)
            try:
                con.execute(
                    "INSERT INTO results (host, site, analysed_at, result_json, owner, run_id, run_by, status, step, started_at)"
                    " VALUES (?, ?, ?, '{}', ?, ?, ?, 'running', 'Starting…', ?)",
                    (host_of(site), site, _now_iso(now), owner, run_id, by, _now_iso(now)),
                )
            except sqlite3.IntegrityError as exc:
                con.execute("ROLLBACK")
                raise DuplicateRun(run_id) from exc
            else:
                con.execute("COMMIT")
    finally:
        con.close()
    return get_run(owner, run_id)


def set_run_step(owner: str, run_id: str, step: str) -> None:
    con = _connect()
    try:
        with con:
            con.execute("UPDATE results SET step = ? WHERE owner = ? AND run_id = ?", (step, owner, run_id))
    finally:
        con.close()


def finish_run(owner: str, run_id: str, result: dict | None, error: str | None) -> None:
    con = _connect()
    try:
        with con:
            con.execute(
                "UPDATE results SET status = ?, step = ?, result_json = ?, error = ?, finished_at = ?, analysed_at = ?"
                " WHERE owner = ? AND run_id = ?",
                ("done" if error is None else "error", "Done" if error is None else "",
                 json.dumps(result) if result is not None else "{}", error or "", _now_iso(), _now_iso(),
                 owner, run_id),
            )
    finally:
        con.close()


def get_run(owner: str, run_id: str) -> dict | None:
    con = _connect()
    try:
        row = con.execute(
            f"SELECT {_RUN_COLS} FROM results WHERE owner = ? AND run_id = ?", (owner, run_id)
        ).fetchone()
    finally:
        con.close()
    return _row_to_run(row) if row else None


def list_runs(owner: str, limit: int = 50) -> list[dict]:
    con = _connect()
    try:
        rows = con.execute(
            f"SELECT {_RUN_COLS} FROM results WHERE owner = ? AND owner != '' ORDER BY id DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    finally:
        con.close()
    return [_row_to_run(r, with_result=False) for r in rows]


def runs_today(owner: str, now: datetime | None = None) -> int:
    day = _now_iso(now)[:10]
    con = _connect()
    try:
        return con.execute(
            "SELECT COUNT(*) FROM results WHERE owner = ? AND substr(started_at, 1, 10) = ? AND "
            + _COUNTS_TOWARD_QUOTA,
            (owner, day),
        ).fetchone()[0]
    finally:
        con.close()


def reap_interrupted(older_than_seconds: int = 900, now: datetime | None = None) -> int:
    """A run still 'running' after a restart is not running. Say so, and free its
    quota slot -- both start_run's own count and runs_today exclude a row this
    marks 'error'/'interrupted' (see _COUNTS_TOWARD_QUOTA), so the owner does not
    lose a run to a process that died before producing a result."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(seconds=older_than_seconds)).isoformat()
    con = _connect()
    try:
        with con:
            cur = con.execute(
                "UPDATE results SET status = 'error', error = 'interrupted', step = '', finished_at = ?"
                " WHERE status = 'running' AND started_at < ?",
                (_now_iso(now), cutoff),
            )
            return cur.rowcount
    finally:
        con.close()


def runs_failed_recent(hours: int = 24, now: datetime | None = None) -> int:
    since = ((now or datetime.now(timezone.utc)) - timedelta(hours=hours)).isoformat()
    con = _connect()
    try:
        return con.execute(
            "SELECT COUNT(*) FROM results WHERE status = 'error' AND finished_at >= ?", (since,)
        ).fetchone()[0]
    finally:
        con.close()
