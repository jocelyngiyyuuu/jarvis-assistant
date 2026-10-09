import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis

from jarvis_core.assistant_agent import ReadOnlyAgent
from jarvis_core.conversation import ConversationStore
from jarvis_core.semantic_memory import SemanticMemory


class ReadOnlyAgentTests(unittest.TestCase):
    def test_tool_result_is_returned_to_model_for_final_answer(self):
        core = Mock()
        core.local_ai.tool_chat.side_effect = [
            {"content": "", "tool_calls": [{"function": {
                "name": "search_memory", "arguments": {"query": "môn học"},
            }}]},
            {"content": "Bạn học Toán (#7)."},
        ]
        core.semantic_memory.search.return_value = [
            {"id": 7, "content": "Tôi đang học Toán", "category": "fact"}
        ]

        answer, trace = ReadOnlyAgent(core).answer(
            "Tôi học môn nào?", [{"role": "assistant", "content": "Tôi đã ghi nhớ."}]
        )

        self.assertEqual(answer, "Bạn học Toán (#7).")
        self.assertEqual(trace, ["search_memory"])
        second_messages = core.local_ai.tool_chat.call_args.args[0]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn('"id": 7', second_messages[-1]["content"])

    def test_model_cannot_call_unlisted_mutating_tool(self):
        core = Mock()
        core.local_ai.tool_chat.side_effect = [
            {"content": "", "tool_calls": [{"function": {
                "name": "run_command", "arguments": {"command": "rm -rf /"},
            }}]},
            {"content": "Tôi không thể chạy lệnh đó."},
        ]

        answer, trace = ReadOnlyAgent(core).answer("xóa file")

        self.assertIn("không thể", answer)
        self.assertEqual(trace, ["run_command"])
        self.assertIn("không được phép", core.local_ai.tool_chat.call_args.args[0][-1]["content"])

    def test_document_reader_rejects_secret_path_and_reads_markdown(self):
        core = Mock()
        with tempfile.TemporaryDirectory() as directory:
            core.base_dir = Path(directory)
            allowed = Path(directory) / "notes.md"
            allowed.write_text("Nội dung được phép", encoding="utf-8")
            secret = Path(directory) / ".config" / "secret.md"
            secret.parent.mkdir()
            secret.write_text("bí mật", encoding="utf-8")
            agent = ReadOnlyAgent(core)

            result = agent.execute_tool("read_text_file", {"path": str(allowed)})
            denied = agent.execute_tool("read_text_file", {"path": str(secret)})

        self.assertEqual(result["content"], "Nội dung được phép")
        self.assertIn("error", denied)

    def test_agent_can_find_then_read_document_in_two_tool_steps(self):
        core = Mock()
        with tempfile.TemporaryDirectory() as directory:
            core.base_dir = Path(directory)
            document = Path(directory) / "plan.md"
            document.write_text("Kế hoạch: học Toán", encoding="utf-8")
            core.files.search.return_value = [{"path": document}]
            core.local_ai.tool_chat.side_effect = [
                {"content": "", "tool_calls": [{"function": {
                    "name": "search_files", "arguments": {"query": "plan.md"},
                }}]},
                {"content": "", "tool_calls": [{"function": {
                    "name": "read_text_file", "arguments": {"path": str(document)},
                }}]},
                {"content": "Tài liệu ghi kế hoạch học Toán."},
            ]

            answer, trace = ReadOnlyAgent(core).answer("Kế hoạch trong file là gì?")

        self.assertEqual(trace, ["search_files", "read_text_file"])
        self.assertIn("học Toán", answer)
        final_messages = core.local_ai.tool_chat.call_args.args[0]
        self.assertIn("Kế hoạch: học Toán", final_messages[-1]["content"])

    def test_agent_retries_when_model_skips_required_local_data_tool(self):
        core = Mock()
        core.local_ai.tool_chat.side_effect = [
            {"content": "Tôi nghĩ bạn thích trà."},
            {"content": "", "tool_calls": [{"function": {
                "name": "search_memory", "arguments": {"query": "đồ uống ưa thích"},
            }}]},
            {"content": "Bộ nhớ ghi bạn thích cà phê (#4)."},
        ]
        core.semantic_memory.search.return_value = [
            {"id": 4, "content": "Tôi thích cà phê", "category": "preference"}
        ]

        answer, trace = ReadOnlyAgent(core).answer(
            "Tôi thích đồ uống nào theo những gì đã lưu?"
        )

        self.assertEqual(trace, ["search_memory"])
        self.assertIn("cà phê", answer)
        self.assertNotIn("trà", answer)

    def test_conversation_history_is_source_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "chat.sqlite3")
            store.add("gtk", "user", "Nội dung riêng GTK")
            store.add("discord", "user", "Nội dung riêng Discord")
            store.add("gtk", "assistant", "Đã hiểu")

            history = store.recent_for_source("gtk")

        self.assertEqual([item["content"] for item in history], [
            "Nội dung riêng GTK", "Đã hiểu"
        ])


