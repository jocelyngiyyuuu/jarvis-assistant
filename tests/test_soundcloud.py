import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import jarvis


class FakeResult:
    def __init__(self, value, *, is_error=False):
        self.content = [type("TextContent", (), {"text": json.dumps(value)})()]
        self.isError = is_error


class FakeSession:
    def __init__(self, values=None):
        self.values = list(values or [])
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "evaluate_script" and self.values:
            return FakeResult(self.values.pop(0))
        return FakeResult({})


class SoundCloudTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        jarvis.soundcloud_tracks = []
        jarvis.soundcloud_page_id = None
        jarvis.last_command_response = None
        jarvis.managed_cdp_targets["soundcloud"].clear()

    def test_soundcloud_uses_isolated_automation_profile(self):
        self.assertEqual(
            jarvis._automation_site_key("https://soundcloud.com/discover"),
            "soundcloud",
        )
        self.assertEqual(
            jarvis._automation_site_key("https://m.soundcloud.com/artist/track"),
            "soundcloud",
        )
        self.assertTrue(jarvis._is_automation_url("https://soundcloud.com/search"))

    def test_existing_soundcloud_tab_is_reowned_without_duplicate(self):
        page = {
            "id": "soundcloud-1", "type": "page",
            "url": "https://soundcloud.com/artist/song",
        }
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps([page]).encode()
        activated = MagicMock(status=200)
        activated.__enter__.return_value = activated
        activated.__exit__.return_value = False
        with patch("jarvis.urlopen", side_effect=[response, activated]) as opened:
            self.assertTrue(
                jarvis._ensure_jarvis_chrome_tab("https://soundcloud.com/discover")
            )
        self.assertEqual(jarvis.managed_cdp_targets["soundcloud"], {"soundcloud-1"})
        self.assertEqual(opened.call_count, 2)
        self.assertIn("/json/activate/soundcloud-1", opened.call_args_list[1].args[0])

    def test_track_url_validation_rejects_navigation_and_foreign_urls(self):
        self.assertTrue(
            jarvis._is_soundcloud_track_url("https://soundcloud.com/artist/song")
        )
        self.assertFalse(
            jarvis._is_soundcloud_track_url("https://soundcloud.com/discover/sets")
        )
        self.assertFalse(
            jarvis._is_soundcloud_track_url("https://evil.example/artist/song")
        )

    async def test_extract_tracks_filters_invalid_and_duplicate_links(self):
        session = FakeSession([[
            {"title": "Bài một", "url": "https://soundcloud.com/artist/song"},
            {"title": "Bài một", "url": "https://soundcloud.com/artist/song"},
            {"title": "Điều hướng", "url": "https://soundcloud.com/discover/sets"},
            {"title": "Giả", "url": "https://evil.example/artist/song"},
        ]])
        tracks = await jarvis.extract_soundcloud_tracks_from_page(session, 7)
        self.assertEqual(session.calls[0][1]["pageId"], 7)
        self.assertEqual(tracks, [{
            "title": "Bài một",
            "url": "https://soundcloud.com/artist/song",
        }])

    def test_tts_shortens_soundcloud_lists(self):
        self.assertEqual(
            jarvis._clean_tts_text("☁️ KẾT QUẢ SOUNDCLOUD: test\n1. Bài một"),
            "Đã cập nhật danh sách SoundCloud.",
        )

    async def test_route_dispatches_soundcloud_commands(self):
        with patch.object(jarvis, "open_soundcloud", AsyncMock(return_value=True)) as open_, \
             patch.object(jarvis, "search_soundcloud", AsyncMock(return_value=True)) as search, \
             patch.object(jarvis, "open_soundcloud_track", AsyncMock(return_value=True)) as track, \
             patch.object(jarvis, "control_soundcloud_playback", AsyncMock(return_value=True)) as playback:
            self.assertTrue(await jarvis.route_command("mở soundcloud"))
            self.assertTrue(await jarvis.route_command("tìm soundcloud nhạc jazz"))
            self.assertTrue(await jarvis.route_command("mở bài soundcloud 2"))
            self.assertTrue(await jarvis.route_command("dừng soundcloud"))
        open_.assert_awaited_once_with()
        search.assert_awaited_once_with("tìm soundcloud nhạc jazz")
        track.assert_awaited_once_with(number=2)
        playback.assert_awaited_once_with(False)

    async def test_now_playing_is_read_only_and_escapes_metadata(self):
        session = FakeSession([{
            "ok": True,
            "title": "[Bài thử](https://evil.example)",
            "artist": "@everyone",
            "paused": False,
            "ended": False,
            "currentTime": 65,
            "duration": 245,
            "trackUrl": "https://soundcloud.com/artist/song",
            "pageUrl": "https://soundcloud.com/discover",
        }])
        with patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=session)), \
             patch.object(jarvis, "find_soundcloud_page", AsyncMock(return_value=8)):
            handled = await jarvis.report_soundcloud_now_playing()
        self.assertTrue(handled)
        self.assertIn("Bài thử", jarvis.last_command_response)
        self.assertNotIn("https://evil.example", jarvis.last_command_response)
        self.assertNotIn("@everyone", jarvis.last_command_response)
        self.assertIn("1:05 / 4:05", jarvis.last_command_response)
        self.assertEqual(session.calls[0], (
            "select_page", {"pageId": 8, "bringToFront": False}
        ))
        script = session.calls[1][1]["function"]
        self.assertNotIn(".play()", script)
        self.assertNotIn(".pause()", script)

    async def test_close_uses_owned_target_lifecycle(self):
        with patch.object(
            jarvis, "close_managed_web_tab", AsyncMock(return_value=True)
        ) as close:
            self.assertTrue(await jarvis.close_soundcloud())
        close.assert_awaited_once_with(
            "soundcloud", "SoundCloud", ("https://soundcloud.com/",)
        )


if __name__ == "__main__":
    unittest.main()
