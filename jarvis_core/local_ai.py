"""Small Ollama client used as Jarvis' local conversational fallback."""

import base64
import json
import os
import re
import subprocess
from collections import deque
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SYSTEM_PROMPT = """Bạn là Jarvis, trợ lý AI local trên Ubuntu.
Hãy trả lời bằng tiếng Việt, rõ ràng và ngắn gọn.
Chỉ dùng tiếng Việt và thuật ngữ tiếng Anh khi cần; không chèn chữ Trung Quốc.
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
        default_model = os.getenv("OLLAMA_MODEL", "qwen3-vl:4b")
        if model is not None:
            # Preserve the existing explicit-constructor behavior used by
            # tests and callers that intentionally select one model.
            self.model = model
            self.vision_model = model
        else:
            self.model = os.getenv("OLLAMA_TEXT_MODEL", default_model)
            self.vision_model = os.getenv("OLLAMA_VISION_MODEL", default_model)
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip() or "30m"
        self.keep_alive = int(keep_alive) if re.fullmatch(r"-?\d+", keep_alive) else keep_alive
        self.vision_keep_alive = (
            os.getenv("OLLAMA_VISION_KEEP_ALIVE", "10m").strip() or "10m"
        )
        self.timeout = timeout
        self.history = deque(maxlen=history_size)

    def warmup(self):
        """Load the text model into Ollama without generating a response."""
        payload = json.dumps({
            "model": self.model,
            "prompt": "",
            "stream": False,
            "keep_alive": self.keep_alive,
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=min(self.timeout, 60)) as response:
                json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError,
                json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể nạp trước Ollama: {error}") from error
        return True

    @staticmethod
    def _strip_thinking(content):
        """Ẩn reasoning mà một số template Qwen3 vẫn trả trong content."""
        content = str(content or "").strip()
        content = re.sub(r"^.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE)
        return content.strip()

    @classmethod
    def _structured_content(cls, result):
        """Read schema JSON from either Ollama message field used by Qwen3-VL."""
        message = result.get("message", {}) if isinstance(result, dict) else {}
        content = cls._strip_thinking(message.get("content", ""))
        if not content:
            # Ollama/Qwen3-VL can place schema-constrained JSON in `thinking`
            # while leaving `content` empty even when think=false.
            content = str(message.get("thinking", "")).strip()
        return content

    def chat(self, message):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": str(message).strip()})
        payload = json.dumps(
            {
                "model": self.model,
                "keep_alive": self.keep_alive,
                "messages": messages,
                "think": False,
                "options": {
                    "num_ctx": 4096,
                    "num_predict": 320,
                    "temperature": 0.2,
                },
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

    @staticmethod
    def _is_direct_conversation(message):
        """Identify clear conversation so it needs only one Ollama call."""
        normalized = " ".join(re.sub(
            r"[^0-9a-zà-ỹđ?]+", " ", str(message).casefold()
        ).split())
        status_subject = any(term in normalized for term in (
            "hệ thống", "he thong", "cpu", "ram", "ổ đĩa", "o dia",
            "uptime", "máy tính", "may tinh",
        ))
        status_intent = any(term in normalized for term in (
            "tình trạng", "tinh trang", "trạng thái", "trang thai",
            "kiểm tra", "kiem tra", "đang dùng", "dang dung",
            "còn bao nhiêu", "con bao nhieu",
        ))
        if status_subject and status_intent:
            return False
        conversational_markers = (
            "là gì", "la gi", "tại sao", "tai sao", "vì sao", "vi sao",
            "như thế nào", "nhu the nao", "giải thích", "giai thich",
            "cho tôi biết", "cho toi biet", "nghĩa là gì", "nghia la gi",
            "xin chào", "xin chao", "chào jarvis", "chao jarvis",
            "kể cho tôi", "ke cho toi",
        )
        return normalized.endswith("?") or any(
            marker in normalized for marker in conversational_markers
        )

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
                "keep_alive": self.keep_alive,
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
        raw_answer = self._structured_content(result)
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
            "keep_alive": self.keep_alive,
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
            data = json.loads(self._structured_content(result))
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

    def summarize_gmail(self, messages, *, meaningful_only=False, folder="inbox"):
        """Summarize Gmail inbox previews locally without adding them to history."""
        source = json.dumps(messages, ensure_ascii=False)
        schema = {
            "type": "object",
            "properties": {
                "overview": {"type": "string"},
                "emails": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sender": {"type": "string"},
                            "subject": {"type": "string"},
                            "summary": {"type": "string"},
                            "action": {"type": "string"},
                            "urgent": {"type": "boolean"},
                            "meaningful": {"type": "boolean"},
                        },
                        "required": ["sender", "subject", "summary", "action", "urgent", "meaningful"],
                    },
                },
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "source": {"type": "string"},
                            "deadline": {"type": "string"},
                        },
                        "required": ["task", "source", "deadline"],
                    },
                },
            },
            "required": ["overview", "emails", "tasks"],
        }
        prompt = f"""/no_think
