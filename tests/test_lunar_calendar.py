import unittest
from datetime import date

from jarvis_core.lunar_calendar import (
    LunarDate,
    find_lunar_day,
    lunar_to_solar,
    solar_to_lunar,
)


class LunarCalendarTests(unittest.TestCase):
    def test_known_tet_dates(self):
        cases = {
            date(2024, 2, 10): LunarDate(1, 1, 2024),
            date(2025, 1, 29): LunarDate(1, 1, 2025),
            date(2026, 2, 17): LunarDate(1, 1, 2026),
        }
        for solar, lunar in cases.items():
            with self.subTest(solar=solar):
                self.assertEqual(solar_to_lunar(solar), lunar)
                self.assertEqual(lunar_to_solar(lunar), solar)

    def test_round_trip_across_regular_and_leap_months(self):
        for solar in (
            date(2023, 3, 22),
            date(2024, 9, 17),
            date(2026, 8, 26),
            date(2030, 1, 1),
        ):
            with self.subTest(solar=solar):
                self.assertEqual(lunar_to_solar(solar_to_lunar(solar)), solar)

    def test_finds_upcoming_full_moon(self):
        start = date(2026, 8, 26)
        result = find_lunar_day(start, 15)

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, start)
        self.assertEqual(solar_to_lunar(result).day, 15)

    def test_rejects_invalid_lunar_date(self):
        self.assertIsNone(lunar_to_solar(LunarDate(31, 1, 2026)))
        self.assertIsNone(lunar_to_solar(LunarDate(1, 13, 2026)))


if __name__ == "__main__":
    unittest.main()
