"""Verified XWayland desktop observation and control helpers."""

from dataclasses import dataclass
from datetime import datetime
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import re
import subprocess

from .text import normalize_text


@dataclass(frozen=True)
class Monitor:
    name: str
    width: int
    height: int
    x: int
    y: int
    primary: bool = False


def parse_xrandr_monitors(output):
    monitors = []
    pattern = re.compile(
        r"^(\S+)\s+connected(?P<primary>\s+primary)?(?:\s+[^\s]+)*?"
        r"\s+(\d+)x(\d+)\+(-?\d+)\+(-?\d+)(?:\s|$)"
    )
    for line in str(output).splitlines():
        match = pattern.search(line)
        if not match:
            continue
        monitors.append(Monitor(
            name=match.group(1),
            width=int(match.group(3)),
            height=int(match.group(4)),
            x=int(match.group(5)),
            y=int(match.group(6)),
            primary=bool(match.group("primary")),
        ))
    return sorted(monitors, key=lambda item: (item.x, item.y, item.name))


def parse_monitor_role(value):
    plain = normalize_text(value)
    if any(term in plain for term in ("man hinh chinh", "monitor chinh", "primary")):
        return "primary"
    if any(term in plain for term in ("man hinh phu", "monitor phu", "secondary")):
        return "secondary"
    match = re.search(r"(?:man hinh|monitor)\s*(\d+)", plain)
    return f"index:{int(match.group(1))}" if match else None


def strip_monitor_suffix(command):
    return re.sub(
        r"\s+(?:trên|tren|sang|ở|o)\s+(?:màn|man)\s+hình\s+"
        r"(?:chính|chinh|phụ|phu|\d+)\s*$",
        "", str(command), flags=re.IGNORECASE,
    ).strip()


