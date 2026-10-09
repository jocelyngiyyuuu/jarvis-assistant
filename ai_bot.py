import os
import json
import asyncio
import heapq
import mimetypes
import re
from datetime import datetime
from pathlib import Path

import aiohttp
import discord


# ============================================================
# CONFIG
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

DISCORD_CHANNEL_ID = 1537845497526624347
DISCORD_OWNER_ID = int(
    os.getenv("DISCORD_OWNER_ID")
    or os.getenv("DISCORD_USER_ID")
    or "884068521015930910"
)

PROJECT_ROOT = (
    Path.home() / "Projects" / "jarvis"
).resolve()
READ_ROOT = Path.home().resolve()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:4b")

MAX_AGENT_TURNS = 15


# ============================================================
# VALIDATION
# ============================================================

def validate_config() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("Thiếu DISCORD_TOKEN.")


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(
    intents=intents
)


# ============================================================
# PROJECT SECURITY
# ============================================================

def safe_path(relative_path: str) -> Path:
    """
    Cho phép kiểm kê file chỉ đọc trong thư mục người dùng.

    Không đi theo symlink ra ngoài home và không cho AI truy cập các vùng hệ
    thống như /proc, /sys hay file của tài khoản khác.
    """

    relative_path = relative_path.strip()

    if relative_path in ("", "."):
        return READ_ROOT

    requested = Path(relative_path).expanduser()
    path = requested.resolve() if requested.is_absolute() else (READ_ROOT / requested).resolve()

    if (
        path != READ_ROOT
        and READ_ROOT not in path.parents
    ):
        raise PermissionError(
            f"Path nằm ngoài thư mục người dùng: {relative_path}"
        )

    return path


SENSITIVE_FILE_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
}

SENSITIVE_PATH_PARTS = {
    ".gnupg",
    ".password-store",
    ".ssh",
    "keyrings",
    "secrets",
}


def is_sensitive_path(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    return (
        path.name.lower() in SENSITIVE_FILE_NAMES
        or bool(lowered_parts.intersection(SENSITIVE_PATH_PARTS))
        or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
    )


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(READ_ROOT)) or "."
    except ValueError:
        return str(path)


def strip_qwen_thinking(content: str) -> str:
    """Hide reasoning emitted by some Qwen templates despite think=false."""
    content = str(content or "").strip()
    content = re.sub(
        r"^.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE
    )
    content = re.sub(
        r"<think>.*?</think>\s*", "", content, flags=re.DOTALL | re.IGNORECASE
    )
    return content.strip()


# ============================================================
# TOOL: LIST FILES
# ============================================================

def tool_list_files(
    path: str = "."
) -> str:

    try:
        directory = safe_path(path)

        if not directory.exists():
            return (
                f"Thư mục không tồn tại: {path}"
            )

        if not directory.is_dir():
            return (
                f"Không phải thư mục: {path}"
            )

        output = []

        for item in sorted(
            directory.iterdir(),
            key=lambda x: x.name.lower()
        ):
            # tránh gửi các thư mục lớn/rác cho AI
            if item.name in {
                ".git",
                ".venv",
                "__pycache__",
                "node_modules",
            }:
                continue

            relative = display_path(item)

            if item.is_dir():
                output.append(
                    f"[DIR]  {relative}"
                )
            else:
                output.append(
                    f"[FILE] {relative}"
                )

        if not output:
            return "Thư mục rỗng."

        return "\n".join(output)

    except Exception as e:
        return (
            f"list_files error: "
            f"{type(e).__name__}: {e}"
        )


# ============================================================
# TOOL: READ FILE
# ============================================================

def tool_read_file(
    path: str
) -> str:

    try:
        file_path = safe_path(path)

        if not file_path.exists():
            return (
                f"File không tồn tại: {path}"
            )

        if not file_path.is_file():
            return (
                f"Không phải file: {path}"
            )

        if is_sensitive_path(file_path):
            return (
                "File được nhận diện là dữ liệu bí mật/đăng nhập. "
                "Jarvis chỉ dùng metadata để đánh giá và không gửi nội dung "
                "file này cho mô hình AI. Không nên xóa nếu chưa có bản sao lưu."
            )

        # tránh đọc file quá lớn vào context
        size = file_path.stat().st_size

        if size > 500_000:
            return (
                f"File quá lớn: {size} bytes. "
                "Không đọc toàn bộ."
            )

        return file_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    except Exception as e:
        return (
            f"read_file error: "
            f"{type(e).__name__}: {e}"
        )


# ============================================================
# TOOL: FIND LARGEST FILES (READ-ONLY)
# ============================================================

