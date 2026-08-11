"""Track and close individual desktop windows opened by Jarvis."""

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
