import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jarvis


class LauncherResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        jarvis.last_command_response = None
        jarvis.youtube_page_id = None
        jarvis.youtube_videos = []

    def assert_specific_response(self):
        self.assertTrue(jarvis.last_command_response)
        self.assertNotEqual(
            jarvis.last_command_response, "✅ Jarvis đã xử lý lệnh."
        )

    def test_google_search_success_sets_specific_response(self):
        with patch.object(jarvis, "open_chrome", return_value=True):
            self.assertTrue(jarvis.search_google(
                "google thời tiết học", allow_prompt=False
            ))
        self.assertIn("Đã mở kết quả Google", jarvis.last_command_response)

    def test_show_desktop_success_sets_specific_response(self):
        with patch.object(
            jarvis.subprocess, "run", return_value=SimpleNamespace(returncode=0)
        ):
            self.assertTrue(jarvis.show_desktop())
        self.assertIn("Đã về Desktop", jarvis.last_command_response)

    async def test_show_youtube_missing_tab_sets_specific_response(self):
        with patch.object(jarvis.subprocess, "run"), patch.object(
            jarvis.asyncio, "sleep", new=AsyncMock()
        ), patch.object(
            jarvis, "get_mcp_session", new=AsyncMock(return_value=object())
        ), patch.object(
            jarvis, "find_youtube_page", new=AsyncMock(return_value=None)
        ):
            self.assertFalse(await jarvis.show_youtube())
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không tìm thấy tab YouTube đang mở.",
        )

    async def test_open_video_without_list_sets_specific_response(self):
        self.assertFalse(await jarvis.open_video(1))
        self.assert_specific_response()

    async def test_youtube_search_failure_sets_specific_response(self):
        with patch.object(
            jarvis, "get_mcp_session",
            new=AsyncMock(side_effect=RuntimeError("private detail")),
        ):
            self.assertFalse(await jarvis.search_youtube("tìm youtube piano"))
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không thể tìm kiếm YouTube lúc này.",
        )
        self.assertNotIn("private detail", jarvis.last_command_response)


if __name__ == "__main__":
    unittest.main()
