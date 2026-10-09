"""Persistent reminder storage shared by GTK, Discord and Terminal."""

import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path


class ReminderStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(reminders)")}
            if "kind" not in columns:
                connection.execute("ALTER TABLE reminders ADD COLUMN kind TEXT NOT NULL DEFAULT 'reminder'")
            if "recipient" not in columns:
                connection.execute("ALTER TABLE reminders ADD COLUMN recipient TEXT NOT NULL DEFAULT ''")
            if "zalo_tag" not in columns:
                connection.execute("ALTER TABLE reminders ADD COLUMN zalo_tag TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE reminders SET status='pending' WHERE status='awaiting_confirmation'")
            connection.commit()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def add(self, content, due_at, source="unknown", kind="reminder", recipient="", zalo_tag=""):
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "INSERT INTO reminders(content,due_at,source,status,created_at,kind,recipient,zalo_tag) VALUES(?,?,?,?,?,?,?,?)",
                (content.strip(), due_at.isoformat(), source, "pending", datetime.now().astimezone().isoformat(), kind, recipient.strip(), zalo_tag.strip()),
            )
            connection.commit()
            return cursor.lastrowid

    def pending(self):
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id,content,due_at,source,kind,recipient,zalo_tag FROM reminders WHERE status='pending' ORDER BY due_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def due(self, now):
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id,content,due_at,source,kind,recipient,zalo_tag FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at",
                (now.isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_delivered(self, reminder_id):
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE reminders SET status='delivered' WHERE id=? AND status='pending'",
                (int(reminder_id),),
            )
            connection.commit()

    def mark_awaiting(self, reminder_id):
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE reminders SET status='awaiting_confirmation' WHERE id=? AND status='pending'",
                (int(reminder_id),),
            )
            connection.commit()

    def mark_cancelled(self, reminder_id):
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=?",
                (int(reminder_id),),
            )
            connection.commit()

    def cancel(self, reminder_id):
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=? AND status='pending'",
                (int(reminder_id),),
            )
            connection.commit()
            return cursor.rowcount > 0
