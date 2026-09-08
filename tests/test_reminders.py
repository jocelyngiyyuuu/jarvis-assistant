import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis import parse_reminder_command, parse_scheduled_zalo_command
from jarvis_core.reminders import ReminderStore


class ReminderCommandTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone(timedelta(hours=7)))

    def test_delay(self):
        due, content = parse_reminder_command("nhắc tôi sau 30p kiểm tra đơn", self.now)
        self.assertEqual(due, self.now + timedelta(minutes=30))
        self.assertEqual(content, "kiểm tra đơn")

    def test_compound_duration_does_not_leak_into_content(self):
        due, content = parse_reminder_command(
            "nhắc tôi sau 1h15p đi phơi đồ", self.now
        )
        self.assertEqual(due, self.now + timedelta(hours=1, minutes=15))
        self.assertEqual(content, "đi phơi đồ")

    def test_compound_duration_with_spaces(self):
        due, content = parse_reminder_command(
            "nhắc tôi sau 1h 15p nữa: đi phơi đồ", self.now
        )
        self.assertEqual(due, self.now + timedelta(hours=1, minutes=15))
        self.assertEqual(content, "đi phơi đồ")

    def test_natural_create_reminder_content_before_duration(self):
        due, content = parse_reminder_command(
            "tạo nhắc nhở phơi đồ sau 1h15p", self.now
        )
        self.assertEqual(due, self.now + timedelta(hours=1, minutes=15))
        self.assertEqual(content, "phơi đồ")

    def test_natural_reminder_with_duration_first(self):
        due, content = parse_reminder_command(
            "sau 1h15p hãy nhắc tôi đi phơi đồ", self.now
        )
        self.assertEqual(due, self.now + timedelta(hours=1, minutes=15))
        self.assertEqual(content, "đi phơi đồ")

    def test_natural_reminder_with_spaced_duration_first(self):
        due, content = parse_reminder_command(
            "sau 1h 15p nhắc tôi đi phơi đồ", self.now
        )
        self.assertEqual(due, self.now + timedelta(hours=1, minutes=15))
        self.assertEqual(content, "đi phơi đồ")

    def test_specific_clock_and_date(self):
        due, content = parse_reminder_command(
            "nhắc tôi lúc 19:30 ngày 13/08/2026: gọi khách hàng", self.now
        )
        self.assertEqual(due, datetime(2026, 8, 13, 19, 30, tzinfo=self.now.tzinfo))
        self.assertEqual(content, "gọi khách hàng")

    def test_scheduled_zalo_message(self):
        due, recipient, content, tag = parse_scheduled_zalo_command(
            "nhắn zalo cho Minh lúc 19:30 ngày 13/08/2026: kiểm tra đơn", self.now
        )
        self.assertEqual(due, datetime(2026, 8, 13, 19, 30, tzinfo=self.now.tzinfo))
        self.assertEqual(recipient, "Minh")
        self.assertEqual(content, "kiểm tra đơn")
        self.assertEqual(tag, "")

    def test_scheduled_zalo_message_in_tag(self):
        _due, recipient, content, tag = parse_scheduled_zalo_command(
            "nhắn zalo thẻ Trả lời sau cho Minh lúc 19:30: kiểm tra đơn", self.now
        )
        self.assertEqual(recipient, "Minh")
        self.assertEqual(content, "kiểm tra đơn")
        self.assertEqual(tag, "Trả lời sau")

    def test_scheduled_zalo_message_after_duration_with_nua(self):
        due, recipient, content, tag = parse_scheduled_zalo_command(
            "nhắn zalo cho Minh sau 2h nữa: Nội dung cần gửi", self.now
        )
        self.assertEqual(due, self.now + timedelta(hours=2))
        self.assertEqual(recipient, "Minh")
        self.assertEqual(content, "Nội dung cần gửi")
        self.assertEqual(tag, "")

    def test_scheduled_zalo_message_with_duration_first(self):
        due, recipient, content, tag = parse_scheduled_zalo_command(
            "sau 1h15p hãy nhắn zalo cho Suri với nội dung ra phơi đồ với a2",
            self.now,
        )
        self.assertEqual(due, self.now + timedelta(hours=1, minutes=15))
        self.assertEqual(recipient, "Suri")
        self.assertEqual(content, "ra phơi đồ với a2")
        self.assertEqual(tag, "")

    def test_scheduled_zalo_message_with_tag_and_duration_first(self):
        due, recipient, content, tag = parse_scheduled_zalo_command(
            "sau 30p nhắn zalo thẻ Gia đình cho Suri với nội dung gọi cho tôi",
            self.now,
        )
        self.assertEqual(due, self.now + timedelta(minutes=30))
        self.assertEqual(recipient, "Suri")
        self.assertEqual(content, "gọi cho tôi")
        self.assertEqual(tag, "Gia đình")

    def test_scheduled_generic_message_defaults_to_zalo_study(self):
        due, recipient, content, tag = parse_scheduled_zalo_command(
            "sau 10s gửi tin nhắn cho Minh với nội dung tét", self.now
        )
        self.assertEqual(due, self.now + timedelta(seconds=10))
        self.assertEqual(recipient, "Minh")
        self.assertEqual(content, "tét")
        self.assertEqual(tag, "")


class ReminderStoreTests(unittest.TestCase):
    def test_persists_claims_and_cancels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reminders.sqlite3"
            store = ReminderStore(path)
            now = datetime.now().astimezone()
            due_id = store.add("việc đến hạn", now - timedelta(seconds=1), "test")
            future_id = store.add("việc tương lai", now + timedelta(hours=1), "test")
            reopened = ReminderStore(path)
            self.assertEqual([row["id"] for row in reopened.due(now)], [due_id])
            reopened.mark_delivered(due_id)
            self.assertEqual(reopened.due(now), [])
            self.assertTrue(reopened.cancel(future_id))
            self.assertEqual(reopened.pending(), [])

    def test_pending_rows_include_content_and_type_for_cancel_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ReminderStore(Path(folder) / "reminders.sqlite3")
            reminder_id = store.add(
                "đi phơi đồ",
                datetime.now().astimezone() + timedelta(hours=1, minutes=15),
                "discord",
            )
            row = store.pending()[0]
            self.assertEqual(row["id"], reminder_id)
            self.assertEqual(row["content"], "đi phơi đồ")
            self.assertEqual(row["kind"], "reminder")


if __name__ == "__main__":
    unittest.main()
