import unittest
from unittest.mock import patch

import jarvis


class ChromeShortcutTests(unittest.TestCase):
    def test_zalo_defaults_to_study_profile(self):
        with patch("jarvis.open_chrome", return_value=True) as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo"))
        opened.assert_called_once_with(
            jarvis.STUDY_PROFILE, "https://chat.zalo.me/"
        )
        self.assertEqual(jarvis.last_command_response, "✅ Đã mở Zalo.")

    def test_explicit_personal_zalo_overrides_default(self):
        with patch("jarvis.open_chrome", return_value=True) as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo cá nhân"))
        opened.assert_called_once_with(
            jarvis.PERSONAL_PROFILE, "https://chat.zalo.me/"
        )
        self.assertEqual(jarvis.last_command_response, "✅ Đã mở Zalo.")

    def test_zalo_does_not_claim_opened_when_chrome_fails(self):
        with patch("jarvis.open_chrome", return_value=False):
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo"))
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không thể mở Zalo lúc này.",
        )


if __name__ == "__main__":
    unittest.main()
