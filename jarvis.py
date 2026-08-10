#!/usr/bin/env python3

import subprocess
from pathlib import Path
from urllib.parse import quote_plus


# ==================================================
# CẤU HÌNH CHROME PROFILE
# ==================================================

PERSONAL_PROFILE = "Default"
STUDY_PROFILE = "Profile 1"


# ==================================================
# CHỌN TÀI KHOẢN CHROME
# ==================================================

def choose_chrome_profile(command=""):
    """
    Nếu câu lệnh đã chỉ rõ tài khoản thì mở luôn.
    Nếu chưa chỉ rõ thì hỏi người dùng.
    """

    command = command.lower()

    # Tài khoản cá nhân
    if any(keyword in command for keyword in [
        "cá nhân",
        "ca nhan",
        "personal",
    ]):
        return PERSONAL_PROFILE

    # Tài khoản học / ChatGPT
    if any(keyword in command for keyword in [
        "tài khoản học",
        "tai khoan hoc",
        "tài khoản chatgpt",
        "tai khoan chatgpt",
        "study",
    ]):
        return STUDY_PROFILE

    # Không chỉ định -> hỏi
    print()
    print("Jarvis: Bạn muốn dùng tài khoản nào?")
    print("1. Cá nhân")
    print("2. Học / ChatGPT")

    choice = input("Chọn: ").strip()

    if choice == "1":
        return PERSONAL_PROFILE

    elif choice == "2":
        return STUDY_PROFILE

    else:
        print("Jarvis: Lựa chọn không hợp lệ.")
        return None


# ==================================================
# MỞ CHROME
# ==================================================

