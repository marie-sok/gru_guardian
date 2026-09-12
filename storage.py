from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("guardian.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                target TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                resolved_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS fix_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                request_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                result TEXT
            )
        """)
        db.commit()


def add_event(kind: str, message: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute(
            "INSERT INTO events(created_at, kind, message) VALUES (?, ?, ?)",
            (_now(), kind, message[:4000]),
        )
        db.commit()


def open_incident(target: str, level: str, message: str) -> int:
    with closing(sqlite3.connect(DB_PATH)) as db:
        cur = db.execute(
            "INSERT INTO incidents(created_at, target, level, message) VALUES (?, ?, ?, ?)",
            (_now(), target, level, message[:4000]),
        )
        db.commit()
        return int(cur.lastrowid)


def resolve_latest_incident(target: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        row = db.execute(
            "SELECT id FROM incidents WHERE target=? AND resolved_at IS NULL ORDER BY id DESC LIMIT 1",
            (target,),
        ).fetchone()
        if row:
            db.execute("UPDATE incidents SET resolved_at=? WHERE id=?", (_now(), row[0]))
            db.commit()


def queue_fix(text: str) -> int:
    with closing(sqlite3.connect(DB_PATH)) as db:
        cur = db.execute(
            "INSERT INTO fix_requests(created_at, request_text) VALUES (?, ?)",
            (_now(), text[:4000]),
        )
        db.commit()
        return int(cur.lastrowid)


def recent_incidents(limit: int = 20) -> list[tuple]:
    with closing(sqlite3.connect(DB_PATH)) as db:
        return db.execute(
            "SELECT id, created_at, target, level, message, resolved_at FROM incidents ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def recent_events(limit: int = 30) -> list[tuple]:
    with closing(sqlite3.connect(DB_PATH)) as db:
        return db.execute(
            "SELECT id, created_at, kind, message FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def recent_fixes(limit: int = 20) -> list[tuple]:
    with closing(sqlite3.connect(DB_PATH)) as db:
        return db.execute(
            "SELECT id, created_at, request_text, status, result FROM fix_requests ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
