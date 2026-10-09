import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

import jarvis


class Text:
    def __init__(self, text):
        self.text = text


class Result:
    def __init__(self, value, is_error=False):
        self.content = [Text(value)]
        self.isError = is_error


class GmailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        jarvis.managed_cdp_targets["gmail"] = {"owned-gmail"}
        patcher = patch("jarvis._cdp_pages", return_value=[{
            "id": "owned-gmail", "type": "page",
            "url": "https://mail.google.com/mail/u/0/#inbox",
        }])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.real_configured_gmail_account = jarvis.configured_gmail_account
        account_patcher = patch(
            "jarvis.configured_gmail_account", return_value="user@example.com"
        )
        account_patcher.start()
        self.addCleanup(account_patcher.stop)

    async def test_find_page_never_falls_back_to_wrong_account(self):
        session = AsyncMock()
        session.call_tool.return_value = Result(
            "4: https://mail.google.com/mail/u/0/#inbox other@example.net Gmail"
        )
        self.assertIsNone(await jarvis.find_gmail_page(session))

    async def test_find_page_fails_closed_for_duplicate_exact_urls(self):
        session = AsyncMock()
        session.call_tool.return_value = Result(
            "4: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail\n"
            "5: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"
        )
        self.assertIsNone(await jarvis.find_gmail_page(session))

    async def test_find_page_does_not_disambiguate_duplicate_url_by_title(self):
        session = AsyncMock()
        session.call_tool.return_value = Result(
            "4: https://mail.google.com/mail/u/0/#inbox Other Gmail\n"
            "5: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"
        )
        self.assertIsNone(await jarvis.find_gmail_page(session))

    async def test_read_inbox_filters_unread_and_limits(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            Result("4: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"),
            Result("{}"),
            Result(json.dumps({"ok": True, "messages": [
                {"id": "a", "sender": "A", "subject": "Một", "snippet": "x", "unread": True},
                {"id": "b", "sender": "B", "subject": "Hai", "snippet": "y", "unread": False},
                {"id": "c", "sender": "C", "subject": "Ba", "snippet": "z", "unread": True},
            ]})),
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            result = await jarvis.read_gmail_inbox(limit=1, unread_only=True)
        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["messages"]], ["a"])

    async def test_login_required_has_clear_message(self):
        with patch("jarvis.read_gmail_inbox", new=AsyncMock(return_value={
            "ok": False, "reason": "login", "messages": [],
        })):
            self.assertTrue(await jarvis.summarize_gmail())
        self.assertIn("đăng nhập một lần", jarvis.last_command_response)

    async def test_summary_is_returned_without_private_stdout_or_tts(self):
        private_mail = "PRIVATE_MAIL_MARKER"
        private_summary = "PRIVATE_SUMMARY_MARKER"
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "jarvis.read_gmail_inbox", new=AsyncMock(return_value={
                "ok": True, "reason": "", "messages": [{
                    "id": "x", "sender": "A", "subject": private_mail,
                    "snippet": private_mail, "unread": True,
                }],
            })
        ), patch("jarvis.asyncio.to_thread", new=AsyncMock(return_value=private_summary)), patch(
            "jarvis.mark_gmail_messages_read", new=AsyncMock(return_value=1)
        ):
            self.assertTrue(await jarvis.summarize_gmail(unread_only=True))
        self.assertIn(private_summary, jarvis.last_command_response)
        self.assertNotIn(private_mail, output.getvalue())
        self.assertNotIn(private_summary, output.getvalue())
        with patch.object(jarvis, "speak") as spoken:
            jarvis.speak_last_response()
        spoken.assert_called_once_with("Đã tóm tắt Gmail theo phạm vi yêu cầu.")

    async def test_summary_falls_back_to_bounded_inbox_preview(self):
        with patch("jarvis.read_gmail_inbox", new=AsyncMock(return_value={
            "ok": True, "reason": "", "messages": [{
                "id": "x", "sender": "Nhà trường", "subject": "Thông báo",
                "snippet": "Lịch học đã thay đổi.", "time": "08:00", "unread": True,
            }],
        })), patch(
            "jarvis.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("timeout"))
        ), patch(
            "jarvis.mark_gmail_messages_read", new=AsyncMock(return_value=1)
        ):
            self.assertTrue(await jarvis.summarize_gmail())
        self.assertIn("Nhà trường", jarvis.last_command_response)
        self.assertIn("Lịch học đã thay đổi", jarvis.last_command_response)
        self.assertNotIn("timeout", jarvis.last_command_response)

    async def test_notification_first_poll_is_quiet_then_sends_new_mail(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "gmail.json"
            first = {"ok": True, "reason": "", "messages": [{
                "id": "old", "sender": "A", "subject": "Old", "snippet": "", "unread": True,
            }]}
            second = {"ok": True, "reason": "", "messages": [{
                "id": "new", "sender": "B", "subject": "New", "snippet": "News", "unread": True,
            }, *first["messages"]]}
            channel = AsyncMock()
            with patch.object(jarvis, "GMAIL_STATE_PATH", state_path), patch(
                "jarvis.read_gmail_inbox", new=AsyncMock(side_effect=[first, second])
            ), patch(
                "jarvis.get_discord_notification_channel", new=AsyncMock(return_value=channel)
            ), patch("jarvis.asyncio.to_thread", new=AsyncMock(return_value="Bản tóm tắt")), patch(
                "jarvis.mark_gmail_messages_read", new=AsyncMock(return_value=1)
            ):
                baseline = await jarvis.check_gmail_for_notifications()
                notified = await jarvis.check_gmail_for_notifications()
        self.assertEqual(baseline["status"], "baseline")
        self.assertEqual(notified, {"status": "notified", "new_count": 1})
        channel.send.assert_awaited_once()
        self.assertIn("1 GMAIL MỚI", channel.send.await_args.args[0])

    def test_configured_gmail_url_targets_requested_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(json.dumps({"account": "user+test@example.com"}), encoding="utf-8")
            with patch.object(jarvis, "GMAIL_ACCOUNT_PATH", path), patch(
                "jarvis.configured_gmail_account",
                new=self.real_configured_gmail_account,
            ):
                self.assertEqual(
                    jarvis.configured_gmail_url(),
                    "https://mail.google.com/mail/u/?authuser=user%2Btest%40example.com",
                )

    async def test_gmail_commands_route_before_local_ai(self):
        with patch("jarvis.summarize_gmail", new=AsyncMock(return_value=True)) as summary:
            self.assertTrue(await jarvis.route_command(
                "tóm tắt gmail chưa đọc", allow_local_ai=False
            ))
        summary.assert_awaited_once_with(unread_only=True)

        with patch("jarvis.summarize_gmail", new=AsyncMock(return_value=True)) as summary:
            self.assertTrue(await jarvis.route_command(
                "tóm tắt gmail", allow_local_ai=False
            ))
        summary.assert_awaited_once_with(unread_only=True)

    async def test_spam_command_requests_meaningful_spam_only(self):
        with patch("jarvis.summarize_gmail", new=AsyncMock(return_value=True)) as summary:
            self.assertTrue(await jarvis.route_command(
                "tóm tắt thư rác", allow_local_ai=False
            ))
        summary.assert_awaited_once_with(
            folder="spam", meaningful_only=True, unread_only=True
        )

    async def test_spam_reader_navigates_to_spam_then_restores_inbox(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            Result("4: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"),
            Result("{}"),
            Result("{}"),
            Result(json.dumps({"ok": True, "messages": [{
                "id": "spam-1", "sender": "Google", "subject": "Cảnh báo bảo mật",
                "snippet": "Có lượt đăng nhập mới", "unread": True,
            }]})),
            Result("{}"),
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            result = await jarvis.read_gmail_inbox(folder="spam")
        self.assertTrue(result["ok"])
        navigate_urls = [
            call.kwargs["arguments"]["url"]
            for call in session.call_tool.await_args_list
            if call.args[0] == "navigate_page"
        ]
        self.assertTrue(navigate_urls[0].endswith("#spam"))
        self.assertTrue(navigate_urls[-1].endswith("#inbox"))

    async def test_spam_reader_restores_inbox_after_evaluation_error(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            Result("4: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"),
            Result("{}"), Result("{}"), RuntimeError("evaluate failed"), Result("{}"),
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            with self.assertRaises(RuntimeError):
                await jarvis.read_gmail_inbox(folder="spam")
        self.assertEqual(
            session.call_tool.await_args_list[-1].kwargs["arguments"]["url"],
            jarvis.configured_gmail_folder_url("inbox"),
        )

    async def test_marks_only_exact_unread_message_ids(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            Result("4: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"),
            Result("{}"), Result(json.dumps({"ok": True, "marked": 1})),
        ]
        messages = [
            {"id": "thread-unread", "unread": True},
            {"id": "thread-read", "unread": False},
        ]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            self.assertEqual(await jarvis.mark_gmail_messages_read(messages), 1)
        script = session.call_tool.await_args_list[-1].kwargs["arguments"]["function"]
        self.assertIn("thread-unread", script)
        self.assertNotIn("thread-read", script)
        self.assertIn("fingerprint([sender, email, subject, snippet, time].join('|'))", script)

    async def test_mark_read_stops_when_exact_page_selection_fails(self):
        session = AsyncMock()
        session.call_tool.side_effect = [
            Result("4: https://mail.google.com/mail/u/0/#inbox user@example.com Gmail"),
            Result("select failed", is_error=True),
            Result(json.dumps({"ok": True, "marked": 1})),
        ]
        messages = [{"id": "thread-unread", "unread": True}]
        with patch("jarvis.get_mcp_session", new=AsyncMock(return_value=session)):
            self.assertEqual(await jarvis.mark_gmail_messages_read(messages), 0)
        self.assertEqual(session.call_tool.await_count, 2)

    def test_inbox_reader_has_stable_fallback_id_for_current_gmail_dom(self):
        self.assertIn(
            "fingerprint([sender, email, subject, snippet, time].join('|'))",
            jarvis.GMAIL_INBOX_SCRIPT,
        )

    def test_meaningful_spam_fallback_excludes_promotions(self):
        messages = [
            {"sender": "Google", "subject": "Cảnh báo bảo mật", "snippet": "Đăng nhập mới"},
            {"sender": "Shop", "subject": "Ưu đãi giảm giá", "snippet": "Sale 50%"},
        ]
        selected = jarvis.meaningful_spam_messages(messages)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["sender"], "Google")


if __name__ == "__main__":
    unittest.main()
