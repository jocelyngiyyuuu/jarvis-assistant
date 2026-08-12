"""Small Ollama client used as Jarvis' local conversational fallback."""

import json
import os
import re
from collections import deque
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SYSTEM_PROMPT = """Bạn là Jarvis, trợ lý AI local trên Ubuntu.
Hãy trả lời bằng tiếng Việt, rõ ràng và ngắn gọn.
Không tuyên bố đã mở, đóng hoặc thay đổi máy tính: các thao tác đó do bộ lệnh
riêng của Jarvis xử lý. Nếu người dùng yêu cầu một thao tác mà bạn không thể
thực hiện, hãy nói rõ và gợi ý họ dùng lệnh Jarvis phù hợp.
"""

TOOL_ROUTER_PROMPT = """Bạn là bộ định tuyến an toàn cho Jarvis trên Ubuntu.
Chỉ trả về một JSON object, không markdown. Các action hợp lệ:
- open_site: args gồm site (chatgpt, gmail, drive, calendar, github, google, chrome, zalo)
  và profile (study hoặc personal).
- close_chrome: args gồm profile (study hoặc personal).
- open_vscode, close_vscode, show_desktop: site và profile là chuỗi rỗng.
- system_status: chỉ dùng khi người dùng hỏi rõ về tình trạng máy, CPU, RAM,
  ổ đĩa hoặc uptime; không dùng cho câu hỏi về chức năng của Jarvis.
- chat: khi người dùng hỏi kiến thức, trò chuyện, hoặc yêu cầu không nằm trong danh sách;
  site và profile là chuỗi rỗng, reply chứa câu trả lời tiếng Việt ngắn gọn.
Không được tạo action shell, xóa file, tắt máy, sleep, hay hành động ngoài danh sách.
Khi người dùng nói soạn/nhắn/gửi tin cho một người mà không nêu dịch vụ,
Jarvis mặc định dùng Zalo Web trong Chrome học; không hỏi chọn Gmail hay profile.
Lệnh mở/vào Zalo không nêu profile cũng mặc định là Zalo Web trong Chrome học.

Quy tắc cho lệnh sai hoặc chưa rõ:
- Nếu câu có lỗi chính tả, thiếu đối tượng/profile, vô nghĩa, hoặc chỉ gần giống một
  lệnh Jarvis, phải dùng action chat và hỏi lại ngắn gọn: "Có phải bạn muốn ...?".
- Trong reply, đưa ra tối đa 2 lệnh đúng trong dấu backtick để người dùng chọn.
- Không tự thực thi một hành động được suy đoán từ câu mơ hồ.
- Có thể gợi ý các lệnh phổ biến như `help`, `mở youtube học`, `mở chrome cá nhân`,
  `mở zalo công việc`,
  `tắt chrome học`, `mở vscode`, `hiện desktop`, `tình trạng hệ thống`,
  `sleep sâu sau 30p`, `hủy sleep sâu`, hoặc `lịch sleep sâu`.
- Câu hỏi về cách hủy Sleep phải gợi ý chính xác lệnh `hủy sleep sâu`, tuyệt đối
  không đổi thành system_status.

Ví dụ: "mở ChatGPT để học" ->
{"action":"open_site","args":{"site":"chatgpt","profile":"study"},"reply":""}
"đóng trình duyệt học giúp tôi" ->
{"action":"close_chrome","args":{"site":"","profile":"study"},"reply":""}
"mở youtub" ->
{"action":"chat","args":{"site":"","profile":""},"reply":"Có phải bạn muốn `mở youtube học` hoặc `mở youtube cá nhân`?"}
"tắt crôm" ->
{"action":"chat","args":{"site":"","profile":""},"reply":"Bạn muốn dùng `tắt chrome học` hay `tắt chrome cá nhân`?"}
"hãy xóa toàn bộ ổ đĩa" ->
{"action":"chat","args":{"site":"","profile":""},"reply":"Tôi không thể thực hiện yêu cầu nguy hiểm đó."}
"""

ALLOWED_ACTIONS = {
    "open_site", "close_chrome", "open_vscode", "close_vscode",
    "show_desktop", "system_status", "chat",
}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
        "args": {
            "type": "object",
            "properties": {
                "site": {
                    "type": "string",
                    "enum": ["chatgpt", "gmail", "drive", "calendar", "github", "google", "chrome", "zalo", ""],
                },
                "profile": {"type": "string", "enum": ["study", "personal", ""]},
            },
            "required": ["site", "profile"],
        },
        "reply": {"type": "string"},
    },
    "required": ["action", "args", "reply"],
}


