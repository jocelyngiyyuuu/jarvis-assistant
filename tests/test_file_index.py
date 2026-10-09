import os
import tempfile
import time
import unittest
from pathlib import Path

from jarvis_core.file_index import FileIndex


class FileIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.index = FileIndex(self.root / "index.sqlite3", [self.root / "files"])
        (self.root / "files" / "Documents").mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rebuild_search_largest_and_recent(self):
        first = self.root / "files" / "Documents" / "Báo cáo.txt"
        second = self.root / "files" / "video.bin"
        first.write_text("hello", encoding="utf-8")
        second.write_bytes(b"x" * 100)
        old = time.time() - 100
        os.utime(second, (old, old))

        status = self.index.rebuild()

        self.assertGreaterEqual(status["count"], 3)
        self.assertEqual(self.index.search("bao cao")[0]["path"], first)
        self.assertEqual(self.index.search("documents", kind="folder")[0]["type"], "folder")
        self.assertEqual(self.index.largest(1)[0]["path"], second)
        self.assertEqual(self.index.recent(1)[0]["path"], first)

    def test_status_marks_new_index_as_stale(self):
        self.assertTrue(self.index.is_stale())
        status = self.index.rebuild()
        self.assertFalse(status["incomplete"])
        self.assertFalse(self.index.is_stale())


if __name__ == "__main__":
    unittest.main()
