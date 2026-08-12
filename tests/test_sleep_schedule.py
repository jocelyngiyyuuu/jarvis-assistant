import unittest
from datetime import datetime, timezone

import jarvis


class SleepClockTests(unittest.TestCase):
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
