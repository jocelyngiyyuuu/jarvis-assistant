#!/usr/bin/env python3

import asyncio
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote_plus

import discord
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


VERSION = "1.0.0-discord"

load_dotenv()

DISCORD_PREFIX = "!jarvis"
discord_client = None
discord_command_lock = asyncio.Lock()

PERSONAL_PROFILE = "Default"
STUDY_PROFILE = "Profile 1"


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
    subprocess.Popen(
        [
            "google-chrome",
            f"--profile-directory={profile}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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

def extract_videos(snapshot_text, limit=10):
    videos = []
    seen = set()

    pattern = re.compile(
        r'uid=([^\s]+)\s+link\s+"([^"]+)"',
        re.IGNORECASE,
    )

    ignored_exact = {
        "youtube",
        "home",
        "trang chủ",
        "shorts",
        "subscriptions",
        "kênh đăng ký",
        "library",
        "thư viện",
        "history",
        "video đã xem",
        "sign in",
        "đăng nhập",
        "explore",
        "khám phá",
    }

    for match in pattern.finditer(snapshot_text):

        uid = match.group(1)

        title = clean_title(
            match.group(2)
        )

        if not title:
            continue

        title_lower = title.lower()

        # Menu chính
        if title_lower in ignored_exact:
            continue

        # Trang chủ YouTube Premium
        if title_lower.startswith(
            "trang chủ youtube"
        ):
            continue

        if title_lower.startswith(
            "youtube premium"
        ):
            continue

        # Menu phụ
        if title_lower.startswith(
            "show more"
        ):
            continue

        if title_lower.startswith(
            "hiển thị thêm"
        ):
            continue

        # Title quá ngắn
        if len(title) < 5:
            continue

        # Trùng tên
        if title_lower in seen:
            continue

        seen.add(title_lower)

        videos.append(
            {
                "uid": uid,
                "title": title,
            }
        )

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

    if mcp_session is not None:
        return mcp_session

    print()
    print("Jarvis: Đang kết nối Chrome...")

    server = create_mcp_server()

    try:
        mcp_stdio_context = stdio_client(server)
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

    session_context = mcp_session_context
    stdio_context = mcp_stdio_context

    mcp_session = None
    mcp_session_context = None
    mcp_stdio_context = None

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

            snapshot_result = await session.call_tool(
                "take_snapshot",
                arguments={"verbose": False},
            )

            snapshot_text = result_to_text(snapshot_result)
            videos = extract_videos(snapshot_text, limit=10)

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

    snapshot_result = await session.call_tool(
        "take_snapshot",
        arguments={"verbose": False},
    )

    snapshot_text = result_to_text(snapshot_result)
    videos = extract_videos(snapshot_text, limit=limit)

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
        _, current_videos = await get_current_youtube_videos(session, limit=30)

        if not current_videos:
            print("Jarvis: Không đọc được video trên tab YouTube hiện tại.")
            return

        current_video = find_video_by_title(current_videos, selected_title)

        if current_video is None:
            print("Jarvis: Video không còn xuất hiện trên trang hiện tại.")
            print('Jarvis: Dùng "làm mới youtube" để cập nhật danh sách.')
            return

        await session.call_tool(
            "click",
            arguments={"uid": current_video["uid"]},
        )
        print("Jarvis: Đã mở video.")

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

        await session.call_tool(
            "click",
            arguments={"uid": selected_video["uid"]},
        )
        print("Jarvis: Đã mở video.")

    except Exception as error:
        print("Jarvis: Không thể nhấn vào video.")
        print(f"Jarvis: Chi tiết: {error}")


# ==========================================================
# TÌM KIẾM VIDEO TRÊN YOUTUBE
# ==========================================================

def extract_youtube_search_query(command):
    patterns = [
        r"^(?:tìm|tim)\s+youtube\s+(.+)$",
        r"^youtube\s+(?:tìm|tim)\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, command.strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip()

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
        return True

    except Exception as error:
        print("Jarvis: Không thể tìm kiếm trên YouTube.")
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
    print(
        "Jarvis: Đang mở VS Code..."
    )

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
    print("  làm mới youtube")
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
    print("  mở github")
    print("  mở vscode")
    print("  mở terminal")
    print()
    print("  mở downloads")
    print("  mở documents")
    print("  mở pictures")
    print("  mở home")
    print()
    print("  về desktop")
    print("  ra desktop")
    print("  hiện desktop")
    print()
    print("  thoát")
    print()


# ==========================================================
# COMMAND ROUTER
# ==========================================================

async def route_command(command):
    command = command.strip()

    command_lower = command.lower()

    if not command_lower:
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
    # TÌM KIẾM YOUTUBE
    # ------------------------------------------------------

    if (
        re.match(r"^(?:tìm|tim)\s+youtube\s+.+", command, re.IGNORECASE)
        or re.match(r"^youtube\s+(?:tìm|tim)\s+.+", command, re.IGNORECASE)
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
    }:

        show_help()

        return True


    # ------------------------------------------------------
    # KHÔNG HIỂU
    # ------------------------------------------------------

    print(
        "Jarvis: Tôi chưa hiểu lệnh đó."
    )

    print(
        'Jarvis: Gõ "help" để xem '
        "các lệnh hiện có."
    )

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

    if command_lower.startswith(("tìm youtube ", "tim youtube ", "youtube tìm ", "youtube tim ")):
        # Nếu chưa có tab YouTube, search_youtube() sẽ cần chọn profile.
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
        print(f'Jarvis: Gửi lệnh theo dạng: {DISCORD_PREFIX} mở vscode')
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

        content = message.content.strip()
        if not content.lower().startswith(DISCORD_PREFIX):
            return

        command = content[len(DISCORD_PREFIX):].strip()

        if not command:
            await message.reply(
                f"Dùng: `{DISCORD_PREFIX} mở vscode`",
                mention_author=False,
            )
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
                f"`{DISCORD_PREFIX} mở youtube cá nhân` hoặc "
                f"`{DISCORD_PREFIX} mở youtube học`.",
                mention_author=False,
            )
            return

        await message.reply(
            f"Đã nhận: `{command}`",
            mention_author=False,
        )

        try:
            async with discord_command_lock:
                await route_command(command)

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

    print()
    print('Jarvis: Sẵn sàng. Gõ "help" để xem lệnh.')

    # Discord chạy nền. input() được đưa sang thread để không chặn event loop.
    discord_task = asyncio.create_task(start_discord_bot())
    running = True

    try:
        while running:
            try:
                print()
                command = (await asyncio.to_thread(input, "Bạn: ")).strip()
                running = await route_command(command)

            except KeyboardInterrupt:
                print()
                print("Jarvis: Tạm biệt.")
                break

            except EOFError:
                print()
                print("Jarvis: Tạm biệt.")
                break

            except Exception as error:
                print()
                print("Jarvis: Có lỗi:")
                print(error)

    finally:
        await stop_discord_bot()

        if not discord_task.done():
            discord_task.cancel()

        try:
            await discord_task
        except asyncio.CancelledError:
            pass

        await close_mcp_session()


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    asyncio.run(main())