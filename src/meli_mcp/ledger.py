"""Minimal local receipt ledger for avoiding automatic duplicate answers."""
from __future__ import annotations
import sqlite3
import time
import os
from contextlib import closing
from .client import ensure_state_dir


def _connect() -> sqlite3.Connection:
    path = ensure_state_dir() / "question-attempts.sqlite3"
    conn = sqlite3.connect(path, timeout=15)
    if os.name != "nt": os.chmod(path, 0o600)
    conn.execute("CREATE TABLE IF NOT EXISTS attempts (question_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at INTEGER NOT NULL)")
    return conn


def get_attempt(question_id: int) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT state FROM attempts WHERE question_id = ?", (question_id,)).fetchone()
        return row[0] if row else None


def set_attempt(question_id: int, state: str) -> None:
    if state not in {"pending", "sent", "uncertain"}:
        raise ValueError("Invalid attempt state")
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO attempts(question_id,state,updated_at) VALUES(?,?,?) ON CONFLICT(question_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at", (question_id, state, int(time.time())))


def clear_attempt(question_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM attempts WHERE question_id=?", (question_id,))
