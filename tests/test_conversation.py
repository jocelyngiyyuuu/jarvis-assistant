import tempfile
import unittest
from pathlib import Path

from jarvis_core.conversation import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def test_shared_timeline_preserves_source_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversation.sqlite3")
            first = store.add("discord", "user", "tình trạng hệ thống")
            second = store.add("discord", "assistant", "Máy đang ổn định")
            rows = store.since(0)

            self.assertEqual([row["id"] for row in rows], [first, second])
            self.assertEqual(rows[0]["source"], "discord")
            self.assertEqual(rows[1]["role"], "assistant")

    def test_recent_is_returned_in_chronological_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversation.sqlite3")
            store.add("gtk", "user", "một")
            store.add("gtk", "user", "hai")
            store.add("gtk", "user", "ba")

            self.assertEqual(
                [row["content"] for row in store.recent(2)], ["hai", "ba"]
            )


if __name__ == "__main__":
    unittest.main()
