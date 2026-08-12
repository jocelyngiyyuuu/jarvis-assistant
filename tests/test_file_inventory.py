import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_core.brain import NaturalLanguageRouter
from jarvis_core.file_manager import SmartFileManager


class FileInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manager = SmartFileManager([self.root])

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _allocate(path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as file_handle:
            file_handle.truncate(size)

    def test_largest_files_are_sorted(self):
        small = self.root / "small.bin"
        large = self.root / "large.bin"
        self._allocate(small, 2 * 1024 * 1024)
        self._allocate(large, 5 * 1024 * 1024)

        with patch.object(self.manager, "_open_file_owners", return_value={}):
            results = self.manager.largest(limit=2, minimum_size=1)

        self.assertEqual(results[0]["path"], large)
        self.assertEqual(results[1]["path"], small)

    def test_largest_falls_back_below_threshold(self):
        item = self.root / "small.txt"
        self._allocate(item, 512)
        with patch.object(self.manager, "_open_file_owners", return_value={}):
            results = self.manager.largest(limit=5, minimum_size=1024 * 1024)
        self.assertEqual([result["path"] for result in results], [item])

    def test_duplicate_files_are_verified_by_hash(self):
        first = self.root / "one.bin"
        second = self.root / "copy.bin"
        different = self.root / "different.bin"
        first.write_bytes(b"same-content")
        second.write_bytes(b"same-content")
        different.write_bytes(b"other-content")

        groups = self.manager.duplicate_files(limit=5, minimum_size=1)

        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0]["paths"]), {first, second})

    def test_cache_is_candidate_but_database_is_protected(self):
        cache = self.root / ".cache" / "download.tmp"
        database = self.root / ".cache" / "memory.sqlite3"
        self._allocate(cache, 2 * 1024 * 1024)
        self._allocate(database, 2 * 1024 * 1024)

        with patch.object(self.manager, "_open_file_owners", return_value={}):
            candidates = self.manager.cleanup_candidates(minimum_size=1)
            database_analysis = self.manager.analyze(database, {})

        self.assertIn(cache, [item["path"] for item in candidates])
        self.assertNotIn(database, [item["path"] for item in candidates])
        self.assertEqual(database_analysis["recommendation"], "không xóa")

    def test_open_file_is_never_cleanup_candidate(self):
        cache = self.root / "__pycache__" / "active.pyc"
        self._allocate(cache, 2 * 1024 * 1024)
        owners = {str(cache.resolve()): {"python (PID 123)"}}

        analysis = self.manager.analyze(cache, owners)

        self.assertEqual(analysis["importance"], "critical")
        self.assertEqual(analysis["cleanup_score"], 0)
        self.assertEqual(analysis["recommendation"], "không xóa")

    def test_old_log_is_review_candidate(self):
        log = self.root / "application.log"
        self._allocate(log, 1024)
        old = time.time() - 10 * 86400
        os.utime(log, (old, old))

        analysis = self.manager.analyze(log, {})

        self.assertGreater(analysis["cleanup_score"], 0)
        self.assertEqual(analysis["recommendation"], "xem xét thủ công")

    def test_directory_usage_ranks_direct_children(self):
        large = self.root / "large"
        small = self.root / "small"
        self._allocate(large / "data.bin", 4096)
        self._allocate(small / "data.bin", 1024)

        report = self.manager.directory_usage(self.root)

        self.assertFalse(report["incomplete"])
        self.assertEqual(report["items"][0]["path"], large)
        self.assertEqual(report["items"][0]["size"], 4096)

    def test_directory_usage_does_not_follow_symlinks(self):
        outside = self.root / "outside.bin"
        self._allocate(outside, 8192)
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "link.bin").symlink_to(outside)

        report = self.manager.directory_usage(folder)

        self.assertEqual(report["items"], [])


class FileInventoryRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = NaturalLanguageRouter()

    def test_inventory_commands(self):
        self.assertEqual(self.router.detect("file lớn nhất").name, "files.largest")
        self.assertEqual(self.router.detect("tìm file trùng").name, "files.duplicates")
        self.assertEqual(
            self.router.detect("downloads cũ").name, "files.stale_downloads"
        )
        self.assertEqual(
            self.router.detect("quét file có thể dọn").name,
            "files.cleanup_candidates",
        )
        self.assertEqual(
            self.router.detect("file đang được sử dụng").name,
            "files.open",
        )
        intent = self.router.detect("phân tích file /tmp/example.log")
        self.assertEqual(intent.name, "files.analyze")
        self.assertEqual(intent.entities["path"], "/tmp/example.log")
        self.assertEqual(
            self.router.detect("dung lượng ổ đĩa").name,
            "files.disk_usage",
        )
        usage = self.router.detect("dung lượng thư mục /tmp")
        self.assertEqual(usage.name, "files.directory_usage")
        self.assertEqual(usage.entities["path"], "/tmp")


if __name__ == "__main__":
    unittest.main()
