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
        self.last_windows = {
            "file": None, "folder": None, "terminal": None,
            "vscode": None, "system-monitor": None,
        }
        self.path_windows = {}
        self.processes = {}

    @staticmethod
    def _window_pid(window_id):
        try:
            result = subprocess.run(
                ["xprop", "-id", str(window_id), "_NET_WM_PID"],
                capture_output=True, text=True, check=False, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        match = re.search(r"=\s*(\d+)\s*$", result.stdout)
        return int(match.group(1)) if match else None

    @staticmethod
    def _process_start_time(pid):
        try:
            stat = Path(f"/proc/{int(pid)}/stat").read_text()
            fields_after_comm = stat.rsplit(") ", 1)[1].split()
            return fields_after_comm[19]
        except (OSError, IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _is_file_manager(window):
        window_class = window.get("class", "").casefold()
        components = {part for part in re.split(r"[.\s]+", window_class) if part}
        return bool(components & {
            "nautilus", "nemo", "thunar", "dolphin", "pcmanfm", "caja",
        })

    @staticmethod
    def _title_matches(expected, actual):
        expected = normalize_text(expected)
        actual = normalize_text(actual)
        return bool(expected and actual and (expected in actual or actual in expected))

    @staticmethod
    def _parse_windows(output):
        windows = []
        for line in output.splitlines():
            # ``wmctrl -lx``: id, desktop, WM_CLASS, hostname, title.
            parts = line.split(None, 4)
            if len(parts) >= 4:
                windows.append({
                    "id": parts[0], "class": parts[2],
                    "title": parts[4] if len(parts) == 5 else "",
                })
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

    def close_new_window(self, previous_ids, *, file_manager_only=False):
        """Rollback one unambiguous new X11 window without adopting it."""
        candidates = [
            window for window in self.list_windows()
            if window["id"] not in previous_ids
            and (not file_manager_only or self._is_file_manager(window))
        ]
        if len(candidates) != 1:
            return False
        window_id = candidates[0]["id"]
        try:
            result = subprocess.run(
                ["wmctrl", "-ic", window_id], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if window_id not in self.snapshot_ids():
                return True
            time.sleep(0.1)
        return False

    def track_opened_path(self, path, previous_ids, timeout=3.0):
        """Find the new window created after xdg-open and remember only that window."""
        path = Path(path)
        kind = "folder" if path.is_dir() else "file"
        deadline = time.monotonic() + timeout
        candidates = []
        while time.monotonic() < deadline:
            windows = self.list_windows()
            expected_titles = {path.name or "Home"}
            if path == Path.home():
                expected_titles.add("Home")
            candidates = [
                window for window in windows
                if window["id"] not in previous_ids
                and self._is_file_manager(window)
                and any(
                    self._title_matches(title, window.get("title", ""))
                    for title in expected_titles
                )
            ]
            if candidates:
                break
            time.sleep(0.15)

        if len(candidates) != 1:
            self.last_windows[kind] = None
            return False

        selected = candidates[0]
        pid = self._window_pid(selected["id"])
        start_time = self._process_start_time(pid) if pid is not None else None
        if pid is None or start_time is None:
            self.last_windows[kind] = None
            return False
        identity = {
            "id": selected["id"],
            "class": selected["class"],
            "title": selected["title"],
            "path": str(path.resolve()),
            "pid": pid,
            "start_time": start_time,
        }
        self.last_windows[kind] = identity
        self.path_windows[str(path.resolve())] = identity
        return True

    def track_opened_window(
        self, kind, previous_ids, timeout=3.0, expected_title=None,
        allowed_classes=None,
    ):
        """Remember one newly-created desktop window for a named managed kind."""
        deadline = time.monotonic() + timeout
        candidates = []
        while time.monotonic() < deadline:
            candidates = [
                window for window in self.list_windows()
                if window["id"] not in previous_ids
            ]
            if candidates:
                break
            time.sleep(0.15)
        if kind == "terminal":
            terminal_tokens = (
                "terminal", "console", "ptyxis", "kgx", "konsole", "xterm", "tilix",
            )
            candidates = [
                window for window in candidates
                if any(token in window.get("class", "").casefold() for token in terminal_tokens)
            ]
        if allowed_classes:
            allowed = {str(value).casefold() for value in allowed_classes}
            candidates = [
                window for window in candidates
                if {
                    part for part in re.split(
                        r"[.\s]+", window.get("class", "").casefold()
                    ) if part
                } == allowed
            ]
        if expected_title:
            expected = str(expected_title).casefold()
            candidates = [
                window for window in candidates
                if expected in window.get("title", "").casefold()
            ]
        if len(candidates) != 1:
            self.last_windows[kind] = None
            return False
        selected = candidates[0]
        pid = self._window_pid(selected["id"])
        start_time = self._process_start_time(pid) if pid is not None else None
        if pid is None or start_time is None:
            self.last_windows[kind] = None
            return False
        self.last_windows[kind] = {
            "id": selected["id"],
            "class": selected["class"],
            "title": selected["title"],
            "path": selected["title"],
            "pid": pid,
            "start_time": start_time,
        }
        return True

    def _close_identity(self, tracked, kind_label, clear):
        current = next((
            window for window in self.list_windows() if window["id"] == tracked["id"]
        ), None)
        current_pid = self._window_pid(tracked["id"]) if current else None
        current_start_time = (
            self._process_start_time(current_pid) if current_pid is not None else None
        )
        if (
            current is None
            or current.get("class", "").casefold() != tracked.get("class", "").casefold()
            or normalize_text(current.get("title", ""))
            != normalize_text(tracked.get("title", ""))
            or current_pid != tracked.get("pid")
            or current_start_time != tracked.get("start_time")
        ):
            clear()
            return False, f"Không còn xác minh được đúng cửa sổ {kind_label} do Jarvis mở."
        try:
            result = subprocess.run(
                ["wmctrl", "-ic", tracked["id"]], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False, "Không thể điều khiển cửa sổ; wmctrl chưa hoạt động."
        if result.returncode != 0:
            return False, f"Desktop không cho phép đóng riêng cửa sổ {kind_label} này."
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if tracked["id"] not in self.snapshot_ids():
                clear()
                return True, f"Đã đóng cửa sổ {kind_label}: {tracked['path']}"
            time.sleep(0.1)
        return False, f"Cửa sổ {kind_label} vẫn còn mở sau yêu cầu đóng."

    def close_last(self, kind):
        tracked = self.last_windows.get(kind)
        kind_label = {
            "folder": "thư mục/project",
            "file": "file",
            "terminal": "Terminal",
            "vscode": "VS Code",
            "system-monitor": "System Monitor",
        }.get(kind, kind)
        if tracked is None:
            return False, f"Không có cửa sổ {kind_label} nào do Jarvis theo dõi."

        return self._close_identity(
            tracked, kind_label, lambda: self.last_windows.__setitem__(kind, None)
        )

    def close_path(self, path):
        canonical = str(Path(path).resolve())
        tracked = self.path_windows.get(canonical)
        if tracked is None:
            return False, f"Không có cửa sổ thư mục {canonical} nào do Jarvis theo dõi."
        def clear():
            self.path_windows.pop(canonical, None)
            if self.last_windows.get("folder") is tracked:
                self.last_windows["folder"] = None
        return self._close_identity(tracked, "thư mục", clear)

    def close_last_file(self):
        return self.close_last("file")

    def close_last_folder(self):
        return self.close_last("folder")

    def close_all_paths(self):
        """Close every still-tracked file/folder window, never the whole manager."""
        identities = []
        seen = set()
        for identity in [
            *self.path_windows.values(),
            self.last_windows.get("file"),
            self.last_windows.get("folder"),
        ]:
            if not identity:
                continue
            key = (
                identity.get("id"), identity.get("pid"),
                identity.get("start_time"),
            )
            if key not in seen:
                seen.add(key)
                identities.append(identity)
        if not identities:
            return False, "Không có cửa sổ File Manager nào do Jarvis theo dõi."

        closed = 0
        failed = 0
        for identity in identities:
            def clear(target=identity):
                for path, tracked in list(self.path_windows.items()):
                    if tracked is target:
                        self.path_windows.pop(path, None)
                for kind in ("file", "folder"):
                    if self.last_windows.get(kind) is target:
                        self.last_windows[kind] = None

            success, _ = self._close_identity(identity, "File Manager", clear)
            closed += int(success)
            failed += int(not success)
        if failed:
            return False, (
                f"Đã đóng {closed} cửa sổ File Manager; "
                f"{failed} cửa sổ không còn xác minh được chính xác."
            )
        return True, f"Đã đóng {closed} cửa sổ File Manager do Jarvis mở."

    def track_process(self, kind, process):
        """Track an exact child process created for one managed app window."""
        self.processes[kind] = process

    def close_process(self, kind, app_name):
        process = self.processes.get(kind)
        if process is None or process.poll() is not None:
            self.processes.pop(kind, None)
            return False, f"Không có {app_name} nào do Jarvis theo dõi."
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False, f"Không thể đóng riêng {app_name} do Jarvis mở."
        self.processes.pop(kind, None)
        return True, f"Đã đóng {app_name} do Jarvis mở."


def chrome_profile_from_close_command(command):
    """Return the requested Chrome profile kind from a close command."""
    match = re.fullmatch(
        r"(?:tắt|tat|đóng|dong|thoát|thoat|close)\s+(?:google\s+)?chrome\s+"
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
        self.site_windows = {}
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
    def _process_has_profile(pid, profile):
        try:
            raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        except (OSError, TypeError, ValueError):
            return False
        args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        flag = f"--profile-directory={profile}"
        if flag in args:
            return True
        if len(args) != 1:
            return False
        return re.search(
            rf"(?<!\S){re.escape(flag)}(?=\s|$)", args[0]
        ) is not None

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

    def track_profile_window(self, profile, previous_ids, timeout=10.0, site_key=None):
        deadline = time.monotonic() + timeout
        candidates = []
        stable_identity = None
        stable_count = 0
        while time.monotonic() < deadline:
            candidates = []
            for window in self.list_windows():
                if (
                    window["id"] in previous_ids
                    or "chrome" not in window.get("class", "").lower()
                ):
                    continue
                if site_key == "github":
                    github_class = window.get("class", "").casefold()
                    github_title = normalize_text(window.get("title", ""))
                    if (
                        github_class != "github.com.google-chrome"
                        or github_title != "github"
                    ):
                        continue
                pid = self._window_pid(window["id"])
                start_time = self._process_start_time(pid) if pid is not None else None
                if (
                    pid is None or start_time is None
                    or not self._process_has_profile(pid, profile)
                ):
                    continue
                candidate = dict(window)
                candidate["pid"] = pid
                candidate["start_time"] = start_time
                candidates.append(candidate)
            if site_key == "github" and len(candidates) == 1:
                identity = (
                    candidates[0]["id"], candidates[0]["class"],
                    normalize_text(candidates[0]["title"]), candidates[0]["pid"],
                )
                if identity == stable_identity:
                    stable_count += 1
                else:
                    stable_identity = identity
                    stable_count = 1
                if stable_count >= 3:
                    break
            elif candidates:
                break
            else:
                stable_identity = None
                stable_count = 0
            time.sleep(0.15)
        if len(candidates) != 1 or (site_key == "github" and stable_count < 3):
            return False
        selected = candidates[0]
        tracked = self.profile_windows.setdefault(profile, [])
        if selected["id"] not in {window["id"] for window in tracked}:
            tracked.append(selected)
        if site_key:
            self.site_windows[(profile, site_key)] = dict(selected)
        return True

    def close_site_window(self, profile, site_key, site_name):
        identity = self.site_windows.get((profile, site_key))
        if not identity:
            return False, f"Không có cửa sổ {site_name} nào do Jarvis theo dõi."
        if isinstance(identity, str):
            window_id = identity
            expected_class = "chrome"
            expected_title = normalize_text(site_name)
        else:
            window_id = identity.get("id")
            expected_class = identity.get("class", "").casefold()
            expected_title = normalize_text(identity.get("title", ""))
        expected_pid = identity.get("pid") if isinstance(identity, dict) else None
        current = next((
            window for window in self.list_windows()
            if self._normalize_window_id(window["id"]) == self._normalize_window_id(window_id)
        ), None)
        current_class = current.get("class", "").casefold() if current else ""
        current_title = normalize_text(current.get("title", "")) if current else ""
        current_pid = self._window_pid(window_id) if current else None
        title_matches = bool(expected_title and current_title == expected_title)
        if (
            current is None
            or current_class != expected_class
            or not title_matches
            or expected_pid is None
            or current_pid != expected_pid
            or not self._process_has_profile(current_pid, profile)
        ):
            self.site_windows.pop((profile, site_key), None)
            return False, f"Không còn xác minh được đúng cửa sổ {site_name} do Jarvis mở."
        try:
            result = subprocess.run(
                ["wmctrl", "-ic", window_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False, f"Không thể đóng riêng cửa sổ {site_name}."
        if result.returncode != 0:
            return False, f"Desktop không cho phép đóng cửa sổ {site_name}."
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            remaining_ids = {
                self._normalize_window_id(window["id"])
                for window in self.list_windows()
            }
            if self._normalize_window_id(window_id) not in remaining_ids:
                break
            time.sleep(0.1)
        else:
            return False, f"Cửa sổ {site_name} vẫn còn mở sau yêu cầu đóng."
        self.site_windows.pop((profile, site_key), None)
        tracked = self.profile_windows.get(profile, [])
        self.profile_windows[profile] = [
            window for window in tracked
            if self._normalize_window_id(window["id"]) != self._normalize_window_id(window_id)
        ]
        return True, f"Đã đóng {site_name}."

    def close_profile(self, profile, profile_name):
        tracked = self.profile_windows.get(profile, [])
        if not tracked:
            return False, f"Không có cửa sổ Chrome {profile_name} nào do Jarvis theo dõi."

        current_by_id = {
            self._normalize_window_id(window["id"]): window
            for window in self.list_windows()
        }
        closable = []
        for identity in tracked:
            window_id = self._normalize_window_id(identity.get("id"))
            current = current_by_id.get(window_id)
            current_pid = self._window_pid(identity.get("id")) if current else None
            current_start = (
                self._process_start_time(current_pid) if current_pid is not None else None
            )
            if (
                current is None
                or current.get("class", "").casefold()
                != identity.get("class", "").casefold()
                or normalize_text(current.get("title", ""))
                != normalize_text(identity.get("title", ""))
                or current_pid != identity.get("pid")
                or current_start != identity.get("start_time")
                or not self._process_has_profile(current_pid, profile)
            ):
                continue
            closable.append(identity)
        if not closable:
            self.profile_windows[profile] = []
            self.site_windows = {
                key: value for key, value in self.site_windows.items()
                if key[0] != profile
            }
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
        deadline = time.monotonic() + 2.0
        closing_ids = {
            self._normalize_window_id(window["id"]) for window in closable
        }
        while time.monotonic() < deadline:
            remaining = {
                self._normalize_window_id(window["id"])
                for window in self.list_windows()
            }
            if closing_ids.isdisjoint(remaining):
                break
            time.sleep(0.1)
        else:
            return False, f"Một số cửa sổ Chrome {profile_name} vẫn còn mở."
        self.profile_windows[profile] = []
        self.site_windows = {
            key: value for key, value in self.site_windows.items()
            if key[0] != profile
        }
        return True, f"Đã đóng {len(closable)} cửa sổ Chrome {profile_name} do Jarvis mở."

    def clear(self):
        self.profile_windows.clear()
        self.site_windows.clear()