def open_chrome(url, command=""):
    profile = choose_chrome_profile(command)

    if profile is None:
        return False

    try:
        subprocess.Popen(
            [
                "google-chrome",
                f"--profile-directory={profile}",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return True

    except FileNotFoundError:
        print("Jarvis: Không tìm thấy Google Chrome.")
        return False


# ==================================================
# YOUTUBE
# ==================================================

def open_youtube(command):
    print("Jarvis: Đang mở YouTube...")

    open_chrome(
        "https://www.youtube.com",
        command
    )


# ==================================================
# GITHUB
# ==================================================

def open_github(command):
    print("Jarvis: Đang mở GitHub...")

    open_chrome(
        "https://github.com",
        command
    )


# ==================================================
# GOOGLE SEARCH
# ==================================================

def search_google(query, command=""):
    query = query.strip()

    if not query:
        print("Jarvis: Bạn chưa nhập nội dung cần tìm.")
        return

    url = "https://www.google.com/search?q=" + quote_plus(query)

    print(f"Jarvis: Đang tìm kiếm '{query}'...")

    open_chrome(
        url,
        command
    )


# ==================================================
# VS CODE
# ==================================================

def open_vscode():
    print("Jarvis: Đang mở VS Code...")

    try:
        subprocess.Popen(
            ["code"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    except FileNotFoundError:
        print("Jarvis: Không tìm thấy VS Code.")


# ==================================================
# TERMINAL
# ==================================================

def open_terminal():
    print("Jarvis: Đang mở Terminal...")

    terminal_commands = [
        ["gnome-terminal"],
        ["kgx"],
        ["x-terminal-emulator"],
    ]

    for terminal in terminal_commands:

        try:
            subprocess.Popen(
                terminal,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            return

        except FileNotFoundError:
            continue

    print("Jarvis: Không tìm thấy Terminal.")


# ==================================================
# MỞ THƯ MỤC
# ==================================================

def open_folder(folder):
    folder = Path(folder).expanduser()

    if not folder.exists():
        print(f"Jarvis: Không tìm thấy thư mục {folder}")
        return

    print(f"Jarvis: Đang mở {folder}...")

    try:
        subprocess.Popen(
            [
                "xdg-open",
                str(folder),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    except FileNotFoundError:
        print("Jarvis: Không tìm thấy trình quản lý file.")


def open_downloads():
    open_folder(
        Path.home() / "Downloads"
    )


def open_documents():
    open_folder(
        Path.home() / "Documents"
    )


def open_pictures():
    open_folder(
        Path.home() / "Pictures"
    )


def open_home():
    open_folder(
        Path.home()
    )


# ==================================================
# COMMAND ROUTER
# ==================================================

def route_command(command):
    """
    Phân tích câu lệnh và chuyển tới chức năng phù hợp.
    """

    original_command = command.strip()
    command = original_command.lower()

    if not command:
        return True


    # ==================================================
    # THOÁT
    # ==================================================

    exit_commands = [
        "thoát",
        "thoat",
        "exit",
        "quit",
        "bye",
        "tạm biệt",
        "tam biet",
    ]

    if command in exit_commands:
        print("Jarvis: Hẹn gặp lại.")
        return False


    # ==================================================
    # YOUTUBE
    # ==================================================

    if (
        "youtube" in command
        or "ytb" in command
    ):
        open_youtube(original_command)
        return True


    # ==================================================
    # GITHUB
    # ==================================================

    if "github" in command:
        open_github(original_command)
        return True


    # ==================================================
    # VS CODE
    # ==================================================

    if (
        "vscode" in command
        or "vs code" in command
        or "visual studio code" in command
    ):
        open_vscode()
        return True


    # ==================================================
    # TERMINAL
    # ==================================================

    if (
        "terminal" in command
        or command == "mở term"
        or command == "mo term"
    ):
        open_terminal()
        return True


    # ==================================================
    # DOWNLOADS
    # ==================================================

    if (
        "downloads" in command
        or "download" in command
        or "tải xuống" in command
        or "tai xuong" in command
    ):
        open_downloads()
        return True


    # ==================================================
    # DOCUMENTS
    # ==================================================

    if (
        "documents" in command
        or "document" in command
        or "tài liệu" in command
        or "tai lieu" in command
    ):
        open_documents()
        return True


    # ==================================================
    # PICTURES
    # ==================================================

    if (
        "pictures" in command
        or "picture" in command
        or "hình ảnh" in command
        or "hinh anh" in command
        or command == "mở ảnh"
        or command == "mo anh"
    ):
        open_pictures()
        return True


    # ==================================================
    # HOME
    # ==================================================

    if command in [
        "mở home",
        "mo home",
        "mở thư mục home",
        "mo thu muc home",
    ]:
        open_home()
        return True


    # ==================================================
    # GOOGLE SEARCH
    # ==================================================

    search_prefixes = [
        "tìm trên google ",
        "tim tren google ",
        "tìm google ",
        "tim google ",
        "google ",
        "tìm kiếm ",
        "tim kiem ",
        "search ",
    ]

    for prefix in search_prefixes:

        if command.startswith(prefix):

            query = original_command[len(prefix):].strip()

            # Xóa phần tài khoản khỏi từ khóa tìm kiếm
            account_words = [
                "bằng tài khoản cá nhân",
                "bang tai khoan ca nhan",
                "bằng tài khoản học",
                "bang tai khoan hoc",
                "bằng tài khoản chatgpt",
                "bang tai khoan chatgpt",
            ]

            clean_query = query

            for account_word in account_words:
                clean_query = clean_query.replace(
                    account_word,
                    ""
                )

            clean_query = clean_query.strip()

            search_google(
                clean_query,
                original_command
            )

            return True


    # ==================================================
    # KHÔNG HIỂU
    # ==================================================

    print("Jarvis: Tôi chưa hiểu lệnh đó.")

    print()
    print("Bạn có thể thử:")
    print("- mở youtube")
    print("- mở youtube bằng tài khoản cá nhân")
    print("- mở youtube bằng tài khoản học")
    print("- mở github")
    print("- mở vscode")
    print("- mở downloads")
    print("- tìm google MOSFET")
    print("- tìm google MOSFET bằng tài khoản học")

    return True


# ==================================================
# MAIN
# ==================================================

def main():

    print("=" * 50)
    print("                 JARVIS v0.7")
    print("=" * 50)

    print("Jarvis: Xin chào.")
    print("Jarvis: Tôi đã sẵn sàng.")
    print("Jarvis: Gõ 'thoát' để đóng.")

    print()

    running = True

    while running:

        try:

            command = input("Bạn: ")

            running = route_command(command)

        except KeyboardInterrupt:

            print()
            print("Jarvis: Đã thoát.")
            break

        except EOFError:

            print()
            print("Jarvis: Đã thoát.")
            break

        except Exception as error:

            print(
                f"Jarvis: Có lỗi xảy ra: {error}"
            )


if __name__ == "__main__":
    main()
