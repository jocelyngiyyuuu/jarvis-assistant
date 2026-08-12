import unittest
from datetime import date

from jarvis import parse_zalo_request


class ZaloCommandTests(unittest.TestCase):
    def test_specific_date(self):
        selected, group, question = parse_zalo_request("tóm tắt zalo ngày 10/08/2026")
        self.assertEqual(selected, date(2026, 8, 10))
        self.assertIsNone(group)
        self.assertIsNone(question)

    def test_group_date_and_question(self):
        selected, group, question = parse_zalo_request(
            "hỏi zalo nhóm PRF193 hôm nay về khảo sát là gì"
        )
        self.assertIsNotNone(selected)
        self.assertEqual(group, "prf193")
        self.assertEqual(question, "khao sat la gi")


if __name__ == "__main__":
    unittest.main()
