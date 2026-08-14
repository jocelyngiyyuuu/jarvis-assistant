import unittest
from unittest.mock import AsyncMock, Mock, patch

import jarvis


class ChromeShortcutTests(unittest.TestCase):
    def test_bare_github_defaults_to_profile_1_without_prompt(self):
        with patch("jarvis.open_chrome", return_value=True) as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở github"))
        opened.assert_called_once_with(jarvis.STUDY_PROFILE, "https://github.com/")
        self.assertNotIn("Hãy nói rõ profile", jarvis.last_command_response)
        self.assertEqual(
            jarvis.last_command_response,
            "✅ Đã mở GitHub bằng Chrome Học / ChatGPT.",
        )

    def test_explicit_personal_github_still_overrides_default(self):
        with patch("jarvis.open_chrome", return_value=True) as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở github cá nhân"))
        opened.assert_called_once_with(jarvis.PERSONAL_PROFILE, "https://github.com/")

    def test_open_folder_tracks_window_for_paired_close(self):
        with patch.object(jarvis.Path, "exists", return_value=True), patch.object(
            jarvis.FILE_WINDOWS, "snapshot_ids", return_value={"0x1"}
        ), patch("jarvis.subprocess.Popen"), patch.object(
            jarvis.FILE_WINDOWS, "track_opened_path", return_value=True
        ) as track:
            self.assertTrue(jarvis.open_folder("/tmp/example", "Example"))
        track.assert_called_once_with(jarvis.Path("/tmp/example"), {"0x1"})

    def test_zalo_defaults_to_study_profile(self):
        with patch("jarvis.open_chrome", return_value=True) as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo"))
        opened.assert_called_once_with(
            jarvis.STUDY_PROFILE, "https://chat.zalo.me/"
        )
        self.assertEqual(jarvis.last_command_response, "✅ Đã mở Zalo.")

    def test_explicit_personal_zalo_overrides_default(self):
        with patch("jarvis.open_chrome", return_value=True) as opened:
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo cá nhân"))
        opened.assert_called_once_with(
            jarvis.PERSONAL_PROFILE, "https://chat.zalo.me/"
        )
        self.assertEqual(jarvis.last_command_response, "✅ Đã mở Zalo.")

    def test_zalo_does_not_claim_opened_when_chrome_fails(self):
        with patch("jarvis.open_chrome", return_value=False):
            self.assertTrue(jarvis.handle_chrome_shortcut("mở zalo"))
        self.assertEqual(
            jarvis.last_command_response,
            "❌ Không thể mở Zalo lúc này.",
        )


