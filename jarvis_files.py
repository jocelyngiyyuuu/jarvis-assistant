#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

from jarvis_core.window_manager import FileWindowManager


# ============================================================
# CẤU HÌNH
# ============================================================

# Jarvis chỉ tìm kiếm trong /home
SEARCH_ROOT = Path("/home")

# Số kết quả tối đa hiển thị
MAX_RESULTS = 20
WINDOWS = FileWindowManager()

# Những thư mục không cần quét
SKIP_DIRS = {
    ".cache",
    ".git",
    "__pycache__",
    "node_modules",
    ".npm",
    ".cargo",
    ".rustup",
    ".local/share/Trash",
}


# ============================================================
# KIỂM TRA THƯ MỤC CÓ NÊN BỎ QUA KHÔNG
# ============================================================

def should_skip_directory(path):
    path = Path(path)

    # Kiểm tra từng phần của đường dẫn
    for part in path.parts:
        if part in SKIP_DIRS:
            return True

    return False


# ============================================================
# TÌM FILE + THƯ MỤC
# ============================================================

def search_paths(keyword, max_results=MAX_RESULTS):
    keyword = keyword.strip().lower()

    if not keyword:
        return []

    results = []

    print()
    print(f"Jarvis: Đang tìm '{keyword}' trong {SEARCH_ROOT} ...")
    print()

    for root, dirs, files in os.walk(
        SEARCH_ROOT,
        topdown=True,
        followlinks=False,
        onerror=lambda error: None,
    ):

        root_path = Path(root)

        # Loại bỏ thư mục không cần thiết
        dirs[:] = [
            directory
            for directory in dirs
            if not should_skip_directory(root_path / directory)
        ]

        # ----------------------------------------------------
        # TÌM THƯ MỤC
        # ----------------------------------------------------

        for directory in dirs:

            if keyword in directory.lower():

                full_path = root_path / directory

                results.append(
                    {
                        "path": full_path,
                        "type": "folder",
                    }
                )

                if len(results) >= max_results:
                    return results

        # ----------------------------------------------------
        # TÌM FILE
        # ----------------------------------------------------

        for filename in files:

            if keyword in filename.lower():

                full_path = root_path / filename

                results.append(
                    {
                        "path": full_path,
                        "type": "file",
                    }
                )

                if len(results) >= max_results:
                    return results

    return results


# ============================================================
# MỞ FILE / THƯ MỤC
# ============================================================

def open_path(path):
    path = Path(path)

    if not path.exists():
        print("Jarvis: File hoặc thư mục không còn tồn tại.")
        return False

    try:
        previous_ids = WINDOWS.snapshot_ids()
        if previous_ids is None:
            print("Jarvis: Không thể xác minh danh sách cửa sổ; chưa mở đường dẫn.")
            return False
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        tracked = WINDOWS.track_opened_path(path, previous_ids)
        if not tracked:
            WINDOWS.close_new_window(previous_ids, file_manager_only=True)
            print("Jarvis: Không xác minh được cửa sổ mới; đã thử hoàn tác.")
            return False

        print()
        print(f"Jarvis: Đã mở:")
        print(path)
        print()

        return True

    except Exception as error:

        print()
        print("Jarvis: Không thể mở file.")
        print(f"Lỗi: {error}")
        print()

        return False


# ============================================================
# HIỂN THỊ KẾT QUẢ
# ============================================================

def show_results(results):
    print()

    if not results:
        print("Jarvis: Không tìm thấy kết quả.")
        print()
        return

    print(f"Jarvis: Tìm thấy {len(results)} kết quả:")
    print()

    for index, result in enumerate(results, start=1):

        path = result["path"]
        item_type = result["type"]

        if item_type == "folder":
            icon = "[THƯ MỤC]"
        else:
            icon = "[FILE]"

        print(f"{index}. {icon}")
        print(f"   {path}")
        print()


# ============================================================
# TÌM VÀ MỞ
# ============================================================

def find_and_open(keyword):
    results = search_paths(keyword)

    if not results:

        print()
        print(f"Jarvis: Không tìm thấy '{keyword}'.")
        print()

        return

    # Nếu chỉ có 1 kết quả
    if len(results) == 1:

        result = results[0]

        print()
        print("Jarvis: Tìm thấy:")
        print(result["path"])
        print()

        open_path(result["path"])

        return

    show_results(results)

    while True:

        choice = input(
            "Jarvis: Chọn số cần mở (0 để hủy): "
        ).strip()

        if choice == "0":

            print()
            print("Jarvis: Đã hủy.")
            print()

            return

        if not choice.isdigit():

            print("Jarvis: Hãy nhập một con số.")
            continue

        number = int(choice)

        if 1 <= number <= len(results):

            selected = results[number - 1]

            open_path(selected["path"])

            return

        print("Jarvis: Số bạn chọn không hợp lệ.")


# ============================================================
# CHỈ TÌM - KHÔNG MỞ
# ============================================================

def find_only(keyword):
    results = search_paths(keyword)

    show_results(results)


# ============================================================
# MỞ ĐƯỜNG DẪN TRỰC TIẾP
# ============================================================

