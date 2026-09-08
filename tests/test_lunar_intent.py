import unittest
from datetime import date
from unittest.mock import patch

import jarvis
from jarvis_core.lunar_intent import answer_lunar_calendar


class LunarIntentTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 26)

    def answer(self, command):
        return answer_lunar_calendar(command, today=self.today)

    def test_screenshot_queries_are_answered_without_ai(self):
        for command in ("rằm lịch âm", "rằm 15 lịch âm là ngày gì"):
            with self.subTest(command=command):
                answer = self.answer(command)
                self.assertIn("Rằm", answer)
                self.assertIn("27/08/2026", answer)

    def test_reports_today_lunar_date(self):
        answer = self.answer("hôm nay là ngày mấy âm lịch")

        self.assertIn("26/08/2026", answer)
        self.assertIn("ngày 14 tháng 7", answer)

    def test_converts_known_solar_date_to_lunar(self):
        answer = self.answer("17/02/2026 là ngày âm lịch nào")

        self.assertIn("ngày 1 tháng 1 năm 2026", answer)

    def test_converts_written_lunar_date_to_solar(self):
        answer = self.answer("ngày 1 tháng 1 năm 2026 âm lịch là ngày dương nào")

        self.assertIn("17/02/2026", answer)

    def test_unrelated_command_is_not_claimed(self):
        self.assertIsNone(self.answer("mở youtube"))


class LunarRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_lunar_query_routes_before_qwen(self):
        with patch.object(
            jarvis.CORE.local_ai, "decide",
            side_effect=AssertionError("Qwen must not calculate calendar dates"),
        ):
            handled = await jarvis.route_command("rằm lịch âm")

        self.assertTrue(handled)
        self.assertIn("Rằm", jarvis.last_command_response)


if __name__ == "__main__":
    unittest.main()