class ChromeShortcutCloseTests(unittest.IsolatedAsyncioTestCase):
    def test_terminal_rolls_back_untracked_new_window(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(
            jarvis.FILE_WINDOWS, "snapshot_ids", return_value={"0x1"}
        ), patch("jarvis.subprocess.Popen", return_value=process), patch(
            "jarvis.shutil.which", return_value="/usr/bin/gnome-terminal"
        ), patch.object(
            jarvis.FILE_WINDOWS, "track_opened_window", return_value=False
        ), patch.object(
            jarvis.FILE_WINDOWS, "close_new_window", return_value=True
        ) as rollback:
            self.assertFalse(jarvis.open_terminal())
        rollback.assert_called_once_with({"0x1"})
        self.assertIn("đã hoàn tác", jarvis.last_command_response)

    def test_open_folder_handles_missing_launcher_and_rolls_back_untracked_window(self):
        path = jarvis.Path("/tmp/example")
        with patch.object(path.__class__, "exists", return_value=True), patch.object(
            jarvis.FILE_WINDOWS, "snapshot_ids", return_value={"0x1"}
        ), patch("jarvis.subprocess.Popen", side_effect=FileNotFoundError):
            self.assertFalse(jarvis.open_folder(path, "Example"))
        self.assertIn("Không tìm thấy trình mở", jarvis.last_command_response)

        with patch.object(path.__class__, "exists", return_value=True), patch.object(
            jarvis.FILE_WINDOWS, "snapshot_ids", return_value={"0x1"}
        ), patch("jarvis.subprocess.Popen"), patch.object(
            jarvis.FILE_WINDOWS, "track_opened_path", return_value=False
        ), patch.object(
            jarvis.FILE_WINDOWS, "close_new_window", return_value=True
        ) as rollback:
            self.assertFalse(jarvis.open_folder(path, "Example"))
        rollback.assert_called_once_with({"0x1"}, file_manager_only=True)

    def test_open_vscode_tracks_exact_new_window_and_handles_missing_binary(self):
        with patch.object(
            jarvis.FILE_WINDOWS, "snapshot_ids", return_value={"0x1"}
        ), patch("jarvis.subprocess.Popen") as popen, patch.object(
            jarvis.FILE_WINDOWS, "track_opened_window", return_value=True
        ) as track:
            self.assertTrue(jarvis.open_vscode())
        self.assertIn("--new-window", popen.call_args.args[0])
        track.assert_called_once_with(
            "vscode", {"0x1"}, allowed_classes={"code"}
        )

        with patch.object(
            jarvis.FILE_WINDOWS, "snapshot_ids", return_value=set()
        ), patch("jarvis.subprocess.Popen", side_effect=FileNotFoundError):
            self.assertFalse(jarvis.open_vscode())
        self.assertIn("Không tìm thấy VS Code", jarvis.last_command_response)

    def test_individual_vscode_close_uses_only_exact_window_manager(self):
        with patch.object(
            jarvis.FILE_WINDOWS, "close_last",
            return_value=(True, "Đã đóng VS Code"),
        ) as close, patch("jarvis._terminate_processes") as terminate:
            self.assertTrue(jarvis.close_vscode())
        close.assert_called_once_with("vscode")
        terminate.assert_not_called()

    def test_individual_file_manager_close_never_quits_or_pkills_process(self):
        with patch.object(
            jarvis.FILE_WINDOWS, "close_all_paths",
            return_value=(True, "Đã đóng 2 cửa sổ File Manager"),
        ) as close, patch("jarvis.subprocess.run") as run:
            self.assertTrue(jarvis.close_file_manager())
        close.assert_called_once()
        run.assert_not_called()

    async def test_google_search_from_ipc_never_prompts_for_profile(self):
        with patch("builtins.input") as prompt, patch.object(
            jarvis, "open_chrome"
        ) as open_chrome:
            self.assertTrue(await jarvis.route_command(
                "google thời tiết", allow_local_ai=False, source="gtk"
            ))
        prompt.assert_not_called()
        open_chrome.assert_not_called()
        self.assertIn("Hãy nói rõ profile", jarvis.last_command_response)

    async def test_bare_github_close_defaults_to_profile_1_without_prompt(self):
        with patch.object(
            jarvis.CHROME_WINDOWS,
            "close_site_window",
            return_value=(True, "Đã đóng GitHub."),
        ) as close:
            self.assertTrue(await jarvis.route_command("tắt github", allow_local_ai=False))
        close.assert_called_once_with(jarvis.STUDY_PROFILE, "github", "GitHub")
        self.assertEqual(jarvis.last_command_response, "✅ Đã đóng GitHub.")

    async def test_explicit_personal_github_close_overrides_default(self):
        with patch.object(
            jarvis.CHROME_WINDOWS,
            "close_site_window",
            return_value=(True, "Đã đóng GitHub."),
        ) as close:
            self.assertTrue(await jarvis.route_command(
                "đóng github cá nhân", allow_local_ai=False
            ))
        close.assert_called_once_with(jarvis.PERSONAL_PROFILE, "github", "GitHub")

    async def test_close_terminal_closes_only_last_tracked_terminal_window(self):
        with patch.object(
            jarvis.FILE_WINDOWS,
            "close_last",
            return_value=(True, "Đã đóng cửa sổ Terminal: Terminal"),
        ) as close:
            self.assertTrue(await jarvis.route_command(
                "tắt terminal", allow_local_ai=False
            ))
        close.assert_called_once_with("terminal")
        self.assertIn("Đã đóng", jarvis.last_command_response)

    async def test_terminal_close_aliases_are_recognized(self):
        for command in ("đóng terminal", "thoát terminal", "tat terminal"):
            with self.subTest(command=command), patch.object(
                jarvis.FILE_WINDOWS,
                "close_last",
                return_value=(True, "Đã đóng cửa sổ Terminal: Terminal"),
            ) as close:
                self.assertTrue(await jarvis.route_command(command, allow_local_ai=False))
                close.assert_called_once_with("terminal")

    async def test_opened_folder_shortcuts_have_close_aliases(self):
        commands = {
            "tắt downloads": jarvis.Path.home() / "Downloads",
            "đóng documents": jarvis.Path.home() / "Documents",
            "thoát pictures": jarvis.Path.home() / "Pictures",
            "tắt home": jarvis.Path.home(),
        }
        for command, expected_path in commands.items():
            with self.subTest(command=command), patch.object(
                jarvis.FILE_WINDOWS,
                "close_path",
                return_value=(True, "Đã đóng đúng thư mục"),
            ) as close:
                self.assertTrue(await jarvis.route_command(command, allow_local_ai=False))
                close.assert_called_once_with(expected_path)

    async def test_existing_open_features_accept_exit_alias(self):
        with patch("jarvis.close_youtube", new=AsyncMock(return_value=True)) as close_youtube:
            self.assertTrue(await jarvis.route_command("thoát youtube", allow_local_ai=False))
            close_youtube.assert_awaited_once()
        with patch("jarvis.close_vscode") as close_vscode:
            self.assertTrue(await jarvis.route_command("thoát vscode", allow_local_ai=False))
            close_vscode.assert_called_once()

    async def test_file_project_and_chrome_profile_accept_exit_alias(self):
        with patch.object(
            jarvis.FILE_WINDOWS, "close_last_file", return_value=(True, "Đã đóng file")
        ) as close_file:
            self.assertTrue(await jarvis.route_command("thoát file", allow_local_ai=False))
            close_file.assert_called_once()
        with patch.object(
            jarvis.FILE_WINDOWS, "close_last_folder", return_value=(True, "Đã đóng project")
        ) as close_folder:
            self.assertTrue(await jarvis.route_command("thoát project", allow_local_ai=False))
            close_folder.assert_called_once()
        with patch("jarvis.close_chrome_profile", new=AsyncMock(return_value=True)) as close_chrome:
            self.assertTrue(await jarvis.route_command("thoát chrome học", allow_local_ai=False))
            close_chrome.assert_awaited_once_with(jarvis.STUDY_PROFILE, "Học / ChatGPT")

    async def test_close_gmail_study_closes_only_tracked_gmail_window(self):
        with patch.object(
            jarvis.CHROME_WINDOWS,
            "close_site_window",
            return_value=(True, "Đã đóng Gmail."),
        ) as close:
            self.assertTrue(await jarvis.route_command(
                "tắt gmail học", allow_local_ai=False
            ))
        close.assert_called_once_with(jarvis.STUDY_PROFILE, "gmail", "Gmail")
        self.assertEqual(jarvis.last_command_response, "✅ Đã đóng Gmail.")

    async def test_close_site_without_profile_fails_instead_of_guessing(self):
        with patch.object(jarvis.CHROME_WINDOWS, "close_site_window") as close:
            self.assertTrue(await jarvis.route_command(
                "đóng gmail", allow_local_ai=False
            ))
        close.assert_not_called()
        self.assertIn("nói rõ profile", jarvis.last_command_response)

    async def test_close_site_aliases_are_paired_with_open_shortcuts(self):
        commands = {
            "thoát chatgpt cá nhân": (jarvis.PERSONAL_PROFILE, "chatgpt", "ChatGPT"),
            "đóng drive học": (jarvis.STUDY_PROFILE, "drive", "Google Drive"),
            "tắt calendar cá nhân": (jarvis.PERSONAL_PROFILE, "calendar", "Google Calendar"),
            "đóng github học": (jarvis.STUDY_PROFILE, "github", "GitHub"),
            "tắt google cá nhân": (jarvis.PERSONAL_PROFILE, "google", "Google"),
        }
        for command, expected in commands.items():
            with self.subTest(command=command), patch.object(
                jarvis.CHROME_WINDOWS,
                "close_site_window",
                return_value=(True, f"Đã đóng {expected[2]}."),
            ) as close:
                self.assertTrue(await jarvis.route_command(command, allow_local_ai=False))
                close.assert_called_once_with(*expected)


if __name__ == "__main__":
    unittest.main()
