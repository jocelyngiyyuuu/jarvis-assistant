import unittest
from datetime import datetime, timezone

import jarvis


class SleepClockTests(unittest.TestCase):
    def test_delay_accepts_multiple_duration_formats(self):
        cases = {
            "sleep sâu sau 1h30": 90 * 60,
            "sleep sâu sau 2h15": 2 * 3600 + 15 * 60,
            "sleep sâu sau 45p": 45 * 60,
            "sleep sâu sau 30m": 30 * 60,
            "sleep sâu sau 90 phút": 90 * 60,
            "sleep sâu sau 2 giờ 5 phút": 2 * 3600 + 5 * 60,
            "sleep sâu sau 1h20p15s": 3600 + 20 * 60 + 15,
            "sleep sâu sau 1:30": 90 * 60,
            "sleep sâu sau 0:45:30": 45 * 60 + 30,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(
                    jarvis.parse_deep_sleep_delay(command), expected
                )

    def test_delay_rejects_invalid_shorthand_minutes(self):
        for command in (
            "sleep sâu sau 1h90",
            "sleep sâu sau 1:60",
            "sleep sâu sau 1:30:99",
        ):
            with self.subTest(command=command):
                self.assertIsNone(jarvis.parse_deep_sleep_delay(command))

    def test_clock_later_today(self):
        now = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
        target, seconds = jarvis.parse_deep_sleep_clock("sleep sâu lúc 23:30", now)
        self.assertEqual((target.hour, target.minute), (23, 30))
        self.assertEqual(seconds, 3 * 3600 + 30 * 60)

    def test_clock_that_passed_moves_to_tomorrow(self):
        now = datetime(2026, 8, 12, 23, 45, tzinfo=timezone.utc)
        target, seconds = jarvis.parse_deep_sleep_clock("sleep sâu 23h30", now)
        self.assertEqual(target.date().isoformat(), "2026-08-13")
        self.assertEqual(seconds, 23 * 3600 + 45 * 60)

    def test_rejects_invalid_clock(self):
        self.assertIsNone(jarvis.parse_deep_sleep_clock("sleep sâu lúc 25:90"))


if __name__ == "__main__":
    unittest.main()
