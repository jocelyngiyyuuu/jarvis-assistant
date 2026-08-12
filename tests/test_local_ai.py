import json
import unittest
from unittest.mock import patch

from jarvis_core.local_ai import LocalAI


class LocalAITests(unittest.TestCase):
    def test_qwen_reasoning_is_hidden(self):
        content = "Phân tích nội bộ\n</think>\n\nĐây là câu trả lời."
        self.assertEqual(LocalAI._strip_thinking(content), "Đây là câu trả lời.")

    def test_normal_answer_is_unchanged(self):
        self.assertEqual(LocalAI._strip_thinking("Xin chào."), "Xin chào.")

    def test_zalo_summary_keeps_links_from_original_messages(self):
        client = LocalAI(model="test")
        ollama_result = {
            "message": {"content": json.dumps({
                "groups": [{
                    "name": "IC21B-PRF193",
                    "summary": "Có yêu cầu khảo sát.",
                    "tasks": ["Hoàn thành khảo sát"],
                }],
                "priorities": [],
            }, ensure_ascii=False)}
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(ollama_result).encode("utf-8")

        with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
            result = client.summarize_zalo_work([{
                "nhom": "IC21B-PRF193",
                "noi_dung": "Làm khảo sát: https://forms.gle/example123.",
            }])

        self.assertIn("Liên kết:", result)
        self.assertIn("https://forms.gle/example123", result)

    def test_zalo_question_is_grounded_and_formats_evidence(self):
        client = LocalAI(model="test")
        ollama_result = {"message": {"content": json.dumps({
            "found": True,
            "answer": "Nhóm yêu cầu hoàn thành khảo sát.",
            "evidence": ["Các bạn tham gia khảo sát nhé"],
        }, ensure_ascii=False)}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
            result = client.answer_zalo_question(
                [{"nhom": "PRF193", "noi_dung": "Các bạn tham gia khảo sát nhé"}],
                "khảo sát là gì",
            )
        self.assertIn("Nhóm yêu cầu", result)
        self.assertIn("Bằng chứng trong nhóm", result)


if __name__ == "__main__":
    unittest.main()
