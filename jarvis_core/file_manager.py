"""Ranked local file discovery for Jarvis."""

import os
import time
from pathlib import Path

from .text import normalize_text


DEFAULT_SKIPS = {
    ".cache", ".git", ".npm", ".cargo", ".rustup", ".venv",
    "__pycache__", "node_modules", "Trash",
}


class SmartFileManager:
    def __init__(self, roots=None, skip_names=None):
        self.roots = [Path(root).expanduser() for root in (roots or [Path.home()])]
        self.skip_names = set(skip_names or DEFAULT_SKIPS)

    def _walk(self):
        for search_root in self.roots:
            for root, dirs, files in os.walk(search_root, topdown=True, onerror=lambda _e: None):
                dirs[:] = [name for name in dirs if name not in self.skip_names]
                root_path = Path(root)
                for name in dirs:
                    yield root_path / name, "folder"
                for name in files:
                    yield root_path / name, "file"

    def search(self, query, item_type="any", limit=20):
        terms = normalize_text(query).split()
        if not terms:
            return []
        matches = []
        now = time.time()
        for path, kind in self._walk():
            if item_type != "any" and kind != item_type:
                continue
            name = normalize_text(path.name)
            full = normalize_text(path)
            if not all(term in full for term in terms):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            exact = int(name == " ".join(terms))
            prefix = int(name.startswith(" ".join(terms)))
            name_hits = sum(term in name for term in terms)
            age_days = max(0.0, (now - stat.st_mtime) / 86400)
            score = exact * 100 + prefix * 40 + name_hits * 10 + 10 / (1 + age_days)
            matches.append({"path": path, "type": kind, "score": score, "modified": stat.st_mtime})
        matches.sort(key=lambda item: (-item["score"], -item["modified"], str(item["path"])))
        return matches[:max(1, min(int(limit), 100))]

    def recent(self, limit=10, item_type="file"):
        results = []
        for path, kind in self._walk():
            if item_type != "any" and kind != item_type:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            results.append({"path": path, "type": kind, "modified": stat.st_mtime})
        results.sort(key=lambda item: item["modified"], reverse=True)
        return results[:max(1, min(int(limit), 100))]
