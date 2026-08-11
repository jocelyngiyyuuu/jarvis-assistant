"""Cross-platform system health snapshots using the Python standard library."""

import os
import shutil
import time
from pathlib import Path


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


class SystemMonitor:
    def __init__(self, disk_path=None):
        self.disk_path = Path(disk_path or Path.home())

    def snapshot(self):
        memory = _read_meminfo()
        total = memory.get("MemTotal", 0)
        available = memory.get("MemAvailable", 0)
        disk = shutil.disk_usage(self.disk_path)
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = (0.0, 0.0, 0.0)
        return {
            "load": load,
            "cpu_count": os.cpu_count() or 1,
            "memory_total": total,
            "memory_used": max(0, total - available),
            "disk_total": disk.total,
            "disk_used": disk.used,
            "uptime": _uptime_seconds(),
            "timestamp": time.time(),
        }

    @staticmethod
    def format(snapshot):
        gib = 1024 ** 3
        memory_total = snapshot["memory_total"] / gib
        memory_used = snapshot["memory_used"] / gib
        disk_total = snapshot["disk_total"] / gib
        disk_used = snapshot["disk_used"] / gib
        uptime = snapshot["uptime"]
        uptime_text = "không xác định" if uptime is None else f"{uptime / 3600:.1f} giờ"
        load = snapshot["load"]
        return (
            "🖥️ **TÌNH TRẠNG HỆ THỐNG**\n"
            f"• CPU: {snapshot['cpu_count']} luồng, tải 1/5/15 phút: "
            f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}\n"
            f"• RAM: {memory_used:.1f}/{memory_total:.1f} GiB\n"
            f"• Ổ đĩa: {disk_used:.1f}/{disk_total:.1f} GiB\n"
            f"• Uptime: {uptime_text}"
        )
