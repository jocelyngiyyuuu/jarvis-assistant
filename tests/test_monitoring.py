import tempfile
import unittest
from pathlib import Path

from jarvis_core.monitoring import SystemMonitor, _read_temperatures


class SystemTemperatureTests(unittest.TestCase):
    @staticmethod
    def _write(path, value):
        path.write_text(str(value), encoding="utf-8")

    def test_reads_only_cpu_and_gpu_temperatures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cpu = root / "hwmon0"
            cpu.mkdir()
            self._write(cpu / "name", "k10temp\n")
            self._write(cpu / "temp1_input", "47125\n")

            gpu = root / "hwmon1"
            gpu.mkdir()
            self._write(gpu / "name", "amdgpu\n")
            self._write(gpu / "temp1_input", "42900\n")

            ignored_ssd = root / "hwmon2"
            ignored_ssd.mkdir()
            self._write(ignored_ssd / "name", "nvme\n")
            self._write(ignored_ssd / "temp1_input", "25900\n")

            self.assertEqual(
                _read_temperatures(root),
                [("CPU", 47.125), ("GPU", 42.9)],
            )

    def test_ignores_invalid_or_impossible_sensor_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "hwmon0"
            invalid.mkdir()
            self._write(invalid / "name", "nvme")
            self._write(invalid / "temp1_input", "not-a-number")
            self._write(invalid / "temp2_input", "65261800")

            self.assertEqual(_read_temperatures(root), [])

    def test_formats_temperature_and_missing_sensor_fallback(self):
        snapshot = {
            "load": (0.1, 0.2, 0.3),
            "cpu_count": 8,
            "memory_total": 8 * 1024**3,
            "memory_used": 4 * 1024**3,
            "disk_total": 100 * 1024**3,
            "disk_used": 25 * 1024**3,
            "uptime": 7200,
            "temperatures": [("CPU", 47.125), ("GPU", 43.0)],
        }
        report = SystemMonitor.format(snapshot)
        self.assertIn("Nhiệt độ: CPU 47.1°C, GPU 43.0°C", report)

        snapshot["temperatures"] = []
        self.assertIn("Nhiệt độ: không có cảm biến", SystemMonitor.format(snapshot))

    def test_formats_multiple_disks_in_system_status(self):
        gib = 1024**3
        snapshot = {
            "load": (0.1, 0.2, 0.3),
            "cpu_count": 8,
            "memory_total": 8 * gib,
            "memory_used": 4 * gib,
            "disk_total": 100 * gib,
            "disk_used": 25 * gib,
            "disks": [
                {"mount": "/", "total": 100 * gib, "used": 25 * gib},
                {
                    "mount": "/run/media/khoa/Projects",
                    "total": 272 * gib,
                    "used": 11 * gib,
                },
            ],
            "uptime": 7200,
            "temperatures": [],
        }

        report = SystemMonitor.format(snapshot)

        self.assertIn("Ổ hệ thống: 25.0/100.0 GiB", report)
        self.assertIn(
            "Ổ dự án: 11.0/272.0 GiB", report
        )
        self.assertNotIn("/run/media/khoa/Projects", report)


if __name__ == "__main__":
    unittest.main()
