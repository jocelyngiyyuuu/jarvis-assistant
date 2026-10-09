import json
import unittest
from unittest.mock import AsyncMock, patch

import jarvis


class YouTubeListTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_keeps_order_and_only_valid_unique_video_links(self):
        raw = [
            {"title": "Video đầu", "url": "https://www.youtube.com/watch?v=first&list=RDfirst"},
            {"title": "Liên kết giả", "url": "https://evil.example/watch?v=bad"},
            {"title": "Video đầu lặp", "url": "https://www.youtube.com/watch?v=first"},
            {"title": "Không phải video", "url": "https://www.youtube.com/playlist?list=123"},
            {"title": "Video thứ hai", "url": "https://www.youtube.com/watch?v=second"},
        ]
        result = type("Result", (), {
            "content": [type("Text", (), {"text": json.dumps(raw)})()]
        })()

        with patch.object(jarvis, "evaluate_mcp_script", AsyncMock(return_value=result)):
            videos = await jarvis.extract_videos_from_page(object(), 17, limit=10)

        self.assertEqual(videos, [raw[0], raw[4]])

    async def test_extract_uses_video_cards_for_page_context(self):
        result = type("Result", (), {
            "content": [type("Text", (), {"text": "[]"})()]
        })()
        with patch.object(
            jarvis, "evaluate_mcp_script", AsyncMock(return_value=result)
        ) as evaluate:
            await jarvis.extract_videos_from_page(object(), 17)

        script = evaluate.await_args.args[2]
        self.assertIn("ytd-search ytd-video-renderer", script)
        self.assertIn("ytd-rich-grid-media", script)
        self.assertIn("ytd-playlist-panel-video-renderer", script)
        self.assertIn("card.querySelector('#video-title')", script)
        self.assertNotIn("document.querySelectorAll('a[href", script)


if __name__ == "__main__":
    unittest.main()
