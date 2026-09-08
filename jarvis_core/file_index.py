"""Persistent local file index for fast Jarvis searches."""

import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from .text import normalize_text


INDEX_SKIPS = {
    ".cache", ".git", ".jarvis_data", ".venv", "node_modules", "__pycache__", ".Trash", "Trash",
    ".npm", ".cargo", ".rustup",
}


class FileIndex:
    def __init__(self, database_path, roots=None):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.roots = [Path(root).expanduser().resolve() for root in (roots or [Path.home()])]
        self._write_lock = threading.Lock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self):
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_index (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    normalized_path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified REAL NOT NULL,
                    suffix TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_file_index_name ON file_index(normalized_name);
                CREATE INDEX IF NOT EXISTS idx_file_index_kind_size ON file_index(kind, size DESC);
                CREATE INDEX IF NOT EXISTS idx_file_index_kind_modified ON file_index(kind, modified DESC);
                CREATE TABLE IF NOT EXISTS file_index_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def rebuild(self, max_items=250000, time_budget=25.0, progress=None):
        """Build in a staging table, then swap atomically so searches remain usable."""
        started = time.monotonic()
        indexed = 0
        incomplete = False
        rows = []
        with self._write_lock, closing(self._connect()) as connection:
            connection.execute("DROP TABLE IF EXISTS file_index_staging")
            connection.execute("CREATE TABLE file_index_staging AS SELECT * FROM file_index WHERE 0")
            for search_root in self.roots:
                for root, dirs, files in os.walk(
                    search_root, topdown=True, followlinks=False, onerror=lambda _e: None
                ):
                    root_path = Path(root)
                    dirs[:] = [
                        name for name in dirs
                        if name not in INDEX_SKIPS and not (root_path / name).is_symlink()
                    ]
                    entries = [(root_path / name, "folder") for name in dirs]
                    entries.extend((root_path / name, "file") for name in files)
                    for path, kind in entries:
                        if indexed >= max_items or time.monotonic() - started >= time_budget:
                            incomplete = True
                            break
                        if path.is_symlink():
                            continue
                        try:
                            stat = path.stat()
                        except OSError:
                            continue
                        path_text = str(path)
                        rows.append(
                            (
                                path_text,
                                path.name,
                                normalize_text(path.name),
                                normalize_text(path_text),
                                kind,
                                stat.st_size if kind == "file" else 0,
                                stat.st_mtime,
                                path.suffix.lower() if kind == "file" else "",
                            )
                        )
                        indexed += 1
                        if len(rows) >= 1000:
                            connection.executemany(
                                "INSERT OR REPLACE INTO file_index_staging VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                rows,
                            )
                            rows.clear()
                        if progress and indexed % 2000 == 0:
                            progress(indexed)
                    if incomplete:
                        break
                if incomplete:
                    break
            if rows:
                connection.executemany(
                    "INSERT OR REPLACE INTO file_index_staging VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
                )
            connection.execute("DELETE FROM file_index")
            connection.execute("INSERT INTO file_index SELECT * FROM file_index_staging")
            connection.execute("DROP TABLE file_index_staging")
            now = str(time.time())
            state = {
                "last_scan": now,
                "count": str(indexed),
                "incomplete": "1" if incomplete else "0",
                "duration": f"{time.monotonic() - started:.3f}",
            }
            connection.executemany(
                "INSERT OR REPLACE INTO file_index_state(key, value) VALUES (?, ?)",
                state.items(),
            )
            connection.commit()
            connection.execute("VACUUM")
        return self.status()

    def status(self):
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT key, value FROM file_index_state").fetchall()
        state = {row["key"]: row["value"] for row in rows}
        return {
            "last_scan": float(state.get("last_scan", 0)),
            "count": int(state.get("count", 0)),
            "incomplete": state.get("incomplete", "0") == "1",
            "duration": float(state.get("duration", 0)),
        }

    def is_stale(self, max_age=3600):
        return time.time() - self.status()["last_scan"] > max_age

    @staticmethod
    def _row(row):
        item = dict(row)
        item["path"] = Path(item["path"])
        item["type"] = item.pop("kind")
        return item

    def search(self, query, kind="any", limit=50):
        terms = normalize_text(query).split()
        if not terms:
            return []
        conditions = []
        parameters = []
        for term in terms:
            conditions.append("normalized_path LIKE ?")
            parameters.append(f"%{term}%")
        if kind != "any":
            conditions.append("kind = ?")
            parameters.append(kind)
        parameters.append(max(1, min(int(limit), 200)))
        sql = (
            "SELECT path, name, kind, size, modified, suffix FROM file_index WHERE "
            + " AND ".join(conditions)
            + " ORDER BY (normalized_name = ?) DESC, modified DESC LIMIT ?"
        )
        # Exact-name ranking parameter belongs immediately before LIMIT.
        parameters.insert(-1, normalize_text(query))
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row(row) for row in rows]

    def largest(self, limit=50):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT path, name, kind, size, modified, suffix FROM file_index "
                "WHERE kind = 'file' ORDER BY size DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def recent(self, limit=50):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT path, name, kind, size, modified, suffix FROM file_index "
                "WHERE kind = 'file' ORDER BY modified DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._row(row) for row in rows]
