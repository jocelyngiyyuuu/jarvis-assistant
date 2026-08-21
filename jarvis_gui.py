#!/usr/bin/env python3
"""Native GTK 4 control surface for Jarvis on Ubuntu."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from jarvis_core.runtime import JarvisCore
from jarvis_core.help_catalog import HELP_CATEGORIES


BASE_DIR = Path(__file__).resolve().parent
CORE = JarvisCore(BASE_DIR)
IPC_SOCKET_PATH = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / f"jarvis-{os.getuid()}.sock"


CSS = b"""
window, .app-root { background-color: #171419; color: #f2eff2; }
headerbar { background-color: #2c1628; color: #ffffff; border-bottom: 3px solid #e95420; }
.sidebar { background-color: #211d22; border-right: 1px solid #464047; padding: 14px; }
.nav-button { color: #d8d3d8; padding: 12px 14px; border-radius: 7px; border: 1px solid transparent; }
.nav-button:hover { background-color: #312c32; border-color: #4d474e; }
.nav-button:checked { background-color: #3a323a; color: #ffb195; border-color: #554d55; border-left: 4px solid #e95420; font-weight: 700; }
.page { background-color: #171419; padding: 26px 30px 24px; }
.content-scroll { background-color: #29252b; border: 1px solid #4a444b; border-radius: 10px; }
.content-list { background-color: #29252b; color: #f2eff2; }
.page-title { font-size: 27px; font-weight: 700; }
.muted { color: #aaa3aa; }
.card { background-color: #302b31; color: #f7f4f7; border: 1px solid #514a52; border-radius: 10px; padding: 16px; }
.assistant-card { border-left: 4px solid #e95420; }
.zalo-summary-card { background-color: #29262d; border-left: 4px solid #55c2ff; padding: 18px 20px; }
.zalo-summary-card label { color: #f7f5f8; }
.status-good { color: #79d58a; font-weight: 700; }
.danger { color: #ff8d9a; }
.metric { font-size: 24px; font-weight: 700; }
.section-title { font-size: 16px; font-weight: 700; }
.disk-card { background-color: #302b31; border: 1px solid #514a52; border-radius: 10px; padding: 12px; }
.warning { color: #f6c177; font-weight: 700; }
.critical { color: #ff8d9a; font-weight: 700; }
.toolbar { background-color: #211d22; border: 1px solid #464047; border-radius: 10px; padding: 10px; }
.help-panel { background-color: #211d22; border: 1px solid #464047; border-radius: 10px; padding: 10px; }
.quick-command { background-color: #302b31; color: #f2eff2; border: 1px solid #514a52; }
entry { padding: 12px; background-color: #332e34; color: #ffffff; border: 1px solid #5b545c; }
button.suggested-action { background-color: #e95420; color: #ffffff; }
textview { background-color: #302b31; color: #f7f4f7; }
"""


def human_size(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


class JarvisWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Jarvis")
        self.set_default_size(1180, 760)
        self.set_size_request(900, 600)
        self.last_conversation_id = 0
        self.index_rebuild_running = False

        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Jarvis"))
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        root.add_css_class("app-root")
        self.set_child(root)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        sidebar.add_css_class("sidebar")
        sidebar.set_size_request(245, -1)
        root.append(sidebar)

        search = Gtk.SearchEntry(placeholder_text="Tìm chức năng…")
        search.set_margin_bottom(10)
        sidebar.append(search)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        root.append(self.stack)

        pages = [
            ("chat", "Trò chuyện", self._chat_page),
            ("memory", "Bộ nhớ", self._memory_page),
            ("files", "File & ổ đĩa", self._files_page),
            ("system", "Hệ thống", self._system_page),
            ("schedule", "Lịch tác vụ", self._schedule_page),
        ]
        first = None
        self.nav_buttons = []
        for name, label, builder in pages:
            page = builder()
            self.stack.add_named(page, name)
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("nav-button")
            button.set_halign(Gtk.Align.FILL)
            button.connect("toggled", self._navigate, name)
            if first is None:
                first = button
            else:
                button.set_group(first)
            sidebar.append(button)
            self.nav_buttons.append((button, label))

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        sidebar.append(spacer)
        self.service_label = Gtk.Label(label="● JarvisCore sẵn sàng", xalign=0)
        self.service_label.add_css_class("status-good")
        sidebar.append(self.service_label)
        model = Gtk.Label(label=f"AI local: {CORE.local_ai.model}", xalign=0)
        model.add_css_class("muted")
        sidebar.append(model)
        first.set_active(True)
        search.connect("search-changed", self._filter_navigation)
        # Mỗi lần mở GTK là một phiên hiển thị mới. Tin cũ vẫn nằm trong
        # SQLite của Jarvis nhưng không tự đổ lại lên màn hình.
        self.last_conversation_id = CORE.conversation.latest_id()
        GLib.timeout_add(700, self._sync_conversation)
        GLib.idle_add(self._ensure_file_index_background)
        GLib.timeout_add_seconds(600, self._periodic_index_check)

    def _navigate(self, button, name):
        if button.get_active():
            self.stack.set_visible_child_name(name)
            if name == "memory":
                self.refresh_memory()
            elif name == "system":
                self.refresh_system()
            elif name == "files":
                self.refresh_disks()

    def _filter_navigation(self, entry):
        query = entry.get_text().casefold().strip()
        for button, label in self.nav_buttons:
            button.set_visible(not query or query in label.casefold())

    @staticmethod
    def _page(title, subtitle):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.add_css_class("page")
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("page-title")
        box.append(heading)
        description = Gtk.Label(label=subtitle, xalign=0)
        description.add_css_class("muted")
        box.append(description)
        return box

    @staticmethod
    def _card(text=""):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        if text:
            label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(92)
            card.append(label)
        return card

    @staticmethod
    def _run_background(work, done):
        def runner():
            try:
                result = work()
            except Exception as error:
                result = f"Không thể hoàn thành: {error}"
            GLib.idle_add(done, result)

        threading.Thread(target=runner, daemon=True).start()

    def _run_legacy_command(self, command):
        request = json.dumps(
            {"source": "gtk", "command": command}, ensure_ascii=False
        ).encode("utf-8") + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(120)
            client.connect(str(IPC_SOCKET_PATH))
            client.sendall(request)
            buffer = b""
            while b"\n" not in buffer:
                chunk = client.recv(65536)
                if not chunk:
                    raise RuntimeError("Jarvis nền đã đóng kết nối.")
                buffer += chunk
        payload = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("response", "Lệnh thất bại."))
        return payload["response"]

    def _sync_conversation(self):
        rows = CORE.conversation.since(self.last_conversation_id, limit=100)
        for row in rows:
            self._append_conversation_event(row)
        return True

    def _append_conversation_event(self, row):
        self.last_conversation_id = max(self.last_conversation_id, row["id"])
        source_names = {"gtk": "Ứng dụng", "discord": "Discord", "terminal": "Terminal"}
        source = source_names.get(row["source"], row["source"])
        if row["role"] == "assistant":
            self._append_chat(f"Jarvis · {source}\n{row['content']}", assistant=True)
        else:
            self._append_chat(f"Bạn · {source}\n{row['content']}")

    def _chat_page(self):
        page = self._page("Trò chuyện với Jarvis", "Hỏi tự nhiên hoặc nhập một lệnh cho Ubuntu.")
        scroll = Gtk.ScrolledWindow()
        self.chat_scroll = scroll
        scroll.add_css_class("content-scroll")
        scroll.set_vexpand(True)
        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.chat_box.set_valign(Gtk.Align.END)
        scroll.set_child(self.chat_box)
        page.append(scroll)
        welcome = self._card("Jarvis sẵn sàng. Các lệnh bộ nhớ, file và hệ thống được xử lý ngay trên máy.")
        welcome.add_css_class("assistant-card")
        self.chat_box.append(welcome)
        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.chat_entry = Gtk.Entry(placeholder_text="Nhập tin nhắn…")
        self.chat_entry.set_hexpand(True)
        self.chat_entry.connect("activate", self._send_chat)
        composer.append(self.chat_entry)
        self.send_button = Gtk.Button(label="Gửi")
        self.send_button.add_css_class("suggested-action")
        self.send_button.connect("clicked", self._send_chat)
        composer.append(self.send_button)
        page.append(composer)
        help_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        help_panel.add_css_class("help-panel")
        help_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        help_title = Gtk.Label(label="Khám phá chức năng", xalign=0)
        help_title.set_hexpand(True)
        help_header.append(help_title)
        whats_new = Gtk.Button(label="✨ Có gì mới?")
        whats_new.connect(
            "clicked", lambda _button: self._send_command_text("có gì mới")
        )
        help_header.append(whats_new)
        help_panel.append(help_header)
        categories = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=6,
            row_spacing=6,
            max_children_per_line=7,
        )
        for key, category in HELP_CATEGORIES.items():
            button = Gtk.Button(label=f"{category['icon']} {category['title']}")
            button.connect("clicked", self._select_help_category, key)
            categories.insert(button, -1)
        help_panel.append(categories)
        self.help_commands = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=6,
            row_spacing=6,
            max_children_per_line=5,
        )
        help_panel.append(self.help_commands)
        page.append(help_panel)
        privacy = Gtk.Label(label=f"{CORE.local_ai.model} chạy local · Nội dung không rời khỏi máy")
        privacy.add_css_class("muted")
        page.append(privacy)
        return page

    def _append_chat(self, text, assistant=False):
        card = self._card(text)
        if assistant:
            card.add_css_class("assistant-card")
            if "TÓM TẮT ZALO CÔNG VIỆC" in text:
                card.add_css_class("zalo-summary-card")
        else:
            card.set_halign(Gtk.Align.END)
        self.chat_box.append(card)
        GLib.idle_add(self._scroll_chat_to_bottom)
        GLib.timeout_add(80, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self):
        adjustment = self.chat_scroll.get_vadjustment()
        adjustment.set_value(max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()))
        return False

    def _send_chat(self, _widget):
        command = self.chat_entry.get_text().strip()
        if not command:
            return
        self.chat_entry.set_text("")
        self._send_command_text(command)

    def _send_command_text(self, command):
        if not command or not self.send_button.get_sensitive():
            return
        self.send_button.set_sensitive(False)

        def work():
            return self._run_legacy_command(command)

        def done(response):
            self.send_button.set_sensitive(True)
            if response.startswith("Không thể hoàn thành:"):
                self._append_chat(response, assistant=True)
            self._sync_conversation()
            return False

        self._run_background(work, done)

    def _select_help_category(self, _button, category_key):
        category = HELP_CATEGORIES[category_key]
        while child := self.help_commands.get_first_child():
            self.help_commands.remove(child)
        for label, command in category["commands"]:
            button = Gtk.Button(label=label)
            button.add_css_class("quick-command")
            button.set_tooltip_text(command)
            button.connect("clicked", self._run_help_command, command)
            self.help_commands.insert(button, -1)
        self._send_command_text(f"help {category_key}")

    def _run_help_command(self, _button, command):
        self._send_command_text(command)

    def _memory_page(self):
        page = self._page("Bộ nhớ", "Thông tin Jarvis đã lưu trên máy.")
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.memory_entry = Gtk.Entry(placeholder_text="Nội dung cần ghi nhớ")
        self.memory_entry.set_hexpand(True)
        controls.append(self.memory_entry)
        add = Gtk.Button(label="Ghi nhớ")
        add.connect("clicked", self._remember)
        controls.append(add)
        page.append(controls)
        self.memory_list = Gtk.ListBox()
        self.memory_list.add_css_class("content-list")
        self.memory_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("content-scroll")
        scroll.set_vexpand(True)
        scroll.set_child(self.memory_list)
        page.append(scroll)
        return page

    def _remember(self, _button):
        content = self.memory_entry.get_text().strip()
        if not content:
            return
        CORE.memory.remember(content)
        self.memory_entry.set_text("")
        self.refresh_memory()

    def refresh_memory(self):
        while child := self.memory_list.get_first_child():
            self.memory_list.remove(child)
        rows = CORE.memory.recall("", limit=50)
        if not rows:
            self.memory_list.append(Gtk.Label(label="Jarvis chưa lưu thông tin nào.", xalign=0))
            return
        for row in rows:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            item.set_margin_top(8)
            item.set_margin_bottom(8)
            item.set_margin_start(12)
            item.set_margin_end(12)
            label = Gtk.Label(
                label=f"#{row['id']}  [{row['category']}]\n{row['content']}",
                xalign=0,
                wrap=True,
                selectable=True,
            )
            label.set_hexpand(True)
            item.append(label)
            copy_button = Gtk.Button(label="Sao chép")
            copy_button.connect("clicked", self._copy_memory, row["content"])
            item.append(copy_button)
            delete_button = Gtk.Button(label="Xóa")
            delete_button.add_css_class("destructive-action")
            delete_button.connect("clicked", self._confirm_delete_memory, row)
            item.append(delete_button)
            self.memory_list.append(item)

    def _copy_memory(self, _button, content):
        from gi.repository import Gdk

        Gdk.Display.get_default().get_clipboard().set(content)

    def _confirm_delete_memory(self, _button, row):
        dialog = Gtk.AlertDialog()
        dialog.set_message(f"Xóa ký ức #{row['id']}?")
        dialog.set_detail(row["content"])
        dialog.set_buttons(["Hủy", "Xóa"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.choose(self, None, self._finish_delete_memory, row["id"])

    def _finish_delete_memory(self, dialog, result, memory_id):
        try:
            choice = dialog.choose_finish(result)
        except GLib.Error:
            return
        if choice != 1:
            return
        CORE.memory.forget_id(memory_id)
        CORE.conversation.add("gtk", "user", f"quên mục {memory_id}")
        CORE.conversation.add("gtk", "assistant", f"🧠 Đã quên mục #{memory_id}.")
        self.refresh_memory()

    def _files_page(self):
        page = self._page(
            "File & ổ đĩa",
            "Tìm kiếm, phân tích dung lượng và nhận diện file quan trọng. Không tự động xóa dữ liệu.",
        )

        disk_heading = Gtk.Label(label="Tổng quan ổ đĩa", xalign=0)
        disk_heading.add_css_class("section-title")
        page.append(disk_heading)
        self.disk_cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page.append(self.disk_cards)

        index_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        index_bar.add_css_class("toolbar")
        self.index_status_label = Gtk.Label(label="Chỉ mục file: đang kiểm tra…", xalign=0)
        self.index_status_label.set_hexpand(True)
        index_bar.append(self.index_status_label)
        rebuild = Gtk.Button(label="Cập nhật chỉ mục")
        rebuild.connect("clicked", self._rebuild_file_index)
        index_bar.append(rebuild)
        page.append(index_bar)

        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_bar.add_css_class("toolbar")
        self.file_search_entry = Gtk.Entry(placeholder_text="Nhập tên file cần tìm…")
        self.file_search_entry.set_hexpand(True)
        self.file_search_entry.connect("activate", self._search_files)
        search_bar.append(self.file_search_entry)
        search_button = Gtk.Button(label="Tìm file")
        search_button.add_css_class("suggested-action")
        search_button.connect("clicked", self._search_files)
        search_bar.append(search_button)
        folder_button = Gtk.Button(label="Tìm thư mục")
        folder_button.connect("clicked", self._search_folders)
        search_bar.append(folder_button)
        page.append(search_bar)

        path_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        path_bar.add_css_class("toolbar")
        self.path_entry = Gtk.Entry(placeholder_text="Đường dẫn, ví dụ: ~/Downloads")
        self.path_entry.set_text(str(Path.home()))
        self.path_entry.set_hexpand(True)
        path_bar.append(self.path_entry)
        directory_button = Gtk.Button(label="Dung lượng thư mục")
        directory_button.connect("clicked", self._directory_usage)
        path_bar.append(directory_button)
        analyze_button = Gtk.Button(label="Phân tích file")
        analyze_button.connect("clicked", self._analyze_file)
        path_bar.append(analyze_button)
        open_button = Gtk.Button(label="Mở đường dẫn")
        open_button.connect("clicked", self._open_path)
        path_bar.append(open_button)
        close_button = Gtk.Button(label="Đóng đường dẫn")
        close_button.connect("clicked", self._close_path)
        path_bar.append(close_button)
        page.append(path_bar)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, callback in (
            ("Ổ đĩa", lambda _b: self.refresh_disks(show_report=True)),
            ("File gần đây", self._recent_files),
            ("File lớn nhất", self._largest_files),
            ("File đang dùng", self._open_files),
            ("Có thể dọn", self._cleanup_files),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            actions.append(button)
        page.append(actions)

        advanced = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, callback in (
            ("Khu vực chiếm chỗ", self._home_hotspots),
            ("File trùng nội dung", self._duplicate_files),
            ("Downloads cũ >30 ngày", self._stale_downloads),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            advanced.append(button)
        page.append(advanced)

        self.file_results = Gtk.ListBox()
        self.file_results.add_css_class("content-list")
        self.file_results.set_selection_mode(Gtk.SelectionMode.NONE)
        self.file_results_scroll = Gtk.ScrolledWindow()
        self.file_results_scroll.add_css_class("content-scroll")
        self.file_results_scroll.set_min_content_height(210)
        self.file_results_scroll.set_child(self.file_results)
        self.file_results_scroll.set_visible(False)
        page.append(self.file_results_scroll)

        self.file_status = Gtk.Label(label="Chọn một thao tác để bắt đầu.", xalign=0, wrap=True, selectable=True)
        self.file_status.add_css_class("card")
        self.file_status.set_valign(Gtk.Align.START)
        self.file_status.set_margin_top(12)
        self.file_status.set_margin_start(12)
        self.file_status.set_margin_end(12)
        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("content-scroll")
        scroll.set_vexpand(True)
        scroll.set_child(self.file_status)
        page.append(scroll)
        return page

    def _set_file_result(self, value):
        self.file_results_scroll.set_visible(False)
        self.file_status.set_text(str(value).replace("**", "").replace("`", ""))
        return False

    def _set_busy(self, message):
        self.file_results_scroll.set_visible(False)
        self.file_status.set_text(message)

    def _ensure_file_index_background(self):
        status = CORE.file_index.status()
        self._update_index_status(status)
        if CORE.file_index.is_stale(max_age=3600):
            self._start_index_rebuild()
        return False

    def _periodic_index_check(self):
        if CORE.file_index.is_stale(max_age=3600):
            self._start_index_rebuild()
        return True

    def _update_index_status(self, status):
        if not status["last_scan"]:
            text = "Chỉ mục file: chưa tạo"
        else:
            scanned = datetime.fromtimestamp(status["last_scan"]).strftime("%H:%M %d/%m")
            suffix = " · quét một phần" if status["incomplete"] else ""
            text = f"Chỉ mục: {status['count']:,} mục · {scanned}{suffix}"
        self.index_status_label.set_text(text)

    def _rebuild_file_index(self, _button):
        self._start_index_rebuild()

    def _start_index_rebuild(self):
        if self.index_rebuild_running:
            return
        self.index_rebuild_running = True
        self.index_status_label.set_text("Chỉ mục: đang quét nền…")

        def progress(count):
            GLib.idle_add(
                self.index_status_label.set_text,
                f"Chỉ mục: đang quét nền · {count:,} mục",
            )

        self._run_background(
            lambda: CORE.file_index.rebuild(progress=progress),
            self._finish_index_rebuild,
        )

    def _finish_index_rebuild(self, status):
        self.index_rebuild_running = False
        if isinstance(status, str):
            self.index_status_label.set_text(status)
        else:
            self._update_index_status(status)
        return False

    def _show_index_results(self, title, items):
        if isinstance(items, str):
            self._set_file_result(items)
            return False
        while child := self.file_results.get_first_child():
            self.file_results.remove(child)
        self.file_status.set_text(
            f"{title}: {len(items)} kết quả. Chỉ nút bạn bấm mới thực hiện thao tác."
        )
        if not items:
            self.file_results_scroll.set_visible(False)
            return False
        for item in items:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            for side in ("top", "bottom", "start", "end"):
                getattr(row, f"set_margin_{side}")(7 if side in {"top", "bottom"} else 10)
            kind = "Thư mục" if item.get("type") == "folder" else human_size(item.get("size", 0))
            modified = datetime.fromtimestamp(item.get("modified", 0)).strftime("%d/%m/%Y %H:%M")
            label = Gtk.Label(
                label=f"{item['name']}\n{item['path']}\n{kind} · sửa {modified}",
                xalign=0,
                wrap=True,
                selectable=True,
            )
            label.set_hexpand(True)
            row.append(label)
            for button_label, callback in (
                ("Mở", self._open_index_item),
                ("Sao chép", self._copy_index_path),
                ("Phân tích", self._analyze_index_item),
            ):
                button = Gtk.Button(label=button_label)
                button.connect("clicked", callback, item)
                row.append(button)
            self.file_results.append(row)
        self.file_results_scroll.set_visible(True)
        return False

    def _open_index_item(self, button, item):
        self.path_entry.set_text(str(item["path"]))
        self._open_path(button)

    def _copy_index_path(self, _button, item):
        from gi.repository import Gdk

        Gdk.Display.get_default().get_clipboard().set(str(item["path"]))
        self.file_status.set_text(f"Đã sao chép đường dẫn: {item['path']}")

    def _analyze_index_item(self, button, item):
        self.path_entry.set_text(str(item["path"]))
        if item.get("type") == "folder":
            self._directory_usage(button)
        else:
            self._analyze_file(button)

    @staticmethod
    def _interactive_items(items):
        results = []
        for item in items:
            path = Path(item["path"])
            try:
                stat = path.stat()
            except OSError:
                continue
            results.append(
                {
                    **item,
                    "path": path,
                    "name": item.get("name", path.name),
                    "type": item.get("type", "folder" if path.is_dir() else "file"),
                    "size": item.get("size", stat.st_size if path.is_file() else 0),
                    "modified": item.get("modified", stat.st_mtime),
                }
            )
        return results

    def refresh_disks(self, show_report=False):
        if show_report:
            self._set_busy("Đang đọc dung lượng các ổ đĩa…")

        def done(disks):
            while child := self.disk_cards.get_first_child():
                self.disk_cards.remove(child)
            for disk in disks[:4]:
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                card.add_css_class("disk-card")
                card.set_hexpand(True)
                title = Gtk.Label(label=f"{disk['mount']}  ·  {disk['filesystem']}", xalign=0)
                title.add_css_class("section-title")
                card.append(title)
                usage = Gtk.Label(
                    label=f"{disk['used_text']} / {disk['total_text']}  ({disk['percent']:.1f}%)",
                    xalign=0,
                )
                if disk["level"] == "warning":
                    usage.add_css_class("warning")
                elif disk["level"] == "critical":
                    usage.add_css_class("critical")
                card.append(usage)
                progress = Gtk.ProgressBar(fraction=min(1.0, disk["percent"] / 100))
                progress.set_show_text(True)
                progress.set_text(f"Còn {disk['free_text']}")
                card.append(progress)
                self.disk_cards.append(card)
            if show_report:
                self._set_file_result(CORE._format_disk_usage(disks))
            return False

        self._run_background(CORE.files.disk_usage, done)

    def _search_files(self, _button):
        query = self.file_search_entry.get_text().strip()
        if not query:
            self._set_file_result("Hãy nhập tên file cần tìm.")
            return
        self._set_busy(f"Đang tìm “{query}” trong thư mục Home…")
        def work():
            items = CORE.file_index.search(query, kind="file", limit=50)
            if not items and CORE.file_index.status()["count"] == 0:
                items = CORE.files.search(query, item_type="file", limit=30)
            return self._interactive_items(items)

        self._run_background(work, lambda items: self._show_index_results(f"FILE: {query}", items))

    def _search_folders(self, _button):
        query = self.file_search_entry.get_text().strip()
        if not query:
            self._set_file_result("Hãy nhập tên thư mục cần tìm.")
            return
        self._set_busy(f"Đang tìm thư mục “{query}” trong Home…")
        def work():
            items = CORE.file_index.search(query, kind="folder", limit=50)
            if not items and CORE.file_index.status()["count"] == 0:
                items = CORE.files.search(query, item_type="folder", limit=30)
            return self._interactive_items(items)

        self._run_background(work, lambda items: self._show_index_results(f"THƯ MỤC: {query}", items))

    def _directory_usage(self, _button):
        path = self.path_entry.get_text().strip() or str(Path.home())
        self._set_busy(f"Đang tính dung lượng trong {path}; không đi theo symlink…")
        self._run_background(
            lambda: CORE._format_directory_usage(
                CORE.files.directory_usage(path, limit=25)
            ),
            self._set_file_result,
        )

    def _analyze_file(self, _button):
        path = self.path_entry.get_text().strip()
        if not path:
            self._set_file_result("Hãy nhập đường dẫn file cần phân tích.")
            return
        self._set_busy(f"Đang phân tích {path}…")
        self._run_background(
            lambda: CORE._format_inventory(
                "PHÂN TÍCH FILE",
                [item] if (item := CORE.files.analyze(path)) else [],
            ),
            self._set_file_result,
        )

    def _recent_files(self, _button):
        self._set_busy("Đang đọc danh sách file gần đây…")
        self._run_background(
            lambda: self._interactive_items(CORE.file_index.recent(50)),
            lambda items: self._show_index_results("FILE GẦN ĐÂY", items),
        )

    def _largest_files(self, _button):
        self._set_busy("Đang đọc file lớn từ chỉ mục…")

        def work():
            items = CORE.file_index.largest(50)
            if not items:
                items = CORE.files.largest(25)
            return self._interactive_items(items)

        self._run_background(
            work, lambda items: self._show_index_results("FILE LỚN NHẤT", items)
        )

    def _cleanup_files(self, _button):
        self._set_busy("Đang phân tích; không có file nào bị xóa…")

        def work():
            candidates = CORE.files.cleanup_candidates(20)
            if candidates:
                return CORE._format_inventory(
                    "ỨNG VIÊN DỌN DẸP (CHƯA XÓA)", candidates
                )
            cache = Path.home() / ".cache"
            report = CORE.files.directory_usage(
                cache, limit=20, max_files=80000, time_budget=12
            )
            if report and report["items"]:
                text = CORE._format_directory_usage(report)
                return (
                    "🧹 KHÔNG CÓ FILE ĐƠN LẺ ĐỦ AN TOÀN ĐỂ ĐỀ XUẤT\n\n"
                    "Các khu vực cache lớn để bạn xem xét:\n\n" + text
                )
            return (
                "✅ Không phát hiện ứng viên dọn dẹp đáng kể. "
                "Jarvis chưa thay đổi hoặc xóa dữ liệu."
            )

        self._run_background(
            work,
            self._set_file_result,
        )

    def _open_files(self, _button):
        self._set_busy("Đang kiểm tra các file được tiến trình sử dụng…")
        self._run_background(
            lambda: CORE._format_inventory(
                "FILE ĐANG ĐƯỢC SỬ DỤNG", CORE.files.open_files(20)
            ),
            self._set_file_result,
        )

    def _home_hotspots(self, _button):
        self.path_entry.set_text(str(Path.home()))
        self._directory_usage(_button)

    def _duplicate_files(self, _button):
        self._set_busy("Đang so sánh kích thước và SHA-256; không xóa file…")
        self._run_background(
            lambda: CORE._format_duplicates(CORE.files.duplicate_files(15)),
            self._set_file_result,
        )

    def _stale_downloads(self, _button):
        downloads = Path.home() / "Downloads"
        self._set_busy("Đang tìm file trên 1 MiB, cũ hơn 30 ngày trong Downloads…")
        self._run_background(
            lambda: CORE._format_inventory(
                "DOWNLOADS CŨ >30 NGÀY (CHƯA XÓA)",
                CORE.files.stale_files(downloads, days=30, limit=25),
            ),
            self._set_file_result,
        )

    def _open_path(self, _button):
        path = Path(self.path_entry.get_text().strip()).expanduser()
        if not path.exists():
            self._set_file_result(f"Không tìm thấy đường dẫn: {path}")
            return
        self._send_command_text(f"mở đường dẫn {path}")

    def _close_path(self, _button):
        path = Path(self.path_entry.get_text().strip()).expanduser()
        self._send_command_text(f"tắt đường dẫn {path}")

    def _system_page(self):
        page = self._page("Hệ thống", "CPU, RAM, ổ đĩa và các dịch vụ của Jarvis.")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.system_metrics = {}
        for key, title in (("cpu", "CPU"), ("ram", "RAM"), ("disk", "Ổ hệ thống"), ("uptime", "Uptime")):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            card.add_css_class("card")
            card.set_hexpand(True)
            card.append(Gtk.Label(label=title, xalign=0))
            value = Gtk.Label(label="—", xalign=0)
            value.add_css_class("metric")
            card.append(value)
            progress = None
            if key != "uptime":
                progress = Gtk.ProgressBar()
                card.append(progress)
            self.system_metrics[key] = (value, progress)
            top.append(card)
        page.append(top)

        service_heading = Gtk.Label(label="Dịch vụ Jarvis", xalign=0)
        service_heading.add_css_class("section-title")
        page.append(service_heading)
        self.service_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        self.service_grid.add_css_class("card")
        page.append(self.service_grid)

        security_heading = Gtk.Label(label="Bảo mật điều khiển Discord", xalign=0)
        security_heading.add_css_class("section-title")
        page.append(security_heading)
        security_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        security_card.add_css_class("card")
        settings = CORE.security.settings()
        remote_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        remote_text = Gtk.Label(
            label="Cho phép điều khiển từ Discord\nKhi tắt, chỉ help và trạng thái hệ thống hoạt động.",
            xalign=0,
            wrap=True,
        )
        remote_text.set_hexpand(True)
        remote_row.append(remote_text)
        self.discord_enabled_switch = Gtk.Switch(active=settings["discord_enabled"])
        self.discord_enabled_switch.connect("notify::active", self._toggle_discord_control)
        remote_row.append(self.discord_enabled_switch)
        security_card.append(remote_row)
        dangerous_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dangerous_text = Gtk.Label(
            label="Cho phép lệnh nguy hiểm từ xa\nSleep/shutdown vẫn cần nút xác nhận 60 giây.",
            xalign=0,
            wrap=True,
        )
        dangerous_text.set_hexpand(True)
        dangerous_row.append(dangerous_text)
        self.dangerous_enabled_switch = Gtk.Switch(active=settings["dangerous_enabled"])
        self.dangerous_enabled_switch.connect("notify::active", self._toggle_dangerous_control)
        dangerous_row.append(self.dangerous_enabled_switch)
        security_card.append(dangerous_row)
        page.append(security_card)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button(label="Làm mới trạng thái")
        refresh.add_css_class("suggested-action")
        refresh.connect("clicked", lambda _b: self.refresh_system())
        actions.append(refresh)
        open_monitor = Gtk.Button(label="Mở System Monitor")
        open_monitor.connect("clicked", self._open_system_monitor)
        actions.append(open_monitor)
        close_monitor = Gtk.Button(label="Đóng System Monitor")
        close_monitor.connect(
            "clicked", lambda _button: self._send_command_text("tắt system monitor")
        )
        actions.append(close_monitor)
        page.append(actions)

        self.system_status = Gtk.Label(
            label="Đang đọc trạng thái…", xalign=0, wrap=True, selectable=True
        )
        self.system_status.add_css_class("card")
        page.append(self.system_status)
        return page

    def refresh_system(self):
        self.system_status.set_text("Đang cập nhật trạng thái hệ thống…")

        def work():
            snapshot = CORE.monitor.snapshot()
            services = {}
            for name in ("jarvis.service", "ollama.service"):
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                services[name] = result.stdout.strip() or "không cài"
            ollama_check = subprocess.run(
                ["ollama", "list"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            if ollama_check.returncode == 0:
                services["ollama.service"] = "active"
            services["IPC GTK"] = "active" if IPC_SOCKET_PATH.exists() else "inactive"
            services["Discord"] = services["jarvis.service"]
            return snapshot, services

        def done(result):
            if isinstance(result, str):
                self.system_status.set_text(result)
                return False
            snapshot, services = result
            memory_percent = (
                snapshot["memory_used"] / snapshot["memory_total"] * 100
                if snapshot["memory_total"] else 0
            )
            disk_percent = (
                snapshot["disk_used"] / snapshot["disk_total"] * 100
                if snapshot["disk_total"] else 0
            )
            cpu_percent = min(100.0, snapshot["load"][0] / snapshot["cpu_count"] * 100)
            uptime = snapshot["uptime"] or 0
            values = {
                "cpu": (f"{cpu_percent:.0f}%", cpu_percent),
                "ram": (f"{human_size(snapshot['memory_used'])} · {memory_percent:.0f}%", memory_percent),
                "disk": (f"{human_size(snapshot['disk_used'])} · {disk_percent:.0f}%", disk_percent),
                "uptime": (f"{uptime / 3600:.1f} giờ", None),
            }
            for key, (text, percent) in values.items():
                label, progress = self.system_metrics[key]
                label.set_text(text)
                if progress is not None:
                    progress.set_fraction(min(1.0, percent / 100))

            while child := self.service_grid.get_first_child():
                self.service_grid.remove(child)
            service_rows = [
                ("Jarvis nền", services["jarvis.service"]),
                ("Discord", services["Discord"]),
                ("Ollama", services["ollama.service"]),
                ("IPC giao diện", services["IPC GTK"]),
                ("Model AI", CORE.local_ai.model),
            ]
            for row, (name, state) in enumerate(service_rows):
                name_label = Gtk.Label(label=name, xalign=0)
                state_label = Gtk.Label(label=state, xalign=0)
                if state == "active" or name == "Model AI":
                    state_label.add_css_class("status-good")
                else:
                    state_label.add_css_class("warning")
                self.service_grid.attach(name_label, 0, row, 1, 1)
                self.service_grid.attach(state_label, 1, row, 1, 1)
            detail = CORE.monitor.format(snapshot).replace("**", "")
            self.system_status.set_text(detail)
            return False

        self._run_background(work, done)

    def _open_system_monitor(self, _button):
        self._send_command_text("mở system monitor")

    def _toggle_discord_control(self, switch, _parameter):
        enabled = switch.get_active()
        CORE.security.set_discord_enabled(enabled)
        CORE.security.audit(
            "gtk", "discord remote control", "settings",
            "enabled" if enabled else "disabled",
        )
        self.system_status.set_text(
            "Điều khiển Discord đã bật." if enabled
            else "Điều khiển Discord đã khóa; help và trạng thái hệ thống vẫn hoạt động."
        )

    def _toggle_dangerous_control(self, switch, _parameter):
        enabled = switch.get_active()
        CORE.security.set_dangerous_enabled(enabled)
        CORE.security.audit(
            "gtk", "dangerous remote commands", "settings",
            "enabled" if enabled else "disabled",
        )
        self.system_status.set_text(
            "Lệnh nguy hiểm từ xa đã bật và luôn yêu cầu xác nhận Discord."
            if enabled else "Lệnh nguy hiểm từ xa đã bị khóa."
        )

    def _schedule_page(self):
        page = self._page("Lịch tác vụ", "Theo dõi lịch nguồn điện mà không làm gián đoạn Discord.")
        notice = self._card(
            "Lịch sleep hiện đang thuộc tiến trình Jarvis chạy nền. Phiên bản đầu của giao diện chỉ hiển thị nguyên tắc an toàn; "
            "hãy tiếp tục đặt hoặc hủy lịch bằng Discord để tránh tạo hai bộ hẹn giờ độc lập."
        )
        notice.add_css_class("assistant-card")
        page.append(notice)
        safe = Gtk.Label(
            label="An toàn: giao diện không tự lưu mật khẩu, không tự suspend và không tạo lịch trùng.",
            xalign=0,
            wrap=True,
        )
        safe.add_css_class("muted")
        page.append(safe)
        return page


class JarvisApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.jarvis.Assistant")

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_style_manager_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    @staticmethod
    def get_style_manager_display():
        from gi.repository import Gdk

        return Gdk.Display.get_default()

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = JarvisWindow(self)
        window.present()


if __name__ == "__main__":
    raise SystemExit(JarvisApplication().run())