Tóm tắt các dòng thư mục {folder} của Gmail bằng tiếng Việt. Chỉ dựa vào người gửi, tiêu đề và
đoạn xem trước; không suy đoán phần nội dung chưa được cung cấp. Action để chuỗi rỗng
nếu không thấy việc cần làm. Đánh dấu urgent chỉ khi dữ liệu nói rõ tính khẩn cấp.
Đánh dấu meaningful=true cho thư có giá trị thực tế như bảo mật tài khoản, giao dịch,
công việc, học tập, hóa đơn, lịch hoặc việc cần làm. Quảng cáo, lừa đảo và nội dung
vô nghĩa phải là false. Chế độ chỉ lấy nội dung có ý nghĩa: {meaningful_only}.
Khi chế độ này là true, overview và emails chỉ được nhắc tới các thư meaningful=true;
nếu không có thì overview nói không tìm thấy nội dung có ý nghĩa và emails để trống.
Tạo tasks từ các yêu cầu hành động được nói rõ trong thư. Mỗi task phải có nguồn là
người gửi hoặc tiêu đề; deadline để trống nếu thư không nói rõ. Không tự suy đoán việc
cần làm hoặc thời hạn. Nếu không có việc cụ thể thì tasks để trống.
Dữ liệu: {source[:30000]}
"""
        payload = json.dumps({
            "model": self.model,
            "keep_alive": self.keep_alive,
            "messages": [
                {"role": "system", "content": "Bạn là trợ lý tóm tắt email riêng tư chạy local. Chỉ xuất JSON schema."},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 450, "num_ctx": 4096},
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=min(self.timeout, 45)) as response:
                result = json.loads(response.read().decode("utf-8"))
            data = json.loads(self._structured_content(result))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể tóm tắt Gmail bằng Ollama: {error}") from error
        emails = data.get("emails", [])
        if not isinstance(emails, list):
            raise RuntimeError("Ollama không trả về bản tóm tắt Gmail đúng định dạng.")
        lines = []
        overview = str(data.get("overview", "")).strip()
        if overview:
            lines.append(overview)
        for item in emails[:20]:
            if not isinstance(item, dict):
                continue
            if meaningful_only and not item.get("meaningful", False):
                continue
            sender = str(item.get("sender", "Không rõ người gửi")).strip()
            subject = str(item.get("subject", "Không có tiêu đề")).strip()
            summary = str(item.get("summary", "")).strip()
            action = str(item.get("action", "")).strip()
            icon = "🔴" if item.get("urgent") else "•"
            block = f"{icon} **{sender}** — {subject}"
            if summary:
                block += f"\n  {summary}"
            if action:
                block += f"\n  Việc cần làm: {action}"
            lines.append(block)
        tasks = data.get("tasks", [])
        valid_tasks = []
        if isinstance(tasks, list):
            for item in tasks[:8]:
                if not isinstance(item, dict):
                    continue
                task = str(item.get("task", "")).strip()
                source_name = str(item.get("source", "")).strip()
                deadline = str(item.get("deadline", "")).strip()
                if not task:
                    continue
                detail = task
                if source_name:
                    detail += f" — Nguồn: {source_name}"
                if deadline:
                    detail += f" — Hạn: {deadline}"
                valid_tasks.append(detail)
        lines.append(
            "✅ **VIỆC CẦN LÀM**\n"
            + ("\n".join(f"• {task}" for task in valid_tasks)
               if valid_tasks else "• Chưa thấy yêu cầu hành động rõ ràng.")
        )
        if not lines:
            raise RuntimeError("Ollama không tìm thấy email hợp lệ để tóm tắt.")
        return "\n\n".join(lines)[:5500]

    def decide(self, message):
        """Return a validated safe action decision, or a conversational reply."""
        if self._is_direct_conversation(message):
            return {
                "action": "chat",
                "args": {"site": "", "profile": ""},
                "reply": self.chat(message),
            }
        payload = json.dumps(
            {
                "model": self.model,
                "keep_alive": self.keep_alive,
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
            decision = json.loads(self._structured_content(result))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể định tuyến bằng Ollama: {error}") from error

        action = str(decision.get("action", "chat")).strip().lower()
        if action not in ALLOWED_ACTIONS:
            action = "chat"
        args = decision.get("args")
        if not isinstance(args, dict):
            args = {}
        reply = str(decision.get("reply", "")).strip()
        normalized_message = " ".join(re.sub(
            r"[^0-9a-zà-ỹđ]+", " ", str(message).casefold()
        ).split())
        status_subject = any(term in normalized_message for term in (
            "hệ thống", "he thong", "cpu", "ram", "ổ đĩa", "o dia",
            "uptime", "máy tính", "may tinh",
        ))
        status_intent = any(term in normalized_message for term in (
            "tình trạng", "tinh trang", "trạng thái", "trang thai",
            "kiểm tra", "kiem tra", "đang dùng", "dang dung",
            "bao nhiêu", "bao nhieu", "còn bao nhiêu", "con bao nhieu",
        ))
        if action == "system_status" and not (status_subject and status_intent):
            action = "chat"
            reply = ""
        if action == "open_site" and args.get("site") in {
            "zalo", "gmail", "github",
        } and args.get("profile") not in {"study", "personal"}:
            args["profile"] = "study"
        if action == "open_site" and (
            args.get("site") not in {"chatgpt", "gmail", "drive", "calendar", "github", "google", "chrome", "zalo"}
            or args.get("profile") not in {"study", "personal"}
        ):
            action = "chat"
        if action == "close_chrome" and args.get("profile") not in {"study", "personal"}:
            action = "chat"
        if action == "chat" and not reply:
            # Never make a hidden second model request. Clear questions are
            # routed directly to chat above; ambiguous inputs get a concise
            # deterministic request for clarification.
            reply = "Tôi chưa hiểu rõ yêu cầu. Bạn hãy nói cụ thể hơn."
        return {"action": action, "args": args, "reply": reply}

    def choose_accessible_target(self, instruction, controls):
        """Choose one visible AT-SPI control without receiving screenshot pixels."""
        candidates = []
        for index, control in enumerate(controls[:120]):
            candidates.append({
                "index": index,
                "name": str(control.get("name", ""))[:160],
                "role": str(control.get("role", ""))[:60],
                "app": str(control.get("app", ""))[:80],
                "window": str(control.get("window", ""))[:120],
            })
        schema = {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": -1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["index", "confidence"],
        }
        prompt = f"""/no_think
