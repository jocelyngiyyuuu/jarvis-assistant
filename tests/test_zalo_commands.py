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
