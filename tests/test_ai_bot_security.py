import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import ai_bot


class AiBotFileToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.read_root_patch = patch.object(ai_bot, "READ_ROOT", self.root)
        self.read_root_patch.start()

    def tearDown(self):
        self.read_root_patch.stop()
        self.temp_dir.cleanup()

    def test_largest_files_are_sorted_and_never_deleted(self):
        small = self.root / "small.bin"
        large = self.root / "large.bin"
        small.write_bytes(b"a" * 10)
        large.write_bytes(b"b" * 100)

        result = ai_bot.tool_find_largest_files(
            ".", limit=2, minimum_size_mb=0
        )

        self.assertLess(result.index("large.bin"), result.index("small.bin"))
        self.assertTrue(small.exists())
        self.assertTrue(large.exists())

    def test_sensitive_file_content_is_never_returned(self):
        secret = self.root / ".env"
        secret.write_text("DISCORD_TOKEN=top-secret", encoding="utf-8")

        result = ai_bot.tool_read_file(".env")

        self.assertIn("bí mật", result)
        self.assertNotIn("top-secret", result)

    def test_analyze_cache_recommends_manual_review_without_deleting(self):
        cache_dir = self.root / ".cache" / "demo"
        cache_dir.mkdir(parents=True)
        cached = cache_dir / "payload.bin"
        cached.write_bytes(b"cache")

        result = ai_bot.tool_analyze_file(".cache/demo/payload.bin")

        self.assertIn("cache", result.lower())
        self.assertIn("không xóa file", result)
        self.assertTrue(cached.exists())

    async def test_removed_mutating_tools_cannot_be_called(self):
        tool_names = {item["function"]["name"] for item in ai_bot.TOOLS}
        self.assertNotIn("write_file", tool_names)
        self.assertNotIn("run_command", tool_names)
        self.assertIn("find_largest_files", tool_names)
        self.assertIn("analyze_file", tool_names)
        self.assertIn(
            "Tool không tồn tại",
            await ai_bot.execute_tool("run_command", {"command": "python3"}),
        )


class AiBotDiscordOwnerTests(unittest.IsolatedAsyncioTestCase):
    class TypingContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def test_non_owner_in_allowed_channel_is_ignored(self):
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=123, __str__=lambda self: "guest"),
            channel=SimpleNamespace(id=ai_bot.DISCORD_CHANNEL_ID),
            content="đọc file",
        )

        with patch.object(ai_bot, "run_agent", new=AsyncMock()) as run_agent:
            await ai_bot.on_message(message)

        run_agent.assert_not_awaited()

    async def test_configured_owner_in_allowed_channel_is_accepted(self):
        channel = SimpleNamespace(
            id=ai_bot.DISCORD_CHANNEL_ID,
            typing=lambda: self.TypingContext(),
        )
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=ai_bot.DISCORD_OWNER_ID),
            channel=channel,
            content="tìm file lớn nhất",
            reply=AsyncMock(),
        )

        with patch.object(
            ai_bot, "run_agent", new=AsyncMock(return_value="kết quả")
        ) as run_agent:
            await ai_bot.on_message(message)

        run_agent.assert_awaited_once_with("tìm file lớn nhất")
        message.reply.assert_awaited_once()

    def test_configured_owner_id_matches_user(self):
        self.assertEqual(ai_bot.DISCORD_OWNER_ID, 884068521015930910)


class AiBotLocalQwenTests(unittest.IsolatedAsyncioTestCase):
    def test_only_local_ollama_qwen_is_configured(self):
        self.assertEqual(ai_bot.OLLAMA_MODEL, "qwen3-vl:4b")
        self.assertEqual(ai_bot.OLLAMA_URL, "http://127.0.0.1:11434")
        self.assertFalse(hasattr(ai_bot, "NINEROUTER_API_KEY"))
        self.assertFalse(hasattr(ai_bot, "NINEROUTER_URL"))

    def test_qwen_reasoning_is_hidden(self):
        response = "phân tích nội bộ\n</think>\nCâu trả lời cuối."
        self.assertEqual(ai_bot.strip_qwen_thinking(response), "Câu trả lời cuối.")

    async def test_agent_accepts_native_ollama_response(self):
        response = {
            "message": {
                "role": "assistant",
                "content": "suy luận\n</think>\nChỉ chạy local.",
            }
        }
        with patch.object(
            ai_bot, "call_ollama", new=AsyncMock(return_value=response)
        ) as call_ollama:
            answer = await ai_bot.run_agent("Bạn đang chạy ở đâu?")

        self.assertEqual(answer, "Chỉ chạy local.")
        call_ollama.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
