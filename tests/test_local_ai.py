import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from jarvis_core.local_ai import LocalAI


class LocalAITests(unittest.TestCase):
    @staticmethod
    def fake_ollama_response(result):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(result).encode("utf-8")
        return FakeResponse()

    def test_text_and_vision_models_can_be_configured_separately(self):
        with patch.dict(os.environ, {
            "OLLAMA_MODEL": "fallback",
            "OLLAMA_TEXT_MODEL": "fast-text",
            "OLLAMA_VISION_MODEL": "vision",
            "OLLAMA_KEEP_ALIVE": "20m",
            "OLLAMA_VISION_KEEP_ALIVE": "5m",
        }):
            client = LocalAI()
        self.assertEqual(client.model, "fast-text")
        self.assertEqual(client.vision_model, "vision")
        self.assertEqual(client.keep_alive, "20m")
        self.assertEqual(client.vision_keep_alive, "5m")

    def test_explicit_model_is_used_for_text_and_vision(self):
        client = LocalAI(model="explicit")
        self.assertEqual(client.model, "explicit")
        self.assertEqual(client.vision_model, "explicit")

    def test_numeric_keep_alive_is_sent_as_an_integer(self):
        with patch.dict(os.environ, {"OLLAMA_KEEP_ALIVE": "-1"}):
            client = LocalAI(model="test")
        self.assertEqual(client.keep_alive, -1)

    def test_zalo_router_fills_safe_default_profile(self):
        client = LocalAI(model="test")
        result = {"message": {"content": json.dumps({
            "action": "open_site",
            "args": {"site": "zalo", "profile": ""},
            "reply": "",
        })}}
        with patch(
            "jarvis_core.local_ai.urlopen",
            return_value=self.fake_ollama_response(result),
        ):
            decision = client.decide("mở zalo")
        self.assertEqual(decision["action"], "open_site")
        self.assertEqual(decision["args"]["profile"], "study")

    def test_knowledge_question_is_not_misrouted_to_system_status(self):
        chat_result = {"message": {"content": "RAM là bộ nhớ tạm thời."}}
        client = LocalAI(model="test")
        with patch(
            "jarvis_core.local_ai.urlopen",
            return_value=self.fake_ollama_response(chat_result),
        ) as opened:
            decision = client.decide("RAM là gì?")
        self.assertEqual(decision["action"], "chat")
        self.assertEqual(decision["reply"], "RAM là bộ nhớ tạm thời.")
        self.assertEqual(opened.call_count, 1)

    def test_explicit_system_status_still_uses_safe_router(self):
        result = {"message": {"content": json.dumps({
            "action": "system_status",
            "args": {"site": "", "profile": ""},
            "reply": "",
        })}}
        client = LocalAI(model="test")
        with patch(
            "jarvis_core.local_ai.urlopen",
            return_value=self.fake_ollama_response(result),
        ) as opened:
            decision = client.decide("kiểm tra RAM đang dùng bao nhiêu")
        self.assertEqual(decision["action"], "system_status")
        self.assertEqual(opened.call_count, 1)

    def test_qwen_reasoning_is_hidden(self):
        content = "Phân tích nội bộ\n</think>\n\nĐây là câu trả lời."
        self.assertEqual(LocalAI._strip_thinking(content), "Đây là câu trả lời.")

    def test_normal_answer_is_unchanged(self):
        self.assertEqual(LocalAI._strip_thinking("Xin chào."), "Xin chào.")

    def test_chat_allows_a_320_token_answer(self):
        client = LocalAI(model="test")
        result = {"message": {"content": "Câu trả lời."}}
        with patch(
            "jarvis_core.local_ai.urlopen",
            return_value=self.fake_ollama_response(result),
        ) as opened:
            client.chat("Hãy phân tích vấn đề này")

        payload = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["options"]["num_predict"], 320)

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

    def test_gmail_summary_formats_local_structured_result(self):
        client = LocalAI(model="test")
        ollama_result = {"message": {"content": json.dumps({
            "overview": "Có một thư cần chú ý.",
            "emails": [{
                "sender": "Trường học",
                "subject": "Lịch thi",
                "summary": "Thông báo lịch thi mới.",
                "action": "Xem lịch trước thứ Hai.",
                "urgent": True,
            }],
            "tasks": [{
                "task": "Xem lịch thi",
                "source": "Lịch thi",
                "deadline": "thứ Hai",
            }],
        }, ensure_ascii=False)}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
            result = client.summarize_gmail([{
                "sender": "Trường học", "subject": "Lịch thi",
                "snippet": "Xem lịch trước thứ Hai", "unread": True,
            }])
        self.assertIn("Có một thư cần chú ý", result)
        self.assertIn("Việc cần làm", result)
        self.assertIn("Xem lịch thi", result)
        self.assertIn("Hạn: thứ Hai", result)

    def test_qwen_selects_accessible_control_by_index(self):
        client = LocalAI(model="test")
        ollama_result = {"message": {"content": json.dumps({
            "index": 1, "confidence": 0.96, "reason": "Tên nút khớp",
        })}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        controls = [
            {"id": "one", "name": "Hủy", "role": "button"},
            {"id": "two", "name": "Gửi", "role": "button"},
        ]
        with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
            selected = client.choose_accessible_target("nút gửi", controls)
        self.assertEqual(selected["id"], "two")

    def test_qwen_rejects_low_confidence_accessible_control(self):
        client = LocalAI(model="test")
        ollama_result = {"message": {"content": json.dumps({
            "index": 0, "confidence": 0.4, "reason": "Không rõ",
        })}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
            self.assertIsNone(client.choose_accessible_target(
                "gửi", [{"id": "one", "name": "Gửi", "role": "button"}]
            ))

    def test_qwen_selects_accessible_control_when_json_is_in_thinking(self):
        client = LocalAI(model="qwen3-vl:4b")
        ollama_result = {"message": {
            "content": "",
            "thinking": json.dumps({
                "index": 0, "confidence": 0.96, "reason": "Tên khớp",
            }),
        }}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        control = {"id": "spam", "name": "Thư rác", "role": "button"}
        with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
            self.assertEqual(
                client.choose_accessible_target("thư rác", [control]), control
            )

    def test_qwen_vl_locates_target_from_png(self):
        client = LocalAI(model="qwen3-vl:4b")
        ollama_result = {"message": {"content": json.dumps({
            "found": True, "x1": 400, "y1": 450, "x2": 600, "y2": 550,
            "confidence": 0.97, "ambiguous": False,
        })}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "screen.png"
            # PNG signature + IHDR length/type + 640x480 dimensions. The API
            # mock does not decode pixels, but this exercises payload wiring.
            image_path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
                + b"\x08\x06\x00\x00\x00"
            )
            with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()) as opened:
                target = client.choose_visual_target("nút gửi", image_path)

        self.assertEqual((target["x"], target["y"]), (320, 240))
        payload = json.loads(opened.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], "qwen3-vl:4b")
        self.assertTrue(payload["messages"][0]["images"][0])

    def test_qwen_vl_rejects_out_of_bounds_target(self):
        client = LocalAI(model="qwen3-vl:4b")
        ollama_result = {"message": {"content": json.dumps({
            "found": True, "x1": 900, "y1": 10, "x2": 1001, "y2": 40,
            "confidence": 0.99, "ambiguous": False,
        })}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(ollama_result).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "screen.png"
            image_path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
            )
            with patch("jarvis_core.local_ai.urlopen", return_value=FakeResponse()):
                self.assertIsNone(client.choose_visual_target("nút gửi", image_path))

    def test_qwen_vl_retries_one_truncated_json_response(self):
        client = LocalAI(model="qwen3-vl:4b")
        results = [
            {"message": {"content": '{"found":true,"x1":400,"y1":450'}},
            {"message": {"content": json.dumps({
                "found": True, "x1": 400, "y1": 450,
                "x2": 600, "y2": 550, "confidence": 0.97,
                "ambiguous": False,
            })}},
        ]

        class FakeResponse:
            def __init__(self, result): self.result = result
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(self.result).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "screen.png"
            image_path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
            )
            with patch(
                "jarvis_core.local_ai.urlopen",
                side_effect=[FakeResponse(result) for result in results],
            ) as opened:
                target = client.choose_visual_target("nút gửi", image_path)
        self.assertEqual((target["x"], target["y"]), (320, 240))
        self.assertEqual(opened.call_count, 2)


if __name__ == "__main__":
    unittest.main()