def tool_find_largest_files(
    path: str = ".",
    limit: int = 20,
    minimum_size_mb: float = 1.0,
) -> str:
    """Return the largest regular files without reading or deleting them."""
    try:
        root = safe_path(path)
        if not root.exists():
            return f"Thư mục không tồn tại: {path}"
        if not root.is_dir():
            return f"Không phải thư mục: {path}"

        limit = max(1, min(int(limit), 50))
        minimum_size = max(0, int(float(minimum_size_mb) * 1024 * 1024))
        largest = []
        inaccessible = 0

        for current_root, directories, files in os.walk(root, followlinks=False):
            # Không đi theo thư mục symlink để tránh thoát khỏi READ_ROOT.
            current = Path(current_root)
            directories[:] = [
                name for name in directories
                if not (current / name).is_symlink()
            ]
            for name in files:
                candidate = current / name
                try:
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    size = candidate.stat().st_size
                except (OSError, PermissionError):
                    inaccessible += 1
                    continue
                if size < minimum_size:
                    continue
                entry = (size, str(candidate))
                if len(largest) < limit:
                    heapq.heappush(largest, entry)
                elif entry > largest[0]:
                    heapq.heapreplace(largest, entry)

        ordered = sorted(largest, reverse=True)
        if not ordered:
            return "Không tìm thấy file phù hợp."

        lines = [
            f"Các file lớn nhất trong {display_path(root)} "
            f"(chỉ đọc metadata, không xóa):"
        ]
        for index, (size, candidate_text) in enumerate(ordered, 1):
            candidate = Path(candidate_text)
            marker = " [NHẠY CẢM/QUAN TRỌNG]" if is_sensitive_path(candidate) else ""
            lines.append(
                f"{index}. {human_size(size)} | {display_path(candidate)}{marker}"
            )
        if inaccessible:
            lines.append(f"Bỏ qua {inaccessible} file không có quyền đọc metadata.")
        return "\n".join(lines)
    except Exception as e:
        return f"find_largest_files error: {type(e).__name__}: {e}"


# ============================================================
# TOOL: ANALYZE ONE FILE (READ-ONLY)
# ============================================================

def tool_analyze_file(path: str) -> str:
    """Describe a file and give conservative, non-destructive cleanup advice."""
    try:
        file_path = safe_path(path)
        if not file_path.exists():
            return f"File không tồn tại: {path}"
        if not file_path.is_file():
            return f"Không phải file: {path}"

        stat_result = file_path.stat()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "không xác định"
        parts = {part.lower() for part in file_path.parts}
        sensitive = is_sensitive_path(file_path)
        project_file = PROJECT_ROOT == file_path or PROJECT_ROOT in file_path.parents
        cache_file = ".cache" in parts or "cache" in parts
        download_file = "downloads" in parts

        if sensitive:
            importance = "Rất quan trọng: có thể chứa thông tin đăng nhập hoặc khóa."
            advice = "Không nên xóa nếu chưa biết rõ tác dụng và chưa sao lưu."
        elif project_file:
            importance = "Có thể là thành phần của dự án Jarvis."
            advice = "Không nên xóa thủ công trước khi kiểm tra Git và nơi file được sử dụng."
        elif cache_file:
            importance = "Có vẻ là dữ liệu cache; ứng dụng thường có thể tạo lại."
            advice = "Có thể cân nhắc xóa thủ công sau khi đóng ứng dụng liên quan."
        elif download_file:
            importance = "Nằm trong Downloads; có thể là bộ cài hoặc file người dùng tải về."
            advice = "Chỉ xóa nếu bạn xác nhận không còn cần nội dung này."
        else:
            importance = "Chưa đủ bằng chứng để khẳng định file không quan trọng."
            advice = "Hãy xem nội dung/tên ứng dụng sở hữu và sao lưu trước khi tự xóa."

        details = [
            f"Đường dẫn: {display_path(file_path)}",
            f"Kích thước: {human_size(stat_result.st_size)} ({stat_result.st_size} bytes)",
            f"Loại dự đoán: {mime_type}",
            f"Sửa lần cuối: {datetime.fromtimestamp(stat_result.st_mtime).isoformat(timespec='seconds')}",
            f"Đánh giá: {importance}",
            f"Khuyến nghị: {advice}",
            "Jarvis không xóa file; quyết định cuối cùng do người dùng thực hiện.",
        ]

        if sensitive:
            details.append("Nội dung được giữ kín và không gửi cho mô hình AI.")
        elif stat_result.st_size <= 100_000:
            try:
                sample = file_path.read_text(encoding="utf-8", errors="strict")[:12_000]
            except (OSError, UnicodeError):
                sample = ""
            if sample:
                details.append("Nội dung mẫu để đánh giá:\n" + sample)
        else:
            details.append("File lớn nên chỉ phân tích metadata, không đọc toàn bộ vào AI.")

        return "\n".join(details)
    except Exception as e:
        return f"analyze_file error: {type(e).__name__}: {e}"


