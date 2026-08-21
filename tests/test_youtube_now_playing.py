import json
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

import jarvis


class FakeResult:
    def __init__(self, value, *, is_error=False):
        self.content = [type("TextContent", (), {"text": json.dumps(value)})()]
        self.isError = is_error


class FakeSession:
    def __init__(self, state):
        self.state = state
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "evaluate_script":
            return FakeResult(self.state)
        return FakeResult({})


class YouTubeNowPlayingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        jarvis.youtube_page_id = 17
        jarvis.last_command_response = None

    def test_recognizes_natural_vietnamese_queries(self):
        for command in (
            "youtube đang phát gì",
            "đang phát gì",
            "video đang phát",
            "bài gì đang phát",
            "YouTube đang phát gì?",
            " đang phát gì... ",
        ):
            self.assertTrue(jarvis.is_youtube_now_playing_command(command), command)
        self.assertFalse(jarvis.is_youtube_now_playing_command("phát tiếp video"))

    async def test_reports_playing_video_without_mutating_playback(self):
        session = FakeSession({
            "ok": True,
            "title": "Một ngày mới",
            "channel": "Kênh Thử Nghiệm",
            "paused": False,
            "ended": False,
            "currentTime": 65,
            "duration": 245,
            "url": "https://www.youtube.com/watch?v=abc",
        })

        with patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=session)), \
             patch.object(jarvis, "find_youtube_page", AsyncMock(return_value=17)):
            handled = await jarvis.report_youtube_now_playing()

        self.assertTrue(handled)
        self.assertIn("Một ngày mới", jarvis.last_command_response)
        self.assertIn("Kênh Thử Nghiệm", jarvis.last_command_response)
        self.assertIn("Đang phát", jarvis.last_command_response)
        self.assertIn("1:05 / 4:05", jarvis.last_command_response)
        select = session.calls[0]
        self.assertEqual(select, (
            "select_page", {"pageId": 17, "bringToFront": False}
        ))
        script = session.calls[1][1]["function"]
        self.assertNotIn(".play(", script)
        self.assertNotIn(".pause(", script)
        self.assertNotIn("navigate_page", [name for name, _args in session.calls])
        self.assertNotIn("click", [name for name, _args in session.calls])

    async def test_escapes_untrusted_markdown_and_rejects_non_youtube_url(self):
        session = FakeSession({
            "ok": True,
            "title": "[nhấn vào đây](https://evil.example)",
            "channel": "# Kênh ||giả||",
            "paused": False,
            "ended": False,
            "currentTime": 1,
            "duration": 2,
            "url": "https://evil.example/watch?v=abc",
        })
        with patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=session)), \
             patch.object(jarvis, "find_youtube_page", AsyncMock(return_value=None)):
            await jarvis.report_youtube_now_playing()
        self.assertNotIn("https://evil.example", jarvis.last_command_response)
        self.assertNotIn("[nhấn vào đây]", jarvis.last_command_response)
        self.assertIn("Không tìm thấy tab YouTube", jarvis.last_command_response)

    async def test_select_tool_error_invalidates_cached_page(self):
        class SelectErrorSession(FakeSession):
            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                if name == "select_page":
                    return FakeResult({"error": "stale"}, is_error=True)
                return FakeResult(self.state)

        session = SelectErrorSession({})
        with patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=session)), \
             patch.object(jarvis, "find_youtube_page", AsyncMock(return_value=None)):
            await jarvis.report_youtube_now_playing()
        self.assertEqual(jarvis.youtube_page_id, None)
        self.assertIn("Không tìm thấy tab YouTube", jarvis.last_command_response)

    async def test_remote_response_does_not_expose_raw_mcp_exception(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
                jarvis, "get_mcp_session",
                AsyncMock(side_effect=RuntimeError("secret path /home/khoa/token")),
            ):
                await jarvis.report_youtube_now_playing()
        self.assertNotIn("/home/khoa/token", jarvis.last_command_response)
        self.assertNotIn("/home/khoa/token", output.getvalue())
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không thể đọc trạng thái YouTube lúc này.",
        )

    async def test_reports_paused_state(self):
        session = FakeSession({
            "ok": True,
            "title": "Video tạm dừng",
            "channel": "",
            "paused": True,
            "ended": False,
            "currentTime": 3,
            "duration": 0,
            "url": "https://www.youtube.com/watch?v=paused",
        })
        with patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=session)), \
             patch.object(jarvis, "find_youtube_page", AsyncMock(return_value=17)):
            await jarvis.report_youtube_now_playing()
        self.assertIn("Tạm dừng", jarvis.last_command_response)
        self.assertIn("0:03", jarvis.last_command_response)

    async def test_reports_when_no_youtube_tab_exists(self):
        jarvis.youtube_page_id = None
        with patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=object())), \
             patch.object(jarvis, "find_youtube_page", AsyncMock(return_value=None)):
            handled = await jarvis.report_youtube_now_playing()
        self.assertTrue(handled)
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không tìm thấy tab YouTube đang mở.",
        )

    async def test_video_reader_never_uses_stale_cached_page_id(self):
        jarvis.youtube_page_id = 17
        session = AsyncMock()
        with patch.object(
            jarvis, "get_mcp_session", AsyncMock(return_value=session)
        ), patch.object(
            jarvis, "find_youtube_page", AsyncMock(return_value=None)
        ):
            self.assertFalse(await jarvis.read_youtube_videos(
                jarvis.STUDY_PROFILE, "Học", retries=1
            ))
        session.call_tool.assert_not_awaited()
        self.assertIsNone(jarvis.youtube_page_id)
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không tìm thấy tab YouTube đang mở.",
        )

    async def test_route_dispatches_now_playing_before_local_ai(self):
        with patch.object(
            jarvis, "report_youtube_now_playing", AsyncMock(return_value=True)
        ) as report:
            handled = await jarvis.route_command("youtube đang phát gì")
        self.assertTrue(handled)
        report.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
