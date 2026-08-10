#!/usr/bin/env python3

import subprocess
from pathlib import Path


# =========================
# MỞ YOUTUBE
# =========================
def open_youtube(account=None):

    # Tài khoản cá nhân
    if account == "personal":
        profile = "Default"

        print("Jarvis: Đang mở YouTube bằng tài khoản cá nhân...")

    # Tài khoản học / ChatGPT
    elif account == "study":
        profile = "Profile 1"

        print("Jarvis: Đang mở YouTube bằng tài khoản học, ChatGPT...")

    # Nếu người dùng chỉ nói "mở youtube"
    else:
        print("\nJarvis: Bạn muốn dùng tài khoản nào?")
        print("1. cá nhân")
        print("2. học, chat gpt")

        choice = input("Chọn: ").strip()

        if choice == "1":
            profile = "Default"

        elif choice == "2":
            profile = "Profile 1"

        else:
            print("Jarvis: Lựa chọn không hợp lệ.")
            return

    subprocess.Popen([
        "google-chrome",
        f"--profile-directory={profile}",
        "https://www.youtube.com"
    ])


# =========================
# MỞ VS CODE
# =========================
def open_vscode():

    print("Jarvis: Đang mở VS Code...")

    subprocess.Popen(
        ["code"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


# =========================
# MỞ DOWNLOADS
# =========================
def open_downloads():

    downloads = Path.home() / "Downloads"

    print("Jarvis: Đang mở thư mục Downloads...")

    subprocess.Popen([
        "xdg-open",
        str(downloads)
    ])


# =========================
# XỬ LÝ LỆNH
# =========================
def process_command(command):

    command = command.strip().lower()

    # -------------------------
    # YOUTUBE
    # -------------------------
    if "youtube" in command:

        # Cá nhân
        if (
            "cá nhân" in command
            or "ca nhan" in command
            or "personal" in command
        ):
            open_youtube("personal")

        # Học / ChatGPT
        elif (
            "học" in command
            or "hoc" in command
            or "chatgpt" in command
            or "chat gpt" in command
        ):
            open_youtube("study")

        # Chỉ nói mở YouTube
        else:
            open_youtube()

    # -------------------------
    # VS CODE
    # -------------------------
    elif (
        "vscode" in command
        or "vs code" in command
        or "visual studio code" in command
    ):
        open_vscode()

    # -------------------------
    # DOWNLOADS
    # -------------------------
    elif (
        "downloads" in command
        or "download" in command
        or "tải xuống" in command
        or "tai xuong" in command
    ):
        open_downloads()

    # -------------------------
    # THOÁT
    # -------------------------
    elif command in [
        "thoát",
        "thoat",
        "exit",
        "quit",
        "tạm biệt",
        "tam biet"
    ]:
        print("Jarvis: Tạm biệt.")
        return False

    # -------------------------
    # KHÔNG HIỂU
    # -------------------------
    else:
        print("Jarvis: Tôi chưa hiểu lệnh này.")

    return True


# =========================
# MAIN
# =========================
def main():

    print("=" * 45)
    print("Jarvis v0.6")
    print("=" * 45)

    print("\nJarvis: Tôi đã sẵn sàng.")

    print("\nVí dụ:")
    print("- mở youtube")
    print("- mở youtube bằng tài khoản cá nhân")
    print("- mở youtube học")
    print("- mở vscode")
    print("- mở downloads")
    print("- thoát")

    print()

    while True:

        try:
            command = input("Bạn: ")

            if not command.strip():
                continue

            if not process_command(command):
                break

        except KeyboardInterrupt:
            print("\nJarvis: Tạm biệt.")
            break

        except EOFError:
            print("\nJarvis: Tạm biệt.")
            break

        except Exception as error:
            print(f"Jarvis: Có lỗi xảy ra: {error}")


# =========================
# CHẠY CHƯƠNG TRÌNH
# =========================
if __name__ == "__main__":
    main()