class LocalAI:
    """Talk to Ollama without adding a third-party HTTP dependency."""

    def __init__(self, model=None, base_url=None, timeout=90, history_size=8):
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:4b")
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout = timeout
        self.history = deque(maxlen=history_size)

    @staticmethod
    def _strip_thinking(content):
        """Ẩn reasoning mà một số template Qwen3 vẫn trả trong content."""
        content = str(content or "").strip()
        content = re.sub(r"^.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
        return content.strip()

    def chat(self, message):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": str(message).strip()})
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "think": False,
                "options": {"num_ctx": 4096},
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể kết nối Ollama: {error}") from error

        answer = self._strip_thinking(result.get("message", {}).get("content", ""))
        if not answer:
            raise RuntimeError("Ollama không trả về nội dung.")

        self.history.append({"role": "user", "content": str(message).strip()})
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def summarize_zalo_work(self, conversations):
        """Summarize locally extracted Zalo work chats without adding to chat history."""
        source = json.dumps(conversations, ensure_ascii=False)
        def group_key(value):
            return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip().casefold()

        links_by_group = {}
        for conversation in conversations:
            name = str(conversation.get("nhom", "Nhóm không tên")).strip()
            content = str(conversation.get("noi_dung", ""))
            links = []
            for link in re.findall(r"https?://[^\s<>\]\[()]+", content):
                link = link.rstrip(".,;:!?\"'")
                if link and link not in links:
                    links.append(link)
            links_by_group[group_key(name)] = links[:8]
        prompt = f"""/no_think
Đọc dữ liệu tin nhắn Zalo và điền đúng cấu trúc JSON đã yêu cầu.
Tóm tắt ngắn bằng tiếng Việt. Việc cần làm là mảng chuỗi; nếu không có thì để [].
Chỉ ghi người phụ trách hoặc thời hạn khi tin nhắn nói rõ, tuyệt đối không suy đoán.
Dữ liệu:
{source[:30000]}
"""
        summary_schema = {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "summary": {"type": "string"},
                            "tasks": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "summary", "tasks"],
                    },
                },
                "priorities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["groups", "priorities"],
        }
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Bạn là trợ lý tổng hợp công việc. Chỉ điền dữ liệu vào JSON schema, không chép lại chỉ dẫn."},
                    {"role": "user", "content": prompt},
                ],
                "format": summary_schema,
                "think": False,
                "options": {"temperature": 0.1, "num_predict": 2200, "num_ctx": 16384},
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể tóm tắt Zalo bằng Ollama: {error}") from error
        raw_answer = self._strip_thinking(result.get("message", {}).get("content", ""))
        try:
            data = json.loads(raw_answer)
            groups = data.get("groups", [])
            priorities = data.get("priorities", [])
        except (json.JSONDecodeError, AttributeError):
            groups, priorities = [], []
        if not isinstance(groups, list) or not groups:
            raise RuntimeError("Ollama không trả về bản tóm tắt Zalo đúng định dạng.")
        sections = []
        for group in groups[:20]:
            if not isinstance(group, dict):
                continue
            name = str(group.get("name", "Nhóm không tên")).strip()
            summary = str(group.get("summary", "")).strip()
            tasks = group.get("tasks", [])
            if not summary:
                continue
            lines = [f"📌 {name}", f"Tóm tắt: {summary}", "Việc cần làm:"]
            valid_tasks = [str(task).strip() for task in tasks if str(task).strip()] if isinstance(tasks, list) else []
            lines.extend(f"• {task}" for task in valid_tasks)
            if not valid_tasks:
                lines.append("• Chưa thấy việc cụ thể.")
            links = links_by_group.get(group_key(name), [])
            if links:
                lines.append("Liên kết:")
                lines.extend(f"🔗 {link}" for link in links)
            sections.append("\n".join(lines))
        if not sections:
            raise RuntimeError("Ollama không tìm được nội dung nhóm hợp lệ.")
        valid_priorities = [str(item).strip() for item in priorities[:3] if str(item).strip()] if isinstance(priorities, list) else []
        if valid_priorities:
            sections.append("⭐ Ưu tiên chung\n" + "\n".join(f"• {item}" for item in valid_priorities))
        return "\n\n".join(sections)[:5500]

    def answer_zalo_question(self, conversations, question):
        """Answer a question using only the locally extracted Zalo messages."""
        source = json.dumps(conversations, ensure_ascii=False)
        schema = {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "answer": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["found", "answer", "evidence"],
        }
        prompt = f"""/no_think
Trả lời câu hỏi chỉ dựa trên dữ liệu Zalo bên dưới. Không dùng kiến thức ngoài và không suy đoán.
Nếu dữ liệu không đủ, đặt found=false và nói rõ không tìm thấy.
Câu hỏi: {question}
Dữ liệu: {source[:30000]}
"""
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Bạn trả lời có căn cứ từ tin nhắn Zalo. Chỉ xuất JSON schema."},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "think": False,
            "options": {"temperature": 0, "num_predict": 700, "num_ctx": 16384},
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            data = json.loads(self._strip_thinking(result.get("message", {}).get("content", "")))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể hỏi dữ liệu Zalo: {error}") from error
        answer = str(data.get("answer", "")).strip()
        evidence = data.get("evidence", [])
        lines = [answer or "Không tìm thấy thông tin phù hợp trong tin nhắn đã đọc."]
        if data.get("found") and isinstance(evidence, list):
            valid = [str(item).strip() for item in evidence[:4] if str(item).strip()]
            if valid:
                lines.extend(["", "Bằng chứng trong nhóm:", *(f"• {item}" for item in valid)])
        return "\n".join(lines)[:3500]

    def decide(self, message):
        """Return a validated safe action decision, or a conversational reply."""
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": TOOL_ROUTER_PROMPT},
                    {"role": "user", "content": str(message).strip()},
                ],
                "format": DECISION_SCHEMA,
                "think": False,
                "options": {"temperature": 0, "num_predict": 300, "num_ctx": 4096},
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "")
            decision = json.loads(content)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể định tuyến bằng Ollama: {error}") from error

        action = str(decision.get("action", "chat")).strip().lower()
        if action not in ALLOWED_ACTIONS:
            action = "chat"
        args = decision.get("args")
        if not isinstance(args, dict):
            args = {}
        reply = str(decision.get("reply", "")).strip()
        if action == "open_site" and (
            args.get("site") not in {"chatgpt", "gmail", "drive", "calendar", "github", "google", "chrome", "zalo"}
            or args.get("profile") not in {"study", "personal"}
        ):
            action = "chat"
        if action == "close_chrome" and args.get("profile") not in {"study", "personal"}:
            action = "chat"
        return {"action": action, "args": args, "reply": reply}
