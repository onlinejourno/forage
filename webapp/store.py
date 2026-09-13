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
from datetime import datetime, timezone
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


def _connect() -> sqlite3.Connection:
    path = db_path()
    if not path.parent.is_dir():
        raise StoreUnavailable(f"{DB_PATH_ENV} directory does not exist")
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            host        TEXT NOT NULL,
            site        TEXT NOT NULL,
            analysed_at TEXT NOT NULL,
            result_json TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS results_host_id ON results (host, id)")
    con.commit()
    return con


def host_of(site: str) -> str:
    """The lookup key: the bare host, lower-cased, whatever form the site came in."""
    s = site.strip()
    if "://" not in s:
        s = "https://" + s
    return (urlparse(s).hostname or "").lower()


def save_result(site: str, result: dict) -> None:
    con = _connect()
    try:
        with con:
            con.execute(
                "INSERT INTO results (host, site, analysed_at, result_json) VALUES (?, ?, ?, ?)",
                (host_of(site), site, datetime.now(timezone.utc).isoformat(), json.dumps(result)),
            )
    finally:
        con.close()


def latest_result(site: str) -> dict | None:
    """The most recent stored run for the site's host, or None."""
    con = _connect()
    try:
        row = con.execute(
            "SELECT site, analysed_at, result_json FROM results WHERE host = ? ORDER BY id DESC LIMIT 1",
            (host_of(site),),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {"site": row[0], "analysed_at": row[1], "result": json.loads(row[2])}
