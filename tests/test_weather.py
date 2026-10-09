import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote_plus

import jarvis


class WeatherCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_weather_period_variants(self):
        cases = {
            "thời tiết hôm nay thế nào": "hôm nay",
            "hôm nay thời tiết thế nào": "hôm nay",
            "thời tiết tuần này thế nào": "tuần này",
            "tuần này thời tiết thế nào": "tuần này",
            "thời tiết 1 tuần nữa như thế nào": "một tuần nữa",
            "thời tiết một tuần nữa như thế nào": "một tuần nữa",
            "thời tiết một tuần thế nào": "một tuần",
            "thời tiết 7 ngày tới": "một tuần",
            "thời tiết tuần tới thế nào": "tuần tới",
        }
        for command, expected_period in cases.items():
            with self.subTest(command=command):
                _query, period = jarvis.weather_period_from_command(command)
                self.assertEqual(period, expected_period)

    async def test_weather_command_opens_google_and_reads_card(self):
        async def immediate(func, *args, **kwargs):
            return func(*args, **kwargs)

        weather = {"days": [
            {
                "temperature": str(29 + index % 3),
                "precipitation": f"{10 + index * 5}%",
                "condition": "Mưa nhỏ" if index else "Có mây",
                "selectedIndex": str(index),
                "dayLabel": "Chủ Nhật" if index in {0, 7} else f"Thứ {index + 1}",
                "high": str(33 - index // 3),
                "low": str(28 - index // 4),
            }
            for index in range(8)
        ]}
        with patch.object(
            jarvis, "open_chrome", return_value=True
        ) as opened, patch.object(
            jarvis, "read_google_weather_card", new=AsyncMock(return_value=weather)
        ), patch.object(
            jarvis.asyncio, "to_thread", side_effect=immediate
        ):
            handled = await jarvis.route_command(
                "thời tiết một tuần nữa thế nào", allow_local_ai=False
            )

        self.assertTrue(handled)
        profile, url = opened.call_args.args
        self.assertEqual(profile, jarvis.JARVIS_CHROME_PROFILE)
        self.assertIn("google.com/search", url)
        self.assertIn("mot tuan nua", jarvis.normalize_core_text(unquote_plus(url)))
        self.assertIn("Dự báo 8 ngày", jarvis.last_command_response)
        self.assertIn("Hôm nay, Chủ Nhật", jarvis.last_command_response)
        self.assertIn("Chủ Nhật tuần sau", jarvis.last_command_response)
        self.assertIn("45 phần trăm", jarvis.last_spoken_response)
        self.assertIn("Tóm tắt tuần", jarvis.last_spoken_response)
        self.assertIn("trung bình mỗi ngày", jarvis.last_spoken_response)
        self.assertIn("cao nhất 45 phần trăm", jarvis.last_spoken_response)

    def test_weather_report_states_rain_and_probability(self):
        report = jarvis.format_google_weather_report({
            "temperature": "24",
            "precipitation": "80%",
            "condition": "Mưa rào",
            "location": "Quy Nhơn, Gia Lai",
        }, "hôm nay")
        self.assertIn("24 độ C", report)
        self.assertIn("hiện có mưa", report)
        self.assertIn("80 phần trăm", report)

    def test_weather_report_rejects_missing_temperature(self):
        self.assertIsNone(jarvis.format_google_weather_report({
            "temperature": "",
            "precipitation": "10%",
        }, "hôm nay"))

    def test_weather_report_omits_google_widget_label_as_location(self):
        report = jarvis.format_google_weather_report({
            "temperature": "29",
            "precipitation": "10%",
            "condition": "Nhiều mây",
            "location": "Thời tiết",
        }, "hôm nay")
        self.assertNotIn("tại Thời tiết", report)
        self.assertIn("29 độ C", report)

    def test_week_ahead_report_validates_each_forecast_day(self):
        data = {"days": [
            {
                "temperature": "31",
                "precipitation": f"{index * 10}%",
                "condition": "Mưa nhỏ",
                "selectedIndex": str(index),
                "dayLabel": "Chủ Nhật" if index in {0, 7} else f"Thứ {index + 1}",
                "high": "31",
                "low": "27",
            }
            for index in range(8)
        ]}
        report = jarvis.format_google_weather_report(data, "một tuần nữa")
        self.assertIn("Dự báo 8 ngày", report)
        self.assertIn("Hôm nay, Chủ Nhật", report)
        self.assertIn("Chủ Nhật tuần sau", report)
        self.assertIn("từ 27 đến 31 độ C", report)
        self.assertIn("70 phần trăm", report)
        self.assertIn("Tóm tắt tuần", report)
        self.assertIn("có 3 ngày khả năng mưa cao", report)
        data["days"][6]["selectedIndex"] = "5"
        self.assertIsNone(
            jarvis.format_google_weather_report(data, "một tuần nữa")
        )

    def test_week_report_accepts_seven_days_and_summarizes_dry_sunny_week(self):
        data = {"days": [
            {
                "precipitation": "0%",
                "condition": "Nắng" if index < 5 else "Có mây",
                "selectedIndex": str(index),
                "dayLabel": f"Ngày {index + 1}",
                "high": "31",
                "low": "25",
            }
            for index in range(7)
        ]}

        report = jarvis.format_google_weather_report(data, "tuần tới")

        self.assertIn("Dự báo 7 ngày", report)
        self.assertIn("trung bình mỗi ngày 0 phần trăm", report)
        self.assertIn("cả tuần không mưa", report)
        self.assertIn("chủ yếu có nắng hoặc trời quang", report)

    async def test_non_weather_conversation_does_not_open_weather(self):
        with patch.object(jarvis, "open_chrome") as opened:
            handled = await jarvis.route_command(
                "tuần tới thế nào", allow_local_ai=False
            )

        self.assertTrue(handled)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
