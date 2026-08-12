import unittest
from unittest.mock import patch

import jarvis


class ChromeShortcutTests(unittest.TestCase):
    def test_zalo_defaults_to_study_profile(self):
        with patch("jarvis.open_chrome") as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo"))
        opened.assert_called_once_with(
            jarvis.STUDY_PROFILE, "https://chat.zalo.me/"
        )

    def test_explicit_personal_zalo_overrides_default(self):
        with patch("jarvis.open_chrome") as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo cá nhân"))
        opened.assert_called_once_with(
            jarvis.PERSONAL_PROFILE, "https://chat.zalo.me/"
        )


if __name__ == "__main__":
    unittest.main()
