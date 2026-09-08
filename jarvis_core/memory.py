"""Persistent, local memory for Jarvis."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .text import normalize_text


class MemoryStore:
    """Store searchable memories in a small SQLite database."""

    CATEGORIES = {"general", "preference", "profile", "task", "fact", "device", "temporary"}

    def __init__(self, database_path):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _transaction(self):
        """Commit successful operations and always close the SQLite handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._transaction() as connection:
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

            # Migration không phá dữ liệu từ schema Memory v1.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(memories)").fetchall()
            }
            migrations = {
                "category": "TEXT NOT NULL DEFAULT 'general'",
                "updated_at": "TEXT",
                "expires_at": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE memories ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                "UPDATE memories SET updated_at = created_at WHERE updated_at IS NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_normalized "
                "ON memories(normalized)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_category "
                "ON memories(category)"
            )

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc)

    def _purge_expired(self, connection):
        connection.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (self._utc_now().isoformat(),),
        )

    def remember(self, content, tags=None, category="general", expires_in=None):
        content = str(content).strip()
        if not content:
            raise ValueError("Nội dung ghi nhớ không được để trống.")
        category = normalize_text(category or "general")
        if category not in self.CATEGORIES:
            category = "general"
        now_value = self._utc_now()
        now = now_value.isoformat()
        expires_at = None
        if expires_in is not None:
            expires_at = (now_value + timedelta(seconds=max(1, int(expires_in)))).isoformat()
            category = "temporary"
        normalized = normalize_text(content)
        with self._lock, self._transaction() as connection:
            self._purge_expired(connection)
            duplicate = connection.execute(
                "SELECT id FROM memories WHERE normalized = ? AND category = ?",
                (normalized, category),
            ).fetchone()
            if duplicate:
                connection.execute(
                    "UPDATE memories SET accessed_at = ? WHERE id = ?",
                    (now, duplicate["id"]),
                )
                return duplicate["id"], False
            cursor = connection.execute(
                "INSERT INTO memories(content, normalized, tags, created_at, accessed_at, "
                "category, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content,
                    normalized,
                    json.dumps(tags or [], ensure_ascii=False),
                    now,
                    now,
                    category,
                    now,
                    expires_at,
                ),
            )
            return cursor.lastrowid, True

    def recall(self, query="", limit=10, category=None):
        query = normalize_text(query)
        sql = (
            "SELECT id, content, tags, created_at, updated_at, category, expires_at "
            "FROM memories"
        )
        conditions = []
        parameters = []
        if query:
            # Mọi từ khóa đều phải xuất hiện, nhưng không cần đúng thứ tự.
            for word in dict.fromkeys(query.split()):
                conditions.append("normalized LIKE ?")
                parameters.append(f"%{word}%")
        if category:
            conditions.append("category = ?")
            parameters.append(normalize_text(category))
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC LIMIT ?"
        parameters.append(max(1, min(int(limit), 50)))
        with self._lock, self._transaction() as connection:
            self._purge_expired(connection)
            rows = connection.execute(sql, parameters).fetchall()
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE memories SET accessed_at = ? WHERE id IN ({placeholders})",
                    [datetime.now(timezone.utc).isoformat(), *ids],
                )
        return [dict(row) for row in rows]

    def update(self, memory_id, content):
        content = str(content).strip()
        if not content:
            raise ValueError("Nội dung cập nhật không được để trống.")
        now = self._utc_now().isoformat()
        with self._lock, self._transaction() as connection:
            self._purge_expired(connection)
            cursor = connection.execute(
                "UPDATE memories SET content = ?, normalized = ?, updated_at = ? "
                "WHERE id = ?",
                (content, normalize_text(content), now, int(memory_id)),
            )
            return cursor.rowcount > 0

    def forget_id(self, memory_id):
        with self._lock, self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE id = ?", (int(memory_id),)
            )
            return cursor.rowcount > 0

    def forget(self, query):
        query = normalize_text(query)
        if not query:
            return 0
        with self._lock, self._transaction() as connection:
            self._purge_expired(connection)
            cursor = connection.execute(
                "DELETE FROM memories WHERE normalized LIKE ?", (f"%{query}%",)
            )
            return cursor.rowcount
