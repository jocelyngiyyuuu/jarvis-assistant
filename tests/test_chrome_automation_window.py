import unittest
import ctypes
import os
import subprocess
from unittest.mock import Mock, patch

from jarvis_core.window_manager import ChromeWindowManager, FileWindowManager


class ManagedDesktopWindowTests(unittest.TestCase):
    def test_snapshot_is_unverifiable_when_enumeration_fails(self):
        manager = FileWindowManager()
        with patch(
            "jarvis_core.window_manager.subprocess.run", side_effect=FileNotFoundError
        ):
            self.assertIsNone(manager.snapshot_ids())

    def test_rollback_without_exact_launched_pid_never_closes_new_window(self):
        manager = FileWindowManager()
        candidate = {"id": "0x2", "class": "org.gnome.Nautilus", "title": "Home"}
        with patch.object(manager, "list_windows", return_value=[candidate]), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            self.assertFalse(manager.close_new_window(set(), file_manager_only=True))
        run.assert_not_called()

    def test_individual_close_keeps_identity_when_enumeration_fails(self):
        manager = FileWindowManager()
        identity = {
            "id": "0x2", "class": "code.Code", "title": "Jarvis VS Code",
            "path": "VS Code", "pid": 222, "start_time": "100",
        }
        manager.last_windows["vscode"] = identity
        with patch(
            "jarvis_core.window_manager.subprocess.run", side_effect=FileNotFoundError
        ):
            success, _ = manager.close_last("vscode")
        self.assertFalse(success)
        self.assertIs(manager.last_windows["vscode"], identity)

    def test_close_all_fails_when_window_enumeration_fails(self):
        manager = FileWindowManager()
        with patch(
            "jarvis_core.window_manager.subprocess.run", side_effect=FileNotFoundError
        ):
            success, closed, _ = manager.close_all_gui_windows()
        self.assertFalse(success)
        self.assertEqual(closed, 0)

    def test_hosting_window_lookup_is_unverifiable_when_enumeration_fails(self):
        manager = FileWindowManager()
        with patch(
            "jarvis_core.window_manager.subprocess.run", side_effect=FileNotFoundError
        ):
            self.assertIsNone(manager.window_ids_for_pid_ancestry(os.getpid()))

    def test_pid_ancestry_finds_exact_hosting_window(self):
        manager = FileWindowManager()
        windows = [
            {"id": "0xhost", "class": "terminal.Terminal", "title": "Jarvis"},
            {"id": "0xother", "class": "terminal.Terminal", "title": "User"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), patch.object(
            manager, "_window_pid", side_effect=[os.getpid(), 999999]
        ):
            self.assertEqual(
                manager.window_ids_for_pid_ancestry(os.getpid()), {"0xhost"}
            )

    def test_named_app_tracking_filters_exact_allowed_class_components(self):
        manager = FileWindowManager()
        windows = [
            {"id": "0x2", "class": "code.evil.App", "title": "Jarvis VS Code"},
            {"id": "0x3", "class": "code.Code", "title": "Jarvis VS Code"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"):
            self.assertTrue(manager.track_opened_window(
                "vscode", set(), expected_title="Jarvis VS Code",
                allowed_classes={"code"}, timeout=0.01,
            ))
        self.assertEqual(manager.last_windows["vscode"]["id"], "0x3")

    def test_named_app_close_reuses_exact_identity_validation(self):
        manager = FileWindowManager()
        manager.last_windows["vscode"] = {
            "id": "0x3", "class": "code.Code", "title": "Jarvis VS Code",
            "path": "VS Code", "pid": 222, "start_time": "100",
        }
        reused = {"id": "0x3", "class": "code.Code", "title": "User VS Code"}
        with patch.object(manager, "list_windows", return_value=[reused]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_last("vscode")
        self.assertFalse(success)
        run.assert_not_called()

    def test_wmctrl_parser_excludes_hostname_from_title(self):
        rows = FileWindowManager._parse_windows(
            "0x0180003b  0 github.com.Google-chrome  Moriarty GitHub\n"
        )
        self.assertEqual(rows, [{
            "id": "0x0180003b", "class": "github.com.Google-chrome",
            "title": "GitHub",
        }])

    def test_folder_tracking_rejects_unrelated_new_window(self):
        manager = FileWindowManager()
        with patch("pathlib.Path.is_dir", return_value=True), patch.object(
            manager, "list_windows", return_value=[
                {"id": "0x2", "class": "org.gnome.Terminal", "title": "Downloads"},
            ]
        ):
            self.assertFalse(manager.track_opened_path("/tmp/Downloads", set(), timeout=0.01))

    def test_file_manager_class_allowlist_rejects_fileshare_substring(self):
        manager = FileWindowManager()
        self.assertFalse(manager._is_file_manager({
            "class": "org.example.Fileshare", "title": "Downloads"
        }))
        self.assertTrue(manager._is_file_manager({
            "class": "org.gnome.Nautilus", "title": "Downloads"
        }))

    def test_preexisting_matching_folder_window_is_never_adopted(self):
        manager = FileWindowManager()
        existing = {
            "id": "0x2", "class": "org.gnome.Nautilus", "title": "Downloads"
        }
        with patch("pathlib.Path.is_dir", return_value=True), patch.object(
            manager, "list_windows", return_value=[existing]
        ), patch.object(manager, "_window_pid", return_value=222), patch.object(
            manager, "_process_start_time", return_value="100"
        ):
            self.assertFalse(manager.track_opened_path(
                "/tmp/Downloads", {"0x2"}, timeout=0.01
            ))
        self.assertNotIn(str(__import__("pathlib").Path("/tmp/Downloads").resolve()), manager.path_windows)

    def test_named_folders_are_tracked_and_closed_independently(self):
        manager = FileWindowManager()
        downloads = {"id": "0x2", "class": "org.gnome.Nautilus", "title": "Downloads"}
        pictures = {"id": "0x3", "class": "org.gnome.Nautilus", "title": "Pictures"}
        with patch("pathlib.Path.is_dir", return_value=True), patch.object(
            manager, "list_windows", return_value=[downloads]
        ), patch.object(manager, "_window_pid", return_value=222), patch.object(
            manager, "_process_start_time", return_value="100"
        ):
            self.assertTrue(manager.track_opened_path("/tmp/Downloads", set(), timeout=0.01))
        with patch("pathlib.Path.is_dir", return_value=True), patch.object(
            manager, "list_windows", return_value=[downloads, pictures]
        ), patch.object(manager, "_window_pid", return_value=222), patch.object(
            manager, "_process_start_time", return_value="100"
        ):
            self.assertTrue(manager.track_opened_path("/tmp/Pictures", {"0x2"}, timeout=0.01))
        with patch.object(manager, "list_windows", side_effect=[[downloads, pictures], [pictures]]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            success, _ = manager.close_path("/tmp/Downloads")
        self.assertTrue(success)
        self.assertEqual(run.call_args.args[0], ["wmctrl", "-ic", "0x2"])
        self.assertIn(str(__import__("pathlib").Path("/tmp/Pictures").resolve()), manager.path_windows)

    def test_reused_desktop_window_id_is_not_closed(self):
        manager = FileWindowManager()
        manager.last_windows["terminal"] = {
            "id": "0x2", "class": "org.gnome.Terminal", "title": "Jarvis Terminal",
            "path": "Jarvis Terminal", "pid": 222,
        }
        with patch.object(manager, "list_windows", return_value=[
            {"id": "0x2", "class": "org.gnome.Nautilus", "title": "Downloads"},
        ]), patch.object(manager, "_window_pid", return_value=333), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_last("terminal")
        self.assertFalse(success)
        run.assert_not_called()

    def test_reused_desktop_pid_start_time_is_not_closed(self):
        manager = FileWindowManager()
        manager.last_windows["terminal"] = {
            "id": "0x2", "class": "org.gnome.Terminal", "title": "Jarvis Terminal",
            "path": "Jarvis Terminal", "pid": 222, "start_time": "100",
        }
        current = {"id": "0x2", "class": "org.gnome.Terminal", "title": "Jarvis Terminal"}
        with patch.object(manager, "list_windows", return_value=[current]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="200"), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_last("terminal")
        self.assertFalse(success)
        run.assert_not_called()

    def test_desktop_title_substring_change_is_not_accepted(self):
        manager = FileWindowManager()
        manager.last_windows["terminal"] = {
            "id": "0x2", "class": "org.gnome.Terminal", "title": "Jarvis Terminal",
            "path": "Jarvis Terminal", "pid": 222, "start_time": "100",
        }
        current = {
            "id": "0x2", "class": "org.gnome.Terminal",
            "title": "Unrelated Jarvis Terminal Window",
        }
        with patch.object(manager, "list_windows", return_value=[current]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_last("terminal")
        self.assertFalse(success)
        run.assert_not_called()

    def test_closes_only_tracked_terminal_process(self):
        manager = FileWindowManager()
        process = Mock()
        process.poll.return_value = None
        manager.track_process("terminal", process)
        success, _ = manager.close_process("terminal", "Terminal")
        self.assertTrue(success)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once()

    def test_tracks_and_closes_only_unique_terminal_window(self):
        manager = FileWindowManager()
        windows = [
            {"id": "0x1", "class": "org.gnome.Terminal", "title": "Old"},
            {"id": "0x2", "class": "org.gnome.Terminal", "title": "Terminal"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"):
            self.assertTrue(manager.track_opened_window("terminal", {"0x1"}))
        with patch.object(manager, "list_windows", side_effect=[windows, []]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            success, _ = manager.close_last("terminal")
        self.assertTrue(success)
        self.assertEqual(run.call_args.args[0], ["wmctrl", "-ic", "0x2"])

    def test_ambiguous_terminal_windows_are_not_tracked(self):
        manager = FileWindowManager()
        with patch.object(manager, "list_windows", return_value=[
            {"id": "0x2", "class": "terminal", "title": "A"},
            {"id": "0x3", "class": "terminal", "title": "B"},
        ]):
            self.assertFalse(manager.track_opened_window("terminal", set(), timeout=0.01))
        self.assertIsNone(manager.last_windows.get("terminal"))

    def test_unrelated_new_window_is_not_tracked_as_terminal(self):
        manager = FileWindowManager()
        with patch.object(manager, "list_windows", return_value=[
            {"id": "0x2", "class": "org.gnome.Nautilus", "title": "Downloads"},
        ]):
            self.assertFalse(manager.track_opened_window("terminal", set(), timeout=0.01))
        self.assertIsNone(manager.last_windows["terminal"])

    def test_terminal_tracking_requires_expected_title_marker(self):
        manager = FileWindowManager()
        with patch.object(manager, "list_windows", return_value=[
            {"id": "0x2", "class": "org.gnome.Ptyxis", "title": "User Terminal"},
        ]):
            self.assertFalse(manager.track_opened_window(
                "terminal", set(), timeout=0.01, expected_title="Jarvis Terminal"
            ))


class ChromeAutomationWindowTests(unittest.TestCase):
    def test_profile_close_retains_all_ownership_when_any_identity_is_unverifiable(self):
        manager = ChromeWindowManager()
        valid = {
            "id": "0x2", "class": "google-chrome", "title": "Gmail",
            "pid": 222, "start_time": "100",
        }
        missing = {
            "id": "0x3", "class": "google-chrome", "title": "Drive",
            "pid": 333, "start_time": "200",
        }
        manager.profile_windows["Default"] = [valid, missing]
        with patch.object(manager, "list_windows", return_value=[valid]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="100"), patch.object(
            manager, "_process_has_profile", return_value=True
        ), patch("jarvis_core.window_manager.subprocess.run") as run:
            success, _ = manager.close_profile("Default", "Cá nhân")
        self.assertFalse(success)
        run.assert_not_called()
        self.assertEqual(manager.profile_windows["Default"], [valid, missing])

    def test_site_close_rejects_process_start_time_change(self):
        manager = ChromeWindowManager()
        identity = {
            "id": "0x2", "class": "google-chrome", "title": "Gmail",
            "pid": 222, "start_time": "100",
        }
        manager.site_windows[("Default", "gmail")] = identity
        with patch.object(manager, "list_windows", return_value=[identity]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_start_time", return_value="200"), patch.object(
            manager, "_process_has_profile", return_value=True
        ), patch("jarvis_core.window_manager.subprocess.run") as run:
            success, _ = manager.close_site_window("Default", "gmail", "Gmail")
        self.assertFalse(success)
        run.assert_not_called()

    def test_site_close_requires_exact_stored_class_and_title(self):
        manager = ChromeWindowManager()
        manager.site_windows[("Profile 1", "github")] = {
            "id": "0x2", "class": "github.com.Google-chrome",
            "title": "GitHub", "pid": 222,
        }
        lookalike = {
            "id": "0x2", "class": "github.com.evil.Google-chrome",
            "title": "Unrelated GitHub",
        }
        with patch.object(manager, "list_windows", return_value=[lookalike]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_has_profile", return_value=True), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_site_window("Profile 1", "github", "GitHub")
        self.assertFalse(success)
        run.assert_not_called()

    def test_profile_close_refuses_reused_or_changed_window_identity(self):
        manager = ChromeWindowManager()
        manager.profile_windows["Profile 1"] = [{
            "id": "0x2", "class": "google-chrome.Google-chrome",
            "title": "Gmail", "pid": 222,
        }]
        reused = {
            "id": "0x2", "class": "org.gnome.Nautilus", "title": "Gmail",
        }
        with patch.object(manager, "list_windows", return_value=[reused]), patch.object(
            manager, "_window_pid", return_value=333
        ), patch.object(manager, "_process_has_profile", return_value=False), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_profile("Profile 1", "Học")
        self.assertFalse(success)
        run.assert_not_called()
        self.assertEqual(len(manager.profile_windows["Profile 1"]), 1)
        self.assertEqual(manager.profile_windows["Profile 1"][0]["id"], "0x2")

    def test_github_tracking_ignores_unrelated_profile_window_until_app_marker(self):
        manager = ChromeWindowManager()
        unrelated = {
            "id": "0x2", "class": "google-chrome.Google-chrome", "title": "Google"
        }
        github = {
            "id": "0x3", "class": "github.com.Google-chrome", "title": "GitHub"
        }
        with patch.object(
            manager, "list_windows", side_effect=[
                [unrelated], [unrelated, github], [unrelated, github], [unrelated, github]
            ]
        ), patch.object(manager, "_window_pid", return_value=222), patch.object(
            manager, "_process_start_time", return_value="100"
        ), patch.object(
            manager, "_process_has_profile", return_value=True
        ):
            self.assertTrue(manager.track_profile_window(
                "Profile 1", set(), site_key="github", timeout=0.5
            ))
        self.assertEqual(
            manager.site_windows[("Profile 1", "github")]["id"], "0x3"
        )

    def test_github_tracking_rejects_lookalike_app_class(self):
        manager = ChromeWindowManager()
        lookalike = {
            "id": "0x2", "class": "github.com.evil.Google-chrome", "title": "GitHub"
        }
        with patch.object(manager, "list_windows", return_value=[lookalike]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_has_profile", return_value=True):
            self.assertFalse(manager.track_profile_window(
                "Profile 1", set(), site_key="github", timeout=0.01
            ))

    def test_github_tracking_waits_for_stable_final_title(self):
        manager = ChromeWindowManager()
        initial = {
            "id": "0x2", "class": "github.com.Google-chrome", "title": "github.com_/"
        }
        stable = {
            "id": "0x2", "class": "github.com.Google-chrome", "title": "GitHub"
        }
        with patch.object(
            manager, "list_windows", side_effect=[
                [initial], [initial], [initial], [stable], [stable], [stable]
            ]
        ), patch.object(manager, "_window_pid", return_value=222), patch.object(
            manager, "_process_start_time", return_value="100"
        ), patch.object(
            manager, "_process_has_profile", return_value=True
        ):
            self.assertTrue(manager.track_profile_window(
                "Profile 1", set(), site_key="github", timeout=1.0
            ))
        self.assertEqual(
            manager.site_windows[("Profile 1", "github")]["title"], "GitHub"
        )

    def test_process_profile_accepts_chrome_rewritten_single_argv(self):
        manager = ChromeWindowManager()
        raw = (
            b"/opt/google/chrome/chrome --ozone-platform=x11 "
            b"--profile-directory=Profile 1 --new-window\0"
        )
        with patch("pathlib.Path.read_bytes", return_value=raw):
            self.assertTrue(manager._process_has_profile(123, "Profile 1"))
            self.assertFalse(manager._process_has_profile(123, "Default"))

    def test_tracks_unique_site_window_and_closes_only_that_window(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x1", "class": "google-chrome", "title": "Old"},
            {"id": "0x2", "class": "google-chrome", "title": "Gmail"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), patch.object(
            manager, "_window_pid", return_value=111
        ), patch.object(
            manager, "_process_start_time", return_value="100"
        ), patch.object(manager, "_process_has_profile", return_value=True):
            self.assertTrue(manager.track_profile_window(
                "Default", {"0x1"}, site_key="gmail"
            ))
        with patch.object(manager, "list_windows", side_effect=[windows, []]), patch.object(
            manager, "_window_pid", return_value=111
        ), patch.object(
            manager, "_process_start_time", return_value="100"
        ), patch.object(manager, "_process_has_profile", return_value=True), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            success, _ = manager.close_site_window("Default", "gmail", "Gmail")
        self.assertTrue(success)
        run.assert_called_once_with(
            ["wmctrl", "-ic", "0x2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        )

    def test_does_not_track_ambiguous_new_site_windows(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x2", "class": "google-chrome", "title": "Gmail A"},
            {"id": "0x3", "class": "google-chrome", "title": "Gmail B"},
        ]
        with patch.object(manager, "list_windows", return_value=windows):
            self.assertFalse(manager.track_profile_window(
                "Default", set(), site_key="gmail", timeout=0.01
            ))
        self.assertEqual(manager.site_windows, {})

    def test_reused_x11_id_with_different_identity_is_not_closed(self):
        manager = ChromeWindowManager()
        manager.site_windows[("Default", "gmail")] = "0x2"
        with patch.object(manager, "list_windows", return_value=[
            {"id": "0x2", "class": "org.gnome.Nautilus", "title": "Downloads"},
        ]), patch.object(manager, "_window_pid", return_value=333), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            success, _ = manager.close_site_window("Default", "gmail", "Gmail")
        self.assertFalse(success)
        run.assert_not_called()

    def test_close_site_does_not_claim_success_while_window_still_exists(self):
        manager = ChromeWindowManager()
        identity = {
            "id": "0x2", "class": "google-chrome", "title": "GitHub", "pid": 222
        }
        manager.site_windows[("Profile 1", "github")] = identity
        with patch.object(manager, "list_windows", return_value=[identity]), patch.object(
            manager, "_window_pid", return_value=222
        ), patch.object(manager, "_process_has_profile", return_value=True), patch(
            "jarvis_core.window_manager.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            success, _ = manager.close_site_window("Profile 1", "github", "GitHub")
        self.assertFalse(success)

    def test_site_tracking_rejects_new_window_from_wrong_profile(self):
        manager = ChromeWindowManager()
        stable = {"id": "0x9", "class": "google-chrome", "title": "GitHub - Google Chrome"}
        with patch.object(manager, "list_windows", return_value=[stable]), patch.object(
            manager, "_window_pid", return_value=999
        ), patch.object(
            manager, "_process_has_profile", return_value=False
        ):
            self.assertFalse(manager.track_profile_window(
                "Profile 1", set(), site_key="github", timeout=0.01
            ))

    def test_activates_new_automation_window_by_id(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x1", "class": "google-chrome.Google-chrome", "title": "Old"},
            {"id": "0x2", "class": "google-chrome (/home/khoa/.config/jarvis-chrome).Google-chrome", "title": "YouTube"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", side_effect=lambda wid: 222 if wid == "0x2" else 111), \
             patch.object(manager, "_focus_window", return_value=True) as focus:
            self.assertTrue(manager.activate_automation_window({"0x1"}, 222, "youtube"))

        focus.assert_called_once_with("0x2")

    def test_reuses_remembered_automation_window(self):
        manager = ChromeWindowManager()
        manager.automation_window_id = "0x9"
        with patch.object(manager, "list_windows", return_value=[
                {"id": "0x9", "class": "google-chrome (/home/khoa/.config/jarvis-chrome).Google-chrome", "title": "Jarvis"},
             ]), patch.object(manager, "_window_pid", return_value=999), \
             patch.object(manager, "_focus_window", return_value=True) as focus:
            self.assertTrue(manager.activate_automation_window(set(), 999, "jarvis"))

        focus.assert_called_once_with("0x9")

    def test_never_activates_concurrent_regular_chrome_window(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x2", "class": "google-chrome.Google-chrome", "title": "Personal"},
            {"id": "0x3", "class": "google-chrome (/home/khoa/.config/jarvis-chrome).Google-chrome", "title": "Jarvis"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", side_effect=lambda wid: 333 if wid == "0x3" else 222), \
             patch.object(manager, "_focus_window", return_value=True) as focus:
            self.assertTrue(manager.activate_automation_window(set(), 333, "jarvis", timeout=0.1))

        focus.assert_called_once_with("0x3")

    def test_focus_falls_back_to_x11_when_wmctrl_is_ignored(self):
        manager = ChromeWindowManager()
        with patch("jarvis_core.window_manager.subprocess.run"), \
             patch.object(manager, "_active_window_id", side_effect=["0x1", "0x3"]), \
             patch.object(manager, "_x11_force_focus", return_value=True) as force:
            self.assertTrue(manager._focus_window("0x3"))
        force.assert_called_once_with("0x3")

    def test_focus_treats_zero_padded_x11_ids_as_equal(self):
        manager = ChromeWindowManager()
        with patch("jarvis_core.window_manager.subprocess.run"), \
             patch.object(manager, "_active_window_id", return_value="0xe0000f"), \
             patch.object(manager, "_x11_force_focus") as force:
            self.assertTrue(manager._focus_window("0x00e0000f"))
        force.assert_not_called()

    def test_accepts_already_active_window_owned_by_automation_pid(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x7", "class": "google-chrome", "title": "Zalo"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x7"), \
             patch.object(manager, "_focus_window", return_value=True) as focus:
            self.assertTrue(manager.activate_automation_window({"0x7"}, 777, "zalo", timeout=0.1))
        focus.assert_called_once_with("0x7")

    def test_restart_selects_only_window_matching_requested_target(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x2", "class": "google-chrome", "title": "YouTube"},
            {"id": "0x7", "class": "google-chrome", "title": "Zalo"},
            {"id": "0x9", "class": "google-chrome", "title": "Personal"},
        ]
        pids = {"0x2": 777, "0x7": 777, "0x9": 999}
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", side_effect=lambda wid: pids[wid]), \
             patch.object(manager, "_active_window_id", return_value="0x9"), \
             patch.object(manager, "_focus_window", return_value=True) as focus:
            self.assertTrue(manager.activate_automation_window(set(w["id"] for w in windows), 777, "zalo", timeout=0.1))
        focus.assert_called_once_with("0x7")

    def test_ambiguous_owner_windows_fail_instead_of_selecting_last(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x2", "class": "google-chrome", "title": "YouTube"},
            {"id": "0x7", "class": "google-chrome", "title": "Zalo"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x1"), \
             patch.object(manager, "_focus_window") as focus:
            self.assertFalse(manager.activate_automation_window({"0x2", "0x7"}, 777, "github", timeout=0.01))
        focus.assert_not_called()

    def test_multiple_new_owner_windows_require_unique_target_match(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x2", "class": "google-chrome", "title": "Loading"},
            {"id": "0x7", "class": "google-chrome", "title": "New Tab"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x1"), \
             patch.object(manager, "_focus_window") as focus:
            self.assertFalse(manager.activate_automation_window(set(), 777, "zalo", timeout=0.01))
        focus.assert_not_called()

    def test_multiple_new_matching_windows_fail_even_when_one_is_active(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x2", "class": "google-chrome", "title": "YouTube A"},
            {"id": "0x7", "class": "google-chrome", "title": "YouTube B"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x7"), \
             patch.object(manager, "_focus_window") as focus:
            self.assertFalse(manager.activate_automation_window(set(), 777, "youtube", timeout=0.01))
        focus.assert_not_called()

    def test_single_pid_owned_window_is_safe_while_title_is_loading(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x7", "class": "google-chrome", "title": "New Tab"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x1"), \
             patch.object(manager, "_focus_window") as focus:
            self.assertTrue(manager.activate_automation_window(set(), 777, "zalo", timeout=0.01))
        focus.assert_called_once_with("0x7")

    def test_previous_ids_are_canonicalized_before_new_window_detection(self):
        manager = ChromeWindowManager()
        windows = [{"id": "0x00e0000f", "class": "google-chrome", "title": "YouTube"}]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x1"), \
             patch.object(manager, "_focus_window") as focus:
            self.assertFalse(manager.activate_automation_window({"0xe0000f"}, 777, "zalo", timeout=0.01))
        focus.assert_not_called()

    def test_x11_fallback_declares_signatures_and_closes_display_on_error(self):
        class Function:
            def __init__(self, result=None, error=None):
                self.result = result
                self.error = error
                self.argtypes = None
                self.restype = None
                self.calls = []
            def __call__(self, *args):
                self.calls.append(args)
                if self.error:
                    raise self.error
                return self.result

        class X11:
            XOpenDisplay = Function(1234)
            XMapRaised = Function(1)
            XSetInputFocus = Function(error=ctypes.ArgumentError("bad pointer"))
            XFlush = Function(1)
            XCloseDisplay = Function(0)

        fake = X11()
        with patch("jarvis_core.window_manager.ctypes.util.find_library", return_value="libX11.so"), \
             patch("jarvis_core.window_manager.ctypes.CDLL", return_value=fake):
            self.assertFalse(ChromeWindowManager._x11_force_focus("0x3"))
        self.assertEqual(fake.XOpenDisplay.argtypes, [ctypes.c_char_p])
        self.assertEqual(fake.XMapRaised.argtypes, [ctypes.c_void_p, ctypes.c_ulong])
        self.assertEqual(fake.XSetInputFocus.argtypes, [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong])
        self.assertEqual(len(fake.XCloseDisplay.calls), 1)


if __name__ == "__main__":
    unittest.main()
