"""Small Ollama client used as Jarvis' local conversational fallback."""

import json
import os
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
- open_site: args gồm site (chatgpt, gmail, drive, calendar, github, google, chrome)
  và profile (study hoặc personal).
- close_chrome: args gồm profile (study hoặc personal).
- open_vscode, close_vscode, show_desktop, system_status: site và profile là chuỗi rỗng.
- chat: khi người dùng hỏi kiến thức, trò chuyện, hoặc yêu cầu không nằm trong danh sách;
  site và profile là chuỗi rỗng, reply chứa câu trả lời tiếng Việt ngắn gọn.
Không được tạo action shell, xóa file, tắt máy, sleep, hay hành động ngoài danh sách.
Ví dụ: "mở ChatGPT để học" ->
{"action":"open_site","args":{"site":"chatgpt","profile":"study"},"reply":""}
"đóng trình duyệt học giúp tôi" ->
{"action":"close_chrome","args":{"site":"","profile":"study"},"reply":""}
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
                    "enum": ["chatgpt", "gmail", "drive", "calendar", "github", "google", "chrome", ""],
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
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout = timeout
        self.history = deque(maxlen=history_size)

    def chat(self, message):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": str(message).strip()})
        payload = json.dumps(
            {"model": self.model, "messages": messages, "stream": False},
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

        answer = str(result.get("message", {}).get("content", "")).strip()
        if not answer:
            raise RuntimeError("Ollama không trả về nội dung.")

        self.history.append({"role": "user", "content": str(message).strip()})
        self.history.append({"role": "assistant", "content": answer})
        return answer

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
                "options": {"temperature": 0, "num_predict": 300},
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
            args.get("site") not in {"chatgpt", "gmail", "drive", "calendar", "github", "google", "chrome"}
            or args.get("profile") not in {"study", "personal"}
        ):
            action = "chat"
        if action == "close_chrome" and args.get("profile") not in {"study", "personal"}:
            action = "chat"
        return {"action": action, "args": args, "reply": reply}
