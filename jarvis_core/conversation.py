"""Shared conversation timeline for Terminal, Discord and GTK."""

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class ConversationStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def add(self, source, role, content):
        content = str(content or "").strip()
        if not content:
            return None
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "INSERT INTO conversation_events(source, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (source, role, content, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
            return cursor.lastrowid

    def since(self, event_id=0, limit=100):
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, source, role, content, created_at FROM conversation_events "
                "WHERE id > ? ORDER BY id ASC LIMIT ?",
                (max(0, int(event_id)), max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit=50):
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, source, role, content, created_at FROM conversation_events "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def latest_id(self):
        """Return the archive cursor without loading old messages into a new UI session."""
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest_id FROM conversation_events"
            ).fetchone()
        return int(row["latest_id"])
