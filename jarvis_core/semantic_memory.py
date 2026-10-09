"""Local embedding search over explicit Jarvis memories."""

import json
import math
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .text import normalize_text


class SemanticMemory:
    def __init__(self, memory_store, database_path, *, model=None, base_url=None):
        self.memory_store = memory_store
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma")
        self.base_url = (base_url or os.getenv(
            "OLLAMA_URL", "http://127.0.0.1:11434"
        )).rstrip("/")
        self._lock = threading.Lock()
        with closing(self._connect()) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    memory_id INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    PRIMARY KEY(memory_id, model)
                )
            """)
            connection.commit()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _embed(self, texts):
        payload = json.dumps({
            "model": self.model, "input": texts, "keep_alive": "1m",
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embed", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        vectors = result.get("embeddings", [])
        if len(vectors) != len(texts) or not all(
            isinstance(vector, list) and vector for vector in vectors
        ):
            raise ValueError("Ollama trả embedding không hợp lệ")
        return vectors

    @staticmethod
    def _lexical_results(query, rows, limit):
        ignored = {
            "toi", "ban", "co", "gi", "la", "cua", "cho", "nhung",
            "dieu", "trong", "bo", "nho", "hay", "ve", "da", "duoc",
            "theo", "nao", "khong",
        }
        words = set(normalize_text(query).split()) - ignored
        if not words:
            return []
        ranked = []
        for row in rows:
            content_words = set(normalize_text(row["content"]).split())
            score = len(words & content_words)
            if score:
                ranked.append((score, row["id"], row))
        ranked.sort(reverse=True)
        return [item[2] for item in ranked[:limit]]

    def search(self, query, limit=5):
        """Return relevant memories; fall back to lexical search if Ollama is down."""
        query = str(query).strip()[:500]
        limit = max(1, min(int(limit), 8))
        if not query:
            return []
        rows = self.memory_store.active_for_search()
        if not rows:
            return []
        try:
            with self._lock, closing(self._connect()) as connection:
                cached = {
                    row["memory_id"]: row for row in connection.execute(
                        "SELECT memory_id, updated_at, vector FROM memory_vectors "
                        "WHERE model=?", (self.model,)
                    )
                }
                missing = [row for row in rows if row["id"] not in cached or
                           cached[row["id"]]["updated_at"] != row["updated_at"]]
                for start in range(0, len(missing), 16):
                    batch = missing[start:start + 16]
                    vectors = self._embed([item["content"][:2000] for item in batch])
                    connection.executemany(
                        "INSERT INTO memory_vectors(memory_id, model, updated_at, vector) "
                        "VALUES(?, ?, ?, ?) ON CONFLICT(memory_id, model) DO UPDATE SET "
                        "updated_at=excluded.updated_at, vector=excluded.vector",
                        [(item["id"], self.model, item["updated_at"],
                          json.dumps(vector)) for item, vector in zip(batch, vectors)],
                    )
                connection.commit()
                query_vector = self._embed([query])[0]
                vectors_by_id = {
                    row["memory_id"]: json.loads(row["vector"])
                    for row in connection.execute(
                        "SELECT memory_id, vector FROM memory_vectors WHERE model=?",
                        (self.model,),
                    )
                }
            norm = math.sqrt(sum(value * value for value in query_vector))
            if norm == 0:
                return self._lexical_results(query, rows, limit)
            scored = []
            for row in rows:
                vector = vectors_by_id.get(row["id"])
                if not vector or len(vector) != len(query_vector):
                    continue
                denominator = norm * math.sqrt(sum(value * value for value in vector))
                if denominator:
                    score = sum(a * b for a, b in zip(query_vector, vector)) / denominator
                    scored.append((score, row["id"], row))
            scored.sort(reverse=True)
            return [item[2] for item in scored[:limit] if item[0] >= 0.45]
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                json.JSONDecodeError, sqlite3.Error):
            return self._lexical_results(query, rows, limit)