# ============================================================
# OLLAMA TOOL DEFINITIONS
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "Liệt kê file và thư mục "
                "bên trong thư mục người dùng. Chỉ đọc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Đường dẫn tương đối "
                            "từ thư mục home."
                        ),
                    }
                },
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Đọc nội dung một file "
                "văn bản trong home. File bí mật sẽ bị che nội dung."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    }
                },
                "required": [
                    "path"
                ],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "find_largest_files",
            "description": (
                "Tìm các file lớn nhất trong home hoặc một thư mục con. "
                "Chỉ đọc metadata và không xóa file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Mặc định là toàn bộ thư mục home.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "minimum_size_mb": {
                        "type": "number",
                        "minimum": 0,
                    },
                },
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "analyze_file",
            "description": (
                "Phân tích metadata, mức quan trọng và đưa ra khuyến nghị "
                "có nên xóa thủ công một file hay không. Không tự xóa file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    }
                },
                "required": [
                    "path"
                ],
            },
        },
    },
]


# ============================================================
# CALL LOCAL OLLAMA
# ============================================================

async def call_ollama(
    messages
):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 8192,
        },
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(
        total=180
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.post(
            f"{OLLAMA_URL}/api/chat",
            headers={"Content-Type": "application/json"},
            json=payload,
        ) as response:

            text = await response.text()

            if response.status != 200:
                raise RuntimeError(
                    f"Ollama HTTP "
                    f"{response.status}: {text}"
                )

            try:
                return json.loads(text)

            except json.JSONDecodeError:
                raise RuntimeError(
                    "Ollama trả JSON không hợp lệ:\n"
                    + text
                )


# ============================================================
# EXECUTE TOOL
# ============================================================

async def execute_tool(
    name: str,
    arguments: dict,
) -> str:

    print(
        f"[TOOL] {name} {arguments}",
        flush=True,
    )

    if name == "list_files":
        return tool_list_files(
            arguments.get(
                "path",
                ".",
            )
        )

    if name == "read_file":
        return tool_read_file(
            arguments["path"]
        )

    if name == "find_largest_files":
        return await asyncio.to_thread(
            tool_find_largest_files,
            arguments.get("path", "."),
            arguments.get("limit", 20),
            arguments.get("minimum_size_mb", 1),
        )

    if name == "analyze_file":
        return await asyncio.to_thread(
            tool_analyze_file,
            arguments["path"],
        )

    return (
        f"Tool không tồn tại: {name}"
    )


# ============================================================
# AGENT
# ============================================================

SYSTEM_PROMPT = f"""
Bạn là trợ lý kiểm kê file chỉ đọc của Jarvis.

Project root:

{PROJECT_ROOT}

Phạm vi đọc:

{READ_ROOT}

Bạn có quyền sử dụng các tool để:
- liệt kê và đọc file văn bản không nhạy cảm
- tìm các file lớn nhất bằng metadata
- phân tích vai trò của một file và khuyến nghị có nên xóa thủ công

QUY TẮC:

1. Chỉ đọc file bên trong thư mục người dùng. Không được ghi, sửa, đổi tên
   hoặc xóa bất kỳ file nào.

2. Khi người dùng hỏi file nào nặng nhất, phải gọi find_largest_files thay vì
   đoán. Sau đó có thể gọi analyze_file cho các file cần đánh giá.

3. Luôn phân biệt rõ: an toàn để cân nhắc xóa, cần kiểm tra thêm, hoặc không
   nên xóa. Không khẳng định một file an toàn để xóa nếu thiếu bằng chứng.

4. Không đọc hay tiết lộ nội dung file chứa token, mật khẩu, SSH key, keyring
   hoặc thông tin đăng nhập. Chỉ dùng metadata và coi chúng là quan trọng.

5. Không được chạy command, sudo, Python hoặc Git tùy ý.

6. Người dùng sẽ tự xóa thủ công. Jarvis chỉ đưa ra phân tích và khuyến nghị.

7. Nếu yêu cầu nguy hiểm hoặc không chắc chắn,
    hãy hỏi người dùng trước.

Hãy trả lời người dùng bằng tiếng Việt
trừ khi họ yêu cầu ngôn ngữ khác.
"""


async def run_agent(
    user_prompt: str
) -> str:

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    for turn in range(
        MAX_AGENT_TURNS
    ):

        print(
            f"[AGENT] turn "
            f"{turn + 1}/{MAX_AGENT_TURNS}",
            flush=True,
        )

        data = await call_ollama(
            messages
        )

        try:
            message = dict(data["message"])
            message["content"] = strip_qwen_thinking(message.get("content", ""))

        except Exception:
            return (
                "❌ Response Ollama "
                "không đúng format:\n"
                + json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                )[:4000]
            )

        # Lưu response assistant
        messages.append(message)

        tool_calls = message.get(
            "tool_calls"
        )

        # AI trả lời bình thường
        if not tool_calls:

            content = message.get(
                "content"
            )

            if not content:
                return (
                    "AI không trả nội dung."
                )

            return content

        # AI muốn dùng tool
        for tool_call in tool_calls:

            try:
                name = (
                    tool_call
                    ["function"]
                    ["name"]
                )

                raw_arguments = (
                    tool_call
                    ["function"]
                    .get(
                        "arguments",
                        "{}",
                    )
                )

                if isinstance(
                    raw_arguments,
                    str,
                ):
                    arguments = json.loads(
                        raw_arguments
                    )

                else:
                    arguments = (
                        raw_arguments
                    )

            except Exception as e:

                result = (
                    "Không parse được "
                    f"tool call: {e}"
                )

            else:
                result = (
                    await execute_tool(
                        name,
                        arguments,
                    )
                )

            messages.append(
                {
                    "role": "tool",
                    "content": result,
                }
            )

    return (
        "⚠️ AI đã dùng quá "
        f"{MAX_AGENT_TURNS} vòng "
        "và bị dừng."
    )