class DesktopController:
    """Control only explicit windows/coordinates through the XWayland bridge."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.preferences_path = self.data_dir / "desktop_preferences.json"
        self.screenshot_dir = self.data_dir / "screenshots"

    def monitors(self):
        try:
            result = subprocess.run(
                ["xrandr", "--query"], capture_output=True, text=True,
                check=False, timeout=3,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return []
        return parse_xrandr_monitors(result.stdout) if result.returncode == 0 else []

    @staticmethod
    def _select_monitor(monitors, role):
        if not monitors:
            return None
        if role == "primary":
            return next((item for item in monitors if item.primary), monitors[0])
        if role == "secondary":
            primary = next((item for item in monitors if item.primary), monitors[0])
            return next((item for item in monitors if item != primary), None)
        match = re.fullmatch(r"index:(\d+)", str(role))
        index = int(match.group(1)) - 1 if match else -1
        return monitors[index] if 0 <= index < len(monitors) else None

    @staticmethod
    def active_window_id():
        try:
            result = subprocess.run(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                capture_output=True, text=True, check=False, timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        match = re.search(r"0x[0-9a-f]+", result.stdout, re.IGNORECASE)
        return match.group(0) if match and int(match.group(0), 16) else None

    def move_window(self, window_id, role):
        if not re.fullmatch(r"0x[0-9a-f]+", str(window_id), re.IGNORECASE):
            return False, "Không xác minh được cửa sổ cần di chuyển."
        monitors = self.monitors()
        monitor = self._select_monitor(monitors, role)
        if monitor is None:
            return False, "Không tìm thấy màn hình bạn yêu cầu."
        commands = (
            ["wmctrl", "-ir", str(window_id), "-b", "remove,maximized_vert,maximized_horz"],
            ["wmctrl", "-ir", str(window_id), "-e", f"0,{monitor.x},{monitor.y},-1,-1"],
            ["wmctrl", "-ir", str(window_id), "-b", "add,maximized_vert,maximized_horz"],
            ["wmctrl", "-ia", str(window_id)],
        )
        try:
            for command in commands:
                result = subprocess.run(
                    command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=3,
                )
                if result.returncode != 0:
                    return False, "Desktop không cho phép di chuyển cửa sổ này."
        except (FileNotFoundError, subprocess.SubprocessError):
            return False, "Thiếu wmctrl hoặc desktop không phản hồi."
        label = "chính" if role == "primary" else "phụ" if role == "secondary" else monitor.name
        return True, f"Đã chuyển cửa sổ sang màn hình {label}."

    def describe(self, windows=None):
        monitors = self.monitors()
        if not monitors:
            return "❌ Không đọc được bố cục màn hình trên phiên desktop hiện tại."
        lines = ["🖥️ **TRẠNG THÁI MÀN HÌNH**"]
        for index, monitor in enumerate(monitors, start=1):
            label = " · chính" if monitor.primary else " · phụ" if len(monitors) > 1 else ""
            lines.append(
                f"• Màn hình {index}: {monitor.name} — {monitor.width}×{monitor.height} "
                f"tại ({monitor.x}, {monitor.y}){label}"
            )
        if windows is not None:
            visible = [item for item in windows if item.get("title")][:12]
            lines.append(f"• Cửa sổ XWayland nhìn thấy: {len(visible)}")
            lines.extend(f"  - {item['title'][:100]}" for item in visible)
        return "\n".join(lines)

    def _read_preferences(self):
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def set_preference(self, app_key, role):
        preferences = self._read_preferences()
        preferences[str(app_key)] = str(role)
        temporary = self.preferences_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(self.preferences_path)

    def clear_preference(self, app_key):
        preferences = self._read_preferences()
        removed = preferences.pop(str(app_key), None) is not None
        temporary = self.preferences_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(self.preferences_path)
        return removed

    def preference(self, app_key):
        role = self._read_preferences().get(str(app_key))
        return role if role in {"primary", "secondary"} or str(role).startswith("index:") else None

    def capture_screen(self):
        self.screenshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.screenshot_dir / f"screen-{timestamp}.png"
        session_type = os.getenv("XDG_SESSION_TYPE", "").casefold()
        if session_type == "wayland":
            command = [
                "/usr/bin/python3",
                str(Path(__file__).with_name("portal_screenshot.py")),
                str(path),
            ]
            timeout = 25
        else:
            command = ["scrot", str(path)]
            timeout = 10
        try:
            result = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=timeout,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            return None
        path.chmod(0o600)
        return path

    def _xtest(self, action, *, x=None, y=None, amount=1):
        x11_name = ctypes.util.find_library("X11")
        xtst_name = ctypes.util.find_library("Xtst")
        if not x11_name or not xtst_name:
            return False
        display = None
        try:
            x11 = ctypes.CDLL(x11_name)
            xtst = ctypes.CDLL(xtst_name)
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            xtst.XTestFakeMotionEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_ulong,
            ]
            xtst.XTestFakeButtonEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong,
            ]
            display = x11.XOpenDisplay(None)
            if not display:
                return False
            if action in {"move", "click"}:
                if not xtst.XTestFakeMotionEvent(display, -1, int(x), int(y), 0):
                    return False
            if action == "click":
                xtst.XTestFakeButtonEvent(display, 1, 1, 0)
                xtst.XTestFakeButtonEvent(display, 1, 0, 0)
            elif action == "scroll":
                button = 4 if int(amount) > 0 else 5
                for _ in range(min(abs(int(amount)), 20)):
                    xtst.XTestFakeButtonEvent(display, button, 1, 0)
                    xtst.XTestFakeButtonEvent(display, button, 0, 0)
            x11.XFlush(display)
            return True
        except (OSError, TypeError, ValueError, ctypes.ArgumentError):
            return False
        finally:
            if display:
                try:
                    x11.XCloseDisplay(display)
                except Exception:
                    pass

    def _coordinates_valid(self, x, y):
        return any(
            monitor.x <= x < monitor.x + monitor.width
            and monitor.y <= y < monitor.y + monitor.height
            for monitor in self.monitors()
        )

    def move_pointer(self, x, y):
        if not self._coordinates_valid(x, y):
            return False
        return self._xtest("move", x=x, y=y)

    def click(self, x, y):
        if not self._coordinates_valid(x, y):
            return False
        return self._xtest("click", x=x, y=y)

    def scroll(self, amount):
        return self._xtest("scroll", amount=amount)
