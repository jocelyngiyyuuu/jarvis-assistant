import unittest
import ctypes
from unittest.mock import patch

from jarvis_core.window_manager import ChromeWindowManager


class ChromeAutomationWindowTests(unittest.TestCase):
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

    def test_single_new_window_must_match_requested_target(self):
        manager = ChromeWindowManager()
        windows = [
            {"id": "0x7", "class": "google-chrome", "title": "New Tab"},
        ]
        with patch.object(manager, "list_windows", return_value=windows), \
             patch.object(manager, "_window_pid", return_value=777), \
             patch.object(manager, "_active_window_id", return_value="0x1"), \
             patch.object(manager, "_focus_window") as focus:
            self.assertFalse(manager.activate_automation_window(set(), 777, "zalo", timeout=0.01))
        focus.assert_not_called()

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
