import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jarvis


class ChromeAutomationProfileTests(unittest.TestCase):
    def tearDown(self):
        jarvis.mcp_session = None
        jarvis.mcp_browser_pid = None
        jarvis.mcp_page_topology_changed = False
        for targets in jarvis.managed_cdp_targets.values():
            targets.clear()

    def test_existing_automation_chrome_opens_requested_url_via_loopback(self):
        pages = MagicMock()
        pages.__enter__ = MagicMock(return_value=pages)
        pages.__exit__ = MagicMock(return_value=False)
        pages.read.return_value = b"[]"
        created = MagicMock()
        created.__enter__ = MagicMock(return_value=created)
        created.__exit__ = MagicMock(return_value=False)
        created.read.return_value = b'{"type":"page"}'
        with patch.object(Path, "mkdir"), patch.object(Path, "chmod"), patch(
            "jarvis._jarvis_chrome_ready", return_value=True
        ), patch(
            "jarvis.urlopen", side_effect=[pages, created]
        ) as opened, patch("jarvis.subprocess.Popen") as popen:
            self.assertTrue(jarvis.ensure_jarvis_chrome("https://mail.google.com/"))
        popen.assert_not_called()
        self.assertEqual(opened.call_args_list[1].args[0].get_method(), "PUT")
        self.assertIn("mail.google.com", opened.call_args_list[1].args[0].full_url)

    def test_mcp_page_url_parser_ignores_current_selected_suffix(self):
        self.assertEqual(
            jarvis.extract_page_url(
                "1: YouTube (https://www.youtube.com/) [selected]"
            ),
            "https://www.youtube.com/",
        )

    def test_existing_unique_automation_tab_restores_ownership(self):
        page = {"id": "target-1", "type": "page", "url": "https://www.youtube.com/"}
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = __import__("json").dumps([page]).encode()
        jarvis.managed_cdp_targets["youtube"].clear()
        activated = MagicMock(status=200)
        activated.__enter__.return_value = activated
        activated.__exit__.return_value = False
        with patch("jarvis.urlopen", side_effect=[response, activated]) as opened:
            self.assertTrue(jarvis._ensure_jarvis_chrome_tab("https://www.youtube.com/"))
        self.assertEqual(jarvis.managed_cdp_targets["youtube"], {"target-1"})
        self.assertIn("/json/activate/target-1", opened.call_args_list[1].args[0])

    def test_mcp_lookup_restores_unique_tab_after_service_restart(self):
        page = {
            "id": "target-1", "type": "page", "url": "https://www.youtube.com/",
        }
        session = AsyncMock()
        session.call_tool.return_value = type("Result", (), {
            "content": [type("Text", (), {
                "text": "2: (39) YouTube (https://www.youtube.com/)",
            })()],
        })()
        jarvis.managed_cdp_targets["youtube"].clear()

        with patch("jarvis._cdp_pages", return_value=[page]), patch(
            "jarvis._jarvis_chrome_ready", return_value=True
        ):
            import asyncio
            page_id = asyncio.run(jarvis.find_youtube_page(session))

        self.assertEqual(page_id, 2)
        self.assertEqual(jarvis.managed_cdp_targets["youtube"], {"target-1"})

    def test_mcp_lookup_does_not_reown_ambiguous_tabs(self):
        pages = [
            {"id": "one", "type": "page", "url": "https://www.youtube.com/"},
            {"id": "two", "type": "page", "url": "https://www.youtube.com/feed"},
        ]
        session = AsyncMock()
        jarvis.managed_cdp_targets["youtube"].clear()

        with patch("jarvis._cdp_pages", return_value=pages), patch(
            "jarvis._jarvis_chrome_ready", return_value=True
        ):
            import asyncio
            page_id = asyncio.run(jarvis.find_youtube_page(session))

        self.assertIsNone(page_id)
        self.assertEqual(jarvis.managed_cdp_targets["youtube"], set())

    def test_new_cdp_tab_records_ownership_before_url_finishes_loading(self):
        pages = MagicMock()
        pages.__enter__.return_value = pages
        pages.__exit__.return_value = False
        pages.read.return_value = b"[]"
        created = MagicMock()
        created.__enter__.return_value = created
        created.__exit__.return_value = False
        created.read.return_value = b'{"id":"youtube-new","type":"page"}'
        with patch("jarvis.urlopen", side_effect=[pages, created]):
            self.assertTrue(
                jarvis._ensure_jarvis_chrome_tab("https://www.youtube.com/")
            )
        self.assertEqual(
            jarvis.managed_cdp_targets["youtube"], {"youtube-new"}
        )
        self.assertTrue(jarvis.mcp_page_topology_changed)
        with patch.object(jarvis, "_cdp_pages", return_value=[{
            "id": "youtube-new", "type": "page", "url": "about:blank",
        }]):
            self.assertTrue(jarvis._record_managed_cdp_target("youtube", set()))

    def test_weather_reuses_exact_tab_and_closes_owned_duplicate(self):
        target_url = "https://www.google.com/search?hl=vi&q=th%E1%BB%9Di+ti%E1%BA%BFt"
        pages = MagicMock()
        pages.__enter__.return_value = pages
        pages.__exit__.return_value = False
        pages.read.return_value = __import__("json").dumps([
            {"id": "weather-current", "type": "page", "url": target_url},
            {
                "id": "weather-old", "type": "page",
                "url": "https://www.google.com/search?hl=vi&q=thoi+tiet+tuan+toi",
            },
        ]).encode()
        closed = MagicMock()
        closed.__enter__.return_value = closed
        closed.__exit__.return_value = False
        activated = MagicMock(status=200)
        activated.__enter__.return_value = activated
        activated.__exit__.return_value = False
        jarvis.managed_cdp_targets["weather"].update({
            "weather-current", "weather-old",
        })

        with patch(
            "jarvis.urlopen", side_effect=[pages, closed, activated]
        ) as opened:
            self.assertTrue(jarvis._ensure_jarvis_chrome_tab(target_url))

        self.assertEqual(
            jarvis.managed_cdp_targets["weather"], {"weather-current"}
        )
        self.assertIn(
            "/json/close/weather-old", opened.call_args_list[1].args[0]
        )
        self.assertIn(
            "/json/activate/weather-current", opened.call_args_list[2].args[0]
        )

    def test_weather_new_period_replaces_previous_owned_tab(self):
        pages = MagicMock()
        pages.__enter__.return_value = pages
        pages.__exit__.return_value = False
        pages.read.return_value = __import__("json").dumps([{
            "id": "weather-old", "type": "page",
            "url": "https://www.google.com/search?hl=vi&q=thoi+tiet",
        }]).encode()
        created = MagicMock()
        created.__enter__.return_value = created
        created.__exit__.return_value = False
        created.read.return_value = b'{"id":"weather-new","type":"page"}'
        closed = MagicMock()
        closed.__enter__.return_value = closed
        closed.__exit__.return_value = False
        jarvis.managed_cdp_targets["weather"].add("weather-old")

        with patch(
            "jarvis.urlopen", side_effect=[pages, created, closed]
        ) as opened:
            self.assertTrue(jarvis._ensure_jarvis_chrome_tab(
                "https://www.google.com/search?hl=vi&q=thoi+tiet+tuan+toi"
            ))

        self.assertEqual(
            jarvis.managed_cdp_targets["weather"], {"weather-new"}
        )
        self.assertEqual(opened.call_args_list[1].args[0].get_method(), "PUT")
        self.assertIn(
            "/json/close/weather-old", opened.call_args_list[2].args[0]
        )

    def test_changed_page_topology_forces_mcp_reconnect(self):
        jarvis.mcp_session = object()
        jarvis.mcp_browser_pid = 123
        jarvis.mcp_page_topology_changed = True
        reconnect = AsyncMock(side_effect=RuntimeError("reconnect reached"))
        with patch.object(jarvis, "close_mcp_session", new=reconnect):
            with self.assertRaisesRegex(RuntimeError, "reconnect reached"):
                import asyncio
                asyncio.run(jarvis.get_mcp_session())
        reconnect.assert_awaited_once_with()

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

    def test_persistent_mcp_tracks_exact_browser_process(self):
        with patch("jarvis._jarvis_debug_port_owner_pid", return_value=456), patch(
            "jarvis._process_has_jarvis_chrome_args", return_value=True
        ):
            self.assertFalse(jarvis._mcp_browser_process_is_current(123))
            self.assertTrue(jarvis._mcp_browser_process_is_current(456))

    def test_youtube_uses_dedicated_automation_chrome(self):
        with patch("jarvis.ensure_jarvis_chrome", return_value=True) as ensure, \
             patch("jarvis._jarvis_debug_port_owner_pid", return_value=321), \
             patch("jarvis._record_managed_cdp_target", return_value=True), \
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
             patch("jarvis._record_managed_cdp_target", return_value=True), \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()), \
             patch.object(
                 jarvis.CHROME_WINDOWS, "activate_automation_window", return_value=True
             ):
            result = jarvis.open_chrome(
                jarvis.STUDY_PROFILE, "https://chat.zalo.me/"
            )

        self.assertTrue(result)
        ensure.assert_called_once_with("https://chat.zalo.me/")

    def test_weather_uses_dedicated_automation_chrome(self):
        url = "https://www.google.com/search?hl=vi&q=th%E1%BB%9Di+ti%E1%BA%BFt"
        self.assertEqual(jarvis._automation_site_key(url), "weather")
        with patch("jarvis.ensure_jarvis_chrome", return_value=True) as ensure, \
             patch("jarvis._jarvis_debug_port_owner_pid", return_value=321), \
             patch("jarvis._record_managed_cdp_target", return_value=True) as record, \
             patch.object(jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()), \
             patch.object(
                 jarvis.CHROME_WINDOWS, "activate_automation_window", return_value=True
             ) as activate:
            result = jarvis.open_chrome(jarvis.JARVIS_CHROME_PROFILE, url)

        self.assertTrue(result)
        ensure.assert_called_once_with(url)
        record.assert_called_once_with("weather", set())
        activate.assert_called_once_with(set(), 321, "thời tiết")

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
             patch("jarvis._record_managed_cdp_target", return_value=True), \
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
             patch("jarvis._record_managed_cdp_target", return_value=True), \
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

    def test_wayland_chrome_forwarded_open_reports_success_without_ownership(self):
        launcher = MagicMock()
        launcher.wait.return_value = 0
        with patch("jarvis.subprocess.Popen", return_value=launcher), patch.object(
            jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()
        ), patch.object(
            jarvis.CHROME_WINDOWS, "track_profile_window", return_value=False
        ):
            self.assertTrue(jarvis.open_chrome(
                jarvis.STUDY_PROFILE, "https://github.com/"
            ))
        self.assertNotIn(
            (jarvis.STUDY_PROFILE, "github"), jarvis.CHROME_WINDOWS.site_windows
        )

    def test_failed_untracked_chrome_launcher_still_reports_failure(self):
        launcher = MagicMock()
        launcher.wait.return_value = 1
        with patch("jarvis.subprocess.Popen", return_value=launcher), patch.object(
            jarvis.CHROME_WINDOWS, "snapshot_ids", return_value=set()
        ), patch.object(
            jarvis.CHROME_WINDOWS, "track_profile_window", return_value=False
        ):
            self.assertFalse(jarvis.open_chrome(
                jarvis.STUDY_PROFILE, "https://github.com/"
            ))

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
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            jarvis, "JARVIS_CHROME_DATA_DIR", Path(temp_dir) / "jarvis-chrome"
        ), patch("jarvis.subprocess.Popen") as popen, \
             patch("jarvis._jarvis_chrome_ready", side_effect=[False, True]):
            self.assertTrue(jarvis.ensure_jarvis_chrome("https://www.youtube.com/"))
            expected_data_dir = f"--user-data-dir={jarvis.JARVIS_CHROME_DATA_DIR}"

        args = popen.call_args.args[0]
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn("--remote-debugging-port=9223", args)
        self.assertIn(expected_data_dir, args)
        self.assertIn("--new-window", args)


if __name__ == "__main__":
    unittest.main()