# ============================================================
# DISCORD MESSAGE CHUNKING
# ============================================================

async def send_chunks(
    message,
    text: str,
):

    text = str(text).strip()

    if not text:
        return

    chunk_size = 1900

    for start in range(
        0,
        len(text),
        chunk_size,
    ):

        chunk = text[
            start:
            start + chunk_size
        ]

        await message.reply(
            chunk,
            mention_author=False,
        )


# ============================================================
# DISCORD EVENTS
# ============================================================

@client.event
async def on_ready():

    print(
        f"✅ Discord bot: {client.user}",
        flush=True,
    )

    print(
        f"✅ Channel ID: "
        f"{DISCORD_CHANNEL_ID}",
        flush=True,
    )

    print(
        f"✅ Owner ID: "
        f"{DISCORD_OWNER_ID}",
        flush=True,
    )

    print(
        f"✅ Jarvis root: "
        f"{PROJECT_ROOT}",
        flush=True,
    )

    print(
        f"✅ Ollama local: "
        f"{OLLAMA_URL}",
        flush=True,
    )

    print(
        f"✅ Model: "
        f"{OLLAMA_MODEL}",
        flush=True,
    )

    print(
        "✅ Không cần !ai",
        flush=True,
    )


@client.event
async def on_message(
    message
):

    # Không đọc message của bot
    if message.author.bot:
        return

    # Chỉ nhận đúng channel
    if (
        message.channel.id
        != DISCORD_CHANNEL_ID
    ):
        print(
            "[DISCORD] Bỏ qua "
            "do sai channel.",
            flush=True,
        )

        return

    # Chỉ chủ sở hữu đã cấu hình mới được điều khiển agent.
    if message.author.id != DISCORD_OWNER_ID:
        print(
            "[DISCORD] Bỏ qua do sai owner: "
            f"user_id={message.author.id}",
            flush=True,
        )
        return

    print(
        "\n"
        f"[DISCORD] "
        f"user={message.author} "
        f"user_id={message.author.id} "
        f"channel_id={message.channel.id} "
        f"content={message.content!r}",
        flush=True,
    )

    prompt = (
        message.content.strip()
    )

    if not prompt:
        return

    print(
        f"[USER] {prompt}",
        flush=True,
    )

    try:

        async with (
            message.channel.typing()
        ):

            answer = (
                await run_agent(
                    prompt
                )
            )

        print(
            f"[AI] {answer}",
            flush=True,
        )

        await send_chunks(
            message,
            answer,
        )

    except Exception as e:

        error = (
            f"{type(e).__name__}: "
            f"{e}"
        )

        print(
            f"[ERROR] {error}",
            flush=True,
        )

        await message.reply(
            f"❌ AI gặp lỗi:\n"
            f"```text\n"
            f"{error[:1500]}\n"
            f"```",
            mention_author=False,
        )


# ============================================================
# START
# ============================================================

def main() -> None:
    validate_config()
    print(
        "🚀 Đang khởi động Jarvis AI...",
        flush=True,
    )
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