Bạn chọn phần tử giao diện phù hợp với yêu cầu tiếng Việt.
Chỉ chọn dựa trên tên, vai trò, ứng dụng và cửa sổ trong danh sách; không suy đoán
phần tử không có trong dữ liệu. Nếu không có đúng mục hoặc có nhiều mục tương đương,
trả index=-1 và confidence=0. Chỉ dùng confidence >= 0.8 khi kết quả rõ ràng.
Yêu cầu: {str(instruction)[:300]}
Danh sách: {json.dumps(candidates, ensure_ascii=False)}
"""
        payload = json.dumps({
            "model": self.model,
            "keep_alive": self.vision_keep_alive,
            "messages": [
                {"role": "system", "content": "Bạn là bộ chọn phần tử UI an toàn. Chỉ xuất JSON schema."},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "think": False,
            "options": {"temperature": 0, "num_predict": 180, "num_ctx": 8192},
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=min(self.timeout, 45)) as response:
                result = json.loads(response.read().decode("utf-8"))
            decision = json.loads(self._structured_content(result))
            index = int(decision.get("index", -1))
            confidence = float(decision.get("confidence", 0))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể chọn nút bằng Qwen: {error}") from error
        if index < 0 or index >= len(controls) or confidence < 0.8:
            return None
        return controls[index]

    def _choose_visual_bbox(self, instruction, image, image_width, image_height,
                            *, refinement=False):
        """Ask Qwen VL for one normalized bounding box."""
        schema = {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "x1": {"type": "integer", "minimum": 0, "maximum": 1000},
                "y1": {"type": "integer", "minimum": 0, "maximum": 1000},
                "x2": {"type": "integer", "minimum": 0, "maximum": 1000},
                "y2": {"type": "integer", "minimum": 0, "maximum": 1000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "ambiguous": {"type": "boolean"},
            },
            "required": [
                "found", "x1", "y1", "x2", "y2", "confidence",
                "ambiguous",
            ],
        }
        stage = (
            "Đây là ảnh phóng to quanh một ứng viên đã tìm thấy. Hãy định vị lại "
            "thật sát vùng có thể bấm; không giữ tọa độ cũ nếu nó lệch."
            if refinement else
            "Đây là ảnh toàn màn hình. Hãy định vị ứng viên duy nhất trước."
        )
        prompt = f"""/no_think
{stage}
Kích thước ảnh hiện tại là {image_width}x{image_height} pixel.
Hãy tìm đúng một điều khiển giao diện phù hợp với yêu cầu tiếng Việt bên dưới.
Trả hộp bao CHẶT quanh toàn bộ vùng có thể bấm bằng (x1,y1,x2,y2) trong hệ
chuẩn hóa 0..1000 của Qwen3-VL: (0,0) là góc trên-trái và (1000,1000) là
góc dưới-phải. Không chỉ khoanh riêng một chữ nếu nút có nền lớn hơn.
Đọc kỹ nhãn, biểu tượng, vị trí tương đối và cửa sổ đang hoạt động. Phân biệt
các mục gần nhau hoặc có từ giống nhau. Nếu mục bị che, không hiện rõ, có nhiều
mục tương đương hoặc bạn không chắc chắn, trả found=false, ambiguous=true và
confidence=0; tuyệt đối không đoán.
Chỉ dùng confidence >= 0.9 khi mục tiêu nhìn thấy rõ và khớp chính xác.
Yêu cầu: {str(instruction)[:300]}
"""
        payload = json.dumps({
            "model": self.vision_model,
            "keep_alive": self.keep_alive,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image).decode("ascii")],
            }],
            "format": schema,
            "think": False,
            "options": {"temperature": 0, "num_predict": 256, "num_ctx": 2048},
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            decision = None
            for attempt in range(2):
                with urlopen(request, timeout=min(self.timeout, 90)) as response:
                    result = json.loads(response.read().decode("utf-8"))
                try:
                    decision = json.loads(self._structured_content(result))
                    break
                except json.JSONDecodeError:
                    if attempt:
                        raise
            if decision is None:
                raise json.JSONDecodeError("empty structured response", "", 0)
            found = bool(decision.get("found", False))
            x1 = int(decision.get("x1", -1))
            y1 = int(decision.get("y1", -1))
            x2 = int(decision.get("x2", -1))
            y2 = int(decision.get("y2", -1))
            confidence = float(decision.get("confidence", 0))
            ambiguous = bool(decision.get("ambiguous", True))
        except RuntimeError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Không thể nhìn màn hình bằng Qwen VL: {error}") from error
        box_area = max(0, x2 - x1) * max(0, y2 - y1)
        if (not found or ambiguous or confidence < 0.9
                or not 0 <= x1 < x2 <= 1000
                or not 0 <= y1 < y2 <= 1000
                or box_area < 4 or box_area > 300000):
            return None
        return {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "confidence": confidence,
        }

    @staticmethod
    def _map_visual_bbox(box, left, top, width, height):
        return [
            left + round(box["x1"] * width / 1000),
            top + round(box["y1"] * height / 1000),
            max(2, round((box["x2"] - box["x1"]) * width / 1000)),
            max(2, round((box["y2"] - box["y1"]) * height / 1000)),
        ]

    def choose_visual_target(self, instruction, image_path):
        """Locate one UI target, then refine it inside a magnified crop."""
        try:
            image = Path(image_path).read_bytes()
        except OSError as error:
            raise RuntimeError(f"Không đọc được ảnh màn hình: {error}") from error
        if len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Ảnh màn hình không phải PNG hợp lệ.")
        width = int.from_bytes(image[16:20], "big")
        height = int.from_bytes(image[20:24], "big")
        if width < 1 or height < 1 or len(image) > 20 * 1024 * 1024:
            raise RuntimeError("Kích thước ảnh màn hình không hợp lệ.")

        # First pass: retain enough detail for small toolbar icons while still
        # fitting Qwen3-VL alongside a 4 GB GPU.
        vision_image = image
        vision_width, vision_height = width, height
        if width > 1280 or height > 720:
            try:
                resized = subprocess.run(
                    ["convert", str(image_path), "-resize", "1280x720>", "png:-"],
                    capture_output=True, check=False, timeout=15,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                resized = None
            if (resized is not None and resized.returncode == 0
                    and len(resized.stdout) >= 24
                    and resized.stdout[:8] == b"\x89PNG\r\n\x1a\n"):
                vision_image = resized.stdout
                vision_width = int.from_bytes(vision_image[16:20], "big")
                vision_height = int.from_bytes(vision_image[20:24], "big")

        selected = self._choose_visual_bbox(
            instruction, vision_image, vision_width, vision_height
        )
        if selected is None:
            return None
        screen_bounds = self._map_visual_bbox(selected, 0, 0, width, height)

        # Second pass: enlarge only the nearby region. This corrects the
        # 10-30 px drift common when a 4B model grounds tiny full-screen UI.
        box_left, box_top, box_width, box_height = screen_bounds
        pad_x = max(80, min(180, box_width))
        pad_y = max(60, min(140, box_height * 2))
        crop_left = max(0, box_left - pad_x)
        crop_top = max(0, box_top - pad_y)
        crop_right = min(width, box_left + box_width + pad_x)
        crop_bottom = min(height, box_top + box_height + pad_y)
        crop_width = crop_right - crop_left
        crop_height = crop_bottom - crop_top
        if width >= 1000 and crop_width >= 40 and crop_height >= 40:
            try:
                cropped = subprocess.run(
                    [
                        "convert", str(image_path), "-crop",
                        f"{crop_width}x{crop_height}+{crop_left}+{crop_top}",
                        "+repage", "-resize", "960x720>", "png:-",
                    ],
                    capture_output=True, check=False, timeout=15,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                cropped = None
            if (cropped is not None and cropped.returncode == 0
                    and len(cropped.stdout) >= 24
                    and cropped.stdout[:8] == b"\x89PNG\r\n\x1a\n"):
                crop_image_width = int.from_bytes(cropped.stdout[16:20], "big")
                crop_image_height = int.from_bytes(cropped.stdout[20:24], "big")
                refined = self._choose_visual_bbox(
                    instruction, cropped.stdout, crop_image_width,
                    crop_image_height, refinement=True,
                )
                if refined is not None:
                    screen_bounds = self._map_visual_bbox(
                        refined, crop_left, crop_top, crop_width, crop_height
                    )
                    selected = refined

        screen_x = min(width - 1, screen_bounds[0] + screen_bounds[2] // 2)
        screen_y = min(height - 1, screen_bounds[1] + screen_bounds[3] // 2)
        return {
            "x": screen_x, "y": screen_y,
            "confidence": selected["confidence"],
            "bounds": screen_bounds,
            "width": width, "height": height,
        }
