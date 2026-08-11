"""Persistent, local memory for Jarvis."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .text import normalize_text


class MemoryStore:
    """Store searchable memories in a small SQLite database."""

    def __init__(self, database_path):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    normalized TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL
                )
                """
            )

    def remember(self, content, tags=None):
        content = str(content).strip()
        if not content:
            raise ValueError("Nội dung ghi nhớ không được để trống.")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO memories(content, normalized, tags, created_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (content, normalize_text(content), json.dumps(tags or [], ensure_ascii=False), now, now),
            )
            return cursor.lastrowid

    def recall(self, query="", limit=10):
        query = normalize_text(query)
        sql = "SELECT id, content, tags, created_at FROM memories"
        parameters = []
        if query:
            sql += " WHERE normalized LIKE ?"
            parameters.append(f"%{query}%")
        sql += " ORDER BY id DESC LIMIT ?"
        parameters.append(max(1, min(int(limit), 50)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE memories SET accessed_at = ? WHERE id IN ({placeholders})",
                    [datetime.now(timezone.utc).isoformat(), *ids],
                )
        return [dict(row) for row in rows]

    def forget(self, query):
        query = normalize_text(query)
        if not query:
            return 0
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE normalized LIKE ?", (f"%{query}%",)
            )
            return cursor.rowcount
