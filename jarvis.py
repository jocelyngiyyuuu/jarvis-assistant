#!/usr/bin/env python3

import asyncio
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import fcntl
import hashlib
import json
import os
import signal
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

import discord
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jarvis_core.runtime import JarvisCore
from jarvis_core.help_catalog import (
    HELP_CATEGORIES,
    format_help_category,
    format_help_overview,
    format_recent_updates,
    format_category_choices,
    resolve_help_category,
)
from jarvis_core.security import classify_remote_command, redact_sensitive
from jarvis_core.text import normalize_text as normalize_core_text
from jarvis_core.voice_trigger import VoiceTriggerEngine, run_voice_trigger_loop
from jarvis_core.window_manager import (
    ChromeWindowManager,
    FileWindowManager,
    chrome_profile_from_close_command,
)


VERSION = "1.1.0-modular-core"

load_dotenv()

discord_client = None
discord_notification_channel_id = None
discord_command_lock = asyncio.Lock()
last_command_response = None
last_spoken_response = None
pending_command_suggestions = {}
SUGGESTION_CONFIRM_TTL_SECONDS = 120

SUGGESTION_AFFIRMATIONS = {
    "dung", "dung roi", "phai", "phai roi", "uh", "u", "ừ", "ok",
    "okay", "xac nhan", "dong y", "chinh xac", "cu chay", "thuc hien di",
}
SUGGESTION_REJECTIONS = {
    "khong", "khong phai", "sai", "bo qua", "huy", "thoi", "cancel",
}
ipc_server = None

# Chỉ cho phép một process Jarvis hoạt động. Nếu Terminal và systemd cùng
# khởi động Jarvis, mỗi process sẽ có event loop/timer riêng và có thể gửi hai
# lệnh suspend độc lập.
instance_lock_file = None

# Chặn một timer/lệnh cũ gọi suspend lần nữa ngay sau khi máy resume. Python
# monotonic clock không tính thời gian hệ thống nằm trong suspend, nên khoảng
# bảo vệ này đo đúng thời gian máy thực sự hoạt động sau lần gọi trước.
SUSPEND_COOLDOWN_SECONDS = 5 * 60
suspend_request_lock = threading.Lock()
last_suspend_request_monotonic = None

# Kết quả tìm file/thư mục gần nhất để có thể chọn bằng số từ Terminal/Discord.
file_search_results = []

PERSONAL_PROFILE = "Default"
STUDY_PROFILE = "Profile 1"
JARVIS_CHROME_PROFILE = "Jarvis"
JARVIS_CHROME_PROFILE_NAME = "Jarvis"
JARVIS_CHROME_DATA_DIR = Path.home() / ".config" / "jarvis-chrome"
JARVIS_CHROME_DEBUG_PORT = 9223
JARVIS_CHROME_DEBUG_URL = f"http://127.0.0.1:{JARVIS_CHROME_DEBUG_PORT}"


