import unittest

from jarvis_core.local_ai import LocalAI


class LocalAITests(unittest.TestCase):
    def test_qwen_reasoning_is_hidden(self):
        content = "Phân tích nội bộ\n</think>\n\nĐây là câu trả lời."
        self.assertEqual(LocalAI._strip_thinking(content), "Đây là câu trả lời.")

    def test_normal_answer_is_unchanged(self):
        self.assertEqual(LocalAI._strip_thinking("Xin chào."), "Xin chào.")


if __name__ == "__main__":
    unittest.main()
