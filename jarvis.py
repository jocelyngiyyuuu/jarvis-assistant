#!/usr/bin/env python3

import asyncio
from difflib import SequenceMatcher
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

import discord
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jarvis_core.runtime import JarvisCore
from jarvis_core.text import normalize_text as normalize_core_text
from jarvis_core.window_manager import (
    ChromeWindowManager,
    FileWindowManager,
    chrome_profile_from_close_command,
)


VERSION = "1.1.0-modular-core"

load_dotenv()

discord_client = None
discord_command_lock = asyncio.Lock()
last_command_response = None

# Kết quả tìm file/thư mục gần nhất để có thể chọn bằng số từ Terminal/Discord.
file_search_results = []

PERSONAL_PROFILE = "Default"
STUDY_PROFILE = "Profile 1"


# ==========================================================
# TTS - WORKER THƯỜNG TRỰC TRONG MÔI TRƯỜNG RIÊNG
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent
CORE = JarvisCore(BASE_DIR)
FILE_WINDOWS = FileWindowManager()
CHROME_WINDOWS = ChromeWindowManager()
TTS_DIR = BASE_DIR / "tts"
TTS_PYTHON = TTS_DIR / "VieNeu-TTS" / ".venv" / "bin" / "python"
TTS_ENGINE = TTS_DIR / "tts_engine.py"
TTS_ENABLED = True
tts_process = None


