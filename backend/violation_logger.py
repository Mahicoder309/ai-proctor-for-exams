"""
violation_logger.py
-------------------
Handles PostgreSQL (Supabase) session and violation event logging.

Uses DATABASE_URL environment variable for the connection string.
Falls back to SQLite if DATABASE_URL is not set (for local dev without Supabase).

Schema
------
  ExamSession    : one row per exam sitting
  ViolationEvent : one row per flagged incident, FK to ExamSession
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# ── Directories (snapshots still saved locally on Render) ──────────────────
BASE_DIR     = Path(__file__).parent
SNAPSHOT_DIR = BASE_DIR / "snapshots"
REPORTS_DIR  = BASE_DIR / "reports"
SNAPSHOT_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# ── Database backend selection ─────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")   # set this on Render / .env

# psycopg2 for PostgreSQL (Supabase), sqlite3 as local fallback
if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    # Supabase PgBouncer URLs contain ?pgbouncer=true which psycopg2 can't parse.
    # Strip it and any other unrecognised query params before connecting.
    def _clean_db_url(url: str) -> str:
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(url)
        # Keep only standard params (drop pgbouncer, sslmode already handled by psycopg2)
        allowed = {"sslmode", "connect_timeout", "application_name"}
        qs = {k: v for k, v in parse_qs(parsed.query).items() if k in allowed}
        clean = parsed._replace(query=urlencode(qs, doseq=True))
        return urlunparse(clean)

    _PG_URL       = _clean_db_url(DATABASE_URL)
    _USE_POSTGRES = True
    print("[DB] Using PostgreSQL (Supabase / PgBouncer)")
else:
    import sqlite3
    _USE_POSTGRES = False
    _SQLITE_PATH  = str(BASE_DIR / "proctor.db")
    print(f"[DB] DATABASE_URL not set — falling back to SQLite at {_SQLITE_PATH}")


# ── Violation type constants ───────────────────────────────────────────────
VT_NO_FACE            = "NO_FACE"
VT_MULTI_FACE         = "MULTIPLE_FACES"
VT_HAND_OVER_FACE     = "HAND_OVER_FACE"
VT_SUSPICIOUS_GESTURE = "SUSPICIOUS_GESTURE"
VT_PHONE_DETECTED     = "PHONE_DETECTED"
VT_CHEATING_OBJECT    = "CHEATING_OBJECT"
VT_EARPHONE_DETECTED  = "EARPHONE_DETECTED"


# ── Connection helpers ─────────────────────────────────────────────────────

@contextmanager
def _get_conn():
    """Context manager that yields a DB connection + cursor, then commits."""
    if _USE_POSTGRES:
        conn = psycopg2.connect(_PG_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(_SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _execute(conn, sql: str, params=()) -> None:
    """Execute a single statement — handles param style differences."""
    if _USE_POSTGRES:
        # psycopg2 uses %s placeholders; replace ? → %s for SQLite compat
        sql = sql.replace("?", "%s")
    cur = conn.cursor()
    cur.execute(sql, params)
    cur.close()


def _fetchall(conn, sql: str, params=()) -> list[dict]:
    if _USE_POSTGRES:
        sql = sql.replace("?", "%s")
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


# ── Schema initialization ──────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist."""
    with _get_conn() as conn:
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS ExamSession (
                id          TEXT PRIMARY KEY,
                student_id  TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                ended_at    TEXT
            )
        """)
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS ViolationEvent (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                type            TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                duration_sec    REAL DEFAULT 0.0,
                snapshot_path   TEXT
            )
        """)
    print("[DB] Tables ready.")


# ── Session management ─────────────────────────────────────────────────────

class ViolationLogger:
    """Thread-safe violation logger for one exam session."""

    def __init__(self, student_id: str) -> None:
        init_db()
        self.session_id  = str(uuid.uuid4())
        self.student_id  = student_id
        self.started_at  = datetime.utcnow()
        self.ended_at: Optional[datetime] = None
        self._violations: list[dict] = []

        with _get_conn() as conn:
            _execute(conn,
                "INSERT INTO ExamSession (id, student_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
                (self.session_id, student_id, self.started_at.isoformat(), None)
            )

    # ------------------------------------------------------------------
    def log_violation(
        self,
        vtype: str,
        frame: Optional[np.ndarray] = None,
        duration_sec: float = 0.0,
    ) -> dict:
        event_id   = str(uuid.uuid4())
        ts         = datetime.utcnow()
        snap_path: Optional[str] = None

        if frame is not None:
            snap_name = f"{ts.strftime('%Y%m%d_%H%M%S')}_{vtype}_{event_id[:8]}.jpg"
            snap_path = str(SNAPSHOT_DIR / snap_name)
            cv2.imwrite(snap_path, frame)

        event = {
            "id":            event_id,
            "session_id":    self.session_id,
            "type":          vtype,
            "timestamp":     ts.isoformat(),
            "duration_sec":  duration_sec,
            "snapshot_path": snap_path,
        }

        with _get_conn() as conn:
            _execute(conn,
                "INSERT INTO ViolationEvent (id, session_id, type, timestamp, duration_sec, snapshot_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event["id"], event["session_id"], event["type"],
                 event["timestamp"], event["duration_sec"], event["snapshot_path"])
            )

        self._violations.append(event)
        return event

    # ------------------------------------------------------------------
    def end_session(self) -> dict:
        self.ended_at = datetime.utcnow()
        with _get_conn() as conn:
            _execute(conn,
                "UPDATE ExamSession SET ended_at = ? WHERE id = ?",
                (self.ended_at.isoformat(), self.session_id)
            )

        duration = (self.ended_at - self.started_at).total_seconds()
        summary = {
            "session_id":       self.session_id,
            "student_id":       self.student_id,
            "started_at":       self.started_at.isoformat(),
            "ended_at":         self.ended_at.isoformat(),
            "duration_sec":     duration,
            "total_violations": len(self._violations),
            "violations":       self._violations,
        }

        # Save JSON sidecar locally too
        json_path = REPORTS_DIR / f"session_{self.session_id[:8]}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        return summary

    # ------------------------------------------------------------------
    def get_violations(self) -> list[dict]:
        return list(self._violations)

    def violation_count(self) -> int:
        return len(self._violations)
