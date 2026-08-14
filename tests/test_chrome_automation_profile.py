import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jarvis


class ChromeAutomationProfileTests(unittest.TestCase):
    def test_discord_bare_github_open_and_close_do_not_require_profile(self):
        for command in (
            "github", "mở github", "mo github",
            "tắt github", "đóng github", "thoát github",
        ):
            with self.subTest(command=command):
                self.assertFalse(jarvis._discord_command_needs_profile(command))

    def test_discord_bare_youtube_commands_do_not_require_profile(self):
        with patch.object(jarvis, "youtube_page_id", None):
            for command in (
                "youtube",
                "mở youtube",
                "vào youtube",
                "tìm youtube gaz",
                "tìm video gaz",
            ):
                with self.subTest(command=command):
                    self.assertFalse(jarvis._discord_command_needs_profile(command))

    def test_bare_youtube_command_never_prompts_for_profile(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), \
             patch("jarvis.open_chrome", return_value=True) as opened, \
             patch("jarvis.asyncio.sleep", new=AsyncMock()), \
             patch("jarvis.read_youtube_videos", new=AsyncMock()):
            import asyncio
            asyncio.run(jarvis.open_youtube("youtube"))

        opened.assert_called_once_with(
            jarvis.JARVIS_CHROME_PROFILE, "https://www.youtube.com/"
        )

    def test_readiness_rejects_unexpected_http_service(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"Browser":"not-chrome"}'
        response.__enter__.return_value = response
        with patch("jarvis._jarvis_debug_port_owner_pid", return_value=None), \
             patch("jarvis.urlopen", return_value=response):
            self.assertFalse(jarvis._jarvis_chrome_ready())

    def test_readiness_rejects_chrome_endpoint_owned_by_wrong_profile(self):
        with patch("jarvis._jarvis_debug_port_owner_pid", return_value=999), \
             patch("jarvis._process_has_jarvis_chrome_args", return_value=False), \
             patch("jarvis.urlopen") as opened:
            self.assertFalse(jarvis._jarvis_chrome_ready())
        opened.assert_not_called()

    def test_listener_owner_rejects_multiple_pids(self):
        result = MagicMock(returncode=0, stdout="123\n456\n")
        with patch("jarvis.subprocess.run", return_value=result):
            self.assertIsNone(jarvis._jarvis_debug_port_owner_pid())

    def test_readiness_rejects_websocket_on_wrong_port(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = (
            b'{"Browser":"Chrome/144",'
            b'"webSocketDebuggerUrl":"ws://127.0.0.1:9999/devtools/browser/x"}'
        )
        response.__enter__.return_value = response
        with patch("jarvis._jarvis_debug_port_owner_pid", return_value=123), \
             patch("jarvis._process_has_jarvis_chrome_args", return_value=True), \
             patch("jarvis.urlopen", return_value=response):
            self.assertFalse(jarvis._jarvis_chrome_ready())

    def test_process_identity_accepts_chrome_rewritten_single_argv(self):
        command = (
            b"/opt/google/chrome/chrome --remote-debugging-port=9223 "
            b"--user-data-dir=/home/khoa/.config/jarvis-chrome\0"
        )
        with patch("jarvis.JARVIS_CHROME_DATA_DIR", Path("/home/khoa/.config/jarvis-chrome")), \
             patch.object(Path, "read_bytes", return_value=command):
            self.assertTrue(jarvis._process_has_jarvis_chrome_args(123))

    def test_existing_profile_permissions_are_restricted(self):
        with patch.object(Path, "mkdir"), patch.object(Path, "chmod") as chmod, \
             patch("jarvis.subprocess.Popen"), \
             patch("jarvis._jarvis_chrome_ready", side_effect=[False, True]):
            self.assertTrue(jarvis.ensure_jarvis_chrome("https://www.youtube.com/"))

        chmod.assert_called_once_with(0o700)

    def test_mcp_connects_to_dedicated_local_debug_port(self):
        server = jarvis.create_mcp_server()

        self.assertIn("--browserUrl", server.args)
        browser_url_index = server.args.index("--browserUrl")
        self.assertEqual(
            server.args[browser_url_index + 1], jarvis.JARVIS_CHROME_DEBUG_URL
        )
        self.assertNotIn("--autoConnect", server.args)

    def test_youtube_uses_dedicated_automation_chrome(self):
        with patch("jarvis.ensure_jarvis_chrome", return_value=True) as ensure, \
             patch("jarvis._jarvis_debug_port_owner_pid", return_value=321), \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()), \
             patch.object(
                 jarvis.CHROME_WINDOWS, "activate_automation_window", return_value=True
             ):
            result = jarvis.open_chrome(
                jarvis.PERSONAL_PROFILE, "https://www.youtube.com/"
            )

        self.assertTrue(result)
        ensure.assert_called_once_with("https://www.youtube.com/")

    def test_zalo_uses_dedicated_automation_chrome(self):
        with patch("jarvis.ensure_jarvis_chrome", return_value=True) as ensure, \
             patch("jarvis._jarvis_debug_port_owner_pid", return_value=321), \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()), \
             patch.object(
                 jarvis.CHROME_WINDOWS, "activate_automation_window", return_value=True
             ):
            result = jarvis.open_chrome(
                jarvis.STUDY_PROFILE, "https://chat.zalo.me/"
            )

        self.assertTrue(result)
        ensure.assert_called_once_with("https://chat.zalo.me/")

    def test_personal_zalo_does_not_use_study_automation_profile(self):
        with patch("jarvis.ensure_jarvis_chrome", return_value=True) as automation, patch(
            "jarvis._jarvis_debug_port_owner_pid", return_value=321
        ), patch("jarvis.subprocess.Popen") as popen, patch.object(
            jarvis.CHROME_WINDOWS, "activate_automation_window", return_value=True
        ), patch.object(
            jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()
        ), patch.object(
            jarvis.CHROME_WINDOWS, "track_profile_window", return_value=True
        ) as track:
            self.assertTrue(jarvis.open_chrome(
                jarvis.PERSONAL_PROFILE, "https://chat.zalo.me/"
            ))
        automation.assert_not_called()
        args = popen.call_args.args[0]
        self.assertIn(f"--profile-directory={jarvis.PERSONAL_PROFILE}", args)
        track.assert_called_once_with(
            jarvis.PERSONAL_PROFILE, set(), site_key="zalo"
        )

    def test_opening_managed_site_activates_dedicated_window(self):
        with patch("jarvis.ensure_jarvis_chrome", return_value=True), \
             patch("jarvis._jarvis_debug_port_owner_pid", return_value=321), \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value={"0x1"}), \
             patch.object(
                 jarvis.CHROME_WINDOWS,
                 "activate_automation_window",
                 return_value=True,
             ) as activate:
            result = jarvis.open_chrome(
                jarvis.PERSONAL_PROFILE, "https://www.youtube.com/"
            )

        self.assertTrue(result)
        activate.assert_called_once_with({"0x1"}, 321, "youtube")

    def test_managed_site_fails_when_exact_window_cannot_be_activated(self):
        with patch("jarvis.ensure_jarvis_chrome", return_value=True), \
             patch("jarvis._jarvis_debug_port_owner_pid", return_value=321), \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()), \
             patch.object(
                 jarvis.CHROME_WINDOWS,
                 "activate_automation_window",
                 return_value=False,
             ):
            self.assertFalse(
                jarvis.open_chrome(
                    jarvis.PERSONAL_PROFILE, "https://www.youtube.com/"
                )
            )

    def test_non_youtube_sites_keep_existing_profiles(self):
        with patch("jarvis.subprocess.Popen") as popen, \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()), \
             patch.object(
                 jarvis.CHROME_WINDOWS, "track_profile_window", return_value=True
             ):
            jarvis.open_chrome(jarvis.STUDY_PROFILE, "https://github.com/")

        args = popen.call_args.args[0]
        self.assertIn("--profile-directory=Profile 1", args)
        self.assertNotIn("--remote-debugging-port=9223", args)

    def test_github_uses_dedicated_app_window_for_exact_tracking(self):
        with patch("jarvis.subprocess.Popen") as popen, patch.object(
            jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()
        ), patch.object(
            jarvis.CHROME_WINDOWS, "track_profile_window", return_value=True
        ):
            self.assertTrue(jarvis.open_chrome(
                jarvis.STUDY_PROFILE, "https://github.com/"
            ))
        args = popen.call_args.args[0]
        self.assertIn("--profile-directory=Profile 1", args)
        self.assertIn("--app=https://github.com/", args)
        self.assertNotIn("https://github.com/", args)

    def test_dedicated_chrome_is_bound_to_loopback_with_separate_data_dir(self):
        with patch("jarvis.subprocess.Popen") as popen, \
             patch("jarvis._jarvis_chrome_ready", side_effect=[False, True]):
            self.assertTrue(jarvis.ensure_jarvis_chrome("https://www.youtube.com/"))

        args = popen.call_args.args[0]
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn("--remote-debugging-port=9223", args)
        self.assertIn(f"--user-data-dir={jarvis.JARVIS_CHROME_DATA_DIR}", args)
        self.assertIn("--new-window", args)


if __name__ == "__main__":
    unittest.main()
