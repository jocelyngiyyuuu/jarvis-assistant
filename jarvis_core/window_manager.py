"""Track and close individual desktop windows opened by Jarvis."""

import ctypes
import ctypes.util
import re
import subprocess
import time
from pathlib import Path

from .text import normalize_text


class FileWindowManager:
    """Best-effort window tracking through wmctrl, without killing an app."""

    def __init__(self):
        self.last_windows = {"file": None, "folder": None}

    @staticmethod
    def _parse_windows(output):
        windows = []
        for line in output.splitlines():
            # ``wmctrl -lx``: id, desktop, WM_CLASS, title.
            parts = line.split(None, 3)
            if len(parts) == 4:
                windows.append({"id": parts[0], "class": parts[2], "title": parts[3]})
        return windows

    def list_windows(self):
        try:
            result = subprocess.run(
                ["wmctrl", "-lx"], capture_output=True, text=True, check=False, timeout=2
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return self._parse_windows(result.stdout)

    def snapshot_ids(self):
        return {window["id"] for window in self.list_windows()}

    def track_opened_path(self, path, previous_ids, timeout=3.0):
        """Find the new window created after xdg-open and remember only that window."""
        path = Path(path)
        kind = "folder" if path.is_dir() else "file"
        deadline = time.monotonic() + timeout
        candidates = []
        while time.monotonic() < deadline:
            windows = self.list_windows()
            candidates = [window for window in windows if window["id"] not in previous_ids]
            if candidates:
                break
            time.sleep(0.15)

        if len(candidates) != 1:
            # Some apps reuse a window. Only accept an unambiguous title match.
            needle = normalize_text(path.stem)
            title_matches = [
                window for window in self.list_windows()
                if needle and needle in normalize_text(window["title"])
            ]
            if len(title_matches) != 1:
                self.last_windows[kind] = None
                return False
            candidates = title_matches

        selected = candidates[0]
        self.last_windows[kind] = {
            "id": selected["id"],
            "title": selected["title"],
            "path": str(path),
        }
        return True

    def close_last(self, kind):
        tracked = self.last_windows.get(kind)
        kind_label = "thư mục/project" if kind == "folder" else "file"
        if tracked is None:
            return False, f"Không có cửa sổ {kind_label} nào do Jarvis theo dõi."

        current_ids = {window["id"] for window in self.list_windows()}
        if tracked["id"] not in current_ids:
            self.last_windows[kind] = None
            return False, f"Cửa sổ {kind_label} gần nhất đã được đóng trước đó."

        try:
            result = subprocess.run(
                ["wmctrl", "-ic", tracked["id"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False, "Không thể điều khiển cửa sổ; wmctrl chưa hoạt động."
        if result.returncode != 0:
            return False, f"Desktop không cho phép đóng riêng cửa sổ {kind_label} này."

        self.last_windows[kind] = None
        return True, f"Đã đóng cửa sổ {kind_label}: {tracked['path']}"

    def close_last_file(self):
        return self.close_last("file")

    def close_last_folder(self):
        return self.close_last("folder")


def chrome_profile_from_close_command(command):
    """Return the requested Chrome profile kind from a close command."""
    match = re.fullmatch(
        r"(?:tắt|tat|đóng|dong|close)\s+(?:google\s+)?chrome\s+"
        r"(?:profile\s+)?("
        r"cá\s*nhân|ca\s*nhan|personal|"
        r"học(?:\s*tập)?(?:\s*/\s*chat\s*gpt)?|"
        r"hoc(?:\s*tap)?(?:\s*/\s*chat\s*gpt)?|"
        r"study|chat\s*gpt"
        r")",
        str(command).strip().lower(),
        re.IGNORECASE,
    )
    if not match:
        return None
    if re.fullmatch(r"cá\s*nhân|ca\s*nhan|personal", match.group(1), re.IGNORECASE):
        return "personal"
    return "study"


class ChromeWindowManager(FileWindowManager):
    """Track Chrome windows separately for each profile used by Jarvis."""

    def __init__(self):
        super().__init__()
        self.profile_windows = {}
        self.automation_window_id = None

    @staticmethod
    def _normalize_window_id(window_id):
        try:
            return hex(int(str(window_id), 16))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _window_pid(window_id):
        try:
            result = subprocess.run(
                ["xprop", "-id", window_id, "_NET_WM_PID"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        match = re.search(r"=\s*(\d+)\s*$", result.stdout)
        return int(match.group(1)) if match else None

    @staticmethod
    def _active_window_id():
        try:
            result = subprocess.run(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                capture_output=True, text=True, check=False, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        match = re.search(r"0x[0-9a-f]+", result.stdout, re.IGNORECASE)
        return ChromeWindowManager._normalize_window_id(match.group(0)) if match else None

    @staticmethod
    def _x11_force_focus(window_id):
        library = ctypes.util.find_library("X11")
        if not library:
            return False
        display = None
        try:
            x11 = ctypes.CDLL(library)
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x11.XMapRaised.restype = ctypes.c_int
            x11.XSetInputFocus.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
            ]
            x11.XSetInputFocus.restype = ctypes.c_int
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XFlush.restype = ctypes.c_int
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.restype = ctypes.c_int
            display = x11.XOpenDisplay(None)
            if not display:
                return False
            window = ctypes.c_ulong(int(window_id, 16))
            x11.XMapRaised(display, window)
            x11.XSetInputFocus(display, window, 2, 0)
            x11.XFlush(display)
            return True
        except (OSError, TypeError, ValueError, ctypes.ArgumentError):
            return False
        finally:
            if display:
                try:
                    x11.XCloseDisplay(display)
                except (OSError, TypeError, ctypes.ArgumentError):
                    pass

    def _focus_window(self, window_id):
        try:
            subprocess.run(
                ["wmctrl", "-ia", window_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        time.sleep(0.05)
        normalized_id = self._normalize_window_id(window_id)
        if self._active_window_id() == normalized_id:
            return True
        if not self._x11_force_focus(window_id):
            return False
        time.sleep(0.05)
        return self._active_window_id() == normalized_id

    def activate_automation_window(
        self, previous_ids, owner_pid, target_hint, timeout=10.0
    ):
        """Activate only the X11 window owned by Jarvis' CDP Chrome PID."""
        deadline = time.monotonic() + timeout
        selected_id = None
        previous_normalized = {
            self._normalize_window_id(window_id) for window_id in previous_ids
        }
        target_hint = str(target_hint or "").casefold()
        while time.monotonic() < deadline:
            windows = [
                window for window in self.list_windows()
                if self._window_pid(window["id"]) == owner_pid
            ]
            current_ids = {
                self._normalize_window_id(window["id"]) for window in windows
            }
            new_windows = [
                window for window in windows
                if self._normalize_window_id(window["id"]) not in previous_normalized
            ]
            if len(new_windows) == 1 and target_hint in new_windows[0].get(
                "title", ""
            ).casefold():
                selected_id = new_windows[0]["id"]
                break
            if len(new_windows) > 1:
                matching_new = [
                    window for window in new_windows
                    if target_hint in window.get("title", "").casefold()
                ]
                if len(matching_new) == 1:
                    selected_id = matching_new[0]["id"]
                    break
                time.sleep(0.15)
                continue
            target_windows = [
                window for window in windows
                if target_hint and target_hint in window.get("title", "").casefold()
            ]
            active_id = self._active_window_id()
            target_ids = {
                self._normalize_window_id(window["id"])
                for window in target_windows
            }
            if active_id in target_ids:
                selected_id = next(
                    window["id"] for window in target_windows
                    if self._normalize_window_id(window["id"]) == active_id
                )
                break
            if (
                self._normalize_window_id(self.automation_window_id) in target_ids
            ):
                selected_id = self.automation_window_id
                break
            if len(target_windows) == 1:
                selected_id = target_windows[0]["id"]
                break
            time.sleep(0.15)

        if selected_id is None:
            return False
        if not self._focus_window(selected_id):
            return False
        self.automation_window_id = selected_id
        return True

    def track_profile_window(self, profile, previous_ids, timeout=10.0):
        deadline = time.monotonic() + timeout
        candidates = []
        while time.monotonic() < deadline:
            candidates = [
                window for window in self.list_windows()
                if window["id"] not in previous_ids
                and "chrome" in window.get("class", "").lower()
            ]
            if candidates:
                break
            time.sleep(0.15)
        if not candidates:
            return False

        # Chrome có thể tạo hơn một X11 window phụ trong lúc khởi động.
        # Cửa sổ trình duyệt chính xuất hiện cuối danh sách wmctrl.
        selected = candidates[-1]
        tracked = self.profile_windows.setdefault(profile, [])
        if selected["id"] not in {window["id"] for window in tracked}:
            tracked.append(selected)
        return True

    def close_profile(self, profile, profile_name):
        tracked = self.profile_windows.get(profile, [])
        if not tracked:
            return False, f"Không có cửa sổ Chrome {profile_name} nào do Jarvis theo dõi."

        current_ids = {window["id"] for window in self.list_windows()}
        closable = [window for window in tracked if window["id"] in current_ids]
        if not closable:
            self.profile_windows[profile] = []
            return False, f"Các cửa sổ Chrome {profile_name} đã được đóng trước đó."

        failed = 0
        for window in closable:
            try:
                result = subprocess.run(
                    ["wmctrl", "-ic", window["id"]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=2,
                )
                failed += int(result.returncode != 0)
            except (FileNotFoundError, subprocess.SubprocessError):
                failed += 1

        if failed:
            return False, f"Không thể đóng riêng {failed} cửa sổ Chrome {profile_name}."
        self.profile_windows[profile] = []
        return True, f"Đã đóng {len(closable)} cửa sổ Chrome {profile_name} do Jarvis mở."

    def clear(self):
        self.profile_windows.clear()
