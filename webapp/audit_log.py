"""Persistent audit log using SQLite.

Records each site analysis so the dashboard can show recently audited sites —
a lightweight signal of which outlets journalists are investigating.
No personal data stored; only the site URL, timestamp, and basic stats.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import os
DB_PATH = Path(os.environ.get("AUDIT_LOG_PATH", Path(__file__).parent / "audit_log.db"))


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audits (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            site_url  TEXT NOT NULL,
            audited_at TEXT NOT NULL,
            sitemap_urls INTEGER,
            sections  INTEGER
        )
    """)
    con.commit()
    return con


def record_audit(site_url: str, sitemap_urls: int = 0, sections: int = 0):
    with _conn() as con:
        con.execute(
            "INSERT INTO audits (site_url, audited_at, sitemap_urls, sections) VALUES (?,?,?,?)",
            (site_url.rstrip("/"), datetime.now(timezone.utc).isoformat(), sitemap_urls, sections),
        )


def recent_audits(limit: int = 30):
    """Return the most recent audits as a list of dicts."""
    with _conn() as con:
        rows = con.execute(
            "SELECT site_url, audited_at, sitemap_urls, sections "
            "FROM audits ORDER BY audited_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [
        {"site": r[0], "audited_at": r[1][:16].replace("T", " ") + " UTC",
         "sitemap_urls": r[2], "sections": r[3]}
        for r in rows
    ]


def site_audit_count(site_url: str) -> int:
    """How many times has this specific site been audited?"""
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM audits WHERE site_url = ?",
            (site_url.rstrip("/"),)
        ).fetchone()
    return row[0] if row else 0


def popular_sites(limit: int = 10):
    """Most-audited sites — shows which outlets journalists are watching."""
    with _conn() as con:
        rows = con.execute(
            "SELECT site_url, COUNT(*) as times_audited, MAX(audited_at) as last_seen "
            "FROM audits GROUP BY site_url ORDER BY times_audited DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [
        {"site": r[0], "times_audited": r[1], "last_seen": r[2][:16].replace("T", " ") + " UTC"}
        for r in rows
    ]
