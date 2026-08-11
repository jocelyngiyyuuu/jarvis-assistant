import tempfile
import unittest
from pathlib import Path

from jarvis_core.brain import NaturalLanguageRouter
from jarvis_core.file_manager import SmartFileManager
from jarvis_core.memory import MemoryStore
from jarvis_core.monitoring import SystemMonitor
from jarvis_core.window_manager import ChromeWindowManager, FileWindowManager


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = NaturalLanguageRouter()

    def test_memory_intent_preserves_vietnamese(self):
        intent = self.brain.detect("ghi nhớ tôi thích cà phê")
        self.assertEqual(intent.name, "memory.remember")
        self.assertEqual(intent.entities["content"], "tôi thích cà phê")

    def test_natural_system_alias(self):
        self.assertEqual(self.brain.detect("máy đang thế nào").name, "system.status")

    def test_unknown_command_falls_back(self):
        self.assertEqual(self.brain.detect("mở youtube").name, "legacy")

    def test_legacy_file_search_is_not_intercepted(self):
        self.assertEqual(self.brain.detect("tìm file báo cáo").name, "legacy")

    def test_smart_file_search_has_its_own_intent(self):
        intent = self.brain.detect("tìm thông minh báo cáo")
        self.assertEqual(intent.name, "files.search")
        self.assertEqual(intent.entities["query"], "báo cáo")


class MemoryTests(unittest.TestCase):
    def test_memory_is_persistent_and_accent_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            MemoryStore(path).remember("Tôi thích cà phê")
            rows = MemoryStore(path).recall("ca phe")
            self.assertEqual(rows[0]["content"], "Tôi thích cà phê")
            self.assertEqual(MemoryStore(path).forget("cà phê"), 1)


class FileManagerTests(unittest.TestCase):
    def test_exact_filename_ranks_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "report.txt").write_text("one", encoding="utf-8")
            (root / "old-report-copy.txt").write_text("two", encoding="utf-8")
            results = SmartFileManager([root]).search("report.txt")
            self.assertEqual(results[0]["path"].name, "report.txt")


class MonitoringTests(unittest.TestCase):
    def test_snapshot_contains_capacity_values(self):
        snapshot = SystemMonitor().snapshot()
        self.assertGreater(snapshot["disk_total"], 0)
        self.assertGreater(snapshot["cpu_count"], 0)


class WindowManagerTests(unittest.TestCase):
    def test_wmctrl_output_parser_preserves_window_title(self):
        output = "0x01200007  0 host Báo cáo quý - Document Viewer\n"
        windows = FileWindowManager._parse_windows(output)
        self.assertEqual(windows[0]["id"], "0x01200007")
        self.assertEqual(windows[0]["title"], "Báo cáo quý - Document Viewer")

    def test_file_and_folder_windows_are_tracked_separately(self):
        manager = FileWindowManager()
        manager.last_windows["file"] = {"id": "0x1", "path": "/tmp/a.txt", "title": "a"}
        manager.last_windows["folder"] = {"id": "0x2", "path": "/tmp/project", "title": "project"}
        self.assertEqual(manager.last_windows["file"]["id"], "0x1")
        self.assertEqual(manager.last_windows["folder"]["id"], "0x2")

    def test_chrome_profiles_are_tracked_separately(self):
        manager = ChromeWindowManager()
        manager.profile_windows["Default"] = [{"id": "0x1", "title": "Personal"}]
        manager.profile_windows["Profile 1"] = [{"id": "0x2", "title": "Study"}]
        manager.clear()
        self.assertEqual(manager.profile_windows, {})


if __name__ == "__main__":
    unittest.main()