def _clean_tts_text(text):
    """Rút gọn phản hồi Terminal/Discord thành câu phù hợp để đọc bằng TTS."""
    if text is None:
        return ""

    value = str(text).strip()
    if not value:
        return ""

    # Danh sách YouTube có thể rất dài; chỉ đọc một câu xác nhận ngắn.
    if "YOUTUBE -" in value.upper() or "KẾT QUẢ YOUTUBE" in value.upper():
        return "Đã cập nhật danh sách YouTube."

    if "KẾT QUẢ FILE" in value.upper() or "KẾT QUẢ THƯ MỤC" in value.upper():
        return "Đã cập nhật danh sách file."

    # Nếu phản hồi có nhiều dòng, ưu tiên dòng đầu tiên có nội dung.
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return ""
    value = lines[0]

    # Bỏ một số ký hiệu dành cho Discord/Markdown nhưng giữ tiếng Việt.
    value = value.replace("**", "").replace("`", "")
    value = re.sub(r"^[✅❌🔊🔇🌙😴📺]+\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip()

    # Tránh đưa câu quá dài vào TTS.
    if len(value) > 220:
        value = value[:217].rstrip() + "..."

    return value


def start_tts_worker():
    """Khởi động VieNeu-TTS một lần và giữ model sống trong worker."""
    global tts_process

    if not TTS_ENABLED:
        return False

    if tts_process is not None and tts_process.poll() is None:
        return True

    if not TTS_PYTHON.exists():
        print(f"[TTS ERROR] Không tìm thấy Python TTS: {TTS_PYTHON}")
        return False

    if not TTS_ENGINE.exists():
        print(f"[TTS ERROR] Không tìm thấy tts_engine.py: {TTS_ENGINE}")
        return False

    try:
        print("Jarvis: Đang khởi động TTS worker...")

        tts_process = subprocess.Popen(
            [
                str(TTS_PYTHON),
                str(TTS_ENGINE),
            ],
            cwd=str(TTS_DIR),
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
            # Giữ stdout để thấy trạng thái READY, ẩn warning dài của torch.
            stderr=subprocess.DEVNULL,
        )

        return True

    except Exception as error:
        print(f"[TTS ERROR] Không thể khởi động worker: {error}")
        tts_process = None
        return False


def stop_tts_worker():
    """Dừng worker sạch khi Jarvis thoát."""
    global tts_process

    process = tts_process
    tts_process = None

    if process is None:
        return

    if process.poll() is not None:
        return

    try:
        if process.stdin is not None:
            process.stdin.write("__EXIT__\n")
            process.stdin.flush()
            process.stdin.close()

        process.wait(timeout=3)
    except Exception:
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def speak(text):
    """
    Gửi text cho TTS worker qua stdin.
    Model VieNeu chỉ load một lần khi worker khởi động.
    """
    global tts_process

    if not TTS_ENABLED:
        return False

    text = _clean_tts_text(text)
    if not text:
        return False

    if not start_tts_worker():
        return False

    try:
        if tts_process is None or tts_process.stdin is None:
            return False

        # Worker dùng từng dòng stdin làm một câu nói.
        text = text.replace("\n", " ").strip()
        tts_process.stdin.write(text + "\n")
        tts_process.stdin.flush()
        return True

    except (BrokenPipeError, OSError, ValueError) as error:
        print(f"[TTS ERROR] Worker mất kết nối: {error}")
        stop_tts_worker()
        return False

    except Exception as error:
        print(f"[TTS ERROR] {error}")
        return False


def jarvis_say(text, *, show=True):
    """In ra Terminal (nếu cần) và phát cùng nội dung bằng TTS."""
    if show:
        print(f"Jarvis: {text}")
    speak(text)


def speak_last_response():
    """Đọc phản hồi cuối mà route_command đã lưu cho Terminal/Discord."""
    if last_command_response:
        speak(last_command_response)


# ==========================================================
# BIẾN LƯU TRẠNG THÁI YOUTUBE
# ==========================================================

youtube_videos = []
youtube_profile = None
youtube_profile_name = None
youtube_page_id = None


# ==========================================================
# MCP PERSISTENT SESSION
# ==========================================================

mcp_stdio_context = None
mcp_session_context = None
mcp_session = None
mcp_errlog = None


# ==========================================================
# CHROME PROFILE
# ==========================================================

def choose_chrome_profile(command=""):
    command_lower = command.lower()

    personal_words = [
        "cá nhân",
        "ca nhan",
        "personal",
    ]

    study_words = [
        "học",
        "hoc",
        "chatgpt",
        "học tập",
        "hoc tap",
        "study",
    ]

    for word in personal_words:
        if word in command_lower:
            return PERSONAL_PROFILE, "Cá nhân"

    for word in study_words:
        if word in command_lower:
            return STUDY_PROFILE, "Học / ChatGPT"

    print()
    print("Jarvis: Bạn muốn dùng Chrome nào?")
    print()
    print("1. Cá nhân")
    print("2. Học / ChatGPT")
    print()

    choice = input("Chọn: ").strip()

    if choice == "1":
        return PERSONAL_PROFILE, "Cá nhân"

    if choice == "2":
        return STUDY_PROFILE, "Học / ChatGPT"

    print("Jarvis: Lựa chọn không hợp lệ.")
    return None, None


# ==========================================================
# MỞ CHROME
# ==========================================================

def open_chrome(profile, url):
    previous_window_ids = CHROME_WINDOWS.snapshot_ids()
    subprocess.Popen(
        [
            "google-chrome",
            # Jarvis đóng riêng từng profile bằng wmctrl. Trên phiên Wayland,
            # buộc cửa sổ Chrome qua XWayland để wmctrl nhìn thấy window ID.
            "--ozone-platform=x11",
            f"--profile-directory={profile}",
            "--new-window",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return CHROME_WINDOWS.track_profile_window(profile, previous_window_ids)


# ==========================================================
# CHROME SHORTCUTS - MỞ THẲNG WEBSITE THEO PROFILE
# ==========================================================

CHROME_SHORTCUTS = {
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "chat gpt": ("ChatGPT", "https://chatgpt.com/"),
    "gmail": ("Gmail", "https://mail.google.com/"),
    "mail": ("Gmail", "https://mail.google.com/"),
    "drive": ("Google Drive", "https://drive.google.com/"),
    "google drive": ("Google Drive", "https://drive.google.com/"),
    "calendar": ("Google Calendar", "https://calendar.google.com/"),
    "lịch google": ("Google Calendar", "https://calendar.google.com/"),
    "lich google": ("Google Calendar", "https://calendar.google.com/"),
    "github": ("GitHub", "https://github.com/"),
    "google": ("Google", "https://www.google.com/"),
    "chrome": ("Chrome", "chrome://newtab/"),
}


def _chrome_profile_from_explicit_command(command):
    """Chỉ lấy profile khi người dùng nói rõ học/cá nhân; không gọi input khi chạy nền."""
    command_lower = command.lower()

    personal_words = (
        "cá nhân",
        "ca nhan",
        "personal",
    )
    study_words = (
        "học",
        "hoc",
        "học tập",
        "hoc tap",
        "study",
    )

    if any(word in command_lower for word in personal_words):
        return PERSONAL_PROFILE, "Cá nhân"

    if any(word in command_lower for word in study_words):
        return STUDY_PROFILE, "Học / ChatGPT"

    return None, None


def handle_chrome_shortcut(command):
    """
    Mở nhanh website trong đúng Chrome profile.

    Ví dụ:
      mở chatgpt học
      mở tab chatgpt cá nhân
      mở gmail học
      mở drive học
      mở github cá nhân
      mở chrome học
    """
    command_clean = normalize_core_text(command)

    if not re.match(r"^(?:mở|mo|vào|vao)(?:\s+tab)?\s+", command_clean):
        return False

    # Bỏ động từ ở đầu và từ 'tab' nếu có.
    target_text = re.sub(
        r"^(?:mở|mo|vào|vao)(?:\s+tab)?\s+",
        "",
        command_clean,
        count=1,
    ).strip()

    # Bỏ từ chỉ profile để còn lại tên website.
    target_text = re.sub(
        r"\s+(?:bằng\s+)?(?:tài\s+khoản\s+)?(?:cá nhân|ca nhan|personal|học tập|hoc tap|học|hoc|study)\s*$",
        "",
        target_text,
        flags=re.IGNORECASE,
    ).strip()

    shortcut = CHROME_SHORTCUTS.get(target_text)
    if shortcut is None:
        return False

    profile, profile_name = _chrome_profile_from_explicit_command(command)

    if profile is None:
        message = (
            f"ℹ️ Hãy nói rõ profile, ví dụ: `mở {target_text} học` "
            f"hoặc `mở {target_text} cá nhân`."
        )
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    site_name, url = shortcut
    open_chrome(profile, url)

    message = f"✅ Đang mở {site_name} bằng Chrome {profile_name}."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


# ==========================================================
# MCP RESULT -> TEXT
# ==========================================================

def result_to_text(result):
    parts = []

    for item in result.content:
        if hasattr(item, "text"):
            parts.append(item.text)

    return "\n".join(parts)


# ==========================================================
# PAGE ID
# ==========================================================

def extract_page_id(line):
    patterns = [
        r"^(\d+):",
        r"^(\d+)\s",
        r"pageId[=:]\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            line.strip(),
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1))

    return None


# ==========================================================
# CLEAN TITLE
# ==========================================================

def clean_title(title):
    title = title.strip()

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    return title


def normalize_text(text):
    return clean_title(text).lower()


def find_video_by_title(videos, query):
    query_norm = normalize_text(query)

    if not query_norm:
        return None

    # Ưu tiên khớp chính xác
    for video in videos:
        if normalize_text(video["title"]) == query_norm:
            return video

    # Sau đó khớp chứa từ khóa
    for video in videos:
        if query_norm in normalize_text(video["title"]):
            return video

    return None


# ==========================================================
# LẤY DANH SÁCH VIDEO
# ==========================================================

def _json_from_mcp_result(result, default=None):
    """Đọc JSON được trả về từ evaluate_script của Chrome DevTools MCP."""
    text = result_to_text(result).strip()

    if not text:
        return default

    candidates = [text]

    fenced = re.search(
        r"```(?:json)?\s*(.*?)```",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    for left, right in (("[", "]"), ("{", "}")):
        first = text.find(left)
        last = text.rfind(right)
        if first != -1 and last > first:
            candidates.append(text[first:last + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    return default


async def extract_videos_from_page(session, limit=10):
    """Chỉ lấy link video thật (/watch?v=...), bỏ sidebar/kênh/menu."""
    script = r'''() => {
        const videos = new Map();
        const anchors = document.querySelectorAll('a[href*="/watch?v="]');

        for (const a of anchors) {
            try {
                const u = new URL(a.href, location.href);
                const videoId = u.searchParams.get('v');
                if (!videoId) continue;

                // Giữ ngữ cảnh playlist/mix. Trước đây Jarvis chỉ giữ `v`,
                // làm mất `list` và `index`, nên video kế tiếp ra ngoài playlist.
                const target = new URL('/watch', location.origin);
                target.searchParams.set('v', videoId);
                for (const key of ['list', 'index', 'start_radio']) {
                    const value = u.searchParams.get(key);
                    if (value) target.searchParams.set(key, value);
                }
                const url = target.href;
                const title = (
                    a.getAttribute('title') ||
                    a.textContent ||
                    a.getAttribute('aria-label') ||
                    ''
                ).replace(/\s+/g, ' ').trim();

                if (!title || title.length < 2) continue;

                const current = videos.get(url);
                if (!current || title.length > current.title.length) {
                    videos.set(url, {title, url});
                }
            } catch (_) {
            }
        }

        return Array.from(videos.values());
    }'''

    result = await session.call_tool(
        "evaluate_script",
        arguments={"function": script},
    )

    raw_videos = _json_from_mcp_result(result, default=[])
    if not isinstance(raw_videos, list):
        return []

    videos = []
    seen = set()

    for item in raw_videos:
        if not isinstance(item, dict):
            continue

        title = clean_title(str(item.get("title", "")))
        url = str(item.get("url", "")).strip()

        if not title or not url or "/watch?v=" not in url:
            continue

        key = url.lower()
        if key in seen:
            continue

        seen.add(key)
        videos.append({"title": title, "url": url})

        if len(videos) >= limit:
            break

    return videos


# ==========================================================
# TẠO MCP SERVER
# ==========================================================

def create_mcp_server():
    return StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "chrome-devtools-mcp@latest",
            "--autoConnect",
            "--no-usage-statistics",
        ],
    )


async def get_mcp_session():
    """Tạo MCP một lần và giữ session cho tới khi Jarvis thoát."""
    global mcp_stdio_context
    global mcp_session_context
    global mcp_session
    global mcp_errlog

    if mcp_session is not None:
        return mcp_session

    print()
    print("Jarvis: Đang kết nối Chrome...")

    server = create_mcp_server()

    try:
        # chrome-devtools-mcp đôi khi ghi cảnh báo lặp lại vào stderr
        # (ví dụ PerformanceIssue). Chuyển stderr sang file log để Terminal
        # của Jarvis không bị spam, nhưng vẫn giữ log để kiểm tra khi cần.
        mcp_errlog = open(
            Path(__file__).resolve().parent / "mcp_errors.log",
            "a",
            encoding="utf-8",
            buffering=1,
        )

        mcp_stdio_context = stdio_client(
            server,
            errlog=mcp_errlog,
        )
        read, write = await mcp_stdio_context.__aenter__()

        mcp_session_context = ClientSession(read, write)
        mcp_session = await mcp_session_context.__aenter__()
        await mcp_session.initialize()

        print("Jarvis: Đã kết nối Chrome.")
        print("Jarvis: Kết nối này sẽ được giữ cho tới khi Jarvis thoát.")
        return mcp_session

    except Exception:
        await close_mcp_session()
        raise


async def close_mcp_session():
    """Đóng MCP session sạch khi Jarvis kết thúc."""
    global mcp_stdio_context
    global mcp_session_context
    global mcp_session
    global mcp_errlog

    session_context = mcp_session_context
    stdio_context = mcp_stdio_context
    errlog = mcp_errlog

    mcp_session = None
    mcp_session_context = None
    mcp_stdio_context = None
    mcp_errlog = None

    if session_context is not None:
        try:
            await session_context.__aexit__(None, None, None)
        except Exception:
            pass

    if stdio_context is not None:
        try:
            await stdio_context.__aexit__(None, None, None)
        except Exception:
            pass

    if errlog is not None:
        try:
            errlog.close()
        except Exception:
            pass


# ==========================================================
# TÌM TAB YOUTUBE
# ==========================================================

async def find_youtube_page(session):
    pages_result = await session.call_tool(
        "list_pages",
        arguments={},
    )

    pages_text = result_to_text(
        pages_result
    )

    youtube_pages = []

    for line in pages_text.splitlines():

        if "youtube.com" not in line.lower():
            continue

        page_id = extract_page_id(line)

        if page_id is not None:
            youtube_pages.append(
                page_id
            )

    if not youtube_pages:
        return None

    # Tab YouTube gần nhất
    return youtube_pages[-1]


# ==========================================================
# ĐỌC DANH SÁCH VIDEO YOUTUBE
# ==========================================================

async def read_youtube_videos(
    profile,
    profile_name,
    retries=6,
    retry_delay=1.0,
):
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    try:
        session = await get_mcp_session()

        # Khi tab YouTube vừa được mở lại, Chrome DevTools có thể thấy tab
        # trước khi nội dung trang chủ đã render xong. Vì vậy Jarvis thử lại
        # snapshot vài lần thay vì báo lỗi ngay ở lần đầu.
        videos = []

        for attempt in range(1, retries + 1):
            found_page_id = await find_youtube_page(session)
            if found_page_id is not None:
                youtube_page_id = found_page_id

            if youtube_page_id is None:
                if attempt < retries:
                    await asyncio.sleep(retry_delay)
                    continue

                print("Jarvis: Không tìm thấy tab YouTube.")
                return False

            await session.call_tool(
                "select_page",
                arguments={
                    "pageId": youtube_page_id,
                    "bringToFront": True,
                },
            )

            videos = await extract_videos_from_page(
                session,
                limit=10,
            )

            if videos:
                break

            if attempt < retries:
                print(
                    f"Jarvis: YouTube đang tải, thử lại "
                    f"({attempt}/{retries})..."
                )
                await asyncio.sleep(retry_delay)

        if not videos:
            print("Jarvis: Trang YouTube đã mở nhưng chưa có danh sách video.")
            print("Jarvis: Thử 'làm mới youtube' sau khi trang tải xong.")
            return False

        youtube_videos = videos
        youtube_profile = profile
        youtube_profile_name = profile_name

        print()
        print("=" * 60)
        print(f"   YOUTUBE - {profile_name.upper()}")
        print("=" * 60)
        print()

        for index, video in enumerate(youtube_videos, start=1):
            print(f"{index:2}. {video['title']}")

        print()
        print("=" * 60)
        print('Jarvis: Dùng "mở video 3" hoặc "mở video <tên>"')
        print("=" * 60)

        set_command_response(
            format_youtube_list(
                f"YOUTUBE - {profile_name.upper()}",
                youtube_videos,
            )
        )
        return True

    except Exception as error:
        print("Jarvis: Không thể đọc YouTube qua Chrome DevTools.")
        print(f"Jarvis: Chi tiết: {error}")
        return False


# ==========================================================
# MỞ YOUTUBE + ĐỌC TRANG CHỦ
# ==========================================================

async def open_youtube(command):
    global youtube_videos
    global youtube_page_id

    profile, profile_name = (
        choose_chrome_profile(command)
    )

    if profile is None:
        return

    # Danh sách UID cũ không còn đáng tin khi tab YouTube trước đã bị đóng.
    youtube_videos = []
    youtube_page_id = None

    print()

    print(
        f"Jarvis: Đang mở YouTube bằng "
        f"{profile_name}..."
    )

    open_chrome(
        profile,
        "https://www.youtube.com/",
    )

    # Cho tab mới xuất hiện; read_youtube_videos() sẽ tự retry
    # cho tới khi danh sách video render xong.
    await asyncio.sleep(0.8)

    await read_youtube_videos(
        profile,
        profile_name,
    )


# ==========================================================
# LẤY SNAPSHOT YOUTUBE HIỆN TẠI
# ==========================================================

async def get_current_youtube_videos(session, limit=20):
    global youtube_page_id

    # Ưu tiên đúng tab Jarvis đã ghi nhớ. Chỉ tìm lại nếu tab đó
    # không còn tồn tại (ví dụ người dùng đã đóng tab YouTube).
    if youtube_page_id is None:
        youtube_page_id = await find_youtube_page(session)

    if youtube_page_id is None:
        return None, []

    try:
        await session.call_tool(
            "select_page",
            arguments={
                "pageId": youtube_page_id,
                "bringToFront": True,
            },
        )
    except Exception:
        youtube_page_id = await find_youtube_page(session)
        if youtube_page_id is None:
            return None, []

        await session.call_tool(
            "select_page",
            arguments={
                "pageId": youtube_page_id,
                "bringToFront": True,
            },
        )

    videos = await extract_videos_from_page(
        session,
        limit=limit,
    )

    return youtube_page_id, videos


# ==========================================================
# LÀM MỚI DANH SÁCH YOUTUBE
# ==========================================================

async def refresh_youtube_videos():
    global youtube_videos

    print()
    print("Jarvis: Đang làm mới danh sách YouTube...")

    try:
        session = await get_mcp_session()
        _, videos = await get_current_youtube_videos(session, limit=10)

        if not videos:
            print("Jarvis: Không tìm thấy video trên tab YouTube hiện tại.")
            return False

        youtube_videos = videos

        print()
        print("=" * 60)
        print("              YOUTUBE - DANH SÁCH MỚI")
        print("=" * 60)
        print()

        for index, video in enumerate(youtube_videos, start=1):
            print(f"{index:2}. {video['title']}")

        print()
        print("=" * 60)
        print('Jarvis: Có thể dùng "mở video 3" hoặc "mở video <tên>".')
        print("=" * 60)

        set_command_response(
            format_youtube_list(
                "YOUTUBE - DANH SÁCH MỚI",
                youtube_videos,
            )
        )
        return True

    except Exception as error:
        print("Jarvis: Không thể làm mới danh sách YouTube.")
        print(f"Jarvis: Chi tiết: {error}")
        return False


# ==========================================================
# MỞ VIDEO ĐÃ CHỌN
# ==========================================================

async def open_video(number):
    global youtube_videos
    global youtube_page_id

    if not youtube_videos:
        print()
        print("Jarvis: Chưa có danh sách video.")
        print('Jarvis: Hãy dùng "mở youtube" trước.')
        return

    if number < 1 or number > len(youtube_videos):
        print("Jarvis: Số video không hợp lệ.")
        return

    selected_video = youtube_videos[number - 1]
    selected_title = selected_video["title"]

    print()
    print("Jarvis: Đang mở:")
    print(selected_title)

    try:
        session = await get_mcp_session()
        selected_url = selected_video.get("url")

        if not selected_url:
            print("Jarvis: Video này chưa có URL hợp lệ.")
            print('Jarvis: Dùng "làm mới youtube" để cập nhật danh sách.')
            return

        if youtube_page_id is None:
            youtube_page_id = await find_youtube_page(session)
        if youtube_page_id is None:
            message = "❌ Không tìm thấy tab YouTube để mở video."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return

        await session.call_tool(
            "select_page",
            arguments={"pageId": youtube_page_id, "bringToFront": True},
        )

        await session.call_tool(
            "navigate_page",
            arguments={
                "type": "url",
                "url": selected_url,
            },
        )
        message = f"▶️ Đã mở video: {selected_title}"
        print(f"Jarvis: {message}")
        set_command_response(message)

    except Exception as error:
        print("Jarvis: Không thể nhấn vào video.")
        print(f"Jarvis: Chi tiết: {error}")


async def open_video_by_name(query):
    query = clean_title(query)

    if not query:
        print("Jarvis: Bạn chưa nhập tên video.")
        return

    print()
    print(f"Jarvis: Đang tìm video: {query}")

    try:
        session = await get_mcp_session()
        _, current_videos = await get_current_youtube_videos(session, limit=40)

        if not current_videos:
            print("Jarvis: Không đọc được video trên tab YouTube hiện tại.")
            return

        selected_video = find_video_by_title(current_videos, query)

        if selected_video is None:
            print("Jarvis: Không tìm thấy video có tên phù hợp trên trang hiện tại.")
            print('Jarvis: Bạn có thể dùng "làm mới youtube" để xem danh sách.')
            return

        print("Jarvis: Đã tìm thấy:")
        print(selected_video["title"])

        selected_url = selected_video.get("url")
        if not selected_url:
            print("Jarvis: Video này chưa có URL hợp lệ.")
            return

        await session.call_tool(
            "navigate_page",
            arguments={
                "type": "url",
                "url": selected_url,
            },
        )
        message = f"▶️ Đã mở video: {selected_video['title']}"
        print(f"Jarvis: {message}")
        set_command_response(message)

    except Exception as error:
        print("Jarvis: Không thể nhấn vào video.")
        print(f"Jarvis: Chi tiết: {error}")


async def control_youtube_playback(should_play):
    """Pause or resume the HTML5 player in Jarvis' current YouTube tab."""
    global youtube_page_id

    try:
        session = await get_mcp_session()
        if youtube_page_id is None:
            youtube_page_id = await find_youtube_page(session)
        if youtube_page_id is None:
            message = "❌ Không tìm thấy tab YouTube đang mở."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

        await session.call_tool(
            "select_page",
            arguments={"pageId": youtube_page_id, "bringToFront": True},
        )

        action = "play" if should_play else "pause"
        script = f'''async () => {{
            const video = document.querySelector('video.html5-main-video, video');
            if (!video) return {{ok: false, error: 'Không tìm thấy trình phát video.'}};
            try {{
                if ('{action}' === 'play') {{
                    await video.play();
                }} else {{
                    video.pause();
                }}
                return {{
                    ok: true,
                    paused: video.paused,
                    title: document.title.replace(/\\s*-\\s*YouTube\\s*$/, '').trim()
                }};
            }} catch (error) {{
                return {{ok: false, error: String(error)}};
            }}
        }}'''
        result = await session.call_tool(
            "evaluate_script",
            arguments={"function": script},
        )
        state = _json_from_mcp_result(result, default={})
        if not isinstance(state, dict) or not state.get("ok"):
            detail = state.get("error") if isinstance(state, dict) else None
            message = f"❌ Không thể điều khiển video{f': {detail}' if detail else '.'}"
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

        title = clean_title(str(state.get("title", "")))
        if should_play:
            message = f"▶️ Đã phát tiếp video{f': {title}' if title else '.'}"
        else:
            message = f"⏸️ Đã dừng video{f': {title}' if title else '.'}"
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True
    except Exception as error:
        message = f"❌ Không thể điều khiển video YouTube: {error}"
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False


# ==========================================================
# TÌM KIẾM VIDEO TRÊN YOUTUBE
# ==========================================================

def extract_youtube_search_query(command):
    """Hiểu cả: tìm youtube X, tìm video X và tìm X."""
    command_clean = command.strip()

    patterns = [
        r"^(?:tìm|tim)\s+youtube\s+(.+)$",
        r"^youtube\s+(?:tìm|tim)\s+(.+)$",
        r"^(?:tìm|tim)\s+video\s+(.+)$",
        r"^(?:tìm|tim)\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, command_clean, re.IGNORECASE)

        if not match:
            continue

        query = match.group(1).strip()
        query_lower = query.lower()

        # Các lệnh Google rõ ràng vẫn được dành cho Google Search.
        if query_lower.startswith(("google ", "trên google ", "tren google ")):
            return ""

        if query_lower.startswith(("kiếm ", "kiem ")):
            return ""

        return query

    return ""


async def search_youtube(command):
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    query = extract_youtube_search_query(command)

    if not query:
        print("Jarvis: Bạn chưa nhập nội dung cần tìm trên YouTube.")
        return False

    print()
    print(f'Jarvis: Đang tìm trên YouTube: "{query}"')

    try:
        session = await get_mcp_session()

        # Luôn ưu tiên đúng tab YouTube đang được Jarvis điều khiển.
        # Không gọi google-chrome nếu tab này vẫn còn tồn tại.
        if youtube_page_id is None:
            youtube_page_id = await find_youtube_page(session)

        if youtube_page_id is not None:
            try:
                await session.call_tool(
                    "select_page",
                    arguments={
                        "pageId": youtube_page_id,
                        "bringToFront": True,
                    },
                )
            except Exception:
                youtube_page_id = await find_youtube_page(session)

        if youtube_page_id is not None:
            search_url = (
                "https://www.youtube.com/results?search_query="
                + quote_plus(query)
            )

            await session.call_tool(
                "navigate_page",
                arguments={
                    "type": "url",
                    "url": search_url,
                },
            )
        else:
            # Chỉ khi thật sự không còn tab YouTube mới mở tab mới.
            profile, profile_name = choose_chrome_profile(command)

            if profile is None:
                return False

            youtube_profile = profile
            youtube_profile_name = profile_name

            search_url = (
                "https://www.youtube.com/results?search_query="
                + quote_plus(query)
            )

            open_chrome(profile, search_url)
            await asyncio.sleep(0.8)
            youtube_page_id = await find_youtube_page(session)

        youtube_videos = []

        videos = []
        for attempt in range(1, 7):
            _, videos = await get_current_youtube_videos(session, limit=10)

            if videos:
                break

            if attempt < 6:
                print(
                    f"Jarvis: Kết quả đang tải, thử lại "
                    f"({attempt}/6)..."
                )
                await asyncio.sleep(1.0)

        if not videos:
            print("Jarvis: Chưa đọc được kết quả tìm kiếm YouTube.")
            print('Jarvis: Thử "làm mới youtube" sau khi trang tải xong.')
            return False

        youtube_videos = videos

        print()
        print("=" * 60)
        print(f"   KẾT QUẢ YOUTUBE: {query}")
        print("=" * 60)
        print()

        for index, video in enumerate(youtube_videos, start=1):
            print(f"{index:2}. {video['title']}")

        print()
        print("=" * 60)
        print('Jarvis: Dùng "mở video 2" hoặc "mở video <tên>"')
        print("=" * 60)

        set_command_response(
            format_youtube_list(
                f"KẾT QUẢ YOUTUBE: {query}",
                youtube_videos,
            )
        )
        return True

    except Exception as error:
        print("Jarvis: Không thể tìm kiếm trên YouTube.")
        print(f"Jarvis: Chi tiết: {error}")
        return False


# ==========================================================
# HIỆN / QUAY LẠI TAB YOUTUBE HIỆN TẠI
# ==========================================================

async def show_youtube():
    """Hiện lại Chrome và đưa đúng tab YouTube hiện tại lên trước."""
    global youtube_page_id

    print()
    print("Jarvis: Đang quay lại YouTube...")

    # 1. Thoát khỏi trạng thái Show Desktop để cửa sổ hiện lại.
    try:
        subprocess.run(
            ["wmctrl", "-k", "off"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        await asyncio.sleep(0.3)

    except FileNotFoundError:
        # Nếu wmctrl không có, thử Super+D.
        try:
            subprocess.run(
                ["xdotool", "key", "super+d"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            await asyncio.sleep(0.3)
        except FileNotFoundError:
            pass

    # 2. Đưa đúng tab YouTube đang được Jarvis điều khiển lên trước.
    try:
        session = await get_mcp_session()

        if youtube_page_id is None:
            youtube_page_id = await find_youtube_page(session)

        if youtube_page_id is None:
            print("Jarvis: Không tìm thấy tab YouTube đang mở.")
            print('Jarvis: Hãy dùng "mở youtube cá nhân" hoặc "mở youtube học" trước.')
            return False

        try:
            await session.call_tool(
                "select_page",
                arguments={
                    "pageId": youtube_page_id,
                    "bringToFront": True,
                },
            )

        except Exception:
            # pageId cũ có thể đã hết hiệu lực nếu tab trước bị đóng.
            youtube_page_id = await find_youtube_page(session)

            if youtube_page_id is None:
                print("Jarvis: Không tìm thấy tab YouTube đang mở.")
                return False

            await session.call_tool(
                "select_page",
                arguments={
                    "pageId": youtube_page_id,
                    "bringToFront": True,
                },
            )

        print("Jarvis: Đã quay lại YouTube.")
        return True

    except Exception as error:
        print("Jarvis: Không thể quay lại YouTube.")
        print(f"Jarvis: Chi tiết: {error}")
        return False


# ==========================================================
# TẮT / ĐÓNG TAB YOUTUBE - KHÔNG TẮT CHROME
# ==========================================================

async def close_youtube():
    """
    Đóng đúng tab YouTube mà Jarvis đang điều khiển.

    - Nếu Chrome còn tab khác: chọn tab khác trước rồi đóng YouTube.
    - Nếu YouTube là tab cuối cùng: đổi tab đó thành New Tab để giữ Chrome mở.
    """
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    print()
    print("Jarvis: Đang tắt YouTube...")

    try:
        session = await get_mcp_session()

        # Nếu page ID cũ chưa có hoặc tab cũ đã bị đóng,
        # tìm lại tab YouTube đang mở.
        if youtube_page_id is None:
            youtube_page_id = await find_youtube_page(session)

        if youtube_page_id is None:
            print("Jarvis: Không tìm thấy tab YouTube đang mở.")
            return False

        # Lấy danh sách toàn bộ tab để biết YouTube có phải tab cuối hay không.
        pages_result = await session.call_tool(
            "list_pages",
            arguments={},
        )

        pages_text = result_to_text(pages_result)
        all_page_ids = []

        for line in pages_text.splitlines():
            page_id = extract_page_id(line)

            if page_id is not None and page_id not in all_page_ids:
                all_page_ids.append(page_id)

        other_page_ids = [
            page_id
            for page_id in all_page_ids
            if page_id != youtube_page_id
        ]

        if other_page_ids:
            # Tránh để MCP đang chọn chính tab sắp đóng.
            # Chuyển sang một tab khác trước.
            try:
                await session.call_tool(
                    "select_page",
                    arguments={
                        "pageId": other_page_ids[-1],
                        "bringToFront": True,
                    },
                )
            except Exception:
                pass

            await session.call_tool(
                "close_page",
                arguments={
                    "pageId": youtube_page_id,
                },
            )

            print("Jarvis: Đã tắt tab YouTube. Chrome vẫn đang chạy.")

        else:
            # Chrome DevTools MCP không cho close_page() đóng tab cuối cùng.
            # Vì vậy đổi tab YouTube cuối cùng thành New Tab,
            # đạt mục tiêu rời YouTube nhưng vẫn giữ Chrome mở.
            await session.call_tool(
                "select_page",
                arguments={
                    "pageId": youtube_page_id,
                    "bringToFront": True,
                },
            )

            await session.call_tool(
                "navigate_page",
                arguments={
                    "type": "url",
                    "url": "chrome://newtab/",
                },
            )

            print(
                "Jarvis: YouTube là tab Chrome cuối cùng, "
                "nên đã chuyển nó về New Tab."
            )
            print("Jarvis: Chrome vẫn đang chạy.")

        # Xóa state cũ vì UID/video/page ID của YouTube không còn hợp lệ.
        youtube_videos = []
        youtube_page_id = None

        return True

    except Exception as error:
        print("Jarvis: Không thể tắt YouTube.")
        print(f"Jarvis: Chi tiết: {error}")
        return False


# ==========================================================
# VỀ TRANG CHỦ YOUTUBE
# ==========================================================

async def go_youtube_home():
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    print()
    print("Jarvis: Đang về trang chủ YouTube...")

    try:
        session = await get_mcp_session()

        # Ưu tiên đúng tab YouTube mà Jarvis đang điều khiển.
        if youtube_page_id is None:
            youtube_page_id = await find_youtube_page(session)

        if youtube_page_id is not None:
            try:
                await session.call_tool(
                    "select_page",
                    arguments={
                        "pageId": youtube_page_id,
                        "bringToFront": True,
                    },
                )
            except Exception:
                # Tab cũ có thể đã bị đóng. Tìm một tab YouTube khác.
                youtube_page_id = await find_youtube_page(session)

                if youtube_page_id is not None:
                    await session.call_tool(
                        "select_page",
                        arguments={
                            "pageId": youtube_page_id,
                            "bringToFront": True,
                        },
                    )

        if youtube_page_id is None:
            # Chỉ mở tab mới khi không còn tab YouTube nào.
            profile = youtube_profile
            profile_name = youtube_profile_name

            if profile is None:
                profile, profile_name = choose_chrome_profile("")

            if profile is None:
                return False

            youtube_profile = profile
            youtube_profile_name = profile_name

            open_chrome(profile, "https://www.youtube.com/")
            await asyncio.sleep(0.8)
            youtube_page_id = await find_youtube_page(session)
        else:
            # Điều hướng NGAY TRÊN TAB HIỆN TẠI, không tạo tab mới.
            await session.call_tool(
                "navigate_page",
                arguments={
                    "type": "url",
                    "url": "https://www.youtube.com/",
                },
            )

        youtube_videos = []

        videos = []
        for attempt in range(1, 7):
            _, videos = await get_current_youtube_videos(session, limit=10)

            if videos:
                break

            if attempt < 6:
                print(
                    f"Jarvis: Trang chủ đang tải, thử lại "
                    f"({attempt}/6)..."
                )
                await asyncio.sleep(1.0)

        if not videos:
            print("Jarvis: Đã về trang chủ nhưng chưa đọc được danh sách video.")
            print('Jarvis: Thử "làm mới youtube" sau khi trang tải xong.')
            return True

        youtube_videos = videos

        print()
        print("=" * 60)
        print("   YOUTUBE - TRANG CHỦ")
        print("=" * 60)
        print()

        for index, video in enumerate(youtube_videos, start=1):
            print(f"{index:2}. {video['title']}")

        print()
        print("=" * 60)
        print('Jarvis: Dùng "mở video 2" hoặc "mở video <tên>"')
        print("=" * 60)

        set_command_response(
            format_youtube_list(
                "YOUTUBE - TRANG CHỦ",
                youtube_videos,
            )
        )
        return True

    except Exception as error:
        print("Jarvis: Không thể về trang chủ YouTube.")
        print(f"Jarvis: Chi tiết: {error}")
        return False


# ==========================================================
# GOOGLE SEARCH
# ==========================================================

def remove_profile_words(text):
    words = [
        "bằng tài khoản cá nhân",
        "bang tai khoan ca nhan",
        "tài khoản cá nhân",
        "tai khoan ca nhan",
        "cá nhân",
        "ca nhan",

        "bằng tài khoản học",
        "bang tai khoan hoc",
        "tài khoản học",
        "tai khoan hoc",
        "học / chatgpt",
        "hoc / chatgpt",
        "chatgpt",
    ]

    result = text

    for word in words:
        result = re.sub(
            re.escape(word),
            "",
            result,
            flags=re.IGNORECASE,
        )

    return result.strip()


def extract_google_query(command):
    prefixes = [
        "tìm trên google",
        "tim tren google",
        "tìm google",
        "tim google",
        "google",
        "tìm kiếm",
        "tim kiem",
        "search",
    ]

    command_clean = command.strip()

    command_lower = (
        command_clean.lower()
    )

    for prefix in prefixes:

        if command_lower.startswith(
            prefix
        ):

            query = command_clean[
                len(prefix):
            ].strip()

            query = remove_profile_words(
                query
            )

            return query

    return ""


def search_google(command):
    query = extract_google_query(
        command
    )

    if not query:
        print(
            "Jarvis: Bạn muốn tìm gì?"
        )

        query = input(
            "Tìm kiếm: "
        ).strip()

    if not query:
        return

    profile, profile_name = (
        choose_chrome_profile(command)
    )

    if profile is None:
        return

    url = (
        "https://www.google.com/search?q="
        + quote_plus(query)
    )

    print(
        f"Jarvis: Đang tìm '{query}' "
        f"bằng {profile_name}..."
    )

    open_chrome(
        profile,
        url,
    )


# ==========================================================
# GITHUB
# ==========================================================

def open_github(command):
    profile, profile_name = (
        choose_chrome_profile(command)
    )

    if profile is None:
        return

    print(
        "Jarvis: Đang mở GitHub bằng "
        f"{profile_name}..."
    )

    open_chrome(
        profile,
        "https://github.com/",
    )


# ==========================================================
# VS CODE
# ==========================================================

def open_vscode():
    message = "Đang mở VS Code."
    print(f"Jarvis: {message}")
    set_command_response(message)

    subprocess.Popen(
        ["code"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ==========================================================
# TERMINAL
# ==========================================================

def open_terminal():
    terminals = [
        "gnome-terminal",
        "kgx",
        "x-terminal-emulator",
    ]

    for terminal in terminals:

        try:

            subprocess.Popen(
                [terminal],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            print(
                "Jarvis: Đã mở Terminal."
            )

            return

        except FileNotFoundError:
            continue

    print(
        "Jarvis: Không tìm thấy Terminal."
    )


# ==========================================================
# FOLDER
# ==========================================================

def open_folder(path, name):
    path = Path(path)

    if not path.exists():
        print(
            f"Jarvis: Không tìm thấy {name}."
        )
        return

    print(
        f"Jarvis: Đang mở {name}..."
    )

    subprocess.Popen(
        [
            "xdg-open",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_downloads():
    open_folder(
        Path.home() / "Downloads",
        "Downloads",
    )


def open_documents():
    open_folder(
        Path.home() / "Documents",
        "Documents",
    )


def open_pictures():
    open_folder(
        Path.home() / "Pictures",
        "Pictures",
    )


def open_home():
    open_folder(
        Path.home(),
        "Home",
    )



# ==========================================================
# ĐÓNG / TẮT ỨNG DỤNG
# ==========================================================

def _terminate_processes(patterns, app_name):
    """Gửi SIGTERM cho các tiến trình khớp pattern; không dùng SIGKILL trừ khi cần."""
    closed_any = False

    for pattern in patterns:
        try:
            result = subprocess.run(
                ["pkill", "-TERM", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            # pkill: 0 = có tiến trình đã được signal, 1 = không tìm thấy.
            if result.returncode == 0:
                closed_any = True

        except FileNotFoundError:
            message = "❌ Không tìm thấy lệnh pkill trên hệ thống."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

    if closed_any:
        message = f"✅ Đã tắt {app_name}."
    else:
        message = f"ℹ️ {app_name} hiện không chạy."

    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


def close_vscode():
    """Đóng toàn bộ tiến trình VS Code của người dùng hiện tại."""
    return _terminate_processes(
        [
            r"/usr/share/code/code",
            r"/snap/code/",
            r"(^|/)code( |$)",
        ],
        "VS Code",
    )


def close_file_manager():
    """Đóng File Manager/Nautilus, bao gồm các cửa sổ Downloads/Documents/Pictures/Home."""
    closed_any = False

    # Nautilus có lệnh quit riêng, sạch hơn pkill.
    try:
        result = subprocess.run(
            ["nautilus", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            closed_any = True
    except FileNotFoundError:
        pass

    # Một số Ubuntu dùng process/file manager khác; thử các tên phổ biến.
    for process_name in ("nautilus", "nemo", "thunar"):
        try:
            result = subprocess.run(
                ["pkill", "-TERM", "-x", process_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                closed_any = True
        except FileNotFoundError:
            break

    if closed_any:
        message = "✅ Đã tắt File Manager."
    else:
        message = "ℹ️ File Manager hiện không chạy."

    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


async def close_chrome():
    """Đóng Chrome sạch và reset MCP/YouTube để lần mở sau kết nối lại từ đầu."""
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    # Đóng MCP trước, tránh giữ session DevTools đã chết sau khi Chrome bị tắt.
    await close_mcp_session()

    youtube_videos = []
    youtube_profile = None
    youtube_profile_name = None
    youtube_page_id = None
    CHROME_WINDOWS.clear()

    return _terminate_processes(
        [
            r"google-chrome",
            r"chrome --type=",
        ],
        "Chrome",
    )


async def close_chrome_profile(profile, profile_name):
    """Chỉ đóng các cửa sổ của profile đã được Jarvis mở và theo dõi."""
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    success, detail = CHROME_WINDOWS.close_profile(profile, profile_name)
    if success and youtube_profile == profile:
        await close_mcp_session()
        youtube_videos = []
        youtube_profile = None
        youtube_profile_name = None
        youtube_page_id = None

    icon = "✅" if success else "❌"
    message = f"{icon} {detail}"
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


async def close_all_managed_apps():
    """
    Đóng các ứng dụng GUI mà Jarvis đang quản lý.

    CỐ Ý KHÔNG đóng Terminal vì Jarvis thường đang chạy ngay trong Terminal đó.
    TTS/Jarvis/Discord cũng được giữ nguyên.
    """
    print("Jarvis: Đang tắt các ứng dụng...")

    # VS Code và File Manager không phụ thuộc MCP.
    close_vscode()
    close_file_manager()

    # Chrome cần đóng MCP sạch trước khi terminate process.
    await close_chrome()

    message = (
        "✅ Đã tắt các ứng dụng Jarvis quản lý: VS Code, Chrome và File Manager. "
        "Terminal/Jarvis vẫn được giữ để tôi tiếp tục nhận lệnh."
    )
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


# ==========================================================
# FILE SYSTEM - TÌM / MỞ FILE VÀ THƯ MỤC TRONG /home
# ==========================================================

FILE_SEARCH_ROOT = Path("/home")
FILE_SEARCH_LIMIT = 20

FILE_SEARCH_SKIP_NAMES = {
    ".cache",
    ".git",
    "__pycache__",
    "node_modules",
    ".npm",
    ".cargo",
    ".rustup",
    "Trash",
}


def _should_skip_file_search_dir(path):
    """Bỏ qua cache/source dependency lớn để việc tìm kiếm nhanh hơn."""
    path = Path(path)

    if any(part in FILE_SEARCH_SKIP_NAMES for part in path.parts):
        return True

    # Trash nằm sâu trong ~/.local/share/Trash.
    if ".local" in path.parts and "share" in path.parts and "Trash" in path.parts:
        return True

    return False


def search_home_paths(keyword, item_type="any", limit=FILE_SEARCH_LIMIT):
    """
    Tìm theo tên trong /home.

    item_type:
      - "file": chỉ file
      - "folder": chỉ thư mục
      - "any": cả hai
    """
    keyword = str(keyword).strip().lower()

    if not keyword:
        return []

    results = []

    for root, dirs, files in os.walk(
        FILE_SEARCH_ROOT,
        topdown=True,
        followlinks=False,
        onerror=lambda _error: None,
    ):
        root_path = Path(root)

        dirs[:] = [
            dirname
            for dirname in dirs
            if not _should_skip_file_search_dir(root_path / dirname)
        ]

        if item_type in {"folder", "any"}:
            for dirname in dirs:
                if keyword in dirname.lower():
                    results.append({
                        "path": root_path / dirname,
                        "type": "folder",
                    })
                    if len(results) >= limit:
                        return results

        if item_type in {"file", "any"}:
            for filename in files:
                if keyword in filename.lower():
                    results.append({
                        "path": root_path / filename,
                        "type": "file",
                    })
                    if len(results) >= limit:
                        return results

    return results


def format_file_search_results(results, title):
    lines = [f"🔎 **{title}**", ""]

    for index, item in enumerate(results, start=1):
        kind = "📁" if item["type"] == "folder" else "📄"
        lines.append(f"{index}. {kind} `{item['path']}`")

    lines.extend([
        "",
        "Dùng `mở file 2` hoặc `mở thư mục 2` để mở kết quả theo số.",
    ])

    return "\n".join(lines)


def open_filesystem_path(path):
    """Mở file/thư mục bằng ứng dụng mặc định; chỉ cho phép đường dẫn trong /home."""
    path = Path(path).expanduser()

    try:
        resolved = path.resolve()
        root = FILE_SEARCH_ROOT.resolve()
    except Exception as error:
        message = f"❌ Không thể kiểm tra đường dẫn: {error}"
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    if resolved != root and root not in resolved.parents:
        message = "❌ Jarvis chỉ được phép mở file/thư mục bên trong /home."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    if not resolved.exists():
        message = "❌ File hoặc thư mục không còn tồn tại."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    try:
        previous_window_ids = FILE_WINDOWS.snapshot_ids()
        subprocess.Popen(
            ["xdg-open", str(resolved)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        tracked = FILE_WINDOWS.track_opened_path(resolved, previous_window_ids)
        kind = "thư mục" if resolved.is_dir() else "file"
        message = f"✅ Đã mở {kind}: {resolved}"
        if not tracked:
            close_command = "tắt thư mục" if resolved.is_dir() else "tắt file"
            message += f"\n⚠️ Desktop không cung cấp mã cửa sổ; có thể chưa dùng được `{close_command}`."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    except Exception as error:
        message = f"❌ Không thể mở {resolved}: {error}"
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False


def search_and_maybe_open_path(keyword, item_type, open_when_unique=False):
    """Tìm file/thư mục và lưu danh sách để Terminal/Discord chọn theo số."""
    global file_search_results

    type_name = "FILE" if item_type == "file" else "THƯ MỤC"

    print()
    print(f"Jarvis: Đang tìm {type_name.lower()} '{keyword}' trong /home...")

    results = search_home_paths(keyword, item_type=item_type)
    file_search_results = results

    if not results:
        message = f"❌ Không tìm thấy {type_name.lower()} có tên chứa '{keyword}' trong /home."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if open_when_unique and len(results) == 1:
        return open_filesystem_path(results[0]["path"])

    title = f"KẾT QUẢ {type_name}: {keyword}"
    response = format_file_search_results(results, title)

    print()
    print("=" * 60)
    print(f"   {title}")
    print("=" * 60)
    for index, item in enumerate(results, start=1):
        kind = "[THƯ MỤC]" if item["type"] == "folder" else "[FILE]"
        print(f"{index:2}. {kind} {item['path']}")
    print("=" * 60)
    print('Jarvis: Dùng "mở file 2" hoặc "mở thư mục 2".')

    set_command_response(response)
    return True


def open_file_search_result(number, expected_type=None):
    """Mở kết quả theo số từ lần tìm file/thư mục gần nhất."""
    if not file_search_results:
        message = "❌ Chưa có danh sách file/thư mục. Hãy dùng `tìm file <tên>` trước."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if number < 1 or number > len(file_search_results):
        message = f"❌ Số không hợp lệ. Hãy chọn từ 1 đến {len(file_search_results)}."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    selected = file_search_results[number - 1]

    if expected_type is not None and selected["type"] != expected_type:
        actual = "thư mục" if selected["type"] == "folder" else "file"
        message = f"❌ Kết quả số {number} là {actual}, không đúng loại bạn yêu cầu."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    open_filesystem_path(selected["path"])
    return True


def handle_filesystem_command(command):
    """
    Xử lý lệnh file trước bộ tìm YouTube để tránh:
      'tìm file report' -> bị hiểu thành tìm video YouTube.
    """
    command_clean = command.strip()

    # Chỉ đóng cửa sổ file gần nhất do Jarvis mở, không kill toàn bộ ứng dụng.
    if re.fullmatch(
        r"(?:tắt|tat|đóng|dong)\s+(?:cửa\s*sổ\s+)?file(?:\s+gần\s+nhất)?",
        command_clean,
        re.IGNORECASE,
    ):
        success, detail = FILE_WINDOWS.close_last_file()
        icon = "✅" if success else "❌"
        message = f"{icon} {detail}"
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if re.fullmatch(
        r"(?:tắt|tat|đóng|dong)\s+(?:cửa\s*sổ\s+)?(?:thư\s*mục|thu\s*muc|project)(?:\s+gần\s+nhất)?",
        command_clean,
        re.IGNORECASE,
    ):
        success, detail = FILE_WINDOWS.close_last_folder()
        icon = "✅" if success else "❌"
        message = f"{icon} {detail}"
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    # Chọn kết quả theo số.
    match = re.fullmatch(
        r"(?:mở|mo)\s+(file|thư\s*mục|thu\s*muc)\s+(\d+)",
        command_clean,
        re.IGNORECASE,
    )
    if match:
        kind = match.group(1).lower()
        number = int(match.group(2))
        expected_type = "file" if kind == "file" else "folder"
        return open_file_search_result(number, expected_type)

    # Tìm file.
    match = re.fullmatch(
        r"(?:tìm|tim)\s+file\s+(.+)",
        command_clean,
        re.IGNORECASE,
    )
    if match:
        return search_and_maybe_open_path(
            match.group(1).strip(),
            item_type="file",
            open_when_unique=False,
        )

    # Mở file theo tên: nếu chỉ có một kết quả thì mở ngay, nhiều kết quả thì liệt kê.
    match = re.fullmatch(
        r"(?:mở|mo)\s+file\s+(.+)",
        command_clean,
        re.IGNORECASE,
    )
    if match:
        return search_and_maybe_open_path(
            match.group(1).strip(),
            item_type="file",
            open_when_unique=True,
        )

    # Tìm thư mục.
    match = re.fullmatch(
        r"(?:tìm|tim)\s+(?:thư\s*mục|thu\s*muc)\s+(.+)",
        command_clean,
        re.IGNORECASE,
    )
    if match:
        return search_and_maybe_open_path(
            match.group(1).strip(),
            item_type="folder",
            open_when_unique=False,
        )

    # Mở thư mục theo tên.
    match = re.fullmatch(
        r"(?:mở|mo)\s+(?:thư\s*mục|thu\s*muc)\s+(.+)",
        command_clean,
        re.IGNORECASE,
    )
    if match:
        return search_and_maybe_open_path(
            match.group(1).strip(),
            item_type="folder",
            open_when_unique=True,
        )

    return False


# ==========================================================
# HIỆN DESKTOP - KHÔNG TẮT ỨNG DỤNG
# ==========================================================

def show_desktop():
    """Ẩn/minimize các cửa sổ để hiện Desktop mà không đóng Chrome."""

    # Cách 1: wmctrl - phù hợp X11 và một số phiên Ubuntu.
    try:
        result = subprocess.run(
            ["wmctrl", "-k", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        if result.returncode == 0:
            print(
                "Jarvis: Đã về Desktop. Chrome vẫn đang chạy."
            )
            return

    except FileNotFoundError:
        pass

    # Cách 2: mô phỏng phím Super+D nếu xdotool có sẵn.
    try:
        result = subprocess.run(
            ["xdotool", "key", "super+d"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        if result.returncode == 0:
            print(
                "Jarvis: Đã về Desktop. Chrome vẫn đang chạy."
            )
            return

    except FileNotFoundError:
        pass

    print()
    print(
        "Jarvis: Chưa thể điều khiển Desktop trên phiên làm việc hiện tại."
    )
    print(
        "Jarvis: Cài wmctrl bằng: sudo apt install wmctrl"
    )



# ==========================================================
# PHẢN HỒI DÙNG CHUNG CHO DISCORD
# ==========================================================

def set_command_response(message):
    """Lưu phản hồi cuối để Discord có thể gửi lại cho người dùng."""
    global last_command_response
    last_command_response = message


def format_youtube_list(title, videos):
    lines = [f"📺 **{title}**", ""]

    for index, video in enumerate(videos, start=1):
        lines.append(f"{index}. {video['title']}")

    lines.extend([
        "",
        "Dùng `mở video 3` hoặc `mở video <tên>`.",
    ])

    return "\n".join(lines)


# ==========================================================
# ÂM LƯỢNG UBUNTU
# ==========================================================

def get_current_volume():
    """Trả về mức âm lượng của output mặc định theo phần trăm."""
    try:
        result = subprocess.run(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return None

        match = re.search(r"(\d+)%", result.stdout)

        if not match:
            return None

        return int(match.group(1))

    except FileNotFoundError:
        return None


def get_mute_state():
    try:
        result = subprocess.run(
            ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return None

        output = result.stdout.lower()

        if "yes" in output:
            return True

        if "no" in output:
            return False

        return None

    except FileNotFoundError:
        return None


def set_volume(value):
    value = max(0, min(100, int(value)))

    try:
        result = subprocess.run(
            [
                "pactl",
                "set-sink-volume",
                "@DEFAULT_SINK@",
                f"{value}%",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return None

        # Đặt âm lượng cũng tự bật tiếng nếu trước đó đang mute.
        subprocess.run(
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        return get_current_volume()

    except FileNotFoundError:
        return None


def change_volume(delta):
    current = get_current_volume()

    if current is None:
        return None, None

    new_volume = max(0, min(100, current + int(delta)))
    actual = set_volume(new_volume)

    return current, actual


def set_mute(muted):
    try:
        result = subprocess.run(
            [
                "pactl",
                "set-sink-mute",
                "@DEFAULT_SINK@",
                "1" if muted else "0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        return result.returncode == 0

    except FileNotFoundError:
        return False


def handle_volume_command(command):
    """
    Xử lý lệnh âm lượng.
    Trả True nếu command là lệnh âm lượng, False nếu không phải.
    """
    command_clean = command.strip()
    command_lower = command_clean.lower()

    # Xem âm lượng hiện tại
    current_commands = {
        "âm lượng",
        "am luong",
        "âm lượng hiện tại",
        "am luong hien tai",
        "mức âm lượng",
        "muc am luong",
        "volume",
        "volume hiện tại",
        "volume hien tai",
        "current volume",
        "cho tôi biết âm lượng",
        "cho toi biet am luong",
    }

    if command_lower in current_commands:
        volume = get_current_volume()

        if volume is None:
            message = (
                "❌ Jarvis không đọc được âm lượng. "
                "Hãy kiểm tra lệnh `pactl` trên Ubuntu."
            )
        else:
            muted = get_mute_state()

            if muted:
                message = f"🔇 Âm lượng hiện tại: {volume}% (đang tắt tiếng)"
            else:
                message = f"🔊 Âm lượng hiện tại: {volume}%"

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    # Đặt âm lượng trực tiếp: âm lượng 50 / đặt âm lượng 50
    set_match = re.fullmatch(
        r"(?:đặt\s+|dat\s+|set\s+)?(?:âm\s+lượng|am\s+luong|volume)\s+(\d{1,3})%?",
        command_clean,
        re.IGNORECASE,
    )

    if set_match:
        requested = int(set_match.group(1))
        requested = max(0, min(100, requested))
        actual = set_volume(requested)

        if actual is None:
            message = "❌ Jarvis không thể đặt âm lượng."
        else:
            message = f"🔊 Đã đặt âm lượng: {actual}%"

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    # Tăng âm lượng; mặc định +5%
    increase_match = re.fullmatch(
        r"(?:tăng\s+âm\s+lượng|tang\s+am\s+luong|volume\s+up|tăng|tang)"
        r"(?:\s+(\d{1,3})%?)?",
        command_clean,
        re.IGNORECASE,
    )

    if increase_match:
        amount = int(increase_match.group(1) or 5)
        before, after = change_volume(amount)

        if after is None:
            message = "❌ Jarvis không thể tăng âm lượng."
        else:
            message = f"🔊 Đã tăng âm lượng: {before}% → {after}%"

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    # Giảm âm lượng; mặc định -5%
    decrease_match = re.fullmatch(
        r"(?:giảm\s+âm\s+lượng|giam\s+am\s+luong|volume\s+down|giảm|giam)"
        r"(?:\s+(\d{1,3})%?)?",
        command_clean,
        re.IGNORECASE,
    )

    if decrease_match:
        amount = int(decrease_match.group(1) or 5)
        before, after = change_volume(-amount)

        if after is None:
            message = "❌ Jarvis không thể giảm âm lượng."
        else:
            message = f"🔊 Đã giảm âm lượng: {before}% → {after}%"

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if command_lower in {
        "tắt tiếng", "tat tieng", "mute",
    }:
        if set_mute(True):
            message = "🔇 Đã tắt tiếng."
        else:
            message = "❌ Jarvis không thể tắt tiếng."

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if command_lower in {
        "bật tiếng", "bat tieng", "unmute",
    }:
        if set_mute(False):
            volume = get_current_volume()
            if volume is None:
                message = "🔊 Đã bật tiếng."
            else:
                message = f"🔊 Đã bật tiếng. Âm lượng hiện tại: {volume}%"
        else:
            message = "❌ Jarvis không thể bật tiếng."

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    return False


# ==========================================================
# HẸN GIỜ SLEEP SÂU / SUSPEND UBUNTU
# ==========================================================

deep_sleep_task = None


def parse_deep_sleep_delay(command):
    """
    Hiểu các lệnh dạng:
      sleep sâu sau 1h
      đặt lịch sleep sâu sau 1 giờ
      hẹn sleep sâu sau 30 phút
      sleep sâu sau 30p
      sleep sâu sau 45s
      sleep sâu sau 1h30p
      sleep sâu sau 1h 20p 15s

    Đơn vị:
      h = giờ
      p = phút
      s = giây
    """
    command_clean = normalize_core_text(command)

    match = re.fullmatch(
        r"(?:(?:dat lich|dat|hen gio|hen)\s+)?"
        r"(?:sleep sau|ngu sau|suspend)(?:\s+(?:sau|after))?\s+(.+)",
        command_clean,
        re.IGNORECASE,
    )

    if not match:
        return None

    duration_text = match.group(1).lower()
    # Chấp nhận cả ký hiệu ngắn và đơn vị tiếng Việt/Anh.
    unit_aliases = (
        (r"(?:gio|tieng|hours?|hrs?)", "h"),
        (r"(?:phut|minutes?|mins?)", "p"),
        (r"(?:giay|seconds?|secs?)", "s"),
    )
    for pattern, replacement in unit_aliases:
        duration_text = re.sub(pattern, replacement, duration_text)
    duration_text = re.sub(r"\s+", "", duration_text)

    duration_match = re.fullmatch(
        r"(?:(\d+)h)?(?:(\d+)p)?(?:(\d+)s)?",
        duration_text,
        re.IGNORECASE,
    )

    if not duration_match:
        return None

    hours_text, minutes_text, seconds_text = duration_match.groups()

    if hours_text is None and minutes_text is None and seconds_text is None:
        return None

    hours = int(hours_text or 0)
    minutes = int(minutes_text or 0)
    seconds = int(seconds_text or 0)

    total_seconds = hours * 3600 + minutes * 60 + seconds

    if total_seconds <= 0:
        return None

    return total_seconds


def format_deep_sleep_time(seconds):
    """Hiển thị thời gian theo đúng ký hiệu h / p / s."""
    parts = []

    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}p")
    if secs:
        parts.append(f"{secs}s")

    return " ".join(parts) if parts else "0s"


async def deep_sleep_after_delay(seconds):
    """Đợi đủ thời gian rồi đưa Ubuntu vào Sleep sâu."""
    global deep_sleep_task

    try:
        await asyncio.sleep(seconds)

        print("Jarvis: Đã đến giờ. Đang chuyển Ubuntu sang Sleep sâu...")

        # Hàm này đã có sẵn trong Jarvis và gọi systemctl suspend.
        success = await asyncio.to_thread(deep_sleep_machine)

        if not success:
            print("Jarvis: Không thể đưa Ubuntu vào Sleep sâu.")

    except asyncio.CancelledError:
        raise

    except Exception as error:
        print(f"Jarvis: Lỗi khi hẹn Sleep sâu: {error}")

    finally:
        deep_sleep_task = None


async def handle_deep_sleep_timer_command(command):
    global deep_sleep_task

    # So khớp trên chuỗi bỏ dấu để mọi cách gõ "hủy/huỷ/huy" và
    # "sâu/sau" đều chạy cùng một lệnh thay vì rơi xuống phần gợi ý.
    command_lower = normalize_core_text(command)

    cancel_commands = {
        "huy sleep sau",
        "huy ngu sau",
        "huy suspend",
        "cancel sleep sau",
        "cancel deep sleep",
    }

    status_commands = {
        "lich sleep sau",
        "lich ngu sau",
        "sleep sau status",
        "deep sleep status",
    }

    if command_lower in cancel_commands:
        if deep_sleep_task is None or deep_sleep_task.done():
            message = "ℹ️ Hiện không có lịch Sleep sâu."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return True

        deep_sleep_task.cancel()
        deep_sleep_task = None

        message = "✅ Đã hủy lịch Sleep sâu."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if command_lower in status_commands:
        if deep_sleep_task is None or deep_sleep_task.done():
            message = "ℹ️ Hiện không có lịch Sleep sâu."
        else:
            message = "⏱️ Đang có một lịch Sleep sâu hoạt động."

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    seconds = parse_deep_sleep_delay(command)
    if seconds is None:
        incomplete_timer = re.match(
            r"(?:(?:dat lich|dat|hen gio|hen)\s+)?"
            r"(?:sleep sau|ngu sau|suspend)(?:\s+(?:sau|after))?(?:\s|$)",
            command_lower,
        )
        if incomplete_timer:
            message = (
                "ℹ️ Thời gian Sleep chưa đầy đủ hoặc chưa hợp lệ. Ví dụ: "
                "`đặt lịch sleep sâu sau 1 giờ` hoặc `sleep sâu sau 30p`."
            )
            print(f"Jarvis: {message}")
            set_command_response(message)
            return True
        return False

    if deep_sleep_task is not None and not deep_sleep_task.done():
        deep_sleep_task.cancel()

    deep_sleep_task = asyncio.create_task(deep_sleep_after_delay(seconds))

    duration_text = format_deep_sleep_time(seconds)
    message = f"⏱️ Đã đặt lịch Sleep sâu sau {duration_text}."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


# ==========================================================
# 2 CHẾ ĐỘ SLEEP UBUNTU
# ==========================================================

DEEP_SLEEP_COMMANDS = {
    "sleep sâu",
    "sleep sau",
    "ngủ sâu",
    "ngu sau",
    "ngủ máy",
    "ngu may",
    "cho máy ngủ",
    "cho may ngu",
    "suspend",
    "suspend máy",
    "suspend may",
}

SCREEN_SLEEP_COMMANDS = {
    "sleep",
    "sleep máy",
    "sleep may",
    "sleep màn hình",
    "sleep man hinh",
    "ngủ màn hình",
    "ngu man hinh",
    "tắt màn hình",
    "tat man hinh",
    "screen off",
    "display off",
}

SCREEN_WAKE_COMMANDS = {
    "mở màn hình",
    "mo man hinh",
    "bật màn hình",
    "bat man hinh",
    "đánh thức màn hình",
    "danh thuc man hinh",
    "wake screen",
    "screen on",
}


def is_deep_sleep_command(command):
    return command.strip().lower() in DEEP_SLEEP_COMMANDS


def is_screen_sleep_command(command):
    return command.strip().lower() in SCREEN_SLEEP_COMMANDS


def is_screen_wake_command(command):
    return command.strip().lower() in SCREEN_WAKE_COMMANDS


def wake_screen():
    """Bật lại màn hình nhưng không bỏ qua màn hình khóa/mật khẩu."""
    session_type = os.getenv("XDG_SESSION_TYPE", "").strip().lower()

    if session_type == "x11":
        try:
            result = subprocess.run(
                ["xset", "dpms", "force", "on"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            pass

    # GNOME/Wayland: bỏ trạng thái blank. Nếu phiên đã khóa, màn hình đăng
    # nhập vẫn được giữ nguyên và người dùng vẫn phải xác thực.
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.ScreenSaver",
                "--object-path", "/org/gnome/ScreenSaver",
                "--method", "org.gnome.ScreenSaver.SetActive",
                "false",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def deep_sleep_machine():
    """Suspend toàn máy. CPU/app/nhạc tạm dừng cho tới khi máy thức lại."""
    try:
        print("Jarvis: Đang đưa máy vào Sleep sâu...")

        subprocess.Popen(
            ["systemctl", "suspend"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return True

    except FileNotFoundError:
        print("Jarvis: Không tìm thấy lệnh systemctl.")
        return False

    except Exception as error:
        print(f"Jarvis: Không thể Sleep sâu: {error}")
        return False


def sleep_screen_only():
    """
    Chỉ blank/tắt màn hình, không suspend hệ thống.
    Nhạc, Discord và Jarvis vẫn tiếp tục chạy.

    - X11: dùng xset DPMS.
    - GNOME/Wayland: dùng org.gnome.ScreenSaver qua session D-Bus.

    Lưu ý: Wayland vẫn có thể có biến DISPLAY do XWayland, vì vậy KHÔNG
    được dùng DISPLAY để quyết định chạy xset.
    """
    session_type = os.getenv("XDG_SESSION_TYPE", "").strip().lower()

    print(f"Jarvis: Phiên hiển thị hiện tại: {session_type or 'không xác định'}")

    # X11 thật sự: tắt display bằng DPMS, không suspend máy.
    if session_type == "x11":
        try:
            result = subprocess.run(
                ["xset", "dpms", "force", "off"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                print("Jarvis: Đã tắt màn hình bằng X11 DPMS. Nhạc vẫn tiếp tục chạy.")
                return True
            print(f"Jarvis: xset thất bại (code {result.returncode}).")
        except FileNotFoundError:
            print("Jarvis: Không tìm thấy xset, chuyển sang GNOME D-Bus...")

    # GNOME/Wayland (và fallback khi session type chưa xác định):
    # SetActive(true) blank màn hình nhưng không suspend máy.
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.ScreenSaver",
                "--object-path", "/org/gnome/ScreenSaver",
                "--method", "org.gnome.ScreenSaver.SetActive",
                "true",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            print("Jarvis: Đã sleep màn hình bằng GNOME D-Bus. Nhạc/Jarvis/Discord vẫn chạy.")
            return True

        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            print(f"Jarvis: GNOME D-Bus thất bại: {detail}")
        else:
            print(f"Jarvis: GNOME D-Bus thất bại (code {result.returncode}).")

    except FileNotFoundError:
        print("Jarvis: Không tìm thấy gdbus.")

    print("Jarvis: Không thể tắt màn hình trên phiên desktop hiện tại.")
    return False


# ==========================================================
# HELP
# ==========================================================

def show_help():
    print()
    print("Jarvis có thể:")
    print()
    print("  mở youtube")
    print("  mở youtube cá nhân")
    print("  mở youtube học")
    print()
    print("  mở video 1")
    print("  mở video 2")
    print("  mở video sekiro")
    print("  dừng video")
    print("  phát tiếp video")
    print("  làm mới youtube")
    print("  quay lại youtube")
    print("  hiện youtube")
    print("  tắt youtube")
    print("  đóng youtube")
    print("  tìm sekiro")
    print("  tìm video sekiro")
    print("  tìm youtube sekiro boss")
    print("  youtube tìm minecraft mod")
    print("  về trang chủ")
    print("  trang chủ youtube")
    print("  home youtube")
    print("  ...")
    print()
    print("  google sekiro")
    print("  tìm google transistor")
    print()
    print("  mở chatgpt học")
    print("  mở chatgpt cá nhân")
    print("  mở gmail học")
    print("  mở drive học")
    print("  mở calendar học")
    print("  mở github học")
    print("  mở github cá nhân")
    print("  mở chrome học")
    print("  mở chrome cá nhân")
    print()
    print("  mở github")
    print("  mở vscode")
    print("  mở terminal")
    print("  tắt vscode")
    print("  tắt chrome")
    print("  tắt chrome cá nhân    # không tắt Chrome học")
    print("  tắt chrome học        # không tắt Chrome cá nhân")
    print("  tắt file manager")
    print("  tắt tất cả      # giữ Terminal/Jarvis tiếp tục chạy")
    print()
    print("  mở downloads")
    print("  mở documents")
    print("  mở pictures")
    print("  mở home")
    print()
    print("  tìm file <tên>")
    print("  mở file <tên>")
    print("  mở file <số>")
    print("  tắt file             # chỉ đóng cửa sổ file Jarvis vừa mở")
    print("  tắt thư mục/project  # chỉ đóng cửa sổ thư mục Jarvis vừa mở")
    print("  tìm thư mục <tên>")
    print("  mở thư mục <tên>")
    print("  mở thư mục <số>")
    print("  tìm thông minh <tên>  # xếp hạng theo độ khớp và độ mới")
    print("  file gần đây")
    print()
    print("  ghi nhớ <nội dung>")
    print("  nhớ lại <từ khóa>")
    print("  xem bộ nhớ")
    print("  quên <từ khóa>")
    print()
    print("  tình trạng hệ thống")
    print()
    print("  về desktop")
    print("  ra desktop")
    print("  hiện desktop")
    print()
    print("  âm lượng")
    print("  âm lượng hiện tại")
    print("  âm lượng 50")
    print("  tăng âm lượng")
    print("  tăng âm lượng 10")
    print("  tăng / tăng 10       # dạng ngắn của tăng âm lượng")
    print("  giảm âm lượng")
    print("  giảm âm lượng 10")
    print("  giảm / giảm 10       # dạng ngắn của giảm âm lượng")
    print("  tắt tiếng")
    print("  bật tiếng")
    print()
    print("  sleep           # chỉ tắt màn hình, nhạc vẫn chạy")
    print("  mở màn hình     # bật màn hình, vẫn giữ khóa/mật khẩu")
    print("  sleep sâu       # Sleep sâu ngay lập tức")
    print("  sleep sâu sau 1h")
    print("  sleep sâu sau 30p")
    print("  sleep sâu sau 45s")
    print("  sleep sâu sau 1h30p")
    print("  hủy sleep sâu")
    print("  lịch sleep sâu")
    print("  tắt màn hình")
    print()
    print("  thoát")

    # Terminal nhận danh sách chi tiết phía trên; Discord cần một phản hồi được
    # lưu riêng, nếu không bot chỉ gửi câu mặc định "đã xử lý lệnh".
    set_command_response(
        "🧰 **JARVIS CÓ THỂ**\n\n"
        "🌐 **Web:** mở/tìm YouTube, Google, ChatGPT, Gmail, Drive, "
        "Calendar và GitHub theo profile học hoặc cá nhân.\n"
        "🖥️ **Ứng dụng:** mở/đóng VS Code, Terminal, Chrome và hiện desktop.\n"
        "📁 **File:** tìm/mở file, thư mục, project và xem file gần đây.\n"
        "🧠 **Bộ nhớ:** ghi nhớ, nhớ lại, xem hoặc quên thông tin.\n"
        "📊 **Hệ thống:** xem CPU, RAM, ổ đĩa và uptime.\n"
        "🔊 **Âm thanh:** xem, tăng, giảm, đặt âm lượng và bật/tắt tiếng.\n"
        "🌙 **Nguồn:** tắt màn hình, Sleep sâu, đặt hoặc hủy lịch Sleep.\n\n"
        "Gõ `help` trên Terminal để xem toàn bộ cú pháp và ví dụ."
    )
    print()


# ==========================================================
# COMMAND ROUTER
# ==========================================================

def _command_from_local_ai_decision(decision, original_command):
    """Map an AI decision onto an existing, reviewed Jarvis command."""
    action = decision.get("action")
    args = decision.get("args", {})
    profile_names = {"study": "học", "personal": "cá nhân"}
    plain = normalize_core_text(original_command)

    if action == "open_site":
        site = str(args.get("site", "")).lower()
        profile = profile_names.get(str(args.get("profile", "")).lower())
        # Không thực thi khi AI chỉ đoán một tên site bị gõ sai.
        if site in CHROME_SHORTCUTS and site in plain and profile:
            return f"mở {site} {profile}"
    elif action == "close_chrome":
        profile = profile_names.get(str(args.get("profile", "")).lower())
        if profile and ("chrome" in plain or "trinh duyet" in plain):
            return f"tắt chrome {profile}"
    elif action == "open_vscode":
        if "vscode" in plain or "visual studio code" in plain:
            return "mở vscode"
    elif action == "close_vscode":
        if "vscode" in plain or "visual studio code" in plain:
            return "tắt vscode"
    elif action == "show_desktop":
        if "desktop" in plain:
            return "hiện desktop"
    elif action == "system_status":
        status_terms = ("tinh trang", "he thong", "cpu", "ram", "o dia", "uptime")
        if any(term in plain for term in status_terms):
            return "tình trạng hệ thống"
    return None


def _suggest_local_ai_command(decision):
    """Turn an uncertain AI action into a confirmation instead of executing it."""
    action = decision.get("action")
    args = decision.get("args", {})
    profile = "học" if args.get("profile") == "study" else "cá nhân"
    if action == "open_site" and args.get("site") in CHROME_SHORTCUTS:
        site = args["site"]
        return f"🤔 Có phải bạn muốn `mở {site} {profile}`?"
    if action == "close_chrome":
        return f"🤔 Có phải bạn muốn `tắt chrome {profile}`?"
    suggestions = {
        "open_vscode": "mở vscode",
        "close_vscode": "tắt vscode",
        "show_desktop": "hiện desktop",
        "system_status": "tình trạng hệ thống",
    }
    if action in suggestions:
        return f"🤔 Có phải bạn muốn `{suggestions[action]}`?"
    return None


COMMAND_SUGGESTION_CATALOG = (
    # Trợ giúp
    ("help", "help"),
    ("tro giup", "help"),
    ("chuc nang", "help"),
    # YouTube và tìm kiếm web
    ("mo youtube", "mở youtube học"),
    ("mo youtube hoc", "mở youtube học"),
    ("mo youtube ca nhan", "mở youtube cá nhân"),
    ("mo video", "mở video <số hoặc tên>"),
    ("dung video", "dừng video"),
    ("tam dung video", "dừng video"),
    ("pause video", "dừng video"),
    ("phat tiep video", "phát tiếp video"),
    ("tiep tuc video", "phát tiếp video"),
    ("resume video", "phát tiếp video"),
    ("lam moi youtube", "làm mới youtube"),
    ("quay lai youtube", "quay lại youtube"),
    ("hien youtube", "hiện youtube"),
    ("tat youtube", "tắt youtube"),
    ("dong youtube", "đóng youtube"),
    ("tim youtube", "tìm youtube <nội dung>"),
    ("tim video", "tìm video <nội dung>"),
    ("trang chu youtube", "trang chủ youtube"),
    ("home youtube", "home youtube"),
    ("google", "google <nội dung>"),
    ("tim google", "tìm google <nội dung>"),
    # Website theo Chrome profile
    ("mo chatgpt hoc", "mở chatgpt học"),
    ("mo chatgpt ca nhan", "mở chatgpt cá nhân"),
    ("mo gmail hoc", "mở gmail học"),
    ("mo gmail ca nhan", "mở gmail cá nhân"),
    ("mo drive hoc", "mở drive học"),
    ("mo drive ca nhan", "mở drive cá nhân"),
    ("mo calendar hoc", "mở calendar học"),
    ("mo calendar ca nhan", "mở calendar cá nhân"),
    ("mo github hoc", "mở github học"),
    ("mo github ca nhan", "mở github cá nhân"),
    ("mo chrome hoc", "mở chrome học"),
    ("mo chrome ca nhan", "mở chrome cá nhân"),
    ("tat chrome hoc", "tắt chrome học"),
    ("tat chrome ca nhan", "tắt chrome cá nhân"),
    # Ứng dụng và cửa sổ
    ("mo github", "mở github"),
    ("mo vscode", "mở vscode"),
    ("tat vscode", "tắt vscode"),
    ("mo terminal", "mở terminal"),
    ("tat chrome", "tắt chrome <học hoặc cá nhân>"),
    ("tat file manager", "tắt file manager"),
    ("tat tat ca", "tắt tất cả"),
    # Thư mục, file và project
    ("mo downloads", "mở downloads"),
    ("mo documents", "mở documents"),
    ("mo pictures", "mở pictures"),
    ("mo home", "mở home"),
    ("tim file", "tìm file <tên>"),
    ("tim tep", "tìm file <tên>"),
    ("mo file", "mở file <tên hoặc số>"),
    ("mo tep", "mở file <tên hoặc số>"),
    ("tat file", "tắt file"),
    ("tim thu muc", "tìm thư mục <tên>"),
    ("mo thu muc", "mở thư mục <tên hoặc số>"),
    ("tat thu muc", "tắt thư mục"),
    ("tim project", "tìm thư mục <tên project>"),
    ("mo project", "mở thư mục <tên project>"),
    ("tat project", "tắt project"),
    ("tim thong minh", "tìm thông minh <tên>"),
    ("file gan day", "file gần đây"),
    # Bộ nhớ
    ("ghi nho", "ghi nhớ <nội dung>"),
    ("nho rang", "ghi nhớ <nội dung>"),
    ("nho lai", "nhớ lại <từ khóa>"),
    ("xem bo nho", "xem bộ nhớ"),
    ("quen", "quên <từ khóa>"),
    ("xoa bo nho", "quên <từ khóa>"),
    # Hệ thống và desktop
    ("tinh trang he thong", "tình trạng hệ thống"),
    ("kiem tra he thong", "tình trạng hệ thống"),
    ("kiem tra may", "tình trạng hệ thống"),
    ("hien desktop", "hiện desktop"),
    ("ve desktop", "về desktop"),
    ("ra desktop", "ra desktop"),
    # Âm lượng
    ("am luong", "âm lượng"),
    ("am luong hien tai", "âm lượng hiện tại"),
    ("dat am luong", "âm lượng <0-100>"),
    ("tang am luong", "tăng âm lượng <số>"),
    ("giam am luong", "giảm âm lượng <số>"),
    ("tat tieng", "tắt tiếng"),
    ("bat tieng", "bật tiếng"),
    # Màn hình và Sleep
    ("sleep man hinh", "sleep màn hình"),
    ("tat man hinh", "tắt màn hình"),
    ("mo man hinh", "mở màn hình"),
    ("bat man hinh", "bật màn hình"),
    ("sleep sau", "sleep sâu"),
    ("ngu sau", "sleep sâu"),
    ("suspend", "sleep sâu"),
    ("sleep sau sau", "sleep sâu sau <thời gian>"),
    ("hen sleep sau", "sleep sâu sau <thời gian>"),
    ("dat lich sleep sau", "đặt lịch sleep sâu sau <thời gian>"),
    ("hen gio sleep sau", "hẹn giờ sleep sâu sau <thời gian>"),
    ("huy sleep sau", "hủy sleep sâu"),
    ("cancel sleep", "hủy sleep sâu"),
    ("lich sleep sau", "lịch sleep sâu"),
    ("sleep status", "lịch sleep sâu"),
    # Thoát chỉ dùng trực tiếp trên Ubuntu; Discord sẽ tiếp tục chặn lệnh này.
    ("thoat", "thoát"),
    ("tam biet", "thoát"),
)


def _suggest_known_command(command):
    """Suggest a reviewed command for questions, incomplete input, and typos."""
    plain = normalize_core_text(command)
    words = plain.split()

    def has_word_like(target, threshold=0.72):
        return any(SequenceMatcher(None, word, target).ratio() >= threshold for word in words)

    mentions_sleep = has_word_like("sleep") or "ngu sau" in plain or "suspend" in plain
    wants_cancel = (
        has_word_like("huy")
        or "cancel" in words
        or "dung lai" in plain
        or "ngung" in words
        or "bo lich" in plain
    )
    if mentions_sleep and wants_cancel:
        return (
            "💡 Để hủy lịch Sleep của máy, hãy gõ `hủy sleep sâu`. "
            "Nếu chưa đặt lịch, Jarvis sẽ báo không có lịch Sleep sâu."
        )

    # Bỏ các từ thường dùng khi người dùng đang hỏi cách viết một lệnh.
    candidate = re.sub(
        r"^(?:jarvis\s+)?(?:toi quen lenh|quen mat lenh|toi quen|quen mat|lenh|toi muon|lam sao de|cach|cho toi hoi)\s+",
        "",
        plain,
    ).strip()
    best_command = None
    best_score = 0.0
    for alias, canonical in COMMAND_SUGGESTION_CATALOG:
        full_score = SequenceMatcher(None, candidate, alias).ratio()
        prefix = candidate[:min(len(candidate), len(alias) + 2)]
        prefix_score = SequenceMatcher(None, prefix, alias).ratio()
        score = max(full_score, prefix_score)
        if score > best_score:
            best_command = canonical
            best_score = score
    if best_command is not None and best_score >= 0.74:
        return f"🤔 Có phải bạn muốn dùng lệnh `{best_command}`?"
    return None


async def route_command(command, *, allow_local_ai=True):
    command = command.strip()

    # Lõi module hóa xử lý memory, tìm file thông minh, natural-language
    # aliases và system monitoring. Lệnh chưa nhận diện tiếp tục qua router 1.0.
    core_response = CORE.handle(command)
    if core_response is not None:
        print(f"Jarvis: {core_response}")
        set_command_response(core_response)
        return True

    command = CORE.brain.normalize_command(command)

    # Người dùng thường thêm dấu câu khi nhập tự nhiên trên Terminal/Discord.
    # Dấu cuối câu không làm thay đổi ý nghĩa của một lệnh chính xác.
    command = re.sub(r"[?!.,;:]+$", "", command).strip()

    command_lower = command.lower()

    if not command_lower:
        return True

    # ------------------------------------------------------
    # BẬT LẠI MÀN HÌNH - KHÔNG BỎ QUA KHÓA/MẬT KHẨU
    # ------------------------------------------------------

    if is_screen_wake_command(command):
        if wake_screen():
            set_command_response("💡 Đã bật lại màn hình. Khóa đăng nhập vẫn được giữ.")
        else:
            set_command_response("❌ Jarvis không thể bật màn hình trên phiên desktop hiện tại.")
        print(f"Jarvis: {last_command_response}")
        return True

    # ------------------------------------------------------
    # HẸN GIỜ SLEEP SÂU / SUSPEND
    # ------------------------------------------------------

    if await handle_deep_sleep_timer_command(command):
        return True

    # ------------------------------------------------------
    # SLEEP MÀN HÌNH - NHẠC/JARVIS VẪN CHẠY
    # ------------------------------------------------------

    if is_screen_sleep_command(command):
        if sleep_screen_only():
            set_command_response(
                "🌙 Đã sleep màn hình. Nhạc, Jarvis và Discord vẫn tiếp tục chạy."
            )
        else:
            set_command_response("❌ Jarvis không thể sleep màn hình.")
        return True

    # ------------------------------------------------------
    # SLEEP SÂU / SUSPEND TOÀN MÁY
    # ------------------------------------------------------

    if is_deep_sleep_command(command):
        if deep_sleep_machine():
            set_command_response("😴 Ubuntu đang chuyển sang Sleep sâu.")
        else:
            set_command_response("❌ Jarvis không thể đưa Ubuntu vào Sleep sâu.")
        return True

    # ------------------------------------------------------
    # ÂM LƯỢNG
    # ------------------------------------------------------

    if handle_volume_command(command):
        return True

    # ------------------------------------------------------
    # MỞ NHANH WEBSITE TRONG CHROME THEO PROFILE
    # ------------------------------------------------------

    if handle_chrome_shortcut(command):
        return True


    # ------------------------------------------------------
    # THOÁT
    # ------------------------------------------------------

    exit_commands = {
        "thoát",
        "thoat",
        "exit",
        "quit",
        "bye",
        "tạm biệt",
        "tam biet",
    }

    if command_lower in exit_commands:

        print(
            "Jarvis: Tạm biệt."
        )

        return False


    # ------------------------------------------------------
    # MỞ VIDEO N
    # ------------------------------------------------------

    video_match = re.fullmatch(
        r"(?:mở|mo)\s+video\s+(\d+)",
        command_lower,
    )

    if video_match:

        number = int(
            video_match.group(1)
        )

        await open_video(number)

        return True


    # ------------------------------------------------------
    # MỞ VIDEO THEO TÊN
    # ------------------------------------------------------

    video_name_match = re.fullmatch(
        r"(?:mở|mo)\s+video\s+(.+)",
        command,
        re.IGNORECASE,
    )

    if video_name_match:
        query = video_name_match.group(1).strip()
        await open_video_by_name(query)
        return True


    # ------------------------------------------------------
    # DỪNG / PHÁT TIẾP VIDEO YOUTUBE
    # ------------------------------------------------------

    if command_lower in {
        "dừng video", "dung video",
        "tạm dừng video", "tam dung video",
        "pause video", "pause youtube",
    }:
        await control_youtube_playback(False)
        return True

    if command_lower in {
        "phát tiếp video", "phat tiep video",
        "tiếp tục video", "tiep tuc video",
        "phát video", "phat video",
        "resume video", "resume youtube",
        "play video", "play youtube",
    }:
        await control_youtube_playback(True)
        return True


    # ------------------------------------------------------
    # LÀM MỚI YOUTUBE
    # ------------------------------------------------------

    if command_lower in {
        "làm mới youtube",
        "lam moi youtube",
        "refresh youtube",
        "cập nhật youtube",
        "cap nhat youtube",
    }:
        await refresh_youtube_videos()
        return True


    # ------------------------------------------------------
    # HIỆN / QUAY LẠI YOUTUBE
    # ------------------------------------------------------

    if command_lower in {
        "quay lại youtube",
        "quay lai youtube",
        "hiện youtube",
        "hien youtube",
        "mở lại youtube",
        "mo lai youtube",
        "show youtube",
    }:
        await show_youtube()
        return True


    # ------------------------------------------------------
    # TẮT / ĐÓNG YOUTUBE - KHÔNG TẮT CHROME
    # ------------------------------------------------------

    if command_lower in {
        "tắt youtube",
        "tat youtube",
        "đóng youtube",
        "dong youtube",
        "đóng tab youtube",
        "dong tab youtube",
        "close youtube",
    }:
        await close_youtube()
        return True


    # ------------------------------------------------------
    # VỀ TRANG CHỦ YOUTUBE
    # ------------------------------------------------------

    if command_lower in {
        "về trang chủ",
        "ve trang chu",
        "về youtube",
        "ve youtube",
        "trang chủ youtube",
        "trang chu youtube",
        "home youtube",
        "youtube home",
    }:
        await go_youtube_home()
        return True


    # ------------------------------------------------------
    # FILE / THƯ MỤC TRONG /home
    # Đặt trước tìm YouTube để "tìm file ..." không bị bắt nhầm.
    # ------------------------------------------------------

    if handle_filesystem_command(command):
        return True


    # ------------------------------------------------------
    # TẮT TẤT CẢ ỨNG DỤNG JARVIS QUẢN LÝ
    # Giữ Terminal/Jarvis/TTS/Discord để Jarvis không tự giết chính mình.
    # ------------------------------------------------------

    if command_lower in {
        "tắt tất cả",
        "tat tat ca",
        "đóng tất cả",
        "dong tat ca",
        "close all",
        "close everything",
    }:
        await close_all_managed_apps()
        return True


    # ------------------------------------------------------
    # TẮT VS CODE
    # ------------------------------------------------------

    if command_lower in {
        "tắt vscode",
        "tat vscode",
        "đóng vscode",
        "dong vscode",
        "tắt vs code",
        "tat vs code",
        "đóng vs code",
        "dong vs code",
        "close vscode",
        "close vs code",
    }:
        close_vscode()
        return True


    # ------------------------------------------------------
    # TẮT CHROME
    # ------------------------------------------------------

    chrome_profile_close = chrome_profile_from_close_command(command_lower)
    if chrome_profile_close:
        if chrome_profile_close == "personal":
            await close_chrome_profile(PERSONAL_PROFILE, "Cá nhân")
        else:
            await close_chrome_profile(STUDY_PROFILE, "Học / ChatGPT")
        return True

    if command_lower in {
        "tắt chrome",
        "tat chrome",
        "đóng chrome",
        "dong chrome",
        "close chrome",
        "tắt google chrome",
        "tat google chrome",
    }:
        await close_chrome()
        return True


    # ------------------------------------------------------
    # TẮT FILE MANAGER
    # ------------------------------------------------------

    if command_lower in {
        "tắt file manager",
        "tat file manager",
        "đóng file manager",
        "dong file manager",
        "tắt thư mục",
        "tat thu muc",
        "đóng thư mục",
        "dong thu muc",
        "tắt nautilus",
        "tat nautilus",
    }:
        close_file_manager()
        return True


    # ------------------------------------------------------
    # TÌM KIẾM YOUTUBE
    # ------------------------------------------------------

    explicit_youtube_search = (
        re.match(r"^(?:tìm|tim)\s+youtube\s+.+", command, re.IGNORECASE)
        or re.match(r"^youtube\s+(?:tìm|tim)\s+.+", command, re.IGNORECASE)
        or re.match(r"^(?:tìm|tim)\s+video\s+.+", command, re.IGNORECASE)
    )

    natural_youtube_search = re.match(
        r"^(?:tìm|tim)\s+.+",
        command,
        re.IGNORECASE,
    )

    explicit_google_search = re.match(
        r"^(?:tìm|tim)\s+(?:google|trên\s+google|tren\s+google|kiếm|kiem)\b",
        command,
        re.IGNORECASE,
    )

    if explicit_youtube_search or (
        natural_youtube_search and not explicit_google_search
    ):
        await search_youtube(command)
        return True


    # ------------------------------------------------------
    # YOUTUBE
    # ------------------------------------------------------

    youtube_commands = [
        "youtube",
        "mở youtube",
        "mo youtube",
        "vào youtube",
        "vao youtube",
    ]

    if (
        command_lower in youtube_commands
        or command_lower.startswith(
            "mở youtube "
        )
        or command_lower.startswith(
            "mo youtube "
        )
    ):

        await open_youtube(command)

        return True


    # ------------------------------------------------------
    # GITHUB
    # ------------------------------------------------------

    if (
        "github" in command_lower
        and (
            command_lower.startswith("mở")
            or command_lower.startswith("mo")
            or command_lower == "github"
        )
    ):

        open_github(command)

        return True


    # ------------------------------------------------------
    # VSCODE
    # ------------------------------------------------------

    vscode_commands = {
        "vscode",
        "vs code",
        "mở vscode",
        "mo vscode",
        "mở vs code",
        "mo vs code",
    }

    if command_lower in vscode_commands:

        open_vscode()

        return True


    # ------------------------------------------------------
    # TERMINAL
    # ------------------------------------------------------

    terminal_commands = {
        "terminal",
        "mở terminal",
        "mo terminal",
        "mở cửa sổ terminal",
        "mo cua so terminal",
    }

    if command_lower in terminal_commands:

        open_terminal()

        return True


    # ------------------------------------------------------
    # DOWNLOADS
    # ------------------------------------------------------

    if command_lower in {
        "downloads",
        "download",
        "mở downloads",
        "mo downloads",
        "mở download",
        "mo download",
    }:

        open_downloads()

        return True


    # ------------------------------------------------------
    # DOCUMENTS
    # ------------------------------------------------------

    if command_lower in {
        "documents",
        "document",
        "mở documents",
        "mo documents",
        "mở document",
        "mo document",
    }:

        open_documents()

        return True


    # ------------------------------------------------------
    # PICTURES
    # ------------------------------------------------------

    if command_lower in {
        "pictures",
        "picture",
        "mở pictures",
        "mo pictures",
        "mở picture",
        "mo picture",
    }:

        open_pictures()

        return True


    # ------------------------------------------------------
    # HOME
    # ------------------------------------------------------

    if command_lower in {
        "home",
        "mở home",
        "mo home",
        "mở thư mục home",
        "mo thu muc home",
    }:

        open_home()

        return True


    # ------------------------------------------------------
    # HIỆN DESKTOP - KHÔNG TẮT CHROME
    # ------------------------------------------------------

    if command_lower in {
        "desktop",
        "về desktop",
        "ve desktop",
        "ra desktop",
        "hiện desktop",
        "hien desktop",
        "mở desktop",
        "mo desktop",
        "về màn hình desktop",
        "ve man hinh desktop",
    }:

        show_desktop()

        return True


    # ------------------------------------------------------
    # GOOGLE SEARCH
    # ------------------------------------------------------

    google_prefixes = [
        "google",
        "tìm google",
        "tim google",
        "tìm trên google",
        "tim tren google",
        "tìm kiếm",
        "tim kiem",
        "search",
    ]

    if any(
        command_lower.startswith(prefix)
        for prefix in google_prefixes
    ):

        search_google(command)

        return True


    # ------------------------------------------------------
    # HELP
    # ------------------------------------------------------

    if command_lower in {
        "help",
        "trợ giúp",
        "tro giup",
        "giúp",
        "giup",
        "có chức năng gì",
        "co chuc nang gi",
        "bạn có chức năng gì",
        "ban co chuc nang gi",
        "jarvis có chức năng gì",
        "jarvis co chuc nang gi",
        "bạn làm được gì",
        "ban lam duoc gi",
        "jarvis làm được gì",
        "jarvis lam duoc gi",
        "bạn có thể làm gì",
        "ban co the lam gi",
        "jarvis có thể làm gì",
        "jarvis co the lam gi",
        "làm gì",
        "lam gi",
    }:

        show_help()

        return True


    # Chỉ gợi ý sau khi toàn bộ lệnh chính xác đã được thử. Nếu đặt phần này
    # sớm hơn, lệnh đúng như "mở youtube cá nhân" sẽ bị hỏi lại thay vì chạy.
    known_suggestion = _suggest_known_command(command)
    if known_suggestion:
        print(f"Jarvis: {known_suggestion}")
        set_command_response(known_suggestion)
        return True


    # ------------------------------------------------------
    # AI LOCAL (OLLAMA) - FALLBACK CHO HỘI THOẠI/LỆNH CHƯA NHẬN DIỆN
    # ------------------------------------------------------

    if not allow_local_ai:
        message = (
            "🤔 Tôi chưa chắc bạn muốn thực hiện lệnh nào. "
            "Hãy viết rõ hơn hoặc gõ `help` để xem các lệnh mẫu."
        )
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    print(f"Jarvis: AI local ({CORE.local_ai.model}) đang phân tích...")
    try:
        decision = await asyncio.to_thread(CORE.local_ai.decide, command)
    except RuntimeError as error:
        local_answer = (
            f"❌ {error} "
            "Hãy kiểm tra dịch vụ Ollama bằng lệnh `ollama list`."
        )
    else:
        routed_command = _command_from_local_ai_decision(decision, command)
        if routed_command:
            print(f"Jarvis: AI hiểu lệnh là: {routed_command}")
            return await route_command(routed_command, allow_local_ai=False)
        local_answer = decision.get("reply") or _suggest_local_ai_command(decision) or (
            "🤔 Tôi chưa hiểu rõ lệnh đó. Hãy viết lại cụ thể hơn hoặc gõ "
            "`help` để xem các lệnh mẫu."
        )
    print(f"Jarvis: {local_answer}")
    set_command_response(local_answer)

    return True


# ==========================================================
# DISCORD
# ==========================================================

def _env_int(name):
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _discord_command_needs_profile(command):
    """Tránh Jarvis chờ input() trên Terminal khi lệnh đến từ Discord."""
    command_lower = command.strip().lower()

    profile_words = (
        "cá nhân", "ca nhan", "personal",
        "học", "hoc", "study", "chatgpt",
    )

    if any(word in command_lower for word in profile_words):
        return False

    if command_lower in {"youtube", "mở youtube", "mo youtube", "vào youtube", "vao youtube"}:
        return True

    youtube_search_prefixes = (
        "tìm youtube ", "tim youtube ",
        "youtube tìm ", "youtube tim ",
        "tìm video ", "tim video ",
    )

    if command_lower.startswith(youtube_search_prefixes):
        # Nếu chưa có tab YouTube, search_youtube() sẽ cần chọn profile.
        return youtube_page_id is None

    if command_lower.startswith(("tìm ", "tim ")):
        google_search_prefixes = (
            "tìm google ", "tim google ",
            "tìm trên google ", "tim tren google ",
            "tìm kiếm ", "tim kiem ",
        )

        if not command_lower.startswith(google_search_prefixes):
            return youtube_page_id is None

    if "github" in command_lower and (
        command_lower.startswith("mở")
        or command_lower.startswith("mo")
        or command_lower == "github"
    ):
        return True

    google_prefixes = (
        "google", "tìm google", "tim google",
        "tìm trên google", "tim tren google",
        "tìm kiếm", "tim kiem", "search",
    )
    if command_lower.startswith(google_prefixes):
        return True

    return False


async def start_discord_bot():
    """Chạy Discord bot song song với giao diện Terminal của Jarvis."""
    global discord_client

    token = os.getenv("DISCORD_TOKEN", "").strip()
    owner_id = _env_int("DISCORD_USER_ID")
    channel_id = _env_int("DISCORD_CHANNEL_ID")

    if not token:
        print("Jarvis: Discord chưa chạy vì thiếu DISCORD_TOKEN trong .env.")
        return

    if owner_id is None:
        print("Jarvis: Discord chưa chạy vì thiếu DISCORD_USER_ID trong .env.")
        print("Jarvis: Thêm ID Discord của bạn để chỉ bạn có quyền điều khiển máy.")
        return

    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(
        intents=intents,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    discord_client = client

    @client.event
    async def on_ready():
        print()
        print(f"Jarvis: Discord đã kết nối: {client.user}")
        print('Jarvis: Discord nhận lệnh trực tiếp, ví dụ: mở vscode')
        if channel_id is not None:
            print(f"Jarvis: Chỉ nhận lệnh trong channel ID {channel_id}.")

    @client.event
    async def on_message(message):
        if message.author.bot:
            return

        if message.author.id != owner_id:
            return

        if channel_id is not None and message.channel.id != channel_id:
            return

        command = message.content.strip()

        if not command:
            return

        if command.lower() in {
            "thoát", "thoat", "exit", "quit", "bye",
            "tạm biệt", "tam biet",
        }:
            await message.reply(
                "Lệnh thoát từ Discord bị khóa. Hãy thoát Jarvis trực tiếp trên Ubuntu.",
                mention_author=False,
            )
            return

        if _discord_command_needs_profile(command):
            await message.reply(
                "Lệnh này cần chỉ rõ Chrome profile. Ví dụ: "
                "`mở youtube cá nhân` hoặc `mở youtube học`.",
                mention_author=False,
            )
            return

        await message.reply(
            f"Đã nhận: `{command}`",
            mention_author=False,
        )

        # Sleep sâu làm mạng/Jarvis tạm dừng, nên Discord phải xác nhận trước.
        if is_deep_sleep_command(command):
            deep_sleep_message = (
                "😴 Jarvis sẽ đưa Ubuntu vào Sleep sâu. Nhạc và chương trình sẽ tạm dừng."
            )
            await message.reply(
                deep_sleep_message,
                mention_author=False,
            )
            speak(deep_sleep_message)
            await asyncio.sleep(0.8)
            deep_sleep_machine()
            return

        # Sleep màn hình không suspend hệ thống nên Discord/Jarvis vẫn hoạt động.
        if is_screen_sleep_command(command):
            screen_sleep_message = (
                "🌙 Jarvis sẽ tắt màn hình. Nhạc và Discord vẫn tiếp tục chạy."
            )
            await message.reply(
                screen_sleep_message,
                mention_author=False,
            )
            speak(screen_sleep_message)
            await asyncio.sleep(0.4)
            sleep_screen_only()
            return

        try:
            global last_command_response
            last_command_response = None

            async with discord_command_lock:
                await route_command(command)

            speak_last_response()

            if last_command_response:
                # Discord giới hạn một message khoảng 2000 ký tự.
                response_text = last_command_response

                if len(response_text) > 1900:
                    response_text = response_text[:1900] + "\n..."

                await message.reply(
                    response_text,
                    mention_author=False,
                )
            else:
                await message.reply(
                    "✅ Jarvis đã xử lý lệnh.",
                    mention_author=False,
                )
        except Exception as error:
            print(f"Jarvis: Lỗi Discord command: {error}")
            await message.reply(
                "❌ Jarvis gặp lỗi khi xử lý lệnh. Xem Terminal Ubuntu để biết chi tiết.",
                mention_author=False,
            )

    try:
        await client.start(token)
    except discord.LoginFailure:
        print("Jarvis: DISCORD_TOKEN không hợp lệ. Hãy kiểm tra/reset token.")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"Jarvis: Không thể kết nối Discord: {error}")


async def stop_discord_bot():
    global discord_client

    if discord_client is not None and not discord_client.is_closed():
        await discord_client.close()

    discord_client = None


# ==========================================================
# MAIN
# ==========================================================

async def main():
    print()
    print("=" * 60)
    print(f"            JARVIS v{VERSION}")
    print("=" * 60)

    # Khi chạy trực tiếp trong Terminal, stdin là TTY và Jarvis giữ nguyên
    # giao diện nhập lệnh cũ. Khi chạy bằng systemd --user, stdin không phải
    # TTY nên Jarvis không gọi input() và tiếp tục sống nhờ Discord task.
    interactive_terminal = sys.stdin.isatty()

    print()
    start_tts_worker()

    if interactive_terminal:
        jarvis_say('Sẵn sàng. Gõ "help" để xem lệnh.')
    else:
        print("Jarvis: Đang chạy nền bằng systemd. Terminal input đã tắt.")
        print("Jarvis: Discord vẫn tiếp tục nhận lệnh.")

    discord_task = asyncio.create_task(start_discord_bot())

    try:
        if interactive_terminal:
            # Chế độ chạy thủ công: giữ nguyên toàn bộ cách nhập lệnh Terminal cũ.
            running = True

            while running:
                try:
                    print()
                    command = (await asyncio.to_thread(input, "Bạn: ")).strip()

                    global last_command_response
                    last_command_response = None

                    running = await route_command(command)
                    speak_last_response()

                except KeyboardInterrupt:
                    print()
                    jarvis_say("Tạm biệt.")
                    break

                except EOFError:
                    print()
                    jarvis_say("Tạm biệt.")
                    break

                except Exception as error:
                    print()
                    print("Jarvis: Có lỗi:")
                    print(error)

        else:
            # Chế độ systemd: không có Terminal để input(). Chờ Discord task
            # để process chính không kết thúc ngay sau khi boot/login Ubuntu.
            await discord_task

    finally:
        await stop_discord_bot()

        if not discord_task.done():
            discord_task.cancel()

        try:
            await discord_task
        except asyncio.CancelledError:
            pass

        await close_mcp_session()
        stop_tts_worker()


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    asyncio.run(main())