# ==========================================================
# TTS - WORKER THƯỜNG TRỰC TRONG MÔI TRƯỜNG RIÊNG
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent
CORE = JarvisCore(BASE_DIR)
IPC_SOCKET_PATH = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / f"jarvis-{os.getuid()}.sock"
FILE_WINDOWS = FileWindowManager()
CHROME_WINDOWS = ChromeWindowManager()
TTS_DIR = BASE_DIR / "tts"
TTS_PYTHON = TTS_DIR / "VieNeu-TTS" / ".venv" / "bin" / "python"
TTS_ENGINE = TTS_DIR / "tts_engine.py"
TTS_ENABLED = True
tts_process = None
voice_trigger_engine = None
voice_trigger_suppressed_until = 0.0
VOICE_TRIGGER_ENABLED = os.getenv("VOICE_TRIGGER_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
VOICE_TRIGGER_MODEL_PATH = Path(os.getenv(
    "VOICE_TRIGGER_MODEL_PATH",
    str(BASE_DIR / ".jarvis_data" / "models" / "vosk-model-small-vn-0.4"),
)).expanduser()
VOICE_TRIGGER_SOURCE = os.getenv("VOICE_TRIGGER_SOURCE", "").strip()
VOICE_TRIGGER_READY_SOUND = os.getenv(
    "VOICE_TRIGGER_READY_SOUND",
    "/usr/share/sounds/freedesktop/stereo/message.oga",
).strip()
CLAP_SCREEN_WAKE_STATE_PATH = BASE_DIR / ".jarvis_data" / "clap_screen_wake.json"


def load_clap_screen_wake_enabled():
    """Keep the user's clap-to-wake preference across Jarvis restarts."""
    try:
        payload = json.loads(
            CLAP_SCREEN_WAKE_STATE_PATH.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return True
    return bool(payload.get("enabled", True)) if isinstance(payload, dict) else True


def save_clap_screen_wake_enabled(enabled):
    CLAP_SCREEN_WAKE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CLAP_SCREEN_WAKE_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"enabled": bool(enabled)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(CLAP_SCREEN_WAKE_STATE_PATH)


CLAP_SCREEN_WAKE_ENABLED = load_clap_screen_wake_enabled()
WOL_PC_MAC = os.getenv("WOL_PC_MAC", "").strip()
WOL_BROADCAST = os.getenv("WOL_BROADCAST", "255.255.255.255").strip()
try:
    WOL_PORT = int(os.getenv("WOL_PORT", "9"))
except ValueError:
    WOL_PORT = 9
PC_SSH_USER = os.getenv("PC_SSH_USER", "").strip()
PC_SSH_HOST = os.getenv("PC_SSH_HOST", "").strip()
DISCORD_RECONNECT_DELAY_SECONDS = 15
GMAIL_STATE_PATH = BASE_DIR / ".jarvis_data" / "gmail_state.json"
GMAIL_ACCOUNT_PATH = BASE_DIR / ".jarvis_data" / "gmail_account.json"
try:
    GMAIL_POLL_SECONDS = max(30, int(os.getenv("GMAIL_POLL_SECONDS", "120")))
except ValueError:
    GMAIL_POLL_SECONDS = 120


def _clean_tts_text(text):
    """Rút gọn phản hồi Terminal/Discord thành câu phù hợp để đọc bằng TTS."""
    if text is None:
        return ""

    value = str(text).strip()
    if not value:
        return ""

    # Danh sách media có thể rất dài; chỉ đọc một câu xác nhận ngắn.
    if "YOUTUBE -" in value.upper() or "KẾT QUẢ YOUTUBE" in value.upper():
        return "Đã cập nhật danh sách YouTube."
    if "SOUNDCLOUD -" in value.upper() or "KẾT QUẢ SOUNDCLOUD" in value.upper():
        return "Đã cập nhật danh sách SoundCloud."

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
    global voice_trigger_suppressed_until

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
        # Tránh cảm biến nghe lại chính giọng TTS. Thời gian này chỉ là cửa sổ
        # bảo vệ; detector sẽ tự hoạt động lại sau khi câu nói kết thúc.
        estimated_seconds = min(18.0, max(2.5, len(text) / 11.0 + 1.5))
        voice_trigger_suppressed_until = max(
            voice_trigger_suppressed_until,
            time.monotonic() + estimated_seconds,
        )
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
    if last_spoken_response is False:
        return
    if last_command_response:
        speak(last_spoken_response or last_command_response)


def voice_trigger_is_suppressed():
    return time.monotonic() < voice_trigger_suppressed_until


def normalize_voice_command(command):
    """Repair common Vietnamese STT spellings for app names."""
    original = str(command).strip()
    plain = normalize_core_text(original)
    youtube_aliases = (
        "youtube", "you tube", "du tup", "diu tup", "iu tup", "u tup",
        "du tuyp", "diu tuyp", "iu tuyp",
    )
    for verb, canonical in (("mo", "mở"), ("tat", "tắt"), ("dong", "tắt")):
        if any(plain == f"{verb} {alias}" for alias in youtube_aliases):
            return f"{canonical} youtube"
    return original


async def announce_voice_ready(_trigger):
    """Use Jarvis' own TTS voice instead of an ambiguous notification sound."""
    speak("Jarvis đang nghe.")
    # Start capture immediately so a second double clap can cancel even while
    # this acknowledgement is playing. capture_command() ignores TTS for STT.
    await asyncio.sleep(0.1)


async def announce_voice_cancelled():
    speak("Jarvis đã tắt chế độ nghe.")


async def announce_voice_unrecognized():
    speak("Jarvis chưa nghe rõ, vui lòng thử lại.")


async def handle_screen_wake_trigger(_trigger):
    """Consume a clap/snap as display wake when screen-wake mode is armed."""
    engine = voice_trigger_engine
    if (
        not CLAP_SCREEN_WAKE_ENABLED
        or engine is None
        or not engine.screen_wake_armed()
    ):
        return False
    success = wake_screen()
    engine.disarm_screen_wake()
    if success:
        print("Jarvis: Đã bật màn hình bằng cử chỉ âm thanh.", flush=True)
        speak("Jarvis đã bật màn hình.")
    else:
        print("Jarvis: Không thể bật màn hình bằng cử chỉ âm thanh.", flush=True)
        speak("Jarvis không thể bật màn hình.")
    return True


async def handle_voice_command(command, trigger):
    """Route a local STT result while blocking risky false recognitions."""
    global last_command_response
    command = normalize_voice_command(command)
    if not command:
        return False
    print(
        f"Bạn (giọng nói/{trigger}): {redact_sensitive(command)}",
        flush=True,
    )
    risk = classify_remote_command(command)
    if risk != "normal":
        message = (
            "⚠️ Lệnh giọng nói này cần xác nhận. "
            "Hãy thực hiện bằng ứng dụng Jarvis hoặc Discord."
        )
        set_command_response(message)
        speak(message)
        CORE.security.audit("voice", command, risk, "blocked_voice_confirmation")
        return False
    CORE.conversation.add("voice", "user", command)
    async with discord_command_lock:
        last_command_response = None
        await route_command(command, source="voice")
        response = last_command_response or "✅ Jarvis đã xử lý lệnh giọng nói."
    CORE.conversation.add("voice", "assistant", response)
    speak_last_response()
    return True


# ==========================================================
# IPC LOCAL - GTK DÙNG CHUNG PHIÊN JARVIS/DISCORD
# ==========================================================

async def _handle_ipc_client(reader, writer):
    """Nhận từng lệnh JSON qua Unix socket và xử lý trong process Jarvis chính."""
    global last_command_response
    try:
        while line := await reader.readline():
            try:
                request = json.loads(line.decode("utf-8"))
                command = str(request.get("command", "")).strip()
                original_command = command
                source = str(request.get("source", "gtk")).strip() or "gtk"
                silent = bool(request.get("silent", False))
                if not command:
                    raise ValueError("Lệnh không được để trống.")
                if not silent:
                    CORE.conversation.add(source, "user", command)
                command, confirmation_note = resolve_command_confirmation(command, source)
                async with discord_command_lock:
                    last_command_response = None
                    if confirmation_note and command == original_command:
                        set_command_response(confirmation_note)
                    else:
                        await route_command(command, source=source)
                    response = last_command_response or "✅ Jarvis đã xử lý lệnh."
                if not silent and classify_remote_command(command) != "sensitive":
                    CORE.conversation.add(source, "assistant", response)
                if not silent:
                    speak_last_response()
                payload = {"ok": True, "response": response}
            except Exception as error:
                payload = {"ok": False, "response": f"Không thể xử lý lệnh: {error}"}
            writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def start_ipc_server():
    global ipc_server
    try:
        IPC_SOCKET_PATH.unlink(missing_ok=True)
        ipc_server = await asyncio.start_unix_server(
            _handle_ipc_client, path=str(IPC_SOCKET_PATH)
        )
        IPC_SOCKET_PATH.chmod(0o600)
        print(f"Jarvis: GTK IPC sẵn sàng tại {IPC_SOCKET_PATH}")
    except OSError as error:
        ipc_server = None
        print(f"Jarvis: Không thể mở GTK IPC: {error}")


async def stop_ipc_server():
    global ipc_server
    if ipc_server is not None:
        ipc_server.close()
        await ipc_server.wait_closed()
        ipc_server = None
    try:
        IPC_SOCKET_PATH.unlink(missing_ok=True)
    except OSError:
        pass


# ==========================================================
# BIẾN LƯU TRẠNG THÁI YOUTUBE
# ==========================================================

youtube_videos = []
youtube_profile = None
youtube_profile_name = None
youtube_page_id = None
soundcloud_tracks = []
soundcloud_page_id = None
managed_cdp_targets = {
    "youtube": set(), "soundcloud": set(), "zalo": set(), "gmail": set(),
}


# ==========================================================
# MCP PERSISTENT SESSION
# ==========================================================

mcp_stdio_context = None
mcp_session_context = None
mcp_session = None
mcp_errlog = None
mcp_browser_pid = None
mcp_page_topology_changed = False


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

def _jarvis_debug_port_owner_pid():
    """Return the PID listening on Jarvis' loopback DevTools port."""
    try:
        result = subprocess.run(
            [
                "lsof", "-nP",
                f"-iTCP:{JARVIS_CHROME_DEBUG_PORT}",
                "-sTCP:LISTEN", "-t",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    try:
        return int(lines[0])
    except ValueError:
        return None


def _process_has_jarvis_chrome_args(pid):
    """Verify the listener belongs to Chrome launched with Jarvis' data dir."""
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ")
    except (OSError, TypeError, ValueError):
        return False
    expected_data_dir = (
        f"--user-data-dir={JARVIS_CHROME_DATA_DIR.resolve()}".encode()
    )
    expected_port = f"--remote-debugging-port={JARVIS_CHROME_DEBUG_PORT}".encode()
    tokens = command.split()
    return expected_data_dir in tokens and expected_port in tokens


def _jarvis_chrome_ready():
    """Return whether the dedicated loopback DevTools endpoint is ready."""
    owner_pid = _jarvis_debug_port_owner_pid()
    if owner_pid is None or not _process_has_jarvis_chrome_args(owner_pid):
        return False
    try:
        with urlopen(f"{JARVIS_CHROME_DEBUG_URL}/json/version", timeout=0.5) as response:
            if response.status != 200:
                return False
            metadata = json.loads(response.read().decode("utf-8"))
            browser = str(metadata.get("Browser", "")).lower()
            websocket_url = str(metadata.get("webSocketDebuggerUrl", ""))
            websocket = urlparse(websocket_url)
            return (
                ("chrome/" in browser or "chromium/" in browser)
                and websocket.scheme == "ws"
                and websocket.hostname == "127.0.0.1"
                and websocket.port == JARVIS_CHROME_DEBUG_PORT
            )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False


def ensure_jarvis_chrome(url="chrome://newtab/"):
    """Start or reuse Jarvis' isolated Chrome profile without consent popups."""
    JARVIS_CHROME_DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    JARVIS_CHROME_DATA_DIR.chmod(0o700)
    if _jarvis_chrome_ready():
        return url == "chrome://newtab/" or _ensure_jarvis_chrome_tab(url)
    args = [
        "google-chrome",
        "--ozone-platform=x11",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={JARVIS_CHROME_DEBUG_PORT}",
        f"--user-data-dir={JARVIS_CHROME_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _attempt in range(40):
        if _jarvis_chrome_ready():
            # A newly created browser receives the URL directly in argv. The
            # CDP fallback above is only needed when Chrome was already alive.
            return True
        time.sleep(0.1)
    return False


def _automation_site_key(url):
    host = (urlparse(url).hostname or "").lower()
    if host == "chat.zalo.me":
        return "zalo"
    if host in {"mail.google.com", "accounts.google.com"}:
        return "gmail"
    if host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    if host == "soundcloud.com" or host.endswith(".soundcloud.com"):
        return "soundcloud"
    return None


def _cdp_pages():
    try:
        with urlopen(f"{JARVIS_CHROME_DEBUG_URL}/json/list", timeout=2) as response:
            pages = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(pages, list):
        return None
    return [page for page in pages if isinstance(page, dict) and page.get("type") == "page"]


def _record_managed_cdp_target(site_key, before_ids):
    pages = _cdp_pages()
    if pages is None:
        return False
    owned = managed_cdp_targets.setdefault(site_key, set())
    current_ids = {page.get("id") for page in pages if page.get("id")}
    # _ensure_jarvis_chrome_tab đã ghi ownership từ chính response /json/new.
    # Chấp nhận target mới đó ngay cả khi Chrome chưa cập nhật URL khỏi
    # about:blank; URL sẽ được kiểm tra lại trước mọi thao tác MCP sau đó.
    newly_created_owned = owned & (current_ids - set(before_ids))
    if len(newly_created_owned) == 1:
        return True

    matching = []
    for page in pages:
        target_id = page.get("id")
        if not target_id or _automation_site_key(page.get("url", "")) != site_key:
            continue
        matching.append(target_id)
    new_ids = [target_id for target_id in matching if target_id not in before_ids]
    if len(new_ids) == 1:
        managed_cdp_targets.setdefault(site_key, set()).add(new_ids[0])
        return True
    return bool(owned.intersection(matching))


def _ensure_jarvis_chrome_tab(url):
    """Open an automation URL through the verified loopback CDP browser."""
    global mcp_page_topology_changed
    try:
        target = urlparse(url)
        if target.scheme not in {"http", "https"}:
            return False
        with urlopen(f"{JARVIS_CHROME_DEBUG_URL}/json/list", timeout=2) as response:
            pages = json.loads(response.read().decode("utf-8"))
        site_key = _automation_site_key(url)
        owned = managed_cdp_targets.setdefault(site_key, set()) if site_key else set()
        target_url = str(url).rstrip("/")
        site_pages = [
            page for page in pages if isinstance(page, dict)
            and _automation_site_key(page.get("url", "")) == site_key
            and page.get("id")
        ]
        # systemd có thể khởi động lại Jarvis trong khi Chrome automation riêng
        # vẫn còn sống. Với đúng một tab của site trong browser đã xác minh,
        # khôi phục ownership thay vì mở một tab trùng lặp.
        if site_key and not owned and len(site_pages) == 1:
            owned.add(site_pages[0]["id"])
        for page in pages if isinstance(pages, list) else []:
            if not isinstance(page, dict) or page.get("id") not in owned:
                continue
            page_url = str(page.get("url", "")).rstrip("/")
            if page_url == target_url:
                return True
            page_host = (urlparse(page_url).hostname or "").lower()
            if site_key == "gmail" and page_host in {
                "mail.google.com", "accounts.google.com",
            }:
                return True
            if site_key == "soundcloud" and (
                page_host == "soundcloud.com"
                or page_host.endswith(".soundcloud.com")
            ):
                return True
        encoded_url = quote_plus(str(url))
        request = Request(
            f"{JARVIS_CHROME_DEBUG_URL}/json/new?{encoded_url}", method="PUT"
        )
        with urlopen(request, timeout=4) as response:
            created = json.loads(response.read().decode("utf-8"))
        created_page = isinstance(created, dict) and created.get("type") == "page"
        if created_page:
            created_id = created.get("id")
            if site_key and created_id:
                owned.add(created_id)
            # chrome-devtools-mcp giữ snapshot page targets của session hiện
            # tại và có thể chưa thấy tab được tạo trực tiếp qua /json/new.
            # Đánh dấu để get_mcp_session kết nối lại trước lần đọc kế tiếp.
            mcp_page_topology_changed = True
        return created_page
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False


def _is_automation_url(url, profile=None):
    try:
        host = (urlparse(url).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "soundcloud.com"
        or host.endswith(".soundcloud.com")
        or (host == "chat.zalo.me" and profile == STUDY_PROFILE)
        or (host == "mail.google.com" and profile == STUDY_PROFILE)
    )


def open_chrome(profile, url):
    previous_window_ids = CHROME_WINDOWS.snapshot_ids()
    if previous_window_ids is None:
        return False
    if _is_automation_url(url, profile):
        automation_host = (urlparse(url).hostname or "").lower()
        target_hint = {
            "chat.zalo.me": "zalo",
            "mail.google.com": "gmail",
            "soundcloud.com": "soundcloud",
            "www.soundcloud.com": "soundcloud",
        }.get(automation_host, "youtube")
        before_pages = _cdp_pages() if _jarvis_chrome_ready() else []
        if before_pages is None:
            return False
        before_target_ids = {page.get("id") for page in before_pages if page.get("id")}
        opened = ensure_jarvis_chrome(url)
        owner_pid = _jarvis_debug_port_owner_pid() if opened else None
        target_owned = (
            _record_managed_cdp_target(target_hint, before_target_ids)
            if owner_pid is not None else False
        )
        return bool(
            target_owned
            and CHROME_WINDOWS.activate_automation_window(
                previous_window_ids, owner_pid, target_hint
            )
        )
    chrome_args = [
            "google-chrome",
            # Jarvis đóng riêng từng profile bằng wmctrl. Trên phiên Wayland,
            # buộc cửa sổ Chrome qua XWayland để wmctrl nhìn thấy window ID.
            "--ozone-platform=x11",
            f"--profile-directory={profile}",
            "--new-window",
        ]
    if (urlparse(url).hostname or "").lower() == "github.com":
        chrome_args.append(f"--app={url}")
    else:
        chrome_args.append(url)
    subprocess.Popen(
        chrome_args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    host = (urlparse(url).hostname or "").lower()
    site_key = {
        "chatgpt.com": "chatgpt",
        "mail.google.com": "gmail",
        "drive.google.com": "drive",
        "calendar.google.com": "calendar",
        "github.com": "github",
        "chat.zalo.me": "zalo",
        "www.google.com": "google",
    }.get(host)
    return CHROME_WINDOWS.track_profile_window(
        profile, previous_window_ids, site_key=site_key
    )


# ==========================================================
# CHROME SHORTCUTS - MỞ THẲNG WEBSITE THEO PROFILE
# ==========================================================

def configured_gmail_account():
    try:
        data = json.loads(GMAIL_ACCOUNT_PATH.read_text(encoding="utf-8"))
        account = str(data.get("account", "")).strip().lower()
    except (OSError, ValueError, AttributeError):
        account = ""
    return account if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", account) else ""


def configured_gmail_url():
    account = configured_gmail_account()
    if not account:
        return "https://mail.google.com/"
    return f"https://mail.google.com/mail/u/?authuser={quote_plus(account)}"


def configured_gmail_folder_url(folder="inbox"):
    folder = "spam" if folder == "spam" else "inbox"
    account = configured_gmail_account()
    if account:
        return f"https://mail.google.com/mail/u/?authuser={quote_plus(account)}#{folder}"
    return f"https://mail.google.com/mail/u/0/#{folder}"


CHROME_SHORTCUTS = {
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "chat gpt": ("ChatGPT", "https://chatgpt.com/"),
    "gmail": ("Gmail", configured_gmail_url()),
    "mail": ("Gmail", configured_gmail_url()),
    "drive": ("Google Drive", "https://drive.google.com/"),
    "google drive": ("Google Drive", "https://drive.google.com/"),
    "calendar": ("Google Calendar", "https://calendar.google.com/"),
    "lịch google": ("Google Calendar", "https://calendar.google.com/"),
    "lich google": ("Google Calendar", "https://calendar.google.com/"),
    "github": ("GitHub", "https://github.com/"),
    "zalo": ("Zalo công việc", "https://chat.zalo.me/"),
    "zalo web": ("Zalo công việc", "https://chat.zalo.me/"),
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
        "công việc",
        "cong viec",
        "work",
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
        r"\s+(?:bằng\s+)?(?:tài\s+khoản\s+)?(?:cá nhân|ca nhan|personal|học tập|hoc tap|học|hoc|study|công việc|cong viec|work)\s*$",
        "",
        target_text,
        flags=re.IGNORECASE,
    ).strip()

    shortcut = CHROME_SHORTCUTS.get(target_text)
    if shortcut is None:
        return False

    profile, profile_name = _chrome_profile_from_explicit_command(command)

    # Zalo, Gmail và GitHub mặc định dùng Profile 1 (học). Người dùng vẫn có thể
    # nói rõ "cá nhân" để ghi đè quy tắc này.
    if profile is None and target_text in {"zalo", "zalo web", "gmail", "mail", "github"}:
        profile, profile_name = STUDY_PROFILE, "Học / ChatGPT"

    if profile is None:
        message = (
            f"ℹ️ Hãy nói rõ profile, ví dụ: `mở {target_text} học` "
            f"hoặc `mở {target_text} cá nhân`."
        )
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    site_name, url = shortcut
    opened = open_chrome(profile, url)

    if target_text in {"zalo", "zalo web"}:
        message = "✅ Đã mở Zalo." if opened else "❌ Không thể mở Zalo lúc này."
    elif target_text in {"gmail", "mail"}:
        message = "✅ Đã mở Gmail." if opened else "❌ Không thể mở Gmail lúc này."
    elif target_text in {"github", "chatgpt", "chat gpt"}:
        message = (
            f"✅ Đã mở {site_name}."
            if opened is not False
            else f"❌ Không thể mở {site_name} lúc này."
        )
    else:
        message = (
            f"✅ Đã mở {site_name} bằng Chrome {profile_name}."
            if opened is not False
            else f"❌ Không thể mở {site_name} lúc này."
        )
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


def handle_chrome_shortcut_close(command):
    """Close only a tracked window paired with a Chrome website shortcut."""
    command_clean = normalize_core_text(command)
    match = re.match(
        r"^(?:tắt|tat|đóng|dong|thoát|thoat|close)(?:\s+tab)?\s+(.+)$",
        command_clean,
    )
    if not match:
        return False
    target_text = re.sub(
        r"\s+(?:bằng\s+)?(?:tài\s+khoản\s+)?(?:cá nhân|ca nhan|personal|học tập|hoc tap|học|hoc|study|công việc|cong viec|work)\s*$",
        "",
        match.group(1).strip(),
        flags=re.IGNORECASE,
    ).strip()
    shortcut = CHROME_SHORTCUTS.get(target_text)
    if shortcut is None or target_text == "chrome":
        return False
    profile, _ = _chrome_profile_from_explicit_command(command)
    if target_text in {"zalo", "zalo web"} and profile != PERSONAL_PROFILE:
        return False
    if profile is None and target_text == "github":
        profile = STUDY_PROFILE
    if profile is None:
        message = (
            f"ℹ️ Hãy nói rõ profile, ví dụ: `tắt {target_text} học` "
            f"hoặc `tắt {target_text} cá nhân`."
        )
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True
    site_name, url = shortcut
    site_key = {
        "chatgpt.com": "chatgpt",
        "mail.google.com": "gmail",
        "drive.google.com": "drive",
        "calendar.google.com": "calendar",
        "github.com": "github",
        "chat.zalo.me": "zalo",
        "www.google.com": "google",
    }.get((urlparse(url).hostname or "").lower())
    if site_key is None:
        return False
    success, detail = CHROME_WINDOWS.close_site_window(profile, site_key, site_name)
    icon = "✅" if success else "ℹ️"
    message = f"{icon} {detail}"
    set_command_response(message)
    print(f"Jarvis: {message}")
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


def extract_page_url(line):
    """Extract a URL from current and legacy chrome-devtools-mcp page lines."""
    match = re.search(r"https?://[^\s)\]}]+", str(line), re.IGNORECASE)
    return match.group(0).rstrip(".,;") if match else ""


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


async def extract_soundcloud_tracks_from_page(session, limit=10):
    """Read playable SoundCloud track links from the currently selected page."""
    script = r'''() => {
        const tracks = new Map();
        const blocked = new Set([
            'discover', 'search', 'you', 'stream', 'upload', 'settings',
            'stations', 'charts', 'likes', 'history', 'terms', 'pages',
            'mobile', 'creators', 'jobs'
        ]);
        const selectors = [
            '.soundList__item a.soundTitle__title[href]',
            '.searchList__item a.soundTitle__title[href]',
            '.systemPlaylistTrackList__item a.trackItem__trackTitle[href]',
            '.playbackSoundBadge__titleLink[href]',
            'a[itemprop="url"][href]'
        ];
        for (const a of document.querySelectorAll(selectors.join(','))) {
            try {
                const u = new URL(a.href, location.href);
                const host = u.hostname.toLowerCase();
                if (host !== 'soundcloud.com' && !host.endsWith('.soundcloud.com')) continue;
                const parts = u.pathname.split('/').filter(Boolean);
                if (parts.length < 2 || blocked.has(parts[0].toLowerCase())) continue;
                const title = (
                    a.getAttribute('title') || a.textContent ||
                    a.getAttribute('aria-label') || ''
                ).replace(/\s+/g, ' ').trim();
                if (!title || title.length < 2) continue;
                u.search = '';
                u.hash = '';
                const url = u.href;
                const current = tracks.get(url);
                if (!current || title.length > current.title.length) {
                    tracks.set(url, {title, url});
                }
            } catch (_) {
            }
        }
        return Array.from(tracks.values());
    }'''
    result = await session.call_tool(
        "evaluate_script", arguments={"function": script}
    )
    raw_tracks = _json_from_mcp_result(result, default=[])
    if not isinstance(raw_tracks, list):
        return []

    tracks = []
    seen = set()
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        title = clean_title(str(item.get("title", "")))
        url = str(item.get("url", "")).strip()
        if not title or not _is_soundcloud_track_url(url):
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        tracks.append({"title": title, "url": url})
        if len(tracks) >= limit:
            break
    return tracks


# ==========================================================
# TẠO MCP SERVER
# ==========================================================

MCP_ERROR_LOG_MAX_BYTES = 5 * 1024 * 1024
MCP_ERROR_LOG_BACKUP_COUNT = 2
MCP_IGNORED_STDERR_LINES = {
    "No handler registered for issue code PerformanceIssue",
}


class MCPErrorLogFilter:
    """Pipe MCP stderr to disk while dropping one known upstream warning."""

    def __init__(self, log_path):
        self._log = open(log_path, "ab", buffering=0)
        self._read_fd, self._write_fd = os.pipe()
        self._closed = False
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def fileno(self):
        return self._write_fd

    def _drain(self):
        buffer = b""
        with os.fdopen(self._read_fd, "rb", buffering=0) as source:
            while chunk := source.read(8192):
                buffer += chunk
                lines = buffer.split(b"\n")
                buffer = lines.pop()
                for line in lines:
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    if text not in MCP_IGNORED_STDERR_LINES:
                        self._log.write(line + b"\n")
            if buffer:
                text = buffer.decode("utf-8", errors="replace").rstrip("\r")
                if text not in MCP_IGNORED_STDERR_LINES:
                    self._log.write(buffer)

    def close(self):
        if self._closed:
            return
        self._closed = True
        os.close(self._write_fd)
        # stdio_client has already stopped the MCP child before this method is
        # called, so EOF is guaranteed. Wait for the complete stderr drain
        # before closing the destination file; never race a live writer.
        self._thread.join()
        self._log.close()


def rotate_mcp_error_log(
    log_path,
    *,
    max_bytes=MCP_ERROR_LOG_MAX_BYTES,
    backup_count=MCP_ERROR_LOG_BACKUP_COUNT,
):
    """Rotate an oversized MCP stderr log before opening a new session."""
    path = Path(log_path)
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
        if backup_count <= 0:
            path.unlink()
            return True
        Path(f"{path}.{backup_count}").unlink(missing_ok=True)
        for index in range(backup_count - 1, 0, -1):
            source = Path(f"{path}.{index}")
            if source.exists():
                source.replace(Path(f"{path}.{index + 1}"))
        path.replace(Path(f"{path}.1"))
        return True
    except OSError as error:
        print(f"Jarvis: Không thể xoay mcp_errors.log: {error}")
        return False


def create_mcp_server():
    return StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "chrome-devtools-mcp@latest",
            "--browserUrl",
            JARVIS_CHROME_DEBUG_URL,
            "--no-usage-statistics",
            "--no-performance-crux",
        ],
    )


def _mcp_browser_process_is_current(tracked_pid):
    """Check that a persistent MCP session still targets the live browser."""
    current_pid = _jarvis_debug_port_owner_pid()
    return bool(
        tracked_pid is not None
        and current_pid == tracked_pid
        and _process_has_jarvis_chrome_args(current_pid)
    )


async def get_mcp_session():
    """Tạo MCP một lần và giữ session cho tới khi Jarvis thoát."""
    global mcp_stdio_context
    global mcp_session_context
    global mcp_session
    global mcp_errlog
    global mcp_browser_pid
    global mcp_page_topology_changed

    if mcp_session is not None:
        if (
            not mcp_page_topology_changed
            and _mcp_browser_process_is_current(mcp_browser_pid)
        ):
            return mcp_session
        # Chrome automation có thể đã bị người dùng đóng rồi mở lại. MCP cũ
        # vẫn còn process nhưng không tự bám sang browser PID mới, khiến mọi
        # lệnh YouTube/Gmail/Zalo trả về rỗng.
        await close_mcp_session()

    print()
    print("Jarvis: Đang kết nối Chrome...")

    if not ensure_jarvis_chrome():
        raise RuntimeError("Không khởi động được Chrome riêng của Jarvis.")
    browser_pid = _jarvis_debug_port_owner_pid()
    if browser_pid is None or not _process_has_jarvis_chrome_args(browser_pid):
        raise RuntimeError("Không xác minh được Chrome riêng của Jarvis.")

    server = create_mcp_server()

    try:
        # chrome-devtools-mcp đôi khi ghi cảnh báo lặp lại vào stderr
        # (ví dụ PerformanceIssue). Chuyển stderr sang file log để Terminal
        # của Jarvis không bị spam, nhưng vẫn giữ log để kiểm tra khi cần.
        mcp_log_path = Path(__file__).resolve().parent / "mcp_errors.log"
        rotate_mcp_error_log(mcp_log_path)
        mcp_errlog = MCPErrorLogFilter(mcp_log_path)

        mcp_stdio_context = stdio_client(
            server,
            errlog=mcp_errlog,  # type: ignore[arg-type]
        )
        read, write = await mcp_stdio_context.__aenter__()

        mcp_session_context = ClientSession(read, write)
        mcp_session = await mcp_session_context.__aenter__()
        await mcp_session.initialize()
        mcp_browser_pid = browser_pid
        mcp_page_topology_changed = False

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
    global mcp_browser_pid

    session_context = mcp_session_context
    stdio_context = mcp_stdio_context
    errlog = mcp_errlog

    mcp_session = None
    mcp_session_context = None
    mcp_stdio_context = None
    mcp_errlog = None
    mcp_browser_pid = None

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

async def _find_owned_mcp_pages(session, site_key):
    owned_ids = set(managed_cdp_targets.get(site_key, set()))
    if not owned_ids:
        return []
    pages = _cdp_pages()
    if pages is None:
        return []
    owned_urls = {
        str(page.get("url", "")).rstrip("/")
        for page in pages
        if (
            page.get("id") in owned_ids
            and page.get("url")
            and _automation_site_key(page.get("url", "")) == site_key
        )
    }
    if not owned_urls:
        return []
    pages_result = await session.call_tool("list_pages", arguments={})
    matches = []
    for line in result_to_text(pages_result).splitlines():
        page_id = extract_page_id(line)
        page_url = extract_page_url(line).rstrip("/")
        if page_id is not None and page_url in owned_urls:
            matches.append((page_id, line))
    return matches


async def find_youtube_page(session):
    matches = await _find_owned_mcp_pages(session, "youtube")
    return matches[0][0] if len(matches) == 1 else None


async def find_soundcloud_page(session):
    matches = await _find_owned_mcp_pages(session, "soundcloud")
    return matches[0][0] if len(matches) == 1 else None


async def find_zalo_page(session):
    matches = await _find_owned_mcp_pages(session, "zalo")
    return matches[0][0] if len(matches) == 1 else None


async def find_gmail_page(session):
    """Return only the unique lifecycle-owned Gmail MCP page."""
    matches = await _find_owned_mcp_pages(session, "gmail")
    if len(matches) != 1:
        return None
    expected_account = configured_gmail_account()
    if expected_account:
        account_matches = [
            (page_id, line) for page_id, line in matches
            if expected_account in line.lower()
        ]
        return account_matches[0][0] if len(account_matches) == 1 else None
    return matches[0][0]


GMAIL_INBOX_SCRIPT = r'''() => {
  const host = location.hostname.toLowerCase();
  if (host === 'accounts.google.com' || document.querySelector('input[type="email"]')) {
    return {ok:false, loginRequired:true, messages:[]};
  }
  const rows = [...document.querySelectorAll('tr.zA')].slice(0, 30);
  if (!rows.length) {
    const ready = !!document.querySelector('[role="main"], a[href*="#inbox"]');
    return {ok:ready, loading:!ready, messages:[]};
  }
  const fingerprint = value => {
    let hash = 0xcbf29ce484222325n;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= BigInt(value.charCodeAt(index));
      hash = BigInt.asUintN(64, hash * 0x100000001b3n);
    }
    return `fp:${hash.toString(16).padStart(16, '0')}`;
  };
  const messages = rows.map((row, index) => {
    const senderNode = row.querySelector('.yW span[email], .yX.xY span[email], .yW span');
    const subjectNode = row.querySelector('.bog, [data-thread-id] .bog');
    const snippetNode = row.querySelector('.y2');
    const timeNode = row.querySelector('td.xW span[title], td.xW span, td.xW');
    const link = row.querySelector('a[href]');
    const sender = (senderNode?.getAttribute('name') || senderNode?.textContent || '').trim();
    const email = (senderNode?.getAttribute('email') || '').trim();
    const subject = (subjectNode?.textContent || '').trim();
    const snippet = (snippetNode?.textContent || '').replace(/^\s*[-–—]\s*/, '').trim();
    const time = (timeNode?.getAttribute('title') || timeNode?.textContent || '').trim();
    const id = row.getAttribute('data-legacy-thread-id') ||
      row.getAttribute('data-thread-id') || link?.getAttribute('href') ||
      fingerprint([sender, email, subject, snippet, time].join('|'));
    return {id, sender, email, subject, snippet, time,
      unread: row.classList.contains('zE') || row.getAttribute('aria-label')?.toLowerCase().includes('unread')};
  }).filter(item => item.sender || item.subject || item.snippet);
  return {ok:true, loginRequired:false, messages};
}'''


async def read_gmail_inbox(limit=20, unread_only=False, bring_to_front=False, folder="inbox"):
    """Read visible Gmail rows without opening individual messages."""
    session = await get_mcp_session()
    page_id = await find_gmail_page(session)
    if page_id is None:
        return {"ok": False, "reason": "not_open", "messages": []}
    selected = await session.call_tool(
        "select_page", arguments={"pageId": page_id, "bringToFront": bring_to_front}
    )
    if _is_mcp_tool_error(selected):
        return {"ok": False, "reason": "unavailable", "messages": []}
    restore_inbox = folder == "spam"
    if restore_inbox:
        navigated = await session.call_tool(
            "navigate_page", arguments={
                "type": "url", "url": configured_gmail_folder_url("spam"),
            },
        )
        if _is_mcp_tool_error(navigated):
            return {"ok": False, "reason": "unavailable", "messages": []}
    result = {}
    try:
        for attempt in range(5):
            result = _json_from_mcp_result(
                await session.call_tool(
                    "evaluate_script", arguments={"function": GMAIL_INBOX_SCRIPT}
                ),
                default={},
            )
            if isinstance(result, dict) and (result.get("ok") or result.get("loginRequired")):
                break
            if attempt < 4:
                await asyncio.sleep(0.6)
    finally:
        if restore_inbox:
            try:
                await session.call_tool(
                    "navigate_page", arguments={
                        "type": "url", "url": configured_gmail_folder_url("inbox"),
                    },
                )
            except Exception:
                pass
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unavailable", "messages": []}
    if result.get("loginRequired"):
        return {"ok": False, "reason": "login", "messages": []}
    if not result.get("ok"):
        return {"ok": False, "reason": "loading", "messages": []}
    messages = []
    for item in result.get("messages", []):
        if not isinstance(item, dict) or (unread_only and not item.get("unread")):
            continue
        clean = {
            key: str(item.get(key, "")).strip()
            for key in ("id", "sender", "email", "subject", "snippet", "time")
        }
        clean["unread"] = bool(item.get("unread"))
        if not clean["id"]:
            raw = "|".join(clean[key] for key in ("sender", "subject", "time", "snippet"))
            clean["id"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        messages.append(clean)
        if len(messages) >= max(1, min(int(limit), 30)):
            break
    return {"ok": True, "reason": "", "messages": messages}


async def mark_gmail_messages_read(messages, folder="inbox"):
    """Mark only the exact unread rows that Jarvis has just summarized."""
    ids = [str(item.get("id", "")).strip() for item in messages if item.get("unread")]
    ids = [value for value in ids if value][:30]
    if not ids:
        return 0
    session = await get_mcp_session()
    page_id = await find_gmail_page(session)
    if page_id is None:
        return 0
    selected = await session.call_tool(
        "select_page", arguments={"pageId": page_id, "bringToFront": False}
    )
    if _is_mcp_tool_error(selected):
        return 0
    restore_inbox = folder == "spam"
    if restore_inbox:
        result = await session.call_tool(
            "navigate_page", arguments={
                "type": "url", "url": configured_gmail_folder_url("spam"),
            },
        )
        if _is_mcp_tool_error(result):
            return 0
    script = f'''async () => {{
      const wanted = new Set({json.dumps(ids, ensure_ascii=False)});
      const normalize = value => (value || '').normalize('NFD')
        .replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
      const fingerprint = value => {{
        let hash = 0xcbf29ce484222325n;
        for (let index = 0; index < value.length; index += 1) {{
          hash ^= BigInt(value.charCodeAt(index));
          hash = BigInt.asUintN(64, hash * 0x100000001b3n);
        }}
        return `fp:${{hash.toString(16).padStart(16, '0')}}`;
      }};
      const rowId = row => {{
        const senderNode = row.querySelector('.yW span[email], .yX.xY span[email], .yW span');
        const subjectNode = row.querySelector('.bog, [data-thread-id] .bog');
        const snippetNode = row.querySelector('.y2');
        const timeNode = row.querySelector('td.xW span[title], td.xW span, td.xW');
        const link = row.querySelector('a[href]');
        const sender = (senderNode?.getAttribute('name') || senderNode?.textContent || '').trim();
        const email = (senderNode?.getAttribute('email') || '').trim();
        const subject = (subjectNode?.textContent || '').trim();
        const snippet = (snippetNode?.textContent || '').replace(/^\\s*[-–—]\\s*/, '').trim();
        const time = (timeNode?.getAttribute('title') || timeNode?.textContent || '').trim();
        return row.getAttribute('data-legacy-thread-id') ||
          row.getAttribute('data-thread-id') || link?.getAttribute('href') ||
          fingerprint([sender, email, subject, snippet, time].join('|'));
      }};
      const rows = [...document.querySelectorAll('tr.zA')]
        .filter(row => wanted.has(rowId(row)) && row.classList.contains('zE'));
      const targetIds = rows.map(rowId);
      const remaining = [];
      for (const row of rows) {{
        const button = [...row.querySelectorAll('[data-tooltip], [aria-label], [title]')].find(el => {{
          const label = normalize(el.getAttribute('data-tooltip') || el.getAttribute('aria-label') || el.title);
          return label.includes('mark as read') || label.includes('danh dau la da doc') ||
            label.includes('danh dau da doc');
        }});
        if (button) {{ button.click(); }} else {{ remaining.push(row); }}
      }}
      if (remaining.length) {{
        for (const row of remaining) {{
          const checkbox = row.querySelector('[role="checkbox"]');
          if (checkbox && checkbox.getAttribute('aria-checked') !== 'true') checkbox.click();
        }}
        await new Promise(resolve => setTimeout(resolve, 250));
        const toolbarRoot = document.querySelector('[gh="mtb"], [role="toolbar"]');
        const toolbar = [...(toolbarRoot?.querySelectorAll('[data-tooltip], [aria-label], [title]') || [])].find(el => {{
          const label = normalize(el.getAttribute('data-tooltip') || el.getAttribute('aria-label') || el.title);
          return label.includes('mark as read') || label.includes('danh dau la da doc') ||
            label.includes('danh dau da doc');
        }});
        if (toolbar) {{ toolbar.click(); }}
      }}
      let stillUnread = new Set(targetIds);
      for (let attempt = 0; attempt < 10 && stillUnread.size; attempt += 1) {{
        await new Promise(resolve => setTimeout(resolve, 300));
        stillUnread = new Set(
          [...document.querySelectorAll('tr.zA.zE')]
            .map(rowId).filter(id => wanted.has(id))
        );
      }}
      const marked = targetIds.filter(id => !stillUnread.has(id)).length;
      return {{ok:true, marked}};
    }}'''
    try:
        result = _json_from_mcp_result(
            await session.call_tool("evaluate_script", arguments={"function": script}),
            default={},
        )
        return int(result.get("marked", 0)) if isinstance(result, dict) else 0
    finally:
        if restore_inbox:
            try:
                await session.call_tool(
                    "navigate_page", arguments={
                        "type": "url", "url": configured_gmail_folder_url("inbox"),
                    },
                )
            except Exception:
                pass


def _gmail_error_message(reason):
    if reason == "not_open":
        return "ℹ️ Chưa có tab Gmail của Jarvis. Hãy dùng `mở gmail` trước."
    if reason == "login":
        return "🔐 Gmail chưa đăng nhập. Hãy đăng nhập một lần trong cửa sổ Gmail của Jarvis."
    if reason == "loading":
        return "⏳ Gmail chưa tải xong. Hãy chờ một chút rồi thử lại."
    return "❌ Jarvis chưa thể đọc Gmail lúc này."


def format_gmail_preview_summary(messages, limit=10):
    """Provide a bounded useful fallback when the local language model is busy."""
    lines = []
    for item in messages[:max(1, limit)]:
        sender = str(item.get("sender") or item.get("email") or "Không rõ người gửi").strip()
        subject = str(item.get("subject") or "Không có tiêu đề").strip()
        snippet = re.sub(r"\s+", " ", str(item.get("snippet", ""))).strip()[:240]
        time_text = str(item.get("time", "")).strip()
        heading = f"• **{sender}** — {subject}"
        if time_text:
            heading += f" ({time_text})"
        lines.append(heading + (f"\n  {snippet}" if snippet else ""))
    remaining = len(messages) - len(lines)
    if remaining > 0:
        lines.append(f"• Còn {remaining} thư khác chưa hiển thị.")
    return "\n\n".join(lines)


def meaningful_spam_messages(messages):
    """Conservative fallback filter used only when Ollama is unavailable."""
    meaningful = (
        "bao mat", "security", "dang nhap", "login", "mat khau", "password",
        "ma xac minh", "verification", "otp", "giao dich", "transaction",
        "thanh toan", "payment", "hoa don", "invoice", "cong viec", "work",
        "truong", "school", "deadline", "lich", "schedule", "tai khoan", "account",
    )
    promotional = (
        "giam gia", "sale", "uu dai", "offer", "khuyen mai", "promotion",
        "unsubscribe", "huy dang ky", "casino", "betting", "crypto bonus",
    )
    selected = []
    for item in messages:
        text = normalize_core_text(" ".join(
            str(item.get(key, "")) for key in ("sender", "subject", "snippet")
        ))
        if any(term in text for term in meaningful) and not any(term in text for term in promotional):
            selected.append(item)
    return selected


def fallback_gmail_tasks(messages):
    """Extract conservative action hints without inventing deadlines or actions."""
    action_terms = (
        "can ", "hay ", "vui long", "xac nhan", "hoan thanh", "thanh toan",
        "kiem tra", "phan hoi", "tra loi", "deadline", "before ", "please ",
        "confirm", "complete", "payment", "reply", "action required",
    )
    tasks = []
    for item in messages:
        source = " ".join(str(item.get(key, "")) for key in ("subject", "snippet"))
        if not any(term in normalize_core_text(source) for term in action_terms):
            continue
        sender = str(item.get("sender") or item.get("email") or "người gửi").strip()
        subject = str(item.get("subject") or "thư không có tiêu đề").strip()
        tasks.append(f"Kiểm tra yêu cầu trong “{subject}” từ {sender}.")
        if len(tasks) >= 5:
            break
    return tasks


async def summarize_gmail(*, unread_only=False, limit=10, folder="inbox", meaningful_only=False):
    """Summarize inbox previews locally and return private output only to the caller."""
    try:
        result = await read_gmail_inbox(
            limit=limit, unread_only=unread_only, bring_to_front=False, folder=folder
        )
        if not result["ok"]:
            set_command_response(_gmail_error_message(result["reason"]))
            return True
        messages = result["messages"]
        if not messages:
            label = "thư rác" if folder == "spam" else "thư chưa đọc" if unread_only else "thư"
            set_command_response(f"📭 Không thấy {label} nào trong danh sách Gmail hiện tại.")
            return True
        try:
            ai_messages = [{
                **item,
                "snippet": str(item.get("snippet", ""))[:320],
            } for item in messages[:10]]
            summary = await asyncio.to_thread(
                CORE.local_ai.summarize_gmail, ai_messages,
                meaningful_only=meaningful_only, folder=folder,
            )
            if len(messages) > len(ai_messages):
                summary += f"\n\nℹ️ Còn {len(messages) - len(ai_messages)} thư khác trong phạm vi đã đọc."
        except Exception:
            fallback_messages = meaningful_spam_messages(messages) if meaningful_only else messages
            if meaningful_only and not fallback_messages:
                try:
                    marked = await mark_gmail_messages_read(messages, folder=folder)
                except Exception:
                    marked = 0
                set_command_response(
                    "📭 Chưa thấy thư rác nào có dấu hiệu chứa nội dung hữu ích hoặc cần hành động."
                    + (f"\n✅ Đã đánh dấu {marked} thư đã kiểm tra là đã đọc." if marked else "")
                )
                return True
            fallback_tasks = fallback_gmail_tasks(fallback_messages)
            summary = (
                "⚠️ AI local chưa hoàn tất kịp; đây là bản tóm lược trực tiếp từ hộp thư:\n\n"
                + format_gmail_preview_summary(fallback_messages)
                + "\n\n✅ **VIỆC CẦN LÀM**\n"
                + ("\n".join(f"• {task}" for task in fallback_tasks)
                   if fallback_tasks else "• Chưa thấy yêu cầu hành động rõ ràng.")
            )
        label = (
            "THƯ RÁC CÓ Ý NGHĨA" if folder == "spam"
            else "GMAIL CHƯA ĐỌC" if unread_only else "GMAIL GẦN ĐÂY"
        )
        unread_count = sum(1 for item in messages if item.get("unread"))
        try:
            marked = await mark_gmail_messages_read(messages, folder=folder)
        except Exception:
            marked = 0
        mark_note = (
            f"\n\n✅ Đã đánh dấu {marked} thư vừa xử lý là đã đọc."
            if marked else
            "\n\n⚠️ Chưa thể đánh dấu thư là đã đọc; Jarvis sẽ không tuyên bố đã làm việc này."
            if unread_count else ""
        )
        set_command_response(
            f"📧 **TÓM TẮT {label}**\n\n{summary}{mark_note}",
            spoken_message="Đã tóm tắt Gmail theo phạm vi yêu cầu.",
        )
        print("Jarvis: Đã tóm tắt Gmail theo phạm vi yêu cầu.")
        return True
    except Exception:
        set_command_response("❌ Không thể tóm tắt Gmail lúc này.")
        print("Jarvis: Không thể tóm tắt Gmail lúc này.")
        return True


async def close_managed_web_tab(site_key, site_name, url_needles):
    """Close only exact CDP targets recorded during this service lifecycle."""
    del url_needles  # Kept for command-call compatibility; URL matching is unsafe.
    owned = set(managed_cdp_targets.get(site_key, set()))
    if not owned:
        message = f"❌ Không còn xác minh được tab {site_name} nào do Jarvis mở."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False
    pages = _cdp_pages()
    if pages is None:
        message = f"❌ Không thể xác minh tab {site_name}; giữ ownership để thử lại."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False
    current_ids = {page.get("id") for page in pages if page.get("id")}
    targets = owned & current_ids
    if not targets:
        managed_cdp_targets.setdefault(site_key, set()).clear()
        message = f"ℹ️ Tab {site_name} do Jarvis mở đã được đóng trước đó."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False

    # Chrome thường tự thoát khi đóng page cuối cùng, làm mất luôn endpoint
    # xác minh. Tạo một tab trống không được quản lý trước để chỉ đóng đúng
    # Zalo/Gmail mục tiêu mà vẫn giữ browser automation sống.
    if targets == current_ids:
        try:
            request = Request(
                f"{JARVIS_CHROME_DEBUG_URL}/json/new?{quote_plus('about:blank')}",
                method="PUT",
            )
            with urlopen(request, timeout=4) as response:
                blank_page = json.loads(response.read().decode("utf-8"))
            if not isinstance(blank_page, dict) or blank_page.get("type") != "page":
                raise ValueError("CDP did not create a page")
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            message = (
                f"❌ Không thể tạo tab an toàn trước khi đóng {site_name}; "
                "Jarvis giữ nguyên tab hiện tại."
            )
            set_command_response(message)
            print(f"Jarvis: {message}")
            return False

    for target_id in targets:
        try:
            with urlopen(
                f"{JARVIS_CHROME_DEBUG_URL}/json/close/{target_id}", timeout=4
            ):
                pass
        except OSError:
            pass

    # /json/close trả về trước khi target biến mất hoàn toàn khỏi /json/list.
    # Chờ tối đa 2 giây để tránh báo thất bại giả dù tab đã đóng thành công.
    pages_after = None
    remaining = set(targets)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        pages_after = _cdp_pages()
        if pages_after is None:
            break
        current_after = {page.get("id") for page in pages_after if page.get("id")}
        remaining = targets & current_after
        if not remaining:
            break
        await asyncio.sleep(0.1)

    failed = sorted(targets if pages_after is None else remaining)
    closed = targets - set(failed)
    managed_cdp_targets.setdefault(site_key, set()).difference_update(closed)
    if failed:
        message = f"❌ Không thể đóng chính xác {len(failed)} tab {site_name}."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False
    message = f"✅ Đã đóng {site_name}."
    set_command_response(message, spoken_message=message)
    print(f"Jarvis: {message}")
    return True


def parse_zalo_date(value):
    """Parse hôm nay/hôm qua or dd/mm[/yyyy] from a Zalo command."""
    plain = normalize_core_text(value)
    now = datetime.now()
    if "hom nay" in plain:
        return now.date()
    if "hom qua" in plain:
        return (now - timedelta(days=1)).date()
    match = re.search(r"(?:ngay\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", plain)
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    year = int(match.group(3) or now.year)
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def parse_zalo_request(command):
    """Return date, optional group and optional grounded question."""
    plain = normalize_core_text(command)
    selected_date = parse_zalo_date(command)
    group = None
    question = None
    group_match = re.search(
        r"(?:nhom|trong nhom)\s+(.+?)(?=\s+(?:hom nay|hom qua|ngay\s+\d|hoi\s+|ve\s+su kien|su kien)|$)",
        plain,
    )
    if group_match:
        group = group_match.group(1).strip()
    question_match = re.search(r"\bve\s+(.+)$", plain)
    if not question_match and plain.startswith(("hoi zalo ", "cho toi biet zalo ", "kiem tra zalo ")):
        question_match = re.search(r"\b(?:su kien|noi dung)\s+(.+)$", plain)
    if question_match:
        question = question_match.group(1).strip()
    return selected_date, group, question


async def summarize_zalo_work(max_chats=20, selected_date=None, group_query=None, question=None):
    """Read visible chats tagged Công việc and summarize them with local AI."""
    try:
        session = None
        page_id = None
        # Với nhiều Chrome profile, autoConnect đôi khi bám vào browser khác.
        # Tạo lại phiên tối đa 3 lần để tìm đúng tab Zalo của profile học.
        for attempt in range(3):
            session = await get_mcp_session()
            page_id = await find_zalo_page(session)
            if page_id is not None:
                break
            if attempt < 2:
                await close_mcp_session()
                await asyncio.sleep(0.4)
        if page_id is None:
            message = "❌ Không tìm thấy tab Zalo Web. Hãy dùng `mở zalo công việc` trước."
            set_command_response(message)
            return True

        await session.call_tool(
            "select_page", arguments={"pageId": page_id, "bringToFront": True}
        )
        prepare_script = r'''async () => {
            const open = document.querySelector('[data-id="div_MiniLabel_OpenLabelList"]');
            if (!open) {
              return {ok:false, error:'Không mở được bộ lọc Công việc.'};
            }
            open.click();
            await new Promise(r => setTimeout(r, 350));
            const labels = [...document.querySelectorAll('[data-id="div_DetailLabelList_Label"]')];
            const label = labels.find(el => (el.textContent || '').trim() === 'Công việc');
            if (!label) return {ok:false, error:'Không tìm thấy nhãn Công việc.'};
            if (!label.classList.contains('active')) {
              label.click();
              await new Promise(r => setTimeout(r, 700));
            } else {
              open.click();
              await new Promise(r => setTimeout(r, 200));
            }
            const rows = [...document.querySelectorAll('[data-id="div_TabMsg_ThrdChItem"].msg-item')];
            return {ok:true, count:rows.length, titles:rows.map(row =>
              (row.querySelector('.conv-item-title__name')?.textContent || '').trim())};
        }'''
        prepared = {}
        for prepare_attempt in range(4):
            prepared = _json_from_mcp_result(
                await session.call_tool(
                    "evaluate_script", arguments={"function": prepare_script}
                ),
                default={},
            )
            if isinstance(prepared, dict) and prepared.get("ok"):
                break
            if prepare_attempt < 3:
                await asyncio.sleep(0.6)
        if not isinstance(prepared, dict) or not prepared.get("ok"):
            detail = prepared.get("error", "Giao diện Zalo đã thay đổi.") if isinstance(prepared, dict) else "Giao diện Zalo đã thay đổi."
            set_command_response(f"❌ Không thể đọc nhãn Công việc: {detail}")
            return True

        titles = [str(title) for title in prepared.get("titles", [])]
        indices = list(range(min(int(prepared.get("count", 0)), max_chats)))
        if group_query:
            normalized_query = normalize_core_text(group_query)
            ranked = sorted(
                ((SequenceMatcher(None, normalized_query, normalize_core_text(title)).ratio(), index)
                 for index, title in enumerate(titles)),
                reverse=True,
            )
            if not ranked or (ranked[0][0] < 0.45 and normalized_query not in normalize_core_text(titles[ranked[0][1]])):
                set_command_response(f"ℹ️ Không tìm thấy nhóm Zalo gần với `{group_query}` trong nhãn Công việc.")
                return True
            indices = [ranked[0][1]]

        conversations = []
        date_text = selected_date.strftime("%d/%m/%Y") if selected_date else ""
        for index in indices:
            script = f'''async () => {{
              const rows = [...document.querySelectorAll('[data-id="div_TabMsg_ThrdChItem"].msg-item')];
              const row = rows[{index}];
              if (!row) return {{ok:false}};
              const title = (row.querySelector('.conv-item-title__name')?.textContent || 'Nhóm {index + 1}').trim();
              const preview = (row.querySelector('.z-conv-message')?.innerText || '').trim();
              (row.querySelector('.conv-item') || row).click();
              await new Promise(r => setTimeout(r, 650));
              const area = document.querySelector('main .message-view__scroll');
              let content = '';
              const wantedDate = {json.dumps(date_text)};
              if (wantedDate && area) {{
                const shortDate = wantedDate.slice(0, 5);
                const today = new Date();
                const pad = n => String(n).padStart(2, '0');
                const todayText = `${{pad(today.getDate())}}/${{pad(today.getMonth()+1)}}/${{today.getFullYear()}}`;
                const blocks = [...area.querySelectorAll('.block-date')];
                content = blocks.filter(block => {{
                  const first = (block.innerText || '').split('\\n')[0].trim().toLowerCase();
                  return first.includes(wantedDate) || first.includes(shortDate) ||
                    (wantedDate === todayText && first.includes('hôm nay'));
                }}).map(block => block.innerText).join('\\n\\n');
              }} else {{
                content = (area?.innerText || '').trim();
              }}
              return {{ok:true, title, preview, content:content.slice(-1400)}};
            }}'''
            item = _json_from_mcp_result(
                await session.call_tool("evaluate_script", arguments={"function": script}),
                default={},
            )
            if not isinstance(item, dict) or not item.get("ok"):
                continue
            content = str(item.get("content", "")).strip()
            preview = str(item.get("preview", "")).strip()
            if selected_date and not content:
                continue
            if not content and (not preview or "Chưa có tin nhắn" in preview):
                continue
            conversations.append({
                "nhom": clean_title(str(item.get("title", "Nhóm không tên"))),
                "noi_dung": content or preview,
            })

        if not conversations:
            scope = f" ngày {date_text}" if selected_date else ""
            set_command_response(f"ℹ️ Không tìm thấy nội dung Zalo{scope} trong phạm vi đã chọn.")
            return True

        if question:
            summary = await asyncio.to_thread(CORE.local_ai.answer_zalo_question, conversations, question)
            heading = "🔎 **TRẢ LỜI TỪ ZALO CÔNG VIỆC**"
        else:
            summary = await asyncio.to_thread(CORE.local_ai.summarize_zalo_work, conversations)
            heading = "📋 **TÓM TẮT ZALO CÔNG VIỆC**"
        scope = f" — {date_text}" if selected_date else ""
        message = f"{heading}{scope}\n\n{summary}"
        set_command_response(
            message,
            spoken_message="Đã tổng hợp nội dung Zalo theo phạm vi yêu cầu.",
        )
        print("Jarvis: Đã tổng hợp nội dung Zalo theo phạm vi yêu cầu.")
        return True
    except Exception:
        message = "❌ Không thể tổng hợp Zalo lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True


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
            youtube_page_id = await find_youtube_page(session)

            if youtube_page_id is None:
                if attempt < retries:
                    await asyncio.sleep(retry_delay)
                    continue

                print("Jarvis: Không tìm thấy tab YouTube.")
                set_command_response("❌ Không tìm thấy tab YouTube đang mở.")
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
            set_command_response(
                "⏳ YouTube đã mở nhưng chưa tải được danh sách video. "
                "Hãy thử `làm mới youtube` sau ít phút."
            )
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
        set_command_response("❌ Không thể cập nhật danh sách YouTube lúc này.")
        return False


# ==========================================================
# MỞ YOUTUBE + ĐỌC TRANG CHỦ
# ==========================================================

async def open_youtube(command):
    global youtube_videos
    global youtube_page_id

    # YouTube luôn dùng profile automation riêng, nên không hỏi lại
    # Chrome Học/Cá nhân cho lệnh không ghi rõ profile.
    profile, profile_name = JARVIS_CHROME_PROFILE, JARVIS_CHROME_PROFILE_NAME

    # Danh sách UID cũ không còn đáng tin khi tab YouTube trước đã bị đóng.
    youtube_videos = []
    youtube_page_id = None

    print()

    print(
        f"Jarvis: Đang mở YouTube bằng "
        f"{profile_name}..."
    )

    opened = open_chrome(
        profile,
        "https://www.youtube.com/",
    )
    if not opened:
        message = "❌ Không thể mở cửa sổ YouTube lúc này."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    # Cho tab mới xuất hiện; read_youtube_videos() sẽ tự retry
    # cho tới khi danh sách video render xong.
    await asyncio.sleep(0.8)

    return await read_youtube_videos(
        profile,
        profile_name,
    )


# ==========================================================
# LẤY SNAPSHOT YOUTUBE HIỆN TẠI
# ==========================================================

async def get_current_youtube_videos(session, limit=20):
    global youtube_page_id

    # Re-resolve through lifecycle-owned CDP targets before every mutation.
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
        page_id, videos = await get_current_youtube_videos(session, limit=10)

        if not videos:
            print("Jarvis: Không tìm thấy video trên tab YouTube hiện tại.")
            message = (
                "❌ Không tìm thấy tab YouTube đang mở."
                if page_id is None
                else "⏳ YouTube chưa tải được danh sách video. Hãy thử lại sau ít phút."
            )
            set_command_response(message)
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
        set_command_response("❌ Không thể cập nhật danh sách YouTube lúc này.")
        return False


# ==========================================================
# MỞ VIDEO ĐÃ CHỌN
# ==========================================================

async def open_video(number):
    global youtube_videos
    global youtube_page_id

    if not youtube_videos:
        message = "ℹ️ Chưa có danh sách video. Hãy dùng `mở YouTube` trước."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    if number < 1 or number > len(youtube_videos):
        message = "ℹ️ Số video không hợp lệ."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    selected_video = youtube_videos[number - 1]
    selected_title = selected_video["title"]

    print()
    print("Jarvis: Đang mở:")
    print(selected_title)

    try:
        session = await get_mcp_session()
        selected_url = selected_video.get("url")

        if not selected_url:
            message = "❌ Video chưa có URL hợp lệ. Hãy dùng `làm mới YouTube`."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

        youtube_page_id = await find_youtube_page(session)
        if youtube_page_id is None:
            message = "❌ Không tìm thấy tab YouTube để mở video."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

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
        return True

    except Exception:
        message = "❌ Không thể mở video YouTube lúc này."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False


async def open_video_by_name(query):
    query = clean_title(query)

    if not query:
        message = "ℹ️ Bạn chưa nhập tên video."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    print()
    print(f"Jarvis: Đang tìm video: {query}")

    try:
        session = await get_mcp_session()
        _, current_videos = await get_current_youtube_videos(session, limit=40)

        if not current_videos:
            message = "❌ Không đọc được video trên tab YouTube hiện tại."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

        selected_video = find_video_by_title(current_videos, query)

        if selected_video is None:
            message = "ℹ️ Không tìm thấy video phù hợp trên trang YouTube hiện tại."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

        print("Jarvis: Đã tìm thấy:")
        print(selected_video["title"])

        selected_url = selected_video.get("url")
        if not selected_url:
            message = "❌ Video chưa có URL hợp lệ."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False

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
        return True

    except Exception:
        message = "❌ Không thể mở video YouTube lúc này."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False


async def control_youtube_playback(should_play):
    """Pause or resume the HTML5 player in Jarvis' current YouTube tab."""
    global youtube_page_id

    try:
        session = await get_mcp_session()
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


def is_youtube_now_playing_command(command):
    """Recognize read-only questions about the current YouTube player."""
    plain = re.sub(r"[?!.,;:]+$", "", normalize_core_text(command)).strip()
    return plain in {
        "youtube dang phat gi",
        "dang phat gi",
        "video dang phat",
        "video dang phat gi",
        "bai gi dang phat",
        "youtube dang mo video gi",
    }


def _format_media_time(value):
    """Format a media time value as H:MM:SS or M:SS."""
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _is_mcp_tool_error(result):
    """Support the MCP SDK's camelCase error flag and compatible fakes."""
    return bool(getattr(result, "isError", getattr(result, "is_error", False)))


def _is_youtube_watch_url(value):
    """Accept only HTTP(S) YouTube watch URLs returned by the selected tab."""
    try:
        parsed = urlparse(str(value).strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (host == "youtube.com" or host.endswith(".youtube.com"))
        and parsed.path == "/watch"
    )


def _escape_discord_text(value):
    """Neutralize Markdown and mentions in metadata controlled by YouTube."""
    text = discord.utils.escape_mentions(clean_title(str(value)))
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~])", r"\\\1", text)


async def report_youtube_now_playing():
    """Read current YouTube metadata without changing playback or page focus."""
    global youtube_page_id

    try:
        session = await get_mcp_session()
        script = r'''() => {
            const video = document.querySelector('video.html5-main-video, video');
            if (!video) {
                return {ok: false, error: 'Tab YouTube chưa mở video.'};
            }
            const text = selector =>
                (document.querySelector(selector)?.textContent || '')
                    .replace(/\s+/g, ' ').trim();
            const meta = selector =>
                (document.querySelector(selector)?.content || '').trim();
            const title =
                text('h1.ytd-watch-metadata yt-formatted-string') ||
                text('h1.title yt-formatted-string') ||
                meta('meta[name="title"]') ||
                document.title.replace(/\s*-\s*YouTube\s*$/, '').trim();
            const channel =
                text('ytd-watch-metadata #owner ytd-channel-name a') ||
                text('#owner-name a') ||
                meta('meta[itemprop="author"]');
            return {
                ok: true,
                title,
                channel,
                paused: Boolean(video.paused),
                ended: Boolean(video.ended),
                currentTime: Number.isFinite(video.currentTime) ? video.currentTime : 0,
                duration: Number.isFinite(video.duration) ? video.duration : 0,
                url: location.href
            };
        }'''
        state = None
        page_id = await find_youtube_page(session)
        # Resolve only the lifecycle-owned page. Never try a stale cached ID.
        for attempt in range(2):
            if page_id is None:
                page_id = await find_youtube_page(session)
            if page_id is None:
                break
            try:
                selected = await session.call_tool(
                    "select_page",
                    arguments={"pageId": page_id, "bringToFront": False},
                )
                if _is_mcp_tool_error(selected):
                    raise RuntimeError("MCP select_page failed")
                result = await session.call_tool(
                    "evaluate_script", arguments={"function": script}
                )
                if _is_mcp_tool_error(result):
                    raise RuntimeError("MCP evaluate_script failed")
                candidate = _json_from_mcp_result(result, default={})
                if isinstance(candidate, dict) and _is_youtube_watch_url(
                    candidate.get("url", "")
                ):
                    youtube_page_id = page_id
                    state = candidate
                    break
            except Exception:
                pass
            youtube_page_id = None
            page_id = None

        if state is None:
            youtube_page_id = None
            message = "❌ Không tìm thấy tab YouTube đang mở."
            set_command_response(message)
            print(f"Jarvis: {message}")
            return True

        if not isinstance(state, dict) or not state.get("ok"):
            detail = state.get("error") if isinstance(state, dict) else None
            message = detail or "Không đọc được trạng thái video YouTube."
            message = f"ℹ️ {message}"
        else:
            title = _escape_discord_text(state.get("title", "")) or "Không rõ tiêu đề"
            channel = _escape_discord_text(state.get("channel", ""))
            if state.get("ended"):
                playback = "Đã kết thúc"
            elif state.get("paused"):
                playback = "Tạm dừng"
            else:
                playback = "Đang phát"
            current = _format_media_time(state.get("currentTime"))
            duration = _format_media_time(state.get("duration"))
            timing = current or ""
            if current and duration and float(state.get("duration") or 0) > 0:
                timing = f"{current} / {duration}"
            lines = [f"🎵 **{title}**"]
            if channel:
                lines.append(f"Kênh: {channel}")
            lines.append(f"Trạng thái: {playback}")
            if timing:
                lines.append(f"Thời gian: {timing}")
            url = str(state.get("url", "")).strip()
            if _is_youtube_watch_url(url):
                lines.append(f"Link: {url}")
            message = "\n".join(lines)

        set_command_response(message)
        print(f"Jarvis: {message}")
        return True
    except Exception:
        print("Jarvis: Lỗi nội bộ khi đọc trạng thái YouTube.")
        message = "❌ Không thể đọc trạng thái YouTube lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True


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
        message = "ℹ️ Bạn chưa nhập nội dung cần tìm trên YouTube."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    print()
    print(f'Jarvis: Đang tìm trên YouTube: "{query}"')

    try:
        session = await get_mcp_session()

        # Luôn ưu tiên đúng tab YouTube đang được Jarvis điều khiển.
        # Không gọi google-chrome nếu tab này vẫn còn tồn tại.
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
            # YouTube luôn dùng Chrome automation riêng, không hỏi profile.
            profile, profile_name = (
                JARVIS_CHROME_PROFILE,
                JARVIS_CHROME_PROFILE_NAME,
            )

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
            set_command_response(
                "⏳ Chưa đọc được kết quả YouTube. Hãy thử `làm mới YouTube`."
            )
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

    except Exception:
        message = "❌ Không thể tìm kiếm YouTube lúc này."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False


# ==========================================================
# SOUNDCLOUD
# ==========================================================

def _is_soundcloud_url(value):
    try:
        parsed = urlparse(str(value).strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host == "soundcloud.com" or host.endswith(".soundcloud.com")
    )


def _is_soundcloud_track_url(value):
    if not _is_soundcloud_url(value):
        return False
    parsed = urlparse(str(value).strip())
    parts = [part for part in parsed.path.split("/") if part]
    blocked = {
        "discover", "search", "you", "stream", "upload", "settings",
        "stations", "charts", "likes", "history", "terms", "pages",
        "mobile", "creators", "jobs",
    }
    return len(parts) >= 2 and parts[0].casefold() not in blocked


def format_soundcloud_list(title, tracks):
    lines = [f"☁️ **{title}**", ""]
    for index, track in enumerate(tracks, start=1):
        lines.append(f"{index}. {track['title']}")
    lines.extend(["", "Dùng `mở bài SoundCloud 3` hoặc `mở bài SoundCloud <tên>`. "])
    return "\n".join(lines).rstrip()


async def get_current_soundcloud_tracks(session, limit=20, *, bring_to_front=True):
    global soundcloud_page_id
    soundcloud_page_id = await find_soundcloud_page(session)
    if soundcloud_page_id is None:
        return None, []
    try:
        selected = await session.call_tool(
            "select_page",
            arguments={
                "pageId": soundcloud_page_id,
                "bringToFront": bring_to_front,
            },
        )
        if _is_mcp_tool_error(selected):
            raise RuntimeError("MCP select_page failed")
    except Exception:
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            return None, []
        selected = await session.call_tool(
            "select_page",
            arguments={
                "pageId": soundcloud_page_id,
                "bringToFront": bring_to_front,
            },
        )
        if _is_mcp_tool_error(selected):
            return None, []
    tracks = await extract_soundcloud_tracks_from_page(session, limit=limit)
    return soundcloud_page_id, tracks


async def refresh_soundcloud_tracks(*, attempts=6):
    global soundcloud_tracks
    try:
        session = await get_mcp_session()
        page_id, tracks = None, []
        for attempt in range(attempts):
            page_id, tracks = await get_current_soundcloud_tracks(session, limit=10)
            if tracks:
                break
            if attempt + 1 < attempts:
                await asyncio.sleep(1.0)
        if not tracks:
            message = (
                "❌ Không tìm thấy tab SoundCloud đang mở."
                if page_id is None else
                "⏳ Đã mở SoundCloud nhưng chưa đọc được danh sách bài hát. "
                "Hãy thử `làm mới SoundCloud`."
            )
            set_command_response(message)
            print(f"Jarvis: {message}")
            return False
        soundcloud_tracks = tracks
        set_command_response(format_soundcloud_list("SOUNDCLOUD - DANH SÁCH MỚI", tracks))
        print(f"Jarvis: Đã cập nhật {len(tracks)} bài hát SoundCloud.")
        return True
    except Exception:
        message = "❌ Không thể cập nhật danh sách SoundCloud lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False


async def open_soundcloud():
    global soundcloud_tracks, soundcloud_page_id
    soundcloud_tracks = []
    try:
        session = await get_mcp_session()
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            opened = open_chrome(
                JARVIS_CHROME_PROFILE, "https://soundcloud.com/discover"
            )
            if not opened:
                raise RuntimeError("open failed")
            await asyncio.sleep(0.8)
        else:
            await session.call_tool(
                "select_page",
                arguments={"pageId": soundcloud_page_id, "bringToFront": True},
            )
    except Exception:
        message = "❌ Không thể mở SoundCloud lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False
    return await refresh_soundcloud_tracks()


def extract_soundcloud_search_query(command):
    for pattern in (
        r"^(?:tìm|tim)\s+soundcloud\s+(.+)$",
        r"^soundcloud\s+(?:tìm|tim)\s+(.+)$",
        r"^(?:tìm|tim)\s+(?:bài|bai|nhạc|nhac)\s+soundcloud\s+(.+)$",
    ):
        match = re.match(pattern, command.strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


async def search_soundcloud(command):
    global soundcloud_tracks, soundcloud_page_id
    query = extract_soundcloud_search_query(command)
    if not query:
        message = "ℹ️ Hãy nói nội dung cần tìm, ví dụ: `tìm SoundCloud nhạc thư giãn`."
        set_command_response(message)
        return False
    search_url = "https://soundcloud.com/search/sounds?q=" + quote_plus(query)
    try:
        session = await get_mcp_session()
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            if not open_chrome(JARVIS_CHROME_PROFILE, search_url):
                raise RuntimeError("open failed")
            await asyncio.sleep(0.8)
        else:
            selected = await session.call_tool(
                "select_page",
                arguments={"pageId": soundcloud_page_id, "bringToFront": True},
            )
            if _is_mcp_tool_error(selected):
                raise RuntimeError("select failed")
            navigated = await session.call_tool(
                "navigate_page", arguments={"type": "url", "url": search_url}
            )
            if _is_mcp_tool_error(navigated):
                raise RuntimeError("navigate failed")
        soundcloud_tracks = []
        for attempt in range(6):
            _, tracks = await get_current_soundcloud_tracks(session, limit=10)
            if tracks:
                soundcloud_tracks = tracks
                set_command_response(
                    format_soundcloud_list(f"KẾT QUẢ SOUNDCLOUD: {query}", tracks)
                )
                print(f"Jarvis: Đã tìm thấy {len(tracks)} bài trên SoundCloud.")
                return True
            if attempt < 5:
                await asyncio.sleep(1.0)
        message = "⏳ Chưa đọc được kết quả SoundCloud. Hãy thử `làm mới SoundCloud`."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False
    except Exception:
        message = "❌ Không thể tìm kiếm SoundCloud lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False


async def open_soundcloud_track(number=None, query=""):
    global soundcloud_tracks, soundcloud_page_id
    try:
        session = await get_mcp_session()
        soundcloud_page_id, current = await get_current_soundcloud_tracks(
            session, limit=40
        )
        candidates = current or soundcloud_tracks
        if soundcloud_page_id is None:
            message = "❌ Không tìm thấy tab SoundCloud đang mở."
            set_command_response(message)
            return False
        if number is not None:
            if number < 1 or number > len(candidates):
                message = "ℹ️ Số bài SoundCloud không hợp lệ."
                set_command_response(message)
                return False
            selected_track = candidates[number - 1]
        else:
            selected_track = find_video_by_title(candidates, clean_title(query))
            if selected_track is None:
                message = "ℹ️ Không tìm thấy bài phù hợp trên trang SoundCloud hiện tại."
                set_command_response(message)
                return False
        url = selected_track.get("url", "")
        if not _is_soundcloud_track_url(url):
            message = "❌ Link bài SoundCloud không hợp lệ."
            set_command_response(message)
            return False
        navigated = await session.call_tool(
            "navigate_page", arguments={"type": "url", "url": url}
        )
        if _is_mcp_tool_error(navigated):
            raise RuntimeError("navigate failed")
        # SoundCloud không tự phát khi chỉ điều hướng tới URL bài hát. Lệnh
        # "mở bài" có cùng ý nghĩa với YouTube nên chủ động bắt đầu phát.
        await asyncio.sleep(0.6)
        started = await control_soundcloud_playback(True)
        title = clean_title(selected_track.get("title", ""))
        prefix = "▶️ Đã mở và phát bài SoundCloud" if started else "✅ Đã mở bài SoundCloud"
        message = f"{prefix}{f': {title}' if title else '.'}"
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True
    except Exception:
        message = "❌ Không thể mở bài SoundCloud lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False


async def control_soundcloud_playback(should_play):
    global soundcloud_page_id
    try:
        session = await get_mcp_session()
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            message = "❌ Không tìm thấy tab SoundCloud đang mở."
            set_command_response(message)
            return False
        await session.call_tool(
            "select_page",
            arguments={"pageId": soundcloud_page_id, "bringToFront": True},
        )
        action = "play" if should_play else "pause"
        script = f'''async () => {{
            const audio = document.querySelector('audio');
            const button = document.querySelector('.playControl');
            try {{
                if (audio && '{action}' === 'pause') {{
                    audio.pause();
                }} else if (audio && '{action}' === 'play') {{
                    try {{
                        await audio.play();
                    }} catch (_) {{
                        if (button) button.click();
                        else return {{ok: false}};
                    }}
                }} else if (button) {{
                    const isPlaying = button.classList.contains('playing') ||
                        button.getAttribute('aria-label')?.toLowerCase().includes('pause');
                    if (('{action}' === 'play') !== isPlaying) button.click();
                }} else {{
                    return {{ok: false}};
                }}
                return {{ok: true}};
            }} catch (_) {{ return {{ok: false}}; }}
        }}'''
        result = await session.call_tool(
            "evaluate_script", arguments={"function": script}
        )
        state = _json_from_mcp_result(result, default={})
        if _is_mcp_tool_error(result) or not isinstance(state, dict) or not state.get("ok"):
            raise RuntimeError("playback failed")
        message = "▶️ Đã phát tiếp SoundCloud." if should_play else "⏸️ Đã tạm dừng SoundCloud."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True
    except Exception:
        message = "❌ Không thể điều khiển SoundCloud lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return False


def is_soundcloud_now_playing_command(command):
    plain = re.sub(r"[?!.,;:]+$", "", normalize_core_text(command)).strip()
    return plain in {
        "soundcloud dang phat gi", "soundcloud dang mo bai gi",
        "bai soundcloud dang phat", "bai soundcloud dang phat gi",
    }


async def report_soundcloud_now_playing():
    global soundcloud_page_id
    try:
        session = await get_mcp_session()
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            message = "❌ Không tìm thấy tab SoundCloud đang mở."
            set_command_response(message)
            return True
        selected = await session.call_tool(
            "select_page",
            arguments={"pageId": soundcloud_page_id, "bringToFront": False},
        )
        if _is_mcp_tool_error(selected):
            raise RuntimeError("select failed")
        script = r'''() => {
            const text = selector => (document.querySelector(selector)?.textContent || '')
                .replace(/\s+/g, ' ').trim();
            const link = document.querySelector('.playbackSoundBadge__titleLink');
            const artistLink = document.querySelector('.playbackSoundBadge__lightLink');
            const audio = document.querySelector('audio');
            return {
                ok: Boolean(link || audio),
                title: (link?.getAttribute('title') ||
                    link?.querySelector('[aria-hidden="true"]')?.textContent ||
                    text('.playbackSoundBadge__titleLink')).replace(/\s+/g, ' ').trim(),
                artist: (artistLink?.getAttribute('title') ||
                    artistLink?.querySelector('[aria-hidden="true"]')?.textContent ||
                    text('.playbackSoundBadge__lightLink')).replace(/\s+/g, ' ').trim(),
                paused: audio ? Boolean(audio.paused) :
                    !document.querySelector('.playControl.playing'),
                ended: audio ? Boolean(audio.ended) : false,
                currentTime: audio && Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
                duration: audio && Number.isFinite(audio.duration) ? audio.duration : 0,
                trackUrl: link?.href || '',
                pageUrl: location.href
            };
        }'''
        result = await session.call_tool("evaluate_script", arguments={"function": script})
        state = _json_from_mcp_result(result, default={})
        if _is_mcp_tool_error(result) or not isinstance(state, dict) or not _is_soundcloud_url(
            state.get("pageUrl", "")
        ):
            raise RuntimeError("invalid state")
        if not state.get("ok"):
            message = "ℹ️ SoundCloud chưa phát bài nào."
        else:
            title = _escape_discord_text(state.get("title", "")) or "Không rõ tiêu đề"
            artist = _escape_discord_text(state.get("artist", ""))
            playback = "Đã kết thúc" if state.get("ended") else (
                "Tạm dừng" if state.get("paused") else "Đang phát"
            )
            lines = [f"☁️ **{title}**"]
            if artist:
                lines.append(f"Nghệ sĩ: {artist}")
            lines.append(f"Trạng thái: {playback}")
            current_seconds = float(state.get("currentTime") or 0)
            duration_seconds = float(state.get("duration") or 0)
            current = _format_media_time(current_seconds)
            duration = _format_media_time(duration_seconds)
            if current_seconds > 0 or duration_seconds > 0:
                lines.append(f"Thời gian: {current}{f' / {duration}' if duration else ''}")
            track_url = str(state.get("trackUrl", "")).strip()
            if _is_soundcloud_track_url(track_url):
                lines.append(f"Link: {track_url}")
            message = "\n".join(lines)
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True
    except Exception:
        soundcloud_page_id = None
        message = "❌ Không thể đọc trạng thái SoundCloud lúc này."
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True


async def close_soundcloud():
    global soundcloud_tracks, soundcloud_page_id
    closed = await close_managed_web_tab(
        "soundcloud", "SoundCloud", ("https://soundcloud.com/",)
    )
    if closed:
        soundcloud_tracks = []
        soundcloud_page_id = None
    return closed


async def show_soundcloud():
    global soundcloud_page_id
    try:
        session = await get_mcp_session()
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            message = "❌ Không tìm thấy tab SoundCloud đang mở."
            set_command_response(message)
            return False
        selected = await session.call_tool(
            "select_page",
            arguments={"pageId": soundcloud_page_id, "bringToFront": True},
        )
        if _is_mcp_tool_error(selected):
            raise RuntimeError("select failed")
        message = "✅ Đã hiện SoundCloud."
        set_command_response(message)
        return True
    except Exception:
        message = "❌ Không thể hiện SoundCloud lúc này."
        set_command_response(message)
        return False


async def go_soundcloud_home():
    global soundcloud_tracks, soundcloud_page_id
    try:
        session = await get_mcp_session()
        soundcloud_page_id = await find_soundcloud_page(session)
        if soundcloud_page_id is None:
            return await open_soundcloud()
        await session.call_tool(
            "select_page",
            arguments={"pageId": soundcloud_page_id, "bringToFront": True},
        )
        navigated = await session.call_tool(
            "navigate_page",
            arguments={"type": "url", "url": "https://soundcloud.com/discover"},
        )
        if _is_mcp_tool_error(navigated):
            raise RuntimeError("navigate failed")
        soundcloud_tracks = []
        return await refresh_soundcloud_tracks()
    except Exception:
        message = "❌ Không thể về trang chủ SoundCloud lúc này."
        set_command_response(message)
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

        youtube_page_id = await find_youtube_page(session)

        if youtube_page_id is None:
            message = "❌ Không tìm thấy tab YouTube đang mở."
            print(f"Jarvis: {message}")
            set_command_response(message)
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
                message = "❌ Không tìm thấy tab YouTube đang mở."
                print(f"Jarvis: {message}")
                set_command_response(message)
                return False

            await session.call_tool(
                "select_page",
                arguments={
                    "pageId": youtube_page_id,
                    "bringToFront": True,
                },
            )

        message = "✅ Đã hiện YouTube."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    except Exception:
        message = "❌ Không thể hiện YouTube lúc này."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False


# ==========================================================
# TẮT / ĐÓNG TAB YOUTUBE - KHÔNG TẮT CHROME
# ==========================================================

async def close_youtube():
    """Đóng đúng tab YouTube thuộc lifecycle của Jarvis."""
    global youtube_videos
    global youtube_page_id

    print()
    print("Jarvis: Đang tắt YouTube...")
    closed = await close_managed_web_tab(
        "youtube", "YouTube", ("https://www.youtube.com/",)
    )
    if closed:
        youtube_videos = []
        youtube_page_id = None
        set_command_response("✅ Đã đóng YouTube.", spoken_message="Đã đóng YouTube.")
    elif last_command_response is None:
        set_command_response("❌ Không thể đóng YouTube lúc này.")
    return closed


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
                set_command_response("❌ Chưa chọn được Chrome để mở YouTube.")
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
            set_command_response(
                "✅ Đã về trang chủ YouTube nhưng danh sách video chưa tải xong."
            )
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

    except Exception:
        message = "❌ Không thể về trang chủ YouTube lúc này."
        print(f"Jarvis: {message}")
        set_command_response(message)
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


def search_google(command, *, allow_prompt=True):
    query = extract_google_query(
        command
    )

    if not query:
        print(
            "Jarvis: Bạn muốn tìm gì?"
        )

        if not allow_prompt:
            message = "ℹ️ Hãy thêm nội dung cần tìm sau lệnh Google."
            set_command_response(message)
            return False
        query = input("Tìm kiếm: ").strip()

    if not query:
        return

    profile, profile_name = _chrome_profile_from_explicit_command(command)
    if profile is None and allow_prompt:
        profile, profile_name = choose_chrome_profile(command)

    if profile is None:
        message = (
            "ℹ️ Hãy nói rõ profile, ví dụ: `google thời tiết học` "
            "hoặc `google thời tiết cá nhân`."
        )
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    url = (
        "https://www.google.com/search?q="
        + quote_plus(query)
    )

    print(
        f"Jarvis: Đang tìm '{query}' "
        f"bằng {profile_name}..."
    )

    opened = open_chrome(
        profile,
        url,
    )
    message = (
        f"✅ Đã mở kết quả Google cho: {query}"
        if opened is not False else
        "❌ Không thể mở kết quả Google lúc này."
    )
    print(f"Jarvis: {message}")
    set_command_response(message)
    return opened is not False


# ==========================================================
# GITHUB
# ==========================================================

def open_github(command):
    profile, _ = _chrome_profile_from_explicit_command(command)
    if profile is None:
        profile = STUDY_PROFILE

    print("Jarvis: Đang mở GitHub...")

    opened = open_chrome(
        profile,
        "https://github.com/",
    )
    message = (
        "✅ Đã mở GitHub."
        if opened is not False
        else "❌ Không thể mở GitHub lúc này."
    )
    print(f"Jarvis: {message}")
    set_command_response(message)
    return opened is not False


# ==========================================================
# VS CODE
# ==========================================================

def open_vscode():
    previous_window_ids = FILE_WINDOWS.snapshot_ids()
    if previous_window_ids is None:
        set_command_response("❌ Không thể xác minh danh sách cửa sổ; chưa mở VS Code.")
        return False
    try:
        process = subprocess.Popen(
            ["code", "--new-window", "--profile", "Jarvis"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        message = "❌ Không tìm thấy VS Code trên hệ thống."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    tracked = FILE_WINDOWS.track_opened_window(
        "vscode", previous_window_ids, allowed_classes={"code"}
    )
    rolled_back = False if tracked else FILE_WINDOWS.close_new_window(
        previous_window_ids, expected_pid=process.pid
    )
    message = (
        "✅ Đã mở VS Code." if tracked else
        "❌ Không xác minh được cửa sổ VS Code mới; đã hoàn tác việc mở."
        if rolled_back else
        "❌ Không xác minh được cửa sổ VS Code mới; không đóng bừa cửa sổ khác."
    )
    print(f"Jarvis: {message}")
    set_command_response(message)
    return tracked


def open_system_monitor():
    candidates = [
        ("gnome-system-monitor", {"gnome-system-monitor"}),
        ("missioncenter", {"missioncenter"}),
        ("gnome-usage", {"gnome-usage"}),
    ]
    for executable, allowed_classes in candidates:
        if not shutil.which(executable):
            continue
        previous_ids = FILE_WINDOWS.snapshot_ids()
        if previous_ids is None:
            set_command_response(
                "❌ Không thể xác minh danh sách cửa sổ; chưa mở System Monitor."
            )
            return False
        try:
            process = subprocess.Popen(
                [executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError:
            continue
        tracked = FILE_WINDOWS.track_opened_window(
            "system-monitor", previous_ids, allowed_classes=allowed_classes
        )
        rolled_back = False if tracked else FILE_WINDOWS.close_new_window(
            previous_ids, expected_pid=process.pid
        )
        message = (
            "✅ Đã mở System Monitor." if tracked else
            "❌ Không xác minh được cửa sổ System Monitor mới; đã hoàn tác việc mở."
            if rolled_back else
            "❌ Không xác minh được cửa sổ System Monitor mới; không đóng bừa cửa sổ khác."
        )
        print(f"Jarvis: {message}")
        set_command_response(message)
        return tracked
    message = "❌ Không tìm thấy ứng dụng System Monitor trên hệ thống."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return False


def close_system_monitor():
    success, detail = FILE_WINDOWS.close_last("system-monitor")
    message = f"{'✅' if success else '❌'} {detail}"
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


# ==========================================================
# TERMINAL
# ==========================================================

def open_terminal():
    previous_window_ids = FILE_WINDOWS.snapshot_ids()
    if previous_window_ids is None:
        set_command_response("❌ Không thể xác minh danh sách cửa sổ; chưa mở Terminal.")
        return False
    terminals = [
        ("gnome-terminal", ["gnome-terminal", "--window", "--title", "Jarvis Terminal"]),
        ("kgx", ["kgx", "--title", "Jarvis Terminal"]),
        (
            "x-terminal-emulator",
            [
                "x-terminal-emulator", "--standalone", "--new-window",
                "--title", "Jarvis Terminal",
            ],
        ),
    ]

    for terminal, argv in terminals:

        try:

            process = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            resolved = shutil.which(argv[0])
            executable = Path(resolved).resolve().name.casefold() if resolved else ""
            if executable == "ptyxis":
                FILE_WINDOWS.track_process("terminal", process)
                tracked = process.poll() is None
            else:
                tracked = FILE_WINDOWS.track_opened_window(
                    "terminal", previous_window_ids, expected_title="Jarvis Terminal"
                )
            if tracked:
                message = "✅ Đã mở Terminal."
            else:
                rolled_back = FILE_WINDOWS.close_new_window(
                    previous_window_ids, expected_pid=process.pid
                )
                message = (
                    "❌ Không xác minh được cửa sổ Terminal mới; đã hoàn tác việc mở."
                    if rolled_back else
                    "❌ Không xác minh được cửa sổ Terminal mới; không đóng bừa cửa sổ khác."
                )
            print(f"Jarvis: {message}")
            set_command_response(message)
            return tracked

        except FileNotFoundError:
            continue

    message = "❌ Không tìm thấy Terminal."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return False


# ==========================================================
# FOLDER
# ==========================================================

def open_folder(path, name):
    path = Path(path)

    if not path.exists():
        message = f"❌ Không tìm thấy {name}."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False

    print(f"Jarvis: Đang mở {name}...")
    previous_window_ids = FILE_WINDOWS.snapshot_ids()
    if previous_window_ids is None:
        message = f"❌ Không thể xác minh danh sách cửa sổ; chưa mở {name}."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False
    try:
        process = subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        message = "❌ Không tìm thấy trình mở file/thư mục trên hệ thống."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return False
    tracked = FILE_WINDOWS.track_opened_path(path, previous_window_ids)
    if not tracked:
        rolled_back = FILE_WINDOWS.close_new_window(
            previous_window_ids, file_manager_only=True, expected_pid=process.pid
        )
        message = (
            f"❌ Không xác minh được cửa sổ {name} mới; đã hoàn tác việc mở."
            if rolled_back else
            f"❌ Không xác minh được cửa sổ {name} mới; không đóng bừa cửa sổ khác."
        )
    else:
        message = f"✅ Đã mở {name}."
    set_command_response(message)
    print(f"Jarvis: {message}")
    return tracked


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


def close_vscode():
    """Close only the exact VS Code window Jarvis previously opened."""
    success, detail = FILE_WINDOWS.close_last("vscode")
    message = f"{'✅' if success else '❌'} {detail}"
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


def close_file_manager():
    """Close only exact file-manager windows tracked by Jarvis."""
    success, detail = FILE_WINDOWS.close_all_paths()
    message = f"{'✅' if success else '❌'} {detail}"
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


def _terminate_jarvis_chrome():
    """Terminate only the validated browser owning Jarvis' loopback CDP port."""
    pid = _jarvis_debug_port_owner_pid()
    if pid is None:
        return False
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    except OSError:
        return False
    required = {
        f"--remote-debugging-port={JARVIS_CHROME_DEBUG_PORT}",
        f"--user-data-dir={JARVIS_CHROME_DATA_DIR}",
    }
    if not required.issubset(set(args)):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        return False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        time.sleep(0.1)
    return False


async def close_chrome():
    """Close only Chrome resources whose ownership Jarvis can prove."""
    global youtube_videos
    global youtube_profile
    global youtube_profile_name
    global youtube_page_id

    # Đóng MCP trước, tránh giữ session DevTools đã chết sau khi Chrome bị tắt.
    await close_mcp_session()

    normal_success, normal_detail = CHROME_WINDOWS.close_all_tracked()
    automation_success = _terminate_jarvis_chrome()
    if automation_success:
        youtube_videos = []
        youtube_profile = None
        youtube_profile_name = None
        youtube_page_id = None
    success = normal_success or automation_success
    details = []
    if normal_success:
        details.append(normal_detail)
    if automation_success:
        details.append("đã đóng Chrome automation riêng của Jarvis")
    if not details:
        details.append("không có Chrome nào còn được Jarvis xác minh quyền sở hữu")
    message = f"{'✅' if success else '❌'} {'; '.join(details)}."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


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
    """Deliberate override: close all GUI windows, including user-owned ones."""
    print("Jarvis: Đang đóng toàn bộ tab và ứng dụng GUI...")
    await close_mcp_session()
    hosting_window_ids = FILE_WINDOWS.window_ids_for_pid_ancestry(os.getpid())
    if hosting_window_ids is None:
        message = (
            "⚠️ Không thể xác minh cửa sổ đang chạy Jarvis; "
            "đã hủy tắt tất cả để không tự đóng Jarvis."
        )
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True
    success, closed, remaining = FILE_WINDOWS.close_all_gui_windows(
        preserved_class_components={"discord", "jarvis"},
        preserved_window_ids=hosting_window_ids,
    )
    if success:
        CHROME_WINDOWS.clear()
        FILE_WINDOWS.last_windows = {
            key: None for key in FILE_WINDOWS.last_windows
        }
        FILE_WINDOWS.path_windows.clear()
    message = (
        f"✅ Đã đóng {closed} cửa sổ GUI cùng toàn bộ tab bên trong; "
        "Jarvis/Discord/TTS được giữ lại."
        if success else
        f"⚠️ Đã đóng {closed} cửa sổ GUI nhưng còn {len(remaining)} cửa sổ chưa đóng được."
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
        if previous_window_ids is None:
            message = f"❌ Không thể xác minh danh sách cửa sổ; chưa mở {resolved.name}."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False
        process = subprocess.Popen(
            ["xdg-open", str(resolved)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        tracked = FILE_WINDOWS.track_opened_path(resolved, previous_window_ids)
        kind = "thư mục" if resolved.is_dir() else "file"
        if not tracked:
            rolled_back = FILE_WINDOWS.close_new_window(
                previous_window_ids, file_manager_only=True, expected_pid=process.pid
            )
            message = (
                f"❌ Không xác minh được cửa sổ {kind} mới; đã hoàn tác việc mở."
                if rolled_back else
                f"❌ Không xác minh được cửa sổ {kind} mới; không đóng bừa cửa sổ khác."
            )
            print(f"Jarvis: {message}")
            set_command_response(message)
            return False
        message = f"✅ Đã mở {kind}: {resolved}"
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
        r"(?:tắt|tat|đóng|dong|thoát|thoat|close)\s+(?:cửa\s*sổ\s+)?file(?:\s+gần\s+nhất)?",
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
        r"(?:tắt|tat|đóng|dong|thoát|thoat|close)\s+(?:cửa\s*sổ\s+)?(?:thư\s*mục|thu\s*muc|project)(?:\s+gần\s+nhất)?",
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
            message = "✅ Đã về Desktop. Các ứng dụng vẫn đang chạy."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return True

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
            message = "✅ Đã về Desktop. Các ứng dụng vẫn đang chạy."
            print(f"Jarvis: {message}")
            set_command_response(message)
            return True

    except FileNotFoundError:
        pass

    message = "❌ Chưa thể điều khiển Desktop trên phiên làm việc hiện tại."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return False



# ==========================================================
# PHẢN HỒI DÙNG CHUNG CHO DISCORD
# ==========================================================

def set_command_response(message, *, spoken_message=None):
    """Lưu phản hồi; ``spoken_message=False`` tắt TTS cho phản hồi đó."""
    global last_command_response, last_spoken_response
    last_command_response = message
    last_spoken_response = (
        False if spoken_message is False else (spoken_message or message)
    )


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
deep_sleep_target = None


def _power_log(event, **details):
    """Ghi dấu vết đủ để xác định Jarvis có phát yêu cầu suspend hay không."""
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    detail_text = " ".join(f"{key}={value}" for key, value in details.items())
    print(
        f"JARVIS_POWER timestamp={timestamp} pid={os.getpid()} "
        f"event={event}{(' ' + detail_text) if detail_text else ''}",
        flush=True,
    )


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


def parse_deep_sleep_clock(command, now=None):
    """Đổi lệnh giờ đồng hồ thành thời điểm local tiếp theo và số giây chờ."""
    command_clean = normalize_core_text(command)
    match = re.fullmatch(
        r"(?:(?:dat lich|dat|hen gio|hen)\s+)?"
        r"(?:sleep sau|ngu sau|suspend)(?:\s+(?:luc|vao luc))?\s+"
        r"(\d{1,2})(?:(?:[:h])\s*(\d{1,2}))?(?:\s*(?:gio))?",
        command_clean,
        re.IGNORECASE,
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    current = now or datetime.now().astimezone()
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target, max(1, int((target - current).total_seconds()))


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
    global deep_sleep_task, deep_sleep_target

    current_task = asyncio.current_task()

    try:
        await asyncio.sleep(seconds)

        print("Jarvis: Đã đến giờ. Đang chuyển Ubuntu sang Sleep sâu...")

        # Xóa lịch trước khi gọi systemctl. Khi process tiếp tục sau resume,
        # timer đã tiêu thụ không thể còn xuất hiện như một lịch đang chạy.
        if deep_sleep_task is current_task:
            deep_sleep_task = None
            deep_sleep_target = None

        success = await asyncio.to_thread(
            deep_sleep_machine,
            source="timer",
        )

        if not success:
            print("Jarvis: Không thể đưa Ubuntu vào Sleep sâu.")

    except asyncio.CancelledError:
        raise

    except Exception as error:
        print(f"Jarvis: Lỗi khi hẹn Sleep sâu: {error}")

    finally:
        # Một task cũ bị cancel có thể hoàn tất sau khi task mới đã được tạo.
        # Chỉ task đang sở hữu biến toàn cục mới được phép xóa nó.
        if deep_sleep_task is current_task:
            deep_sleep_task = None
            deep_sleep_target = None


async def handle_deep_sleep_timer_command(command):
    global deep_sleep_task, deep_sleep_target

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
        deep_sleep_target = None

        message = "✅ Đã hủy lịch Sleep sâu."
        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    if command_lower in status_commands:
        if deep_sleep_task is None or deep_sleep_task.done():
            message = "ℹ️ Hiện không có lịch Sleep sâu."
        else:
            target_text = (
                deep_sleep_target.strftime("%H:%M ngày %d/%m/%Y")
                if deep_sleep_target else "thời điểm đã đặt"
            )
            message = f"⏱️ Lịch Sleep sâu: {target_text}."

        print(f"Jarvis: {message}")
        set_command_response(message)
        return True

    clock_schedule = parse_deep_sleep_clock(command)
    seconds = parse_deep_sleep_delay(command)
    if clock_schedule is not None:
        target, seconds = clock_schedule
    if seconds is None:
        incomplete_timer = re.match(
            r"(?:(?:dat lich|dat|hen gio|hen)\s+)?"
            r"(?:sleep sau|ngu sau|suspend)(?:\s+(?:sau|after))?(?:\s|$)",
            command_lower,
        )
        if incomplete_timer:
            message = (
                "ℹ️ Thời gian Sleep chưa đầy đủ hoặc chưa hợp lệ. Ví dụ: "
                "`sleep sâu sau 30p` hoặc `sleep sâu lúc 23:30`."
            )
            print(f"Jarvis: {message}")
            set_command_response(message)
            return True
        return False

    if deep_sleep_task is not None and not deep_sleep_task.done():
        deep_sleep_task.cancel()

    deep_sleep_task = asyncio.create_task(deep_sleep_after_delay(seconds))
    if clock_schedule is not None:
        deep_sleep_target = target
        message = (
            f"⏱️ Đã đặt Sleep sâu lúc {target.strftime('%H:%M ngày %d/%m/%Y')} "
            f"(còn {format_deep_sleep_time(seconds)})."
        )
    else:
        deep_sleep_target = datetime.now().astimezone() + timedelta(seconds=seconds)
        duration_text = format_deep_sleep_time(seconds)
        message = f"⏱️ Đã đặt lịch Sleep sâu sau {duration_text}."
    print(f"Jarvis: {message}")
    set_command_response(message)
    return True


REMINDER_DURATION_PART = r"\d+\s*(?:gio|giay|phut|h|p|s)"


def _duration_seconds(value):
    """Parse compact/mixed durations such as 1h15p, 1h 15p or 2gio30phut."""
    plain = normalize_core_text(value)
    parts = re.findall(r"(\d+)\s*(gio|giay|phut|h|p|s)", plain)
    if not parts:
        return None
    consumed = re.sub(r"\s+", "", plain)
    rebuilt = "".join(f"{amount}{unit}" for amount, unit in parts)
    if consumed != rebuilt:
        return None
    total = 0
    for amount, unit in parts:
        multiplier = 3600 if unit in {"h", "gio"} else 60 if unit in {"p", "phut"} else 1
        total += int(amount) * multiplier
    return total if total > 0 else None


def parse_reminder_command(command, now=None):
    """Parse `nhắc tôi sau ...` and `nhắc tôi lúc ... [ngày ...]` commands."""
    raw = str(command).strip()
    plain = normalize_core_text(raw)
    current = now or datetime.now().astimezone()
    duration = rf"((?:{REMINDER_DURATION_PART}\s*)+)"
    leading_delay_match = re.match(
        rf"sau\s+{duration}(?:\s+nua)?\s+(?:hay\s+)?nhac(?:\s+toi|\s+nho)?\s+(.+)$",
        plain,
    )
    if leading_delay_match:
        seconds = _duration_seconds(leading_delay_match.group(1))
        content = raw[leading_delay_match.start(2):].strip().lstrip(":,- ")
        return current + timedelta(seconds=seconds), content

    delay_match = re.match(
        rf"(?:(?:dat|hen|tao)(?:\s+lich)?\s+)?nhac(?:\s+toi|\s+nho)?\s+sau\s+{duration}"
        rf"(?:\s+nua)?\s*[:,-]?\s*(.+)$",
        plain,
    )
    if delay_match:
        seconds = _duration_seconds(delay_match.group(1))
        content = raw[delay_match.start(2):].strip().lstrip(":,- ")
        content = re.sub(r"^(?:nữa|nua)\s*[:,-]?\s*", "", content, flags=re.IGNORECASE)
        return current + timedelta(seconds=seconds), content

    # Cách nói tự nhiên: "tạo nhắc nhở phơi đồ sau 1h15p".
    trailing_match = re.match(
        rf"(?:(?:dat|hen|tao)(?:\s+lich)?\s+)?nhac(?:\s+nho)?\s+(.+?)\s+sau\s+{duration}(?:\s+nua)?$",
        plain,
    )
    if trailing_match:
        seconds = _duration_seconds(trailing_match.group(2))
        content = raw[trailing_match.start(1):trailing_match.end(1)].strip()
        return current + timedelta(seconds=seconds), content
    clock_match = re.match(
        r"(?:(?:dat|hen|tao)(?:\s+lich)?\s+)?nhac(?:\s+toi|\s+nho)?\s+(?:luc|vao luc)\s+"
        r"(\d{1,2})(?::|h)(\d{1,2})?"
        r"(?:\s+ngay\s+(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?)?"
        r"\s*[:,-]?\s*(.+)$",
        plain,
    )
    if not clock_match:
        return None
    hour, minute = int(clock_match.group(1)), int(clock_match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    day, month, year = clock_match.group(3), clock_match.group(4), clock_match.group(5)
    try:
        if day:
            target = current.replace(
                year=int(year or current.year), month=int(month), day=int(day),
                hour=hour, minute=minute, second=0, microsecond=0,
            )
        else:
            target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= current:
                target += timedelta(days=1)
    except ValueError:
        return None
    if target <= current:
        return None
    content = raw[clock_match.start(6):].strip().lstrip(":,- ")
    return target, content


def parse_scheduled_zalo_command(command, now=None):
    """Parse `nhắn zalo cho <người> lúc/sau <thời gian>: <nội dung>`."""
    raw = str(command).strip()
    plain = normalize_core_text(raw)
    duration = rf"((?:{REMINDER_DURATION_PART}\s*)+)"
    leading_match = re.match(
        rf"sau\s+{duration}(?:\s+nua)?\s+(?:hay\s+)?(?:nhan|gui)"
        rf"(?:\s+tin nhan)?(?:\s+zalo)?"
        rf"(?:\s+(?:trong\s+)?the\s+(tra loi sau|gia dinh))?\s+cho\s+(.+?)\s+"
        rf"(?:voi\s+)?noi dung\s*[:,-]?\s*(.+)$",
        plain,
    )
    if leading_match:
        seconds = _duration_seconds(leading_match.group(1))
        tag_key = leading_match.group(2)
        raw_recipient = raw[leading_match.start(3):leading_match.end(3)].strip()
        raw_content = raw[leading_match.start(4):].strip().lstrip(":,- ")
        tag_name = (
            "Trả lời sau" if tag_key == "tra loi sau"
            else "Gia đình" if tag_key == "gia dinh" else ""
        )
        current = now or datetime.now().astimezone()
        return current + timedelta(seconds=seconds), raw_recipient, raw_content, tag_name

    match = re.match(
        r"(?:hen\s+)?(?:nhan|gui)(?:\s+tin nhan)?(?:\s+zalo)?"
        r"(?:\s+(?:trong\s+)?the\s+(tra loi sau|gia dinh))?\s+cho\s+(.+?)\s+(sau|luc)\s+(.+)$",
        plain,
    )
    if not match:
        return None
    tag, recipient, mode, tail = match.groups()
    synthetic = f"nhac toi {mode} {tail}"
    parsed = parse_reminder_command(synthetic, now=now)
    if not parsed:
        return None
    due_at, normalized_content = parsed
    raw_recipient = raw[match.start(2):match.end(2)].strip()
    raw_tail = raw[match.start(4):]
    content = raw_tail[-len(normalized_content):] if normalized_content else normalized_content
    tag_name = "Trả lời sau" if tag == "tra loi sau" else "Gia đình" if tag == "gia dinh" else ""
    return due_at, raw_recipient, content.strip().lstrip(":,- "), tag_name


async def send_zalo_message(recipient, content, send=False, tag=""):
    """Find one unambiguous Zalo contact and prepare or send a message."""
    session = await get_mcp_session()
    page_id = await find_zalo_page(session)
    if page_id is None:
        open_chrome(STUDY_PROFILE, "https://chat.zalo.me/")
        await asyncio.sleep(2.5)
        page_id = await find_zalo_page(session)
    if page_id is None:
        return False, "Không tìm thấy tab Zalo Web."
    await session.call_tool("select_page", arguments={"pageId": page_id, "bringToFront": True})
    script = f'''async () => {{
      const recipient = {json.dumps(recipient)};
      const wantedTag = {json.dumps(tag)};
      const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\\s+/g,' ').trim();
      const search = document.querySelector('nav input[type="text"], nav input, nav [role="textbox"]');
      if (!search) return {{ok:false,error:'Không tìm thấy ô tìm kiếm Zalo.'}};
      search.focus();
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      if (valueSetter) valueSetter.call(search, recipient); else search.value = recipient;
      search.dispatchEvent(new Event('input', {{bubbles:true}}));
      search.dispatchEvent(new Event('change', {{bubbles:true}}));
      await new Promise(r => setTimeout(r, 1200));
      const rows = [...document.querySelectorAll('.conv-item')];
      const matches = rows.map((row,index) => ({{row,index,name:(row.querySelector('.conv-item-title__name, [class*="title"]')?.textContent || '').trim(), text:(row.innerText||'')}}))
        .filter(item => normalize(item.name) === normalize(recipient) && (!wantedTag || normalize(item.text).includes(normalize(wantedTag))));
      if (matches.length !== 1) return {{ok:false,error:matches.length ? `Có ${{matches.length}} kết quả trùng tên.` : 'Không tìm thấy đúng người nhận.', names:rows.slice(0,8).map(r=>r.innerText.split('\\n')[0])}};
      (matches[0].row.querySelector('.conv-item') || matches[0].row).click();
      await new Promise(r => setTimeout(r, 800));
      const editor = document.querySelector('main [contenteditable="true"]');
      if (!editor) return {{ok:false,error:'Không tìm thấy ô soạn tin.'}};
      const existingCount = [...document.querySelectorAll('main .chat-item')]
        .filter(item => (item.innerText || '').includes({json.dumps(content)})).length;
      editor.focus(); document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, {json.dumps(content)});
      return {{ok:true,name:matches[0].name,existingCount}};
    }}'''
    result = _json_from_mcp_result(
        await session.call_tool("evaluate_script", arguments={"function": script}), default={}
    )
    if not isinstance(result, dict) or not result.get("ok"):
        detail = result.get("error", "Không thể soạn tin Zalo.") if isinstance(result, dict) else "Không thể soạn tin Zalo."
        candidates = result.get("names", []) if isinstance(result, dict) else []
        if candidates:
            detail += " Tên đang thấy: " + ", ".join(str(name) for name in candidates if name)[:300]
        return False, detail
    if send:
        await session.call_tool("press_key", arguments={"key": "Enter"})
        verify_script = f'''() => {{
          const content = {json.dumps(content)};
          const editor = document.querySelector('main [contenteditable="true"]');
          const currentCount = [...document.querySelectorAll('main .chat-item')]
            .filter(item => (item.innerText || '').includes(content)).length;
          return {{
            editorEmpty: !editor || !(editor.innerText || '').trim(),
            messageAdded: currentCount > {int(result.get('existingCount', 0))},
            currentCount
          }};
        }}'''
        verified = {}
        for _attempt in range(3):
            await asyncio.sleep(0.7)
            verified = _json_from_mcp_result(
                await session.call_tool("evaluate_script", arguments={"function": verify_script}),
                default={},
            )
            if isinstance(verified, dict) and verified.get("editorEmpty") and verified.get("messageAdded"):
                break
        if not isinstance(verified, dict) or not (
            verified.get("editorEmpty") and verified.get("messageAdded")
        ):
            return False, (
                "Zalo chưa xác nhận tin đã được gửi. Nội dung có thể vẫn nằm trong ô soạn; "
                "hãy kiểm tra cửa sổ Zalo."
            )
        return True, f"Đã gửi và xác minh tin Zalo cho {result.get('name', recipient)}."
    return True, f"Đã soạn bản nháp Zalo cho {result.get('name', recipient)} (chưa gửi)."


def handle_reminder_command(command, source="terminal"):
    plain = normalize_core_text(command)

    def format_pending_reminders(rows, heading="⏰ **LỊCH NHẮC VIỆC**"):
        lines = [heading]
        for row in rows[:30]:
            due = datetime.fromisoformat(row["due_at"])
            if row.get("kind") == "zalo_send":
                tag = f" · thẻ {row['zalo_tag']}" if row.get("zalo_tag") else ""
                detail = f"Zalo → {row.get('recipient', '')}{tag} · {row['content']}"
            else:
                detail = f"Nhắc Discord · {row['content']}"
            lines.append(
                f"#{row['id']} · {due.strftime('%H:%M ngày %d/%m/%Y')}\n   {detail}"
            )
        return "\n".join(lines)

    if plain in {"lich nhac", "xem lich nhac", "danh sach nhac", "cac lich nhac"}:
        rows = CORE.reminders.pending()
        if not rows:
            message = "ℹ️ Hiện không có lịch nhắc việc."
        else:
            message = format_pending_reminders(rows)
        set_command_response(message)
        return True
    if plain in {
        "huy lich nhac", "huy nhac", "xoa lich nhac", "xoa nhac",
        "toi muon huy lich nhac", "toi muon xoa lich nhac",
    }:
        rows = CORE.reminders.pending()
        if not rows:
            message = "ℹ️ Hiện không có lịch nhắc việc nào để hủy."
        else:
            message = (
                format_pending_reminders(rows, "🗑️ **BẠN MUỐN HỦY LỊCH NÀO?**")
                + "\n\nTrả lời: `hủy lịch nhắc #<số>` — ví dụ `hủy lịch nhắc #2`."
            )
        set_command_response(message)
        return True
    cancel = re.fullmatch(
        r"(?:huy|xoa)\s+(?:lich\s+)?nhac(?:\s+(?:muc|so))?\s+#?(\d+)", plain
    )
    if cancel:
        reminder_id = int(cancel.group(1))
        deleted = CORE.reminders.cancel(reminder_id)
        set_command_response(
            f"✅ Đã hủy lịch nhắc #{reminder_id}." if deleted
            else f"ℹ️ Không tìm thấy lịch nhắc đang chờ #{reminder_id}."
        )
        return True
    starts_as_reminder = re.match(
        r"(?:(?:dat|hen|tao)(?:\s+lich)?\s+)?nhac(?:\s+toi|\s+nho)?(?:\s+|$)",
        plain,
    ) or re.match(
        rf"sau\s+(?:{REMINDER_DURATION_PART}\s*)+(?:\s+nua)?\s+(?:hay\s+)?nhac(?:\s+toi|\s+nho)?(?:\s+|$)",
        plain,
    )
    if not starts_as_reminder:
        return False
    parsed = parse_reminder_command(command)
    if parsed is None:
        set_command_response(
            "ℹ️ Lịch nhắc chưa hợp lệ. Ví dụ: `nhắc tôi sau 1h15p đi phơi đồ`, "
            "`tạo nhắc nhở phơi đồ sau 1h15p` hoặc "
            "`nhắc tôi lúc 19:30 ngày 13/08/2026: gọi khách hàng`."
        )
        return True
    due_at, content = parsed
    reminder_id = CORE.reminders.add(content, due_at, source)
    set_command_response(
        f"⏰ Đã đặt lịch nhắc #{reminder_id} lúc "
        f"{due_at.strftime('%H:%M ngày %d/%m/%Y')}: {content}"
    )
    return True


async def handle_zalo_send_command(command, source="terminal"):
    plain = normalize_core_text(command)
    scheduled = parse_scheduled_zalo_command(command)
    if scheduled:
        due_at, recipient, content, tag = scheduled
        reminder_id = CORE.reminders.add(
            content, due_at, source, kind="zalo_send", recipient=recipient, zalo_tag=tag
        )
        set_command_response(
            f"📨 Đã hẹn tin Zalo #{reminder_id} cho {recipient}"
            f"{f' trong thẻ {tag}' if tag else ''} lúc "
            f"{due_at.strftime('%H:%M ngày %d/%m/%Y')}. Tới giờ Discord sẽ yêu cầu xác nhận gửi."
        )
        return True
    draft = re.match(
        r"soan zalo(?:\s+(?:trong\s+)?the\s+(tra loi sau|gia dinh))?\s+cho\s+(.+?)\s*:\s*(.+)$",
        plain,
    )
    if not draft:
        draft = re.match(
            r"soan\s+(?:tin nhan\s+)?(?:cho\s+)(.+?)\s+"
            r"(?:voi\s+)?noi dung\s*[:,-]?\s*(.+)$",
            plain,
        )
        if draft:
            raw_recipient = command[draft.start(1):draft.end(1)].strip()
            raw_content = command[draft.start(2):].strip().lstrip(":,- ")
            ok, message = await send_zalo_message(
                raw_recipient, raw_content, send=False
            )
            set_command_response(("📝 " if ok else "❌ ") + message)
            return True
    if draft:
        tag_key = draft.group(1)
        tag = "Trả lời sau" if tag_key == "tra loi sau" else "Gia đình" if tag_key == "gia dinh" else ""
        raw_recipient = command[draft.start(2):draft.end(2)].strip()
        raw_content = command[draft.start(3):].strip()
        ok, message = await send_zalo_message(raw_recipient, raw_content, send=False, tag=tag)
        set_command_response(("📝 " if ok else "❌ ") + message)
        return True
    immediate = re.match(
        r"(?:gui|nhan)\s+(?:tin nhan\s+)?(?:zalo\s+)?cho\s+(.+?)\s+"
        r"(?:voi\s+)?noi dung\s*[:,-]?\s*(.+)$",
        plain,
    )
    if immediate:
        raw_recipient = command[immediate.start(1):immediate.end(1)].strip()
        raw_content = command[immediate.start(2):].strip().lstrip(":,- ")
        # Discord chỉ tới đây sau khi nút nguy hiểm đã được xác nhận.
        should_send = source == "discord"
        ok, message = await send_zalo_message(
            raw_recipient, raw_content, send=should_send
        )
        prefix = "✅ " if ok and should_send else "📝 " if ok else "❌ "
        if ok and not should_send:
            message += " Hãy gửi từ Discord để có nút xác nhận an toàn."
        set_command_response(prefix + message)
        return True
    return False


async def reminder_dispatch_loop():
    """Deliver persistent due reminders to the configured Discord channel."""
    while True:
        try:
            if discord_client is not None and not discord_client.is_closed() and discord_client.is_ready():
                for row in CORE.reminders.due(datetime.now().astimezone()):
                    is_zalo = row.get("kind") == "zalo_send"
                    message = (
                        f"📨 **TIN ZALO ĐẾN GIỜ #{row['id']}**\n"
                        f"Người nhận: **{redact_sensitive(row.get('recipient', ''))}**\n"
                        f"Thẻ: **{redact_sensitive(row.get('zalo_tag') or 'Không chỉ định')}**\n"
                        f"Nội dung: {redact_sensitive(row['content'])}"
                        if is_zalo else
                        f"⏰ **NHẮC VIỆC #{row['id']}**\n{redact_sensitive(row['content'])}"
                    )
                    channel_id = (
                        _env_int("DISCORD_CHANNEL_ID")
                        or discord_notification_channel_id
                        or CORE.security.last_discord_channel_id()
                    )
                    channel = discord_client.get_channel(channel_id) if channel_id else None
                    if channel is None and channel_id:
                        try:
                            channel = await discord_client.fetch_channel(channel_id)
                        except Exception:
                            channel = None
                    if channel is not None:
                        if is_zalo:
                            CORE.reminders.mark_awaiting(row["id"])
                            await channel.send(
                                message + "\n\nXác nhận trong 60 giây để gửi.",
                                view=DiscordZaloSendConfirmationView(row),
                            )
                        else:
                            await channel.send(message)
                            CORE.reminders.mark_delivered(row["id"])
                        CORE.conversation.add("discord", "assistant", message)
                        print(f"Jarvis: {message}")
        except Exception as error:
            print(f"Jarvis: Lỗi gửi lịch nhắc Discord: {error}")
        await asyncio.sleep(10)


def load_gmail_state():
    """Load only opaque IDs needed to distinguish newly seen Gmail rows."""
    try:
        data = json.loads(GMAIL_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"initialized": False, "seen_ids": [], "account_hash": ""}
    seen_ids = data.get("seen_ids", []) if isinstance(data, dict) else []
    return {
        "initialized": bool(data.get("initialized")) if isinstance(data, dict) else False,
        "seen_ids": [str(value) for value in seen_ids if str(value).strip()][-500:],
        "account_hash": str(data.get("account_hash", "")) if isinstance(data, dict) else "",
    }


def save_gmail_state(state):
    GMAIL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = GMAIL_STATE_PATH.with_suffix(".tmp")
    payload = {
        "initialized": bool(state.get("initialized")),
        "seen_ids": [str(value) for value in state.get("seen_ids", [])][-500:],
        "account_hash": str(state.get("account_hash", "")),
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.chmod(0o600)
    temporary.replace(GMAIL_STATE_PATH)


async def get_discord_notification_channel():
    if discord_client is None or discord_client.is_closed() or not discord_client.is_ready():
        return None
    channel_id = (
        _env_int("DISCORD_CHANNEL_ID")
        or discord_notification_channel_id
        or CORE.security.last_discord_channel_id()
    )
    channel = discord_client.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        try:
            channel = await discord_client.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


async def check_gmail_for_notifications():
    """Poll Gmail once; establish a quiet baseline, then notify only new unread rows."""
    result = await read_gmail_inbox(limit=30, unread_only=True, bring_to_front=False)
    if not result["ok"]:
        return {"status": result["reason"], "new_count": 0}
    messages = result["messages"]
    state = load_gmail_state()
    account = configured_gmail_account()
    account_hash = hashlib.sha256(account.encode("utf-8")).hexdigest() if account else ""
    if state.get("account_hash") != account_hash:
        state = {"initialized": False, "seen_ids": [], "account_hash": account_hash}
    current_ids = [item["id"] for item in messages]
    if not state["initialized"]:
        save_gmail_state({
            "initialized": True, "seen_ids": current_ids, "account_hash": account_hash,
        })
        return {"status": "baseline", "new_count": 0}
    seen = set(state["seen_ids"])
    new_messages = [item for item in messages if item["id"] not in seen]
    merged = list(dict.fromkeys(current_ids + state["seen_ids"]))[:500]
    if not new_messages:
        save_gmail_state({
            "initialized": True, "seen_ids": merged, "account_hash": account_hash,
        })
        return {"status": "unchanged", "new_count": 0}
    channel = await get_discord_notification_channel()
    if channel is None:
        return {"status": "no_channel", "new_count": len(new_messages)}
    try:
        summary = await asyncio.to_thread(CORE.local_ai.summarize_gmail, new_messages[:10])
    except Exception:
        summary = format_gmail_preview_summary(new_messages)
    message = f"📬 **{len(new_messages)} GMAIL MỚI**\n\n{summary}"
    chunks = [message[index:index + 1900] for index in range(0, len(message), 1900)]
    for chunk in chunks:
        await channel.send(chunk)
    save_gmail_state({
        "initialized": True, "seen_ids": merged, "account_hash": account_hash,
    })
    try:
        await mark_gmail_messages_read(new_messages, folder="inbox")
    except Exception:
        pass
    print(f"Jarvis: Đã gửi thông báo {len(new_messages)} Gmail mới qua Discord.")
    return {"status": "notified", "new_count": len(new_messages)}


async def gmail_monitor_loop():
    """Keep Gmail notification polling isolated from interactive browser commands."""
    await asyncio.sleep(15)
    while True:
        try:
            async with discord_command_lock:
                await check_gmail_for_notifications()
        except asyncio.CancelledError:
            raise
        except Exception:
            print("Jarvis: Lần kiểm tra Gmail nền chưa thành công; sẽ tự thử lại.")
        await asyncio.sleep(GMAIL_POLL_SECONDS)


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

WOL_PC_COMMANDS = {
    "bật pc",
    "bat pc",
    "bật máy tính",
    "bat may tinh",
    "bật máy tính pc",
    "bat may tinh pc",
    "bật máy tính của tôi",
    "bat may tinh cua toi",
    "đánh thức pc",
    "danh thuc pc",
    "wake pc",
    "wake on lan pc",
}

REMOTE_PC_SHUTDOWN_COMMANDS = {
    "tắt pc",
    "tat pc",
    "tắt máy tính",
    "tat may tinh",
    "tắt máy tính pc",
    "tat may tinh pc",
    "shutdown pc",
}


def build_wol_magic_packet(mac_address):
    """Create a standard Wake-on-LAN packet from a configured MAC address."""
    compact = re.sub(r"[^0-9A-Fa-f]", "", str(mac_address))
    if len(compact) != 12:
        raise ValueError("địa chỉ MAC phải gồm 12 ký tự hexadecimal")
    try:
        mac_bytes = bytes.fromhex(compact)
    except ValueError as error:
        raise ValueError("địa chỉ MAC không hợp lệ") from error
    return b"\xff" * 6 + mac_bytes * 16


def send_wake_on_lan(
    mac_address=None, *, broadcast=None, port=None, repeat=3
):
    """Broadcast magic packets from the always-on Jarvis device."""
    target_mac = str(mac_address or WOL_PC_MAC).strip()
    if not target_mac:
        raise ValueError("chưa cấu hình WOL_PC_MAC")
    target_broadcast = str(broadcast or WOL_BROADCAST).strip()
    target_port = WOL_PORT if port is None else int(port)
    if not 1 <= target_port <= 65535:
        raise ValueError("WOL_PORT phải nằm trong khoảng 1-65535")
    packet = build_wol_magic_packet(target_mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(max(1, int(repeat))):
            client.sendto(packet, (target_broadcast, target_port))
    return max(1, int(repeat))


def shutdown_remote_pc(user=None, host=None):
    """Shut down the configured Windows PC over non-interactive SSH."""
    target_user = str(user or PC_SSH_USER).strip()
    target_host = str(host or PC_SSH_HOST).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", target_user):
        raise ValueError("PC_SSH_USER chưa được cấu hình hợp lệ")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", target_host):
        raise ValueError("PC_SSH_HOST chưa được cấu hình hợp lệ")
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "-o", "ConnectionAttempts=1",
                f"{target_user}@{target_host}",
                "shutdown /s /t 0",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as error:
        raise OSError("không tìm thấy chương trình ssh") from error
    except subprocess.TimeoutExpired as error:
        raise OSError("kết nối SSH quá thời gian") from error
    if result.returncode != 0:
        raise OSError(
            "SSH thất bại; hãy kiểm tra PC đang bật, OpenSSH Server, "
            "SSH key và known_hosts"
        )
    return True


def is_deep_sleep_command(command):
    return command.strip().lower() in DEEP_SLEEP_COMMANDS


def is_screen_sleep_command(command):
    return command.strip().lower() in SCREEN_SLEEP_COMMANDS


def is_screen_wake_command(command):
    return command.strip().lower() in SCREEN_WAKE_COMMANDS


def is_wol_pc_command(command):
    return command.strip().lower() in WOL_PC_COMMANDS


def is_remote_pc_shutdown_command(command):
    return command.strip().lower() in REMOTE_PC_SHUTDOWN_COMMANDS


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
                if voice_trigger_engine is not None:
                    voice_trigger_engine.disarm_screen_wake()
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
        success = result.returncode == 0
        if success and voice_trigger_engine is not None:
            voice_trigger_engine.disarm_screen_wake()
        return success
    except FileNotFoundError:
        return False


def configure_clap_screen_wake_after_sleep():
    """Arm temporary microphone capture only when the user allows it."""
    if voice_trigger_engine is None:
        return
    if CLAP_SCREEN_WAKE_ENABLED:
        voice_trigger_engine.arm_screen_wake()
    else:
        voice_trigger_engine.disarm_screen_wake()


def set_clap_screen_wake_enabled(enabled):
    """Change clap/snap screen wake independently of voice recognition."""
    global CLAP_SCREEN_WAKE_ENABLED
    CLAP_SCREEN_WAKE_ENABLED = bool(enabled)
    if voice_trigger_engine is not None and not CLAP_SCREEN_WAKE_ENABLED:
        voice_trigger_engine.disarm_screen_wake()
    save_clap_screen_wake_enabled(CLAP_SCREEN_WAKE_ENABLED)


def deep_sleep_machine(*, source="command"):
    """Suspend toàn máy. CPU/app/nhạc tạm dừng cho tới khi máy thức lại."""
    global last_suspend_request_monotonic

    try:
        with suspend_request_lock:
            now = time.monotonic()
            if last_suspend_request_monotonic is not None:
                elapsed = now - last_suspend_request_monotonic
                if elapsed < SUSPEND_COOLDOWN_SECONDS:
                    remaining = int(SUSPEND_COOLDOWN_SECONDS - elapsed)
                    _power_log(
                        "suspend_blocked_cooldown",
                        source=source,
                        remaining_seconds=remaining,
                    )
                    print(
                        "Jarvis: Đã chặn yêu cầu Sleep sâu lặp lại "
                        f"({remaining}s bảo vệ còn lại)."
                    )
                    return False

            # Đánh dấu trước khi gọi systemctl để mọi đường lệnh đồng thời đều
            # bị chặn, kể cả khi systemctl chưa kịp chuyển máy sang suspend.
            last_suspend_request_monotonic = now

        print("Jarvis: Đang đưa máy vào Sleep sâu...")
        _power_log("suspend_requested", source=source)

        result = subprocess.run(
            ["systemctl", "suspend"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        if result.returncode == 0:
            _power_log("suspend_command_finished", source=source, returncode=0)
            return True

        _power_log(
            "suspend_command_failed",
            source=source,
            returncode=result.returncode,
        )
        print(f"Jarvis: systemctl suspend thất bại (code {result.returncode}).")
        return False

    except FileNotFoundError:
        _power_log("suspend_command_missing", source=source)
        print("Jarvis: Không tìm thấy lệnh systemctl.")
        return False

    except Exception as error:
        _power_log("suspend_command_error", source=source, error=repr(error))
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
                configure_clap_screen_wake_after_sleep()
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
            configure_clap_screen_wake_after_sleep()
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


def _show_help_legacy():
    """Danh sách đầy đủ cũ, giữ lại để tham khảo khi bảo trì."""
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
    print("  file lớn nhất")
    print("  quét file có thể dọn  # chỉ báo cáo, không tự xóa")
    print("  file đang được sử dụng")
    print("  phân tích file <đường dẫn>")
    print("  dung lượng ổ đĩa")
    print("  dung lượng thư mục <đường dẫn>")
    print()
    print("  ghi nhớ <nội dung>")
    print("  ghi nhớ thiết bị: <nội dung>")
    print("  ghi nhớ tạm thời 2 ngày: <nội dung>")
    print("  nhớ lại <từ khóa>")
    print("  xem bộ nhớ")
    print("  cập nhật mục <ID> thành <nội dung>")
    print("  quên mục <ID>")
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


def show_help_category(category_key):
    """Hiển thị một nhóm trợ giúp; định nghĩa này thay thế help legacy dài."""
    message = format_help_category(category_key)
    print()
    print(message.replace("**", "").replace("`", ""))
    print()
    set_command_response(message)


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
    ("youtube dang phat gi", "youtube đang phát gì"),
    ("video dang phat", "youtube đang phát gì"),
    ("bai gi dang phat", "youtube đang phát gì"),
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
    ("mo zalo", "mở zalo công việc"),
    ("mo zalo web", "mở zalo"),
    ("mo zalo cong viec", "mở zalo công việc"),
    ("vao zalo", "mở zalo"),
    ("tat zalo", "tắt zalo"),
    ("dong zalo", "đóng zalo"),
    ("thoat zalo", "thoát zalo"),
    ("tom tat zalo cong viec", "tóm tắt zalo công việc"),
    ("tong hop zalo cong viec", "tóm tắt zalo công việc"),
    ("tom tat zalo hom nay", "tóm tắt zalo hôm nay"),
    ("tom tat zalo ngay", "tóm tắt zalo ngày <dd/mm/yyyy>"),
    ("tom tat zalo nhom", "tóm tắt zalo nhóm <tên nhóm>"),
    ("mo drive hoc", "mở drive học"),
    ("mo drive ca nhan", "mở drive cá nhân"),
    ("mo calendar hoc", "mở calendar học"),
    ("mo calendar ca nhan", "mở calendar cá nhân"),
    ("mo github hoc", "mở github học"),
    ("mo github ca nhan", "mở github cá nhân"),
    ("mo chrome hoc", "mở chrome học"),
    ("mo chrome ca nhan", "mở chrome cá nhân"),
    ("tat chatgpt hoc", "tắt chatgpt học"),
    ("tat gmail hoc", "tắt gmail học"),
    ("tat drive hoc", "tắt drive học"),
    ("tat calendar hoc", "tắt calendar học"),
    ("tat github hoc", "tắt github học"),
    ("tat chrome hoc", "tắt chrome học"),
    ("tat chrome ca nhan", "tắt chrome cá nhân"),
    # Ứng dụng và cửa sổ
    ("mo github", "mở github"),
    ("tat github", "tắt github"),
    ("dong github", "đóng github"),
    ("thoat github", "thoát github"),
    ("mo vscode", "mở vscode"),
    ("tat vscode", "tắt vscode"),
    ("mo terminal", "mở terminal"),
    ("tat terminal", "tắt terminal"),
    ("dong terminal", "đóng terminal"),
    ("tat chrome", "tắt chrome <học hoặc cá nhân>"),
    ("tat file manager", "tắt file manager"),
    ("tat tat ca", "tắt tất cả"),
    # Thư mục, file và project
    ("mo downloads", "mở downloads"),
    ("tat downloads", "tắt downloads"),
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
    ("nhac toi sau", "nhắc tôi sau <thời gian> <nội dung>"),
    ("nhac toi luc", "nhắc tôi lúc <giờ> [ngày dd/mm/yyyy]: <nội dung>"),
    ("lich nhac", "lịch nhắc"),
    ("huy lich nhac", "hủy lịch nhắc <số>"),
    ("soan zalo cho", "soạn zalo cho <tên>: <nội dung>"),
    ("soan tin nhan cho", "soạn tin nhắn cho <tên> với nội dung <nội dung>"),
    ("nhan zalo cho", "nhắn zalo cho <tên> lúc <giờ>: <nội dung>"),
    ("nhan zalo cho sau", "nhắn zalo cho <tên> sau <thời gian> nữa: <nội dung>"),
    # Thoát chỉ dùng trực tiếp trên Ubuntu; Discord sẽ tiếp tục chặn lệnh này.
    ("thoat", "thoát"),
    ("tam biet", "thoát"),
)


def _remember_command_suggestion(source, command):
    """Remember only complete, executable suggestions for a short time."""
    if not command or "<" in command or ">" in command:
        pending_command_suggestions.pop(source, None)
        return
    pending_command_suggestions[source] = (command, time.monotonic())


def resolve_command_confirmation(command, source="terminal"):
    """Expand a short yes/no reply against the last suggestion for this source."""
    plain = normalize_core_text(command)
    pending = pending_command_suggestions.get(source)
    if not pending:
        return command, None

    suggested_command, created_at = pending
    if time.monotonic() - created_at > SUGGESTION_CONFIRM_TTL_SECONDS:
        pending_command_suggestions.pop(source, None)
        if plain in SUGGESTION_AFFIRMATIONS:
            return command, "⌛ Gợi ý trước đã hết hạn. Hãy nhập lại lệnh bạn muốn chạy."
        return command, None

    if plain in SUGGESTION_REJECTIONS:
        pending_command_suggestions.pop(source, None)
        return command, "👌 Đã bỏ qua lệnh được gợi ý."
    if plain in SUGGESTION_AFFIRMATIONS:
        pending_command_suggestions.pop(source, None)
        return suggested_command, f"✅ Đã xác nhận lệnh `{suggested_command}`."

    # Một lệnh mới thay thế câu hỏi xác nhận cũ.
    pending_command_suggestions.pop(source, None)
    return command, None


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


async def route_command(command, *, allow_local_ai=True, source="terminal"):
    command = command.strip()

    if normalize_core_text(command) in SUGGESTION_AFFIRMATIONS:
        set_command_response(
            "ℹ️ Không có lệnh nào đang chờ xác nhận bằng chữ. "
            "Với gửi Zalo hoặc lệnh nguy hiểm, hãy dùng nút xác nhận trên Discord."
        )
        return True

    # Các lệnh lưu/gửi nội dung phải chạy trước bước chuẩn hóa để giữ nguyên
    # chữ hoa, dấu tiếng Việt và dấu câu trong lời nhắc/tin nhắn.
    if await handle_zalo_send_command(command, source=source):
        print(f"Jarvis: {last_command_response}")
        return True
    if handle_reminder_command(command, source=source):
        print(f"Jarvis: {last_command_response}")
        return True

    # Truy vấn chỉ đọc phải được nhận diện trước local AI. Các hàm này không
    # phát/dừng media, reload trang hoặc đưa Chrome ra trước màn hình.
    if is_soundcloud_now_playing_command(command):
        return await report_soundcloud_now_playing()
    if is_youtube_now_playing_command(command):
        return await report_youtube_now_playing()

    command_lower_raw = command.casefold()
    for prefix in ("mở đường dẫn ", "mo duong dan "):
        if command_lower_raw.startswith(prefix):
            return open_filesystem_path(command[len(prefix):].strip())
    for prefix in (
        "tắt đường dẫn ", "tat duong dan ", "đóng đường dẫn ",
        "dong duong dan ", "thoát đường dẫn ", "thoat duong dan ",
    ):
        if command_lower_raw.startswith(prefix):
            success, detail = FILE_WINDOWS.close_path(command[len(prefix):].strip())
            message = f"{'✅' if success else '❌'} {detail}"
            print(f"Jarvis: {message}")
            set_command_response(message)
            return True

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

    if normalize_core_text(command) in {
        "lich su tro chuyen", "xem lich su tro chuyen", "lich su chat", "xem chat cu",
    }:
        rows = CORE.conversation.recent(20)
        if not rows:
            message = "ℹ️ Jarvis chưa có lịch sử trò chuyện."
        else:
            source_names = {"gtk": "Ứng dụng", "discord": "Discord", "terminal": "Terminal"}
            lines = ["🗃️ **LỊCH SỬ TRÒ CHUYỆN GẦN ĐÂY**"]
            for row in rows:
                role = "Bạn" if row["role"] == "user" else "Jarvis"
                source_name = source_names.get(row["source"], row["source"])
                content = redact_sensitive(row["content"]).replace("\n", " ")[:180]
                lines.append(f"• {role} · {source_name}: {content}")
            message = "\n".join(lines)
        set_command_response(message)
        return True

    plain_command = normalize_core_text(command)
    is_gmail_spam = any(term in plain_command for term in ("thu rac", "spam")) and any(
        term in plain_command for term in ("tom tat", "tong hop", "doc", "co gi", "kiem tra")
    )
    if is_gmail_spam:
        return await summarize_gmail(
            folder="spam", meaningful_only=True, unread_only=True
        )

    is_gmail_summary = any(name in plain_command for name in ("gmail", "email")) and any(
        term in plain_command for term in ("tom tat", "tong hop", "doc", "co gi", "kiem tra", "moi")
    )
    if is_gmail_summary:
        include_read = any(term in plain_command for term in ("gan day", "tat ca"))
        unread_only = not include_read
        return await summarize_gmail(unread_only=unread_only)

    is_zalo_analysis = "zalo" in plain_command and any(term in plain_command for term in (
        "tom tat", "tong hop", "doc", "co gi", "viec can lam", "hoi",
        "su kien", "kiem tra", "cho toi biet",
    ))
    if is_zalo_analysis:
        selected_date, group_query, question = parse_zalo_request(command)
        mentions_date = any(term in plain_command for term in ("hom nay", "hom qua", "ngay"))
        if mentions_date and selected_date is None:
            message = "ℹ️ Ngày không hợp lệ. Ví dụ: `tóm tắt zalo ngày 12/08/2026`."
            set_command_response(message)
            return True
        return await summarize_zalo_work(
            selected_date=selected_date,
            group_query=group_query,
            question=question,
        )

    # ------------------------------------------------------
    # WAKE-ON-LAN CHO PC KHÁC TRONG CÙNG MẠNG
    # ------------------------------------------------------

    if is_wol_pc_command(command):
        try:
            send_wake_on_lan()
            set_command_response("🖥️ Đã bật PC.")
        except (OSError, ValueError) as error:
            set_command_response(f"❌ Không thể gửi Wake-on-LAN: {error}.")
        print(f"Jarvis: {last_command_response}")
        return True

    if is_remote_pc_shutdown_command(command):
        try:
            shutdown_remote_pc()
            set_command_response("🖥️ Đã tắt PC.")
        except (OSError, ValueError) as error:
            set_command_response(f"❌ Không thể tắt PC qua SSH: {error}.")
        print(f"Jarvis: {last_command_response}")
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
                "🌙 Đã sleep màn hình. Nhạc, Jarvis và Discord vẫn tiếp tục chạy.",
                # Speaking after the display is blanked is picked up by the
                # deliberately sensitive screen-wake impulse detector.
                spoken_message=False,
            )
        else:
            set_command_response("❌ Jarvis không thể sleep màn hình.")
        return True

    # ------------------------------------------------------
    # SLEEP SÂU / SUSPEND TOÀN MÁY
    # ------------------------------------------------------

    if is_deep_sleep_command(command):
        if deep_sleep_machine(source="router"):
            set_command_response("😴 Ubuntu đang chuyển sang Sleep sâu.")
        else:
            set_command_response("❌ Jarvis không thể đưa Ubuntu vào Sleep sâu.")
        return True

    # ------------------------------------------------------
    # ÂM LƯỢNG
    # ------------------------------------------------------

    if handle_volume_command(command):
        return True

    voice_control = normalize_core_text(command)
    if voice_control in {
        "tat vo tay", "tat vo tay bat man hinh", "tat vo tay de bat man hinh",
        "tat tinh nang vo tay", "tat cam bien vo tay",
    }:
        try:
            set_clap_screen_wake_enabled(False)
            set_command_response(
                "👏 Đã tắt vỗ tay để bật màn hình. Jarvis sẽ không mở "
                "microphone chờ tiếng vỗ khi màn hình sleep."
            )
        except OSError as error:
            set_command_response(f"❌ Không thể lưu cài đặt vỗ tay: {error}")
        print(f"Jarvis: {last_command_response}")
        return True

    if voice_control in {
        "bat vo tay", "bat vo tay bat man hinh", "bat vo tay de bat man hinh",
        "bat tinh nang vo tay", "bat cam bien vo tay",
    }:
        try:
            set_clap_screen_wake_enabled(True)
            set_command_response(
                "👏 Đã bật vỗ tay để bật màn hình. Tính năng sẽ sẵn sàng "
                "mỗi khi Jarvis tắt màn hình."
            )
        except OSError as error:
            set_command_response(f"❌ Không thể lưu cài đặt vỗ tay: {error}")
        print(f"Jarvis: {last_command_response}")
        return True

    if voice_control in {
        "trang thai vo tay", "vo tay bat man hinh",
        "trang thai vo tay bat man hinh",
    }:
        message = (
            "👏 Vỗ tay để bật màn hình đang bật."
            if CLAP_SCREEN_WAKE_ENABLED else
            "🔇 Vỗ tay để bật màn hình đang tắt."
        )
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True

    if voice_control in {
        "tat giong noi", "tat giong noi tam thoi", "tat cam bien giong noi",
        "tat cam bien am thanh", "tam dung giong noi", "tam dung cam bien",
    }:
        if voice_trigger_engine is None:
            set_command_response("ℹ️ Cảm biến giọng nói chưa hoạt động.")
        else:
            voice_trigger_engine.pause()
            set_command_response(
                "🔇 Đã tạm tắt cảm biến vỗ tay và giọng nói. "
                "Dùng `bật giọng nói` để khôi phục."
            )
        print(f"Jarvis: {last_command_response}")
        return True

    if voice_control in {
        "bat giong noi", "bat lai giong noi", "bat cam bien giong noi",
        "bat cam bien am thanh", "tiep tuc giong noi", "tiep tuc cam bien",
    }:
        if voice_trigger_engine is None:
            set_command_response("ℹ️ Cảm biến giọng nói chưa hoạt động.")
        else:
            voice_trigger_engine.resume()
            set_command_response("🎙️ Đã bật lại cảm biến vỗ tay và giọng nói.")
        print(f"Jarvis: {last_command_response}")
        return True

    if voice_control in {
        "trang thai cam bien am thanh", "cam bien am thanh",
        "trang thai giong noi", "microphone jarvis",
    }:
        paused = bool(
            voice_trigger_engine is not None
            and voice_trigger_engine.is_paused()
        )
        ready = bool(
            VOICE_TRIGGER_ENABLED
            and voice_trigger_engine is not None
            and voice_trigger_engine.available()
            and not voice_trigger_engine.stop_event.is_set()
            and not paused
        )
        message = (
            "🔇 Cảm biến vỗ tay và giọng nói đang tạm tắt. "
            "Dùng `bật giọng nói` để khôi phục."
            if paused else
            "🎙️ Cảm biến âm thanh đang bật. Vỗ tay 2 lần hoặc búng tay "
            "1 lần, chờ Jarvis nói 'Jarvis đang nghe' xong rồi nói lệnh."
            if ready else
            "ℹ️ Cảm biến âm thanh chưa hoạt động. Hãy kiểm tra model, microphone "
            "và cấu hình VOICE_TRIGGER_ENABLED."
        )
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True

    # ------------------------------------------------------
    # SOUNDCLOUD
    # ------------------------------------------------------

    if extract_soundcloud_search_query(command):
        await search_soundcloud(command)
        return True

    soundcloud_number = re.fullmatch(
        r"(?:mở|mo)\s+(?:bài|bai|nhạc|nhac)?\s*soundcloud\s+(\d+)",
        command,
        re.IGNORECASE,
    )
    if soundcloud_number:
        await open_soundcloud_track(number=int(soundcloud_number.group(1)))
        return True

    soundcloud_name = re.fullmatch(
        r"(?:mở|mo)\s+(?:bài|bai|nhạc|nhac)\s+soundcloud\s+(.+)",
        command,
        re.IGNORECASE,
    )
    if soundcloud_name:
        await open_soundcloud_track(query=soundcloud_name.group(1).strip())
        return True

    if command_lower in {
        "dừng soundcloud", "dung soundcloud", "tạm dừng soundcloud",
        "tam dung soundcloud", "pause soundcloud",
    }:
        await control_soundcloud_playback(False)
        return True

    if command_lower in {
        "phát soundcloud", "phat soundcloud", "phát tiếp soundcloud",
        "phat tiep soundcloud", "tiếp tục soundcloud", "tiep tuc soundcloud",
        "play soundcloud", "resume soundcloud",
    }:
        await control_soundcloud_playback(True)
        return True

    if command_lower in {
        "làm mới soundcloud", "lam moi soundcloud", "refresh soundcloud",
        "cập nhật soundcloud", "cap nhat soundcloud",
    }:
        await refresh_soundcloud_tracks()
        return True

    if command_lower in {
        "hiện soundcloud", "hien soundcloud", "mở lại soundcloud",
        "mo lai soundcloud", "quay lại soundcloud", "quay lai soundcloud",
        "show soundcloud",
    }:
        await show_soundcloud()
        return True

    if command_lower in {
        "trang chủ soundcloud", "trang chu soundcloud", "soundcloud home",
        "home soundcloud", "về soundcloud", "ve soundcloud",
    }:
        await go_soundcloud_home()
        return True

    if command_lower in {
        "tắt soundcloud", "tat soundcloud", "đóng soundcloud",
        "dong soundcloud", "đóng tab soundcloud", "dong tab soundcloud",
        "thoát soundcloud", "thoat soundcloud", "close soundcloud",
    }:
        await close_soundcloud()
        return True

    if command_lower in {
        "soundcloud", "mở soundcloud", "mo soundcloud",
        "vào soundcloud", "vao soundcloud",
    }:
        await open_soundcloud()
        return True

    # ------------------------------------------------------
    # TẮT / ĐÓNG WEBSITE JARVIS QUẢN LÝ
    # ------------------------------------------------------

    close_zalo_commands = {
        "tắt zalo", "tat zalo", "đóng zalo", "dong zalo",
        "thoát zalo", "thoat zalo", "close zalo",
        "tắt zalo web", "tat zalo web", "đóng zalo web", "dong zalo web",
    }
    if command_lower in close_zalo_commands:
        await close_managed_web_tab(
            "zalo", "Zalo", ("https://chat.zalo.me/",)
        )
        return True

    close_gmail_commands = {
        "tắt gmail", "tat gmail", "đóng gmail", "dong gmail",
        "thoát gmail", "thoat gmail", "close gmail",
        "tắt gmail học", "tat gmail hoc", "đóng gmail học", "dong gmail hoc",
    }
    if command_lower in close_gmail_commands:
        await close_managed_web_tab(
            "gmail", "Gmail", ("https://mail.google.com/", "https://accounts.google.com/")
        )
        return True

    # ------------------------------------------------------
    # MỞ NHANH WEBSITE TRONG CHROME THEO PROFILE
    # ------------------------------------------------------

    if handle_chrome_shortcut_close(command):
        return True

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
        "thoát youtube",
        "thoat youtube",
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
    # TẮT THƯ MỤC SHORTCUT - CHỈ CỬA SỔ GẦN NHẤT JARVIS MỞ
    # ------------------------------------------------------

    folder_shortcut_close = re.fullmatch(
        r"(?:tắt|tat|đóng|dong|thoát|thoat|close)\s+"
        r"(?:downloads?|documents?|pictures?|home)",
        command_lower,
    )
    if folder_shortcut_close:
        target = folder_shortcut_close.group(0).split()[-1]
        folder_path = {
            "download": Path.home() / "Downloads",
            "downloads": Path.home() / "Downloads",
            "document": Path.home() / "Documents",
            "documents": Path.home() / "Documents",
            "picture": Path.home() / "Pictures",
            "pictures": Path.home() / "Pictures",
            "home": Path.home(),
        }[target]
        success, detail = FILE_WINDOWS.close_path(folder_path)
        message = f"{'✅' if success else 'ℹ️'} {detail}"
        set_command_response(message)
        print(f"Jarvis: {message}")
        return True

    # ------------------------------------------------------
    # TẮT TERMINAL - CHỈ CỬA SỔ JARVIS VỪA MỞ
    # ------------------------------------------------------

    if command_lower in {
        "tắt terminal", "tat terminal", "đóng terminal", "dong terminal",
        "thoát terminal", "thoat terminal", "close terminal",
        "tắt cửa sổ terminal", "tat cua so terminal",
    }:
        success, detail = FILE_WINDOWS.close_last("terminal")
        if not success:
            success, process_detail = FILE_WINDOWS.close_process("terminal", "Terminal")
            if success or "do Jarvis theo dõi" not in detail:
                detail = process_detail
        message = f"{'✅' if success else 'ℹ️'} {detail}"
        set_command_response(message)
        print(f"Jarvis: {message}")
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
        "thoát vscode",
        "thoat vscode",
        "thoát vs code",
        "thoat vs code",
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
    # SYSTEM MONITOR
    # ------------------------------------------------------

    if command_lower in {
        "system monitor", "mở system monitor", "mo system monitor",
        "mở trình giám sát hệ thống", "mo trinh giam sat he thong",
    }:
        open_system_monitor()
        return True

    if command_lower in {
        "tắt system monitor", "tat system monitor",
        "đóng system monitor", "dong system monitor",
        "thoát system monitor", "thoat system monitor",
    }:
        close_system_monitor()
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

        search_google(
            command,
            allow_prompt=(source == "terminal" and sys.stdin.isatty()),
        )

        return True


    # ------------------------------------------------------
    # HELP
    # ------------------------------------------------------

    whats_new_commands = {
        "co gi moi", "chuc nang moi", "jarvis co gi moi",
        "sau update ban co the lam nhung gi",
        "sau update jarvis co the lam nhung gi",
        "sau cap nhat ban co the lam nhung gi",
        "ban vua update gi", "da update nhung gi",
    }
    if normalize_core_text(command) in whats_new_commands:
        message = format_recent_updates()
        print()
        print(message.replace("**", "").replace("`", ""))
        print()
        set_command_response(message)
        return True

    help_match = re.match(
        r"^(?:help|tro giup|giup|chuc nang|kham pha chuc nang)(?:\s+(.+))?$",
        normalize_core_text(command),
    )
    if help_match and help_match.group(1):
        category_key = resolve_help_category(help_match.group(1))
        if category_key:
            show_help_category(category_key)
        else:
            message = f"Tôi chưa tìm thấy nhóm đó. Hãy chọn: {format_category_choices()}."
            print(f"Jarvis: {message}")
            set_command_response(message)
        return True

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
        "khám phá chức năng",
        "kham pha chuc nang",
    }:

        overview = format_help_overview()
        print()
        print(overview.replace("**", "").replace("`", ""))
        print()
        set_command_response(overview)

        return True


    # Chỉ gợi ý sau khi toàn bộ lệnh chính xác đã được thử. Nếu đặt phần này
    # sớm hơn, lệnh đúng như "mở youtube cá nhân" sẽ bị hỏi lại thay vì chạy.
    known_suggestion = _suggest_known_command(command)
    if known_suggestion:
        match = re.search(r"`([^`]+)`", known_suggestion)
        _remember_command_suggestion(source, match.group(1) if match else None)
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
            return await route_command(
                routed_command, allow_local_ai=False, source=source
            )
        local_answer = decision.get("reply") or _suggest_local_ai_command(decision) or (
            "🤔 Tôi chưa hiểu rõ lệnh đó. Hãy viết lại cụ thể hơn hoặc gõ "
            "`help` để xem các lệnh mẫu."
        )
    print(f"Jarvis: {local_answer}")
    suggestion_match = re.search(
        r"Có phải bạn muốn(?: dùng lệnh)? `([^`]+)`\?", local_answer
    )
    if suggestion_match:
        _remember_command_suggestion(source, suggestion_match.group(1))
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


class DiscordHelpCommandView(discord.ui.View):
    """Các nút chỉ đưa cú pháp mẫu, không tự chạy hành động hệ thống."""

    def __init__(self, category_key, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        category = HELP_CATEGORIES[category_key]
        for index, (label, command) in enumerate(category["commands"][:20]):
            button = discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                row=min(3, index // 5),
            )

            async def show_command(interaction, sample=command):
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        "Bạn không có quyền điều khiển Jarvis này.", ephemeral=True
                    )
                    return
                await interaction.response.send_message(
                    f"Lệnh mẫu:\n```\n{sample}\n```\nBạn có thể sao chép hoặc gửi chính xác lệnh này.",
                    ephemeral=True,
                )

            button.callback = show_command
            self.add_item(button)


class DiscordHelpCategoryView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        for index, (key, category) in enumerate(HELP_CATEGORIES.items()):
            button = discord.ui.Button(
                label=category["title"][:80],
                emoji=category["icon"],
                style=discord.ButtonStyle.primary if key == "memory" else discord.ButtonStyle.secondary,
                row=index // 5,
            )

            async def show_category(interaction, category_key=key):
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        "Bạn không có quyền điều khiển Jarvis này.", ephemeral=True
                    )
                    return
                response = format_help_category(category_key)
                CORE.conversation.add("discord", "user", f"help {category_key}")
                CORE.conversation.add("discord", "assistant", response)
                await interaction.response.send_message(
                    response,
                    view=DiscordHelpCommandView(category_key, self.owner_id),
                    ephemeral=True,
                )

            button.callback = show_category
            self.add_item(button)


class DiscordZaloSendConfirmationView(discord.ui.View):
    """One-time confirmation for a scheduled external Zalo message."""
    def __init__(self, reminder):
        super().__init__(timeout=60)
        self.reminder = reminder
        self.used = False

    async def interaction_check(self, interaction):
        owner_id = _env_int("DISCORD_USER_ID")
        if self.used or interaction.user.id != owner_id:
            await interaction.response.send_message("Bạn không thể xác nhận yêu cầu này.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Xác nhận gửi Zalo", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, _button):
        self.used = True
        await interaction.response.edit_message(content="⏳ Jarvis đang gửi tin Zalo…", view=None)
        ok, detail = await send_zalo_message(
            self.reminder.get("recipient", ""), self.reminder["content"], send=True,
            tag=self.reminder.get("zalo_tag", ""),
        )
        if ok:
            CORE.reminders.mark_delivered(self.reminder["id"])
            await interaction.followup.send(f"✅ {detail}")
        else:
            CORE.reminders.mark_cancelled(self.reminder["id"])
            await interaction.followup.send(f"❌ {detail}")

    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, _button):
        self.used = True
        CORE.reminders.mark_cancelled(self.reminder["id"])
        await interaction.response.edit_message(content="Đã hủy gửi tin Zalo.", view=None)

    async def on_timeout(self):
        if not self.used:
            CORE.reminders.mark_cancelled(self.reminder["id"])
            self.used = True


class DiscordDangerConfirmationView(discord.ui.View):
    """Single-use, owner-only confirmation that expires after 60 seconds."""

    def __init__(self, command, owner_id, channel_id):
        super().__init__(timeout=60)
        self.command = command
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.used = False

    async def _check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Bạn không có quyền xác nhận yêu cầu này.", ephemeral=True
            )
            return False
        if self.used:
            await interaction.response.send_message(
                "Yêu cầu này đã được xử lý hoặc hết hiệu lực.", ephemeral=True
            )
            return False
        self.used = True
        for child in self.children:
            child.disabled = True
        return True

    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, _button):
        if not await self._check(interaction):
            return
        CORE.security.audit(
            "discord", self.command, "confirm", "confirmed",
            interaction.user.id, interaction.channel_id,
        )
        await interaction.response.edit_message(
            content=f"✅ Đã xác nhận: `{self.command}`\nJarvis đang thực hiện…",
            view=self,
        )
        global last_command_response
        try:
            async with discord_command_lock:
                last_command_response = None
                await route_command(self.command, source="discord")
                response = redact_sensitive(
                    last_command_response or "✅ Jarvis đã xử lý lệnh."
                )
            CORE.conversation.add("discord", "user", self.command)
            CORE.conversation.add("discord", "assistant", response)
            await interaction.followup.send(response[:1900], ephemeral=True)
        except Exception as error:
            CORE.security.audit(
                "discord", self.command, "confirm", "error",
                interaction.user.id, interaction.channel_id,
            )
            await interaction.followup.send(
                f"❌ Không thể thực hiện: {redact_sensitive(error)}", ephemeral=True
            )

    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, _button):
        if not await self._check(interaction):
            return
        CORE.security.audit(
            "discord", self.command, "confirm", "cancelled",
            interaction.user.id, interaction.channel_id,
        )
        await interaction.response.edit_message(
            content=f"Đã hủy yêu cầu: `{self.command}`", view=self
        )

    async def on_timeout(self):
        self.used = True


def _discord_command_needs_profile(command):
    """Tránh Jarvis chờ input() trên Terminal khi lệnh đến từ Discord."""
    command_lower = command.strip().lower()

    # YouTube luôn dùng Chrome automation riêng của Jarvis. Cả lệnh mở và
    # tìm kiếm đều không còn cần chọn profile Học/Cá nhân.
    bare_youtube_commands = {
        "youtube", "mở youtube", "mo youtube", "vào youtube", "vao youtube",
    }
    youtube_search_prefixes = (
        "tìm youtube ", "tim youtube ",
        "youtube tìm ", "youtube tim ",
        "tìm video ", "tim video ",
    )
    if command_lower in bare_youtube_commands or command_lower.startswith(
        youtube_search_prefixes
    ):
        return False

    profile_words = (
        "cá nhân", "ca nhan", "personal",
        "học", "hoc", "study", "chatgpt",
    )

    if any(word in command_lower for word in profile_words):
        return False


    if command_lower.startswith(("tìm ", "tim ")):
        google_search_prefixes = (
            "tìm google ", "tim google ",
            "tìm trên google ", "tim tren google ",
            "tìm kiếm ", "tim kiem ",
        )

        if not command_lower.startswith(google_search_prefixes):
            return youtube_page_id is None

    bare_github_commands = {
        "github", "mở github", "mo github", "vào github", "vao github",
        "tắt github", "tat github", "đóng github", "dong github",
        "thoát github", "thoat github",
    }
    if command_lower in bare_github_commands:
        return False

    if command_lower in {
        "gmail", "mở gmail", "mo gmail", "vào gmail", "vao gmail",
        "tóm tắt gmail", "tom tat gmail", "tóm tắt email", "tom tat email",
        "gmail mới", "gmail moi", "email mới", "email moi", "kiểm tra gmail", "kiem tra gmail",
    }:
        return False

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
    global discord_client, discord_notification_channel_id

    token = os.getenv("DISCORD_TOKEN", "").strip()
    owner_id = _env_int("DISCORD_USER_ID")
    channel_id = _env_int("DISCORD_CHANNEL_ID")

    if not token:
        print("Jarvis: Discord chưa chạy vì thiếu DISCORD_TOKEN trong .env.")
        return False

    if owner_id is None:
        print("Jarvis: Discord chưa chạy vì thiếu DISCORD_USER_ID trong .env.")
        print("Jarvis: Thêm ID Discord của bạn để chỉ bạn có quyền điều khiển máy.")
        return False

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
        global discord_notification_channel_id
        if message.author.bot:
            return

        if message.author.id != owner_id:
            return

        if channel_id is not None and message.channel.id != channel_id:
            return

        # Khi .env không khóa một channel cụ thể, dùng kênh hợp lệ gần nhất
        # của chính chủ làm nơi gửi lời nhắc chủ động.
        discord_notification_channel_id = message.channel.id

        command = message.content.strip()

        if not command:
            return

        command, confirmation_note = resolve_command_confirmation(command, "discord")
        if confirmation_note and normalize_core_text(message.content) in SUGGESTION_REJECTIONS:
            await message.reply(confirmation_note, mention_author=False)
            return
        if confirmation_note and command == message.content.strip():
            await message.reply(confirmation_note, mention_author=False)
            return

        settings = CORE.security.settings()
        if not CORE.security.rate_limit(message.author.id):
            CORE.security.audit(
                "discord", command, "rate_limit", "blocked",
                message.author.id, message.channel.id,
            )
            await message.reply(
                "⏳ Bạn gửi lệnh quá nhanh. Hãy chờ khoảng 10 giây rồi thử lại.",
                mention_author=False,
            )
            return

        risk = classify_remote_command(command)
        if not settings["discord_enabled"] and not CORE.security.is_allowed_when_locked(command):
            CORE.security.audit(
                "discord", command, risk, "remote_locked",
                message.author.id, message.channel.id,
            )
            await message.reply(
                "🔒 Điều khiển Discord đang bị khóa trên Ubuntu. Chỉ help và trạng thái hệ thống được phép.",
                mention_author=False,
            )
            return
        if risk == "forbidden":
            CORE.security.audit(
                "discord", command, risk, "forbidden",
                message.author.id, message.channel.id,
            )
            await message.reply(
                "⛔ Jarvis từ chối đọc bí mật hoặc xóa vĩnh viễn qua Discord.",
                mention_author=False,
            )
            return
        if risk == "confirm":
            if not settings["dangerous_enabled"]:
                CORE.security.audit(
                    "discord", command, risk, "dangerous_locked",
                    message.author.id, message.channel.id,
                )
                await message.reply(
                    "🔒 Lệnh nguy hiểm từ xa đang bị tắt trong ứng dụng Ubuntu.",
                    mention_author=False,
                )
                return
            CORE.security.audit(
                "discord", command, risk, "pending_confirmation",
                message.author.id, message.channel.id,
            )
            warning = (
                f"⚠️ **Yêu cầu cần xác nhận**\n`{command}`\n\n"
                "Chỉ tài khoản của bạn có thể xác nhận. Yêu cầu hết hạn sau 60 giây."
            )
            await message.reply(
                warning,
                mention_author=False,
                view=DiscordDangerConfirmationView(
                    command, owner_id, message.channel.id
                ),
            )
            return

        CORE.security.audit(
            "discord", command, risk, "accepted",
            message.author.id, message.channel.id,
        )
        CORE.conversation.add("discord", "user", command)

        if command.lower() in {
            "thoát", "thoat", "exit", "quit", "bye",
            "tạm biệt", "tam biet",
        }:
            response_text = "Lệnh thoát từ Discord bị khóa. Hãy thoát Jarvis trực tiếp trên Ubuntu."
            CORE.conversation.add("discord", "assistant", response_text)
            await message.reply(
                response_text,
                mention_author=False,
            )
            return

        if _discord_command_needs_profile(command):
            response_text = (
                "Lệnh này cần chỉ rõ Chrome profile. Ví dụ: "
                "`mở youtube cá nhân` hoặc `mở youtube học`."
            )
            CORE.conversation.add("discord", "assistant", response_text)
            await message.reply(
                response_text,
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
            CORE.conversation.add("discord", "assistant", deep_sleep_message)
            await message.reply(
                deep_sleep_message,
                mention_author=False,
            )
            speak(deep_sleep_message)
            await asyncio.sleep(0.8)
            deep_sleep_machine(source="discord")
            return

        # Sleep màn hình không suspend hệ thống nên Discord/Jarvis vẫn hoạt động.
        if is_screen_sleep_command(command):
            screen_sleep_message = (
                "🌙 Jarvis sẽ tắt màn hình. Nhạc và Discord vẫn tiếp tục chạy."
            )
            CORE.conversation.add("discord", "assistant", screen_sleep_message)
            await message.reply(
                screen_sleep_message,
                mention_author=False,
            )
            await asyncio.sleep(0.4)
            sleep_screen_only()
            return

        try:
            global last_command_response
            last_command_response = None

            async with discord_command_lock:
                await route_command(command, source="discord")

            speak_last_response()

            if last_command_response:
                # Discord giới hạn một message khoảng 2000 ký tự. Bản tổng hợp
                # Zalo có thể dài vì gồm tới 20 nhóm, nên chia theo dòng thay vì cắt mất.
                response_text = redact_sensitive(last_command_response)
                if risk != "sensitive":
                    CORE.conversation.add("discord", "assistant", response_text)
                response_view = None
                if last_command_response.startswith("🧰 **JARVIS CÓ THỂ"):
                    response_view = DiscordHelpCategoryView(owner_id)
                else:
                    help_match = re.match(
                        r"^(?:help|tro giup|giup|chuc nang|kham pha chuc nang)(?:\s+(.+))?$",
                        normalize_core_text(command),
                    )
                    if help_match and help_match.group(1):
                        category_key = resolve_help_category(help_match.group(1))
                        if category_key:
                            response_view = DiscordHelpCommandView(
                                category_key, owner_id
                            )
                chunks = []
                remaining = response_text
                while remaining:
                    if len(remaining) <= 1900:
                        chunks.append(remaining)
                        break
                    split_at = remaining.rfind("\n", 0, 1900)
                    if split_at < 500:
                        split_at = 1900
                    chunks.append(remaining[:split_at].rstrip())
                    remaining = remaining[split_at:].lstrip()
                await message.reply(
                    chunks[0], mention_author=False, view=response_view
                )
                for chunk in chunks[1:]:
                    await message.channel.send(chunk)
            else:
                CORE.conversation.add("discord", "assistant", "✅ Jarvis đã xử lý lệnh.")
                await message.reply(
                    "✅ Jarvis đã xử lý lệnh.",
                    mention_author=False,
                )
        except Exception as error:
            print(f"Jarvis: Lỗi Discord command: {error}")
            CORE.conversation.add(
                "discord", "assistant", "❌ Jarvis gặp lỗi khi xử lý lệnh."
            )
            await message.reply(
                "❌ Jarvis gặp lỗi khi xử lý lệnh. Xem Terminal Ubuntu để biết chi tiết.",
                mention_author=False,
            )

    try:
        await client.start(token)
        return True
    except discord.LoginFailure:
        print("Jarvis: DISCORD_TOKEN không hợp lệ. Hãy kiểm tra/reset token.")
        return False
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"Jarvis: Không thể kết nối Discord: {error}")
        return True
    finally:
        if not client.is_closed():
            await client.close()
        if discord_client is client:
            discord_client = None


async def supervise_discord_bot():
    """Reconnect Discord after transient failures without stopping Jarvis."""
    while True:
        try:
            should_retry = await start_discord_bot()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Jarvis: Lỗi vòng kết nối Discord: {error}")
            should_retry = True
        if not should_retry:
            return
        print(
            "Jarvis: Sẽ thử kết nối lại Discord sau "
            f"{DISCORD_RECONNECT_DELAY_SECONDS} giây."
        )
        await asyncio.sleep(DISCORD_RECONNECT_DELAY_SECONDS)


async def stop_discord_bot():
    global discord_client

    if discord_client is not None and not discord_client.is_closed():
        await discord_client.close()

    discord_client = None


# ==========================================================
# MAIN
# ==========================================================

def acquire_instance_lock():
    """Giữ process lock trong suốt vòng đời Jarvis."""
    global instance_lock_file

    lock_name = f"jarvis-{os.getuid()}.lock"
    runtime_dir = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp"))
    lock_paths = [runtime_dir / lock_name]
    fallback_path = Path("/tmp") / lock_name
    if fallback_path not in lock_paths:
        lock_paths.append(fallback_path)

    last_error = None
    for lock_path in lock_paths:
        lock_file = None
        try:
            lock_file = lock_path.open("a+")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(f"{os.getpid()}\n")
            lock_file.flush()
            instance_lock_file = lock_file
            return True
        except BlockingIOError:
            if lock_file is not None:
                lock_file.close()
            print(
                "Jarvis: Một phiên Jarvis khác đang chạy. "
                "Đã dừng phiên mới để tránh trùng lịch Sleep sâu."
            )
            return False
        except OSError as error:
            last_error = error
            if lock_file is not None:
                lock_file.close()

    print(f"Jarvis: Không thể tạo khóa tiến trình: {last_error}")
    return False

async def main():
    global voice_trigger_engine
    if not acquire_instance_lock():
        return

    print()
    print("=" * 60)
    print(f"            JARVIS v{VERSION}")
    print("=" * 60)

    # Khi chạy trực tiếp trong Terminal, stdin là TTY và Jarvis giữ nguyên
    # giao diện nhập lệnh cũ. Khi chạy bằng systemd --user, stdin không phải
    # TTY nên Jarvis không gọi input() và tiếp tục sống nhờ lịch nhắc/IPC.
    interactive_terminal = sys.stdin.isatty()

    print()
    start_tts_worker()
    await start_ipc_server()

    if interactive_terminal:
        jarvis_say('Sẵn sàng. Gõ "help" để xem lệnh.')
    else:
        print("Jarvis: Đang chạy nền bằng systemd. Terminal input đã tắt.")
        print("Jarvis: Discord vẫn tiếp tục nhận lệnh.")

    discord_task = asyncio.create_task(supervise_discord_bot())
    reminder_task = asyncio.create_task(reminder_dispatch_loop())
    gmail_task = asyncio.create_task(gmail_monitor_loop())
    voice_task = None
    if VOICE_TRIGGER_ENABLED:
        voice_trigger_engine = VoiceTriggerEngine(
            VOICE_TRIGGER_MODEL_PATH,
            source=VOICE_TRIGGER_SOURCE,
            suppression_callback=voice_trigger_is_suppressed,
        )
        if voice_trigger_engine.available():
            voice_task = asyncio.create_task(run_voice_trigger_loop(
                voice_trigger_engine,
                handle_voice_command,
                ready_callback=announce_voice_ready,
                cancelled_callback=announce_voice_cancelled,
                unrecognized_callback=announce_voice_unrecognized,
                trigger_callback=handle_screen_wake_trigger,
                ready_sound=VOICE_TRIGGER_READY_SOUND,
            ))
            print(
                "Jarvis: Cảm biến âm thanh đã bật "
                "(vỗ tay 2 lần hoặc búng tay 1 lần)."
            )
        else:
            print(
                "Jarvis: Cảm biến âm thanh chưa sẵn sàng; "
                f"thiếu model tại {VOICE_TRIGGER_MODEL_PATH} hoặc thiếu parec."
            )

    try:
        if interactive_terminal:
            # Chế độ chạy thủ công: giữ nguyên toàn bộ cách nhập lệnh Terminal cũ.
            running = True

            while running:
                try:
                    print()
                    command = (await asyncio.to_thread(input, "Bạn: ")).strip()
                    original_command = command

                    global last_command_response
                    last_command_response = None

                    if command:
                        CORE.conversation.add("terminal", "user", command)
                    command, confirmation_note = resolve_command_confirmation(
                        command, "terminal"
                    )
                    if confirmation_note and command == original_command:
                        set_command_response(confirmation_note)
                        running = True
                    else:
                        running = await route_command(command, source="terminal")
                    if last_command_response and classify_remote_command(command) != "sensitive":
                        CORE.conversation.add(
                            "terminal", "assistant", last_command_response
                        )
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
            # Chế độ systemd phải tiếp tục phục vụ IPC/TTS/lịch nhắc ngay cả
            # khi Discord thiếu cấu hình hoặc tạm mất mạng.
            await reminder_task

    finally:
        if voice_trigger_engine is not None:
            voice_trigger_engine.stop()
        await stop_ipc_server()
        await stop_discord_bot()

        if not discord_task.done():
            discord_task.cancel()
        if not reminder_task.done():
            reminder_task.cancel()
        if not gmail_task.done():
            gmail_task.cancel()
        if voice_task is not None and not voice_task.done():
            voice_task.cancel()

        try:
            await discord_task
        except asyncio.CancelledError:
            pass
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
        try:
            await gmail_task
        except asyncio.CancelledError:
            pass

        await close_mcp_session()
        stop_tts_worker()


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    asyncio.run(main())