class SemanticMemoryTests(unittest.TestCase):
    def test_embedding_search_ranks_relevant_memory_and_caches_vectors(self):
        memories = Mock()
        memories.active_for_search.return_value = [
            {"id": 1, "content": "Tôi học Toán", "category": "fact", "updated_at": "a"},
            {"id": 2, "content": "Tôi thích nhạc", "category": "preference", "updated_at": "b"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            search = SemanticMemory(
                memories, Path(directory) / "vectors.sqlite3", model="test"
            )
            search._embed = Mock(side_effect=[
                [[1.0, 0.0], [0.0, 1.0]], [[1.0, 0.0]], [[1.0, 0.0]],
            ])

            first = search.search("môn học")
            second = search.search("môn học")

        self.assertEqual([row["id"] for row in first], [1])
        self.assertEqual([row["id"] for row in second], [1])
        self.assertEqual(search._embed.call_count, 3)

    def test_embedding_failure_falls_back_to_keyword_search(self):
        memories = Mock()
        memories.active_for_search.return_value = [
            {"id": 1, "content": "Tôi học Toán", "category": "fact", "updated_at": "a"}
        ]
        with tempfile.TemporaryDirectory() as directory:
            search = SemanticMemory(
                memories, Path(directory) / "vectors.sqlite3", model="test"
            )
            search._embed = Mock(side_effect=OSError("offline"))
            result = search.search("học Toán")

        self.assertEqual([row["id"] for row in result], [1])


class AgentRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_read_only_request_does_not_confuse_profile_with_file(self):
        self.assertTrue(jarvis._is_read_only_ai_request("Tìm file báo cáo"))
        self.assertFalse(jarvis._is_read_only_ai_request("Tôi muốn Chrome profile học"))

    async def test_natural_file_command_ignores_location_filler(self):
        with patch.object(jarvis, "search_and_maybe_open_path", return_value=True) as search:
            handled = await jarvis.route_command("Tìm file báo cáo trong máy", source="gtk")

        self.assertTrue(handled)
        search.assert_called_once_with("báo cáo", item_type="file", open_when_unique=False)

    async def test_open_document_question_reaches_agent_before_suggestion(self):
        with patch.object(jarvis.CORE.agent, "answer", return_value=(
            "Tìm thấy báo cáo.", ["search_files"]
        )) as answer, patch.object(jarvis.CORE.local_ai, "decide") as decide:
            handled = await jarvis.route_command(
                "Có tài liệu hướng dẫn API ở đâu?", source="gtk"
            )

        self.assertTrue(handled)
        answer.assert_called_once()
        decide.assert_not_called()
        self.assertEqual(jarvis.last_command_response, "Tìm thấy báo cáo.")

    async def test_chat_fallback_uses_only_prior_history_from_same_source(self):
        current = "Giải thích thuật toán tham lam?"
        history = [
            {"role": "user", "content": "Câu hỏi trước"},
            {"role": "assistant", "content": "Câu trả lời trước"},
            {"role": "user", "content": current},
        ]
        with patch.object(jarvis.CORE.local_ai, "decide", return_value={
            "action": "chat", "args": {}, "reply": "",
        }), patch.object(
            jarvis.CORE.conversation, "recent_for_source", return_value=history
        ) as recent, patch.object(
            jarvis.CORE.agent, "answer", return_value=("Câu trả lời AI", [])
        ) as answer:
            handled = await jarvis.route_command(current, source="gtk")

        self.assertTrue(handled)
        self.assertEqual(jarvis.last_command_response, "Câu trả lời AI")
        recent.assert_called_once_with("gtk", limit=9)
        answer.assert_called_once_with(current.rstrip("?"), history[:-1])