def open_direct_path(path_text):
    path_text = path_text.strip()

    # Hỗ trợ ~/Downloads
    path = Path(path_text).expanduser()

    if not path.exists():

        print()
        print("Jarvis: Đường dẫn không tồn tại:")
        print(path)
        print()

        return

    # Giới hạn trong /home
    try:

        resolved_path = path.resolve()
        home_root = SEARCH_ROOT.resolve()

        if home_root not in resolved_path.parents and resolved_path != home_root:

            print()
            print("Jarvis: Không cho phép mở ngoài /home.")
            print()

            return

    except Exception:

        print("Jarvis: Không thể kiểm tra đường dẫn.")
        return

    open_path(path)


# ============================================================
# XỬ LÝ LỆNH
# ============================================================

def handle_command(command):
    command = command.strip()

    command_lower = command.lower()

    for prefix in (
        "tắt đường dẫn ", "tat duong dan ", "đóng đường dẫn ",
        "dong duong dan ", "thoát đường dẫn ", "thoat duong dan ",
    ):
        if command_lower.startswith(prefix):
            _success, detail = WINDOWS.close_path(command[len(prefix):].strip())
            print(f"Jarvis: {detail}")
            return True

    close_last = {
        "tắt file": "file", "đóng file": "file", "thoát file": "file",
        "tắt thư mục": "folder", "đóng thư mục": "folder",
        "thoát thư mục": "folder",
    }
    if command_lower in close_last:
        _success, detail = WINDOWS.close_last(close_last[command_lower])
        print(f"Jarvis: {detail}")
        return True

    # --------------------------------------------------------
    # THOÁT
    # --------------------------------------------------------

    if command_lower in {
        "thoát",
        "thoat",
        "exit",
        "quit",
    }:

        print()
        print("Jarvis: Đã thoát.")
        print()

        return False

    # --------------------------------------------------------
    # MỞ ĐƯỜNG DẪN
    # VD:
    # mở đường dẫn ~/Downloads
    # --------------------------------------------------------

    prefixes = [
        "mở đường dẫn ",
        "mo duong dan ",
    ]

    for prefix in prefixes:

        if command_lower.startswith(prefix):

            value = command[len(prefix):].strip()

            open_direct_path(value)

            return True

    # --------------------------------------------------------
    # TÌM FILE
    # --------------------------------------------------------

    prefixes = [
        "tìm file ",
        "tim file ",
    ]

    for prefix in prefixes:

        if command_lower.startswith(prefix):

            keyword = command[len(prefix):].strip()

            find_only(keyword)

            return True

    # --------------------------------------------------------
    # TÌM THƯ MỤC
    # --------------------------------------------------------

    prefixes = [
        "tìm thư mục ",
        "tim thu muc ",
    ]

    for prefix in prefixes:

        if command_lower.startswith(prefix):

            keyword = command[len(prefix):].strip()

            find_only(keyword)

            return True

    # --------------------------------------------------------
    # MỞ FILE
    # --------------------------------------------------------

    prefixes = [
        "mở file ",
        "mo file ",
    ]

    for prefix in prefixes:

        if command_lower.startswith(prefix):

            keyword = command[len(prefix):].strip()

            find_and_open(keyword)

            return True

    # --------------------------------------------------------
    # MỞ THƯ MỤC
    # --------------------------------------------------------

    prefixes = [
        "mở thư mục ",
        "mo thu muc ",
    ]

    for prefix in prefixes:

        if command_lower.startswith(prefix):

            keyword = command[len(prefix):].strip()

            find_and_open(keyword)

            return True

    # --------------------------------------------------------
    # MỞ ...
    #
    # Ví dụ:
    # mở report
    # mở downloads
    # --------------------------------------------------------

    prefixes = [
        "mở ",
        "mo ",
    ]

    for prefix in prefixes:

        if command_lower.startswith(prefix):

            keyword = command[len(prefix):].strip()

            find_and_open(keyword)

            return True

    # --------------------------------------------------------
    # KHÔNG HIỂU LỆNH
    # --------------------------------------------------------

    print()
    print("Jarvis: Tôi chưa hiểu lệnh đó.")
    print()

    print("Các lệnh hiện có:")
    print("  mở file <tên>")
    print("  tìm file <tên>")
    print("  mở thư mục <tên>")
    print("  tìm thư mục <tên>")
    print("  mở <tên>")
    print("  mở đường dẫn <đường dẫn>")
    print("  thoát")
    print()

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("JARVIS FILE SYSTEM")
    print("=" * 60)

    print()
    print("Jarvis: Hệ thống quản lý file đã hoạt động.")
    print(f"Jarvis: Phạm vi truy cập: {SEARCH_ROOT}")
    print()

    print("Ví dụ:")
    print("  mở file jarvis.py")
    print("  tìm file report")
    print("  mở thư mục Downloads")
    print("  tìm thư mục Projects")
    print("  mở Downloads")
    print("  mở đường dẫn ~/Downloads")
    print("  thoát")
    print()

    while True:

        try:

            command = input("Bạn: ")

            keep_running = handle_command(command)

            if not keep_running:
                break

        except KeyboardInterrupt:

            print()
            print()
            print("Jarvis: Đã thoát.")
            break

        except EOFError:

            print()
            print("Jarvis: Đã thoát.")
            break

        except Exception as error:

            print()
            print("Jarvis: Có lỗi xảy ra.")
            print(f"Lỗi: {error}")
            print()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
