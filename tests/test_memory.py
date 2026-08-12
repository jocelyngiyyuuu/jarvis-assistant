import sqlite3
import tempfile
import unittest
from pathlib import Path

from jarvis_core.brain import NaturalLanguageRouter
from jarvis_core.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "memory.sqlite3"
        self.memory = MemoryStore(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prevents_exact_duplicates_in_same_category(self):
        first_id, created = self.memory.remember("Tôi thích cà phê", category="preference")
        second_id, created_again = self.memory.remember("tôi thích cà phê", category="preference")

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first_id, second_id)

    def test_multi_word_recall_does_not_require_word_order(self):
        self.memory.remember("Laptop sử dụng card NVIDIA RTX 3050", category="device")

        rows = self.memory.recall("RTX laptop")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "device")

    def test_update_and_forget_by_id(self):
        memory_id, _ = self.memory.remember("Thông tin cũ")

        self.assertTrue(self.memory.update(memory_id, "Thông tin mới"))
        self.assertEqual(self.memory.recall("mới")[0]["content"], "Thông tin mới")
        self.assertTrue(self.memory.forget_id(memory_id))
        self.assertEqual(self.memory.recall("mới"), [])

    def test_expired_memory_is_removed(self):
        memory_id, _ = self.memory.remember("Sắp hết hạn", expires_in=60)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE memories SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (memory_id,),
            )

        self.assertEqual(self.memory.recall("hết hạn"), [])

    def test_migrates_v1_database(self):
        old_database = Path(self.temp_dir.name) / "old.sqlite3"
        with sqlite3.connect(old_database) as connection:
            connection.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    normalized TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO memories(content, normalized, created_at, accessed_at) "
                "VALUES ('Ký ức cũ', 'ky uc cu', '2026-01-01', '2026-01-01')"
            )

        migrated = MemoryStore(old_database)
        row = migrated.recall("ký ức")[0]
        self.assertEqual(row["category"], "general")
        self.assertEqual(row["content"], "Ký ức cũ")


class MemoryRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = NaturalLanguageRouter()

    def test_category_command(self):
        intent = self.router.detect("ghi nhớ thiết bị: laptop dùng RTX 3050")
        self.assertEqual(intent.name, "memory.remember")
        self.assertEqual(intent.entities["category"], "device")
        self.assertEqual(intent.entities["content"], "laptop dùng RTX 3050")

    def test_temporary_command(self):
        intent = self.router.detect("ghi nhớ tạm thời 2 ngày: đang kiểm tra suspend")
        self.assertEqual(intent.entities["expires_in"], 2 * 86400)
        self.assertEqual(intent.entities["content"], "đang kiểm tra suspend")

    def test_update_and_delete_id_commands(self):
        update = self.router.detect("cập nhật mục 12 thành nội dung mới")
        delete = self.router.detect("quên mục 12")
        self.assertEqual(update.name, "memory.update")
        self.assertEqual(update.entities["id"], 12)
        self.assertEqual(update.entities["content"], "nội dung mới")
        self.assertEqual(delete.name, "memory.forget_id")
        self.assertEqual(delete.entities["id"], 12)


if __name__ == "__main__":
    unittest.main()
