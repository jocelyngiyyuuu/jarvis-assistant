import unittest
from datetime import date
import inspect
import io
import json
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

import jarvis
from jarvis import parse_zalo_request


class ZaloCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_managed_zalo_tab_closes_unique_match_only(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            type("Result", (), {"content": [type("Text", (), {"text": (
                "1: https://chat.zalo.me/ Zalo\n2: https://www.youtube.com/ YouTube"
            )})()], "isError": False})(),
            type("Result", (), {"content": [], "isError": False})(),
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            self.assertTrue(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        self.assertEqual(
            session.call_tool.await_args_list[-1].args[0], "close_page"
        )
        self.assertEqual(session.call_tool.await_args_list[-1].kwargs["arguments"], {"pageId": 1})

    async def test_close_managed_zalo_tab_closes_all_exact_matches(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            type("Result", (), {"content": [type("Text", (), {"text": (
                "1: https://chat.zalo.me/ Zalo A\n"
                "2: https://chat.zalo.me/ Zalo B\n"
                "3: https://www.youtube.com/ YouTube"
            )})()], "isError": False})(),
            type("Result", (), {"content": [], "isError": False})(),
            type("Result", (), {"content": [], "isError": False})(),
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            self.assertTrue(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        close_calls = [
            call.kwargs["arguments"] for call in session.call_tool.await_args_list
            if call.args[0] == "close_page"
        ]
        self.assertEqual(close_calls, [{"pageId": 1}, {"pageId": 2}])

    async def test_close_zalo_matches_page_url_not_url_text_in_title(self):
        session = AsyncMock()
        session.call_tool.return_value = type("Result", (), {"content": [
            type("Text", (), {"text": (
                "1: https://example.com/ Article about https://chat.zalo.me/"
            )})()
        ], "isError": False})()
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            self.assertFalse(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        self.assertEqual(session.call_tool.await_count, 1)

    async def test_close_last_zalo_tab_navigates_without_closing_chrome(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            type("Result", (), {"content": [type("Text", (), {
                "text": "1: https://chat.zalo.me/ Zalo"
            })()], "isError": False})(),
            type("Result", (), {"content": [], "isError": False})(),
            type("Result", (), {"content": [], "isError": False})(),
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            self.assertTrue(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        tools = [call.args[0] for call in session.call_tool.await_args_list]
        self.assertEqual(tools, ["list_pages", "select_page", "navigate_page"])
        self.assertNotIn("close_page", tools)

    async def test_close_zalo_command_closes_only_zalo_tab(self):
        with patch("jarvis.close_managed_web_tab", new=AsyncMock(return_value=True)) as close:
            handled = await jarvis.route_command("tắt zalo", allow_local_ai=False)

        self.assertTrue(handled)
        close.assert_awaited_once_with(
            "zalo", "Zalo", ("https://chat.zalo.me/",)
        )

    async def test_close_zalo_aliases_do_not_fall_through_to_open_suggestion(self):
        for command in ("đóng zalo", "thoát zalo", "tat zalo", "dong zalo"):
            with self.subTest(command=command), patch(
                "jarvis.close_managed_web_tab", new=AsyncMock(return_value=True)
            ) as close:
                handled = await jarvis.route_command(command, allow_local_ai=False)
                self.assertTrue(handled)
                close.assert_awaited_once()

    def test_summary_uses_current_zalo_conversation_row_selector(self):
        source = inspect.getsource(jarvis.summarize_zalo_work)
        self.assertIn('[data-id="div_TabMsg_ThrdChItem"].msg-item', source)
        self.assertIn('[data-id="div_DetailLabelList_Label"]', source)
        self.assertIn("split('\\\\n')", source)
        self.assertIn("join('\\\\n\\\\n')", source)
        self.assertNotIn('[role="grid"] .msg-item', source)

    def test_specific_date(self):
        selected, group, question = parse_zalo_request("tóm tắt zalo ngày 10/08/2026")
        self.assertEqual(selected, date(2026, 8, 10))
        self.assertIsNone(group)
        self.assertIsNone(question)

    def test_group_date_and_question(self):
        selected, group, question = parse_zalo_request(
            "hỏi zalo nhóm PRF193 hôm nay về khảo sát là gì"
        )
        self.assertIsNotNone(selected)
        self.assertEqual(group, "prf193")
        self.assertEqual(question, "khao sat la gi")

    async def test_summary_exception_does_not_expose_internal_details(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            jarvis,
            "get_mcp_session",
            AsyncMock(side_effect=RuntimeError("secret /home/khoa/token")),
        ):
            handled = await jarvis.summarize_zalo_work()
        self.assertTrue(handled)
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không thể tổng hợp Zalo lúc này.",
        )
        self.assertNotIn("/home/khoa/token", jarvis.last_command_response)
        self.assertNotIn("/home/khoa/token", output.getvalue())

    async def test_successful_summary_is_returned_but_not_logged_to_stdout(self):
        class Text:
            def __init__(self, text):
                self.text = text

        class Result:
            def __init__(self, value):
                self.content = [Text(value)]

        class Session:
            async def call_tool(self, name, arguments):
                if name == "list_pages":
                    return Result("1: https://chat.zalo.me/")
                if name == "select_page":
                    return Result("{}")
                script = arguments["function"]
                if "div_MiniLabel_OpenLabelList" in script:
                    return Result(json.dumps({"ok": True, "count": 1, "titles": ["Nhóm"]}))
                return Result(json.dumps({
                    "ok": True,
                    "title": "Nhóm",
                    "preview": "",
                    "content": "PRIVATE_CHAT_MARKER",
                }))

        output = io.StringIO()
        with redirect_stdout(output), \
             patch.object(jarvis, "get_mcp_session", AsyncMock(return_value=Session())), \
             patch.object(
                 jarvis.CORE.local_ai,
                 "summarize_zalo_work",
                 return_value="PRIVATE_SUMMARY_MARKER",
             ):
            handled = await jarvis.summarize_zalo_work(max_chats=1)
        self.assertTrue(handled)
        self.assertIn("PRIVATE_SUMMARY_MARKER", jarvis.last_command_response)
        self.assertNotIn("PRIVATE_CHAT_MARKER", output.getvalue())
        self.assertNotIn("PRIVATE_SUMMARY_MARKER", output.getvalue())

        with patch.object(jarvis, "speak") as spoken:
            jarvis.speak_last_response()
        spoken.assert_called_once_with(
            "Đã tổng hợp nội dung Zalo theo phạm vi yêu cầu."
        )
        self.assertNotIn("PRIVATE_SUMMARY_MARKER", spoken.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
