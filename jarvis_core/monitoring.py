"""Cross-platform system health snapshots using the Python standard library."""

import os
import shutil
import time
from pathlib import Path


_HWMON_LABELS = {
    "amdgpu": "GPU",
    "coretemp": "CPU",
    "k10temp": "CPU",
    "zenpower": "CPU",
}


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _temperature_label(hwmon_name):
    normalized = hwmon_name.casefold()
    return _HWMON_LABELS.get(normalized)


def _read_temperatures(hwmon_root=Path("/sys/class/hwmon")):
    """Read one representative, sane temperature from each hwmon device."""
    readings = []
    try:
        devices = sorted(Path(hwmon_root).glob("hwmon*"))
    except OSError:
        return readings

    for device in devices:
        name = _read_text(device / "name")
        label = _temperature_label(name)
        if label is None:
            continue
        try:
            inputs = sorted(device.glob("temp*_input"))
        except OSError:
            continue
        for input_path in inputs:
            try:
                celsius = float(_read_text(input_path)) / 1000
            except ValueError:
                continue
            # Broken/unsupported hwmon channels commonly expose impossible
            # sentinel values. Do not show those as real system temperatures.
            if not -20 <= celsius <= 150:
                continue
            readings.append((label, celsius))
            break

    counts = {}
    labeled = []
    for label, celsius in readings:
        counts[label] = counts.get(label, 0) + 1
        suffix = f" {counts[label]}" if counts[label] > 1 else ""
        labeled.append((f"{label}{suffix}", celsius))
    return labeled


def _read_meminfo():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, raw = line.split(":", 1)
            values[name] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return {}
    return values


def _uptime_seconds():
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _mount_point(path):
    """Return the mount point containing *path*."""
    current = Path(path).expanduser().resolve()
    try:
        current_device = current.stat().st_dev
    except OSError:
        return current
    while current.parent != current:
        try:
            if current.parent.stat().st_dev != current_device:
                break
        except OSError:
            break
        current = current.parent
    return current


class SystemMonitor:
    def __init__(self, disk_path=None, hwmon_root=None, disk_paths=None):
        self.disk_path = Path(disk_path or Path.home())
        self.disk_paths = [Path(path) for path in (disk_paths or [self.disk_path])]
        self.hwmon_root = Path(hwmon_root or "/sys/class/hwmon")

    def _disk_snapshots(self):
        disks = []
        seen_devices = set()
        for path in self.disk_paths:
            try:
                device = path.expanduser().resolve().stat().st_dev
                if device in seen_devices:
                    continue
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            seen_devices.add(device)
            disks.append(
                {
                    "mount": str(_mount_point(path)),
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                }
            )
        return disks

    def snapshot(self):
        memory = _read_meminfo()
        total = memory.get("MemTotal", 0)
        available = memory.get("MemAvailable", 0)
        disks = self._disk_snapshots()
        # Keep the original fields for callers that consume snapshots directly.
        primary_disk = disks[0] if disks else {"total": 0, "used": 0}
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = (0.0, 0.0, 0.0)
        return {
            "load": load,
            "cpu_count": os.cpu_count() or 1,
            "memory_total": total,
            "memory_used": max(0, total - available),
            "disk_total": primary_disk["total"],
            "disk_used": primary_disk["used"],
            "disks": disks,
            "temperatures": _read_temperatures(self.hwmon_root),
            "uptime": _uptime_seconds(),
            "timestamp": time.time(),
        }

    @staticmethod
    def format(snapshot):
        gib = 1024 ** 3
        memory_total = snapshot["memory_total"] / gib
        memory_used = snapshot["memory_used"] / gib
        uptime = snapshot["uptime"]
        uptime_text = "không xác định" if uptime is None else f"{uptime / 3600:.1f} giờ"
        load = snapshot["load"]
        temperatures = snapshot.get("temperatures", [])
        temperature_text = ", ".join(
            f"{label} {celsius:.1f}°C" for label, celsius in temperatures
        ) or "không có cảm biến"
        disks = snapshot.get("disks") or [
            {
                "mount": "/",
                "used": snapshot["disk_used"],
                "total": snapshot["disk_total"],
            }
        ]
        disk_lines = []
        for index, disk in enumerate(disks, 1):
            disk_used = disk["used"] / gib
            disk_total = disk["total"] / gib
            label = "Ổ hệ thống" if index == 1 else "Ổ dự án" if index == 2 else f"Ổ thứ {index}"
            disk_lines.append(
                f"• {label}: {disk_used:.1f}/{disk_total:.1f} GiB"
            )
        return (
            "🖥️ **TÌNH TRẠNG HỆ THỐNG**\n"
            f"• CPU: {snapshot['cpu_count']} luồng, tải 1/5/15 phút: "
            f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}\n"
            f"• Nhiệt độ: {temperature_text}\n"
            f"• RAM: {memory_used:.1f}/{memory_total:.1f} GiB\n"
            f"{'\n'.join(disk_lines)}\n"
            f"• Uptime: {uptime_text}"
        )
