"""Durable, single-entry schedule for shutting down the remote PC."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class PcShutdownScheduleStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS pc_shutdown_schedule (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )
            """)
            connection.commit()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self):
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT due_at, status, detail FROM pc_shutdown_schedule WHERE id=1"
            ).fetchone()
        return dict(row) if row else None

    def set_pending(self, due_at):
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must include a timezone")
        with self._lock, closing(self._connect()) as connection:
            connection.execute("""
                INSERT INTO pc_shutdown_schedule(id, due_at, status, detail)
                VALUES(1, ?, 'pending', '')
                ON CONFLICT(id) DO UPDATE SET
                    due_at=excluded.due_at, status='pending', detail=''
            """, (due_at.isoformat(),))
            connection.commit()

    def transition(self, due_at, from_status, to_status, detail=""):
        """Update only the same schedule, so an old task cannot change a new one."""
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute("""
                UPDATE pc_shutdown_schedule SET status=?, detail=?
                WHERE id=1 AND due_at=? AND status=?
            """, (to_status, detail[:500], due_at.isoformat(), from_status))
            connection.commit()
            return cursor.rowcount == 1

    def cancel_pending(self):
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute("""
                UPDATE pc_shutdown_schedule SET status='cancelled', detail=''
                WHERE id=1 AND status='pending'
            """)
            connection.commit()
            return cursor.rowcount == 1
