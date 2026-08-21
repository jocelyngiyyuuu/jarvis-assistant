import unittest
from datetime import date
import inspect
import io
import json
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, Mock, patch

import jarvis
from jarvis import parse_zalo_request


class ZaloCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        jarvis.managed_cdp_targets["zalo"] = {"owned-zalo"}
        patcher = patch("jarvis._cdp_pages", return_value=[{
            "id": "owned-zalo", "type": "page", "url": "https://chat.zalo.me/",
        }])
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_close_zalo_closes_only_stored_cdp_target(self):
        jarvis.managed_cdp_targets["zalo"].add("owned-zalo")
        pages_before = [
            {"id": "owned-zalo", "type": "page", "url": "https://chat.zalo.me/"},
            {"id": "user-zalo", "type": "page", "url": "https://chat.zalo.me/"},
        ]
        pages_after = [pages_before[1]]
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("jarvis._cdp_pages", side_effect=[pages_before, pages_after]), patch(
            "jarvis.urlopen", return_value=response
        ) as close:
            self.assertTrue(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        self.assertIn("/json/close/owned-zalo", close.call_args.args[0])
        self.assertNotIn("user-zalo", close.call_args.args[0])
        self.assertEqual(jarvis.managed_cdp_targets["zalo"], set())

    async def test_close_zalo_waits_for_target_to_disappear(self):
        target = {
            "id": "owned-zalo", "type": "page", "url": "https://chat.zalo.me/",
        }
        other = {"id": "other", "type": "page", "url": "https://www.youtube.com/"}
        before = [target, other]
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch(
            "jarvis._cdp_pages", side_effect=[before, before, [other]]
        ), patch("jarvis.urlopen", return_value=response), patch(
            "jarvis.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            self.assertTrue(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        sleep.assert_awaited_once_with(0.1)
        self.assertEqual(jarvis.managed_cdp_targets["zalo"], set())

    async def test_close_last_zalo_tab_creates_safe_blank_page_first(self):
        target = {
            "id": "owned-zalo", "type": "page", "url": "https://chat.zalo.me/",
        }
        blank = {"id": "blank", "type": "page", "url": "about:blank"}
        blank_response = Mock(status=200)
        blank_response.read.return_value = json.dumps(blank).encode()
        blank_response.__enter__ = Mock(return_value=blank_response)
        blank_response.__exit__ = Mock(return_value=False)
        close_response = Mock(status=200)
        close_response.__enter__ = Mock(return_value=close_response)
        close_response.__exit__ = Mock(return_value=False)
        with patch("jarvis._cdp_pages", side_effect=[[target], [blank]]), patch(
            "jarvis.urlopen", side_effect=[blank_response, close_response]
        ) as opened:
            self.assertTrue(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        self.assertEqual(opened.call_args_list[0].args[0].get_method(), "PUT")
        self.assertIn("about%3Ablank", opened.call_args_list[0].args[0].full_url)
        self.assertIn("/json/close/owned-zalo", opened.call_args_list[1].args[0])

    async def test_close_zalo_without_registry_fails_closed_without_url_scan(self):
        jarvis.managed_cdp_targets["zalo"].clear()
        with patch("jarvis._cdp_pages") as pages, patch("jarvis.urlopen") as close:
            self.assertFalse(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        pages.assert_not_called()
        close.assert_not_called()

    async def test_close_zalo_cdp_outage_retains_registry_for_retry(self):
        with patch("jarvis._cdp_pages", return_value=None), patch(
            "jarvis.urlopen"
        ) as close:
            self.assertFalse(await jarvis.close_managed_web_tab(
                "zalo", "Zalo", ("https://chat.zalo.me/",)
            ))
        close.assert_not_called()
        self.assertEqual(jarvis.managed_cdp_targets["zalo"], {"owned-zalo"})

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

    async def test_personal_zalo_close_uses_exact_personal_profile_window(self):
        with patch.object(
            jarvis.CHROME_WINDOWS, "close_site_window",
            return_value=(True, "Đã đóng Zalo."),
        ) as close, patch(
            "jarvis.close_managed_web_tab", new=AsyncMock()
        ) as cdp_close:
            self.assertTrue(await jarvis.route_command(
                "tắt zalo cá nhân", allow_local_ai=False
            ))
        close.assert_called_once_with(
            jarvis.PERSONAL_PROFILE, "zalo", "Zalo công việc"
        )
        cdp_close.assert_not_awaited()

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
                 jarvis.asyncio,
                 "to_thread",
                 AsyncMock(side_effect=lambda function, *args: function(*args)),
             ), \
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
