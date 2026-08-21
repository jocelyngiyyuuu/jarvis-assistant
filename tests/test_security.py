import tempfile
import unittest
from pathlib import Path

from jarvis_core.security import (
    RemoteSecurity,
    classify_remote_command,
    redact_sensitive,
)


class SecurityPolicyTests(unittest.TestCase):
    def test_classifies_remote_risk(self):
        self.assertEqual(classify_remote_command("tình trạng hệ thống"), "normal")
        self.assertEqual(classify_remote_command("sleep sâu lúc 23:30"), "confirm")
        self.assertEqual(classify_remote_command("hủy sleep sâu"), "confirm")
        self.assertEqual(
            classify_remote_command("gửi tin nhắn cho Minh với nội dung test"),
            "confirm",
        )
        self.assertEqual(
            classify_remote_command("sau 10s gửi tin nhắn cho Minh với nội dung test"),
            "normal",
        )
        self.assertEqual(classify_remote_command("đọc .env"), "forbidden")
        self.assertEqual(classify_remote_command("xóa vĩnh viễn file"), "forbidden")
        self.assertEqual(classify_remote_command("tóm tắt gmail"), "sensitive")
        self.assertEqual(classify_remote_command("gmail mới"), "sensitive")
        for command in (
            "đọc gmail", "gmail có gì", "tổng hợp gmail", "kiểm tra email",
            "tóm tắt mail", "tóm tắt thư điện tử",
        ):
            self.assertEqual(classify_remote_command(command), "sensitive", command)

    def test_redacts_tokens_and_passwords(self):
        sample = "DISCORD_" + "TOKEN=" + "abc.def.123456789 " + "pass" + "word=hunter2"
        value = redact_sensitive(sample)
        self.assertNotIn("abc.def", value)
        self.assertNotIn("hunter2", value)

    def test_settings_rate_limit_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            security = RemoteSecurity(Path(directory))
            security.set_discord_enabled(False)
            self.assertFalse(security.settings()["discord_enabled"])
            self.assertTrue(security.is_allowed_when_locked("help bộ nhớ"))
            for _ in range(5):
                self.assertTrue(security.rate_limit(123))
            self.assertFalse(security.rate_limit(123))
            security.audit("discord", "sleep sâu", "confirm", "cancelled", 123, 456)
            self.assertEqual(security.recent_events(1)[0]["outcome"], "cancelled")


if __name__ == "__main__":
    unittest.main()
