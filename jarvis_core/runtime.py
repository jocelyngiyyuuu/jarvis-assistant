"""Composition root for Jarvis core services."""

from pathlib import Path

from .brain import NaturalLanguageRouter
from .conversation import ConversationStore
from .file_manager import SmartFileManager
from .file_index import FileIndex
from .local_ai import LocalAI
from .memory import MemoryStore
from .monitoring import SystemMonitor
from .security import RemoteSecurity


class JarvisCore:
    def __init__(self, base_dir):
        base_dir = Path(base_dir)
        self.brain = NaturalLanguageRouter()
        self.memory = MemoryStore(base_dir / ".jarvis_data" / "memory.sqlite3")
        self.files = SmartFileManager([Path.home()])
        self.file_index = FileIndex(
            base_dir / ".jarvis_data" / "file_index.sqlite3", [Path.home()]
        )
        self.monitor = SystemMonitor(Path.home())
        self.local_ai = LocalAI()
        self.conversation = ConversationStore(
            base_dir / ".jarvis_data" / "conversation.sqlite3"
        )
        self.security = RemoteSecurity(base_dir / ".jarvis_data")

    def handle(self, command):
        intent = self.brain.detect(command)
        if intent.name == "legacy":
            return None
        if intent.name == "memory.remember":
            memory_id, created = self.memory.remember(
                intent.entities["content"],
                category=intent.entities.get("category", "general"),
                expires_in=intent.entities.get("expires_in"),
            )
            if not created:
                return f"🧠 Nội dung này đã tồn tại ở mục #{memory_id}."
            return f"🧠 Đã ghi nhớ (#{memory_id}): {intent.entities['content']}"
        if intent.name == "memory.recall":
            rows = self.memory.recall(
                intent.entities.get("query", ""),
                category=intent.entities.get("category"),
            )
            if not rows:
                return "🧠 Tôi chưa có ký ức phù hợp."
            return "🧠 **BỘ NHỚ JARVIS**\n" + "\n".join(
                f"#{row['id']} [{row['category']}] {row['content']}" for row in rows
            )
        if intent.name == "memory.update":
            updated = self.memory.update(intent.entities["id"], intent.entities["content"])
            if not updated:
                return f"🧠 Không tìm thấy mục #{intent.entities['id']}."
            return f"🧠 Đã cập nhật mục #{intent.entities['id']}."
        if intent.name == "memory.forget_id":
            deleted = self.memory.forget_id(intent.entities["id"])
            if not deleted:
                return f"🧠 Không tìm thấy mục #{intent.entities['id']}."
            return f"🧠 Đã quên mục #{intent.entities['id']}."
        if intent.name == "memory.forget":
            count = self.memory.forget(intent.entities["query"])
            return f"🧠 Đã quên {count} mục phù hợp."
        if intent.name == "system.status":
            return self.monitor.format(self.monitor.snapshot())
        if intent.name == "files.recent":
            results = self.files.recent(intent.entities["limit"])
            return self._format_files("FILE GẦN ĐÂY", results)
        if intent.name == "files.search":
            results = self.files.search(intent.entities["query"], item_type="file")
            return self._format_files(f"TÌM THÔNG MINH: {intent.entities['query']}", results)
        if intent.name == "files.largest":
            return self._format_inventory(
                "FILE LỚN NHẤT", self.files.largest(intent.entities["limit"])
            )
        if intent.name == "files.duplicates":
            return self._format_duplicates(
                self.files.duplicate_files(intent.entities["limit"])
            )
        if intent.name == "files.stale_downloads":
            return self._format_inventory(
                "DOWNLOADS CŨ (CHƯA XÓA)",
                self.files.stale_files(
                    Path.home() / "Downloads",
                    days=intent.entities["days"],
                    limit=intent.entities["limit"],
                ),
            )
        if intent.name == "files.cleanup_candidates":
            return self._format_inventory(
                "ỨNG VIÊN DỌN DẸP (CHƯA XÓA)",
                self.files.cleanup_candidates(intent.entities["limit"]),
            )
        if intent.name == "files.open":
            return self._format_inventory(
                "FILE ĐANG ĐƯỢC SỬ DỤNG", self.files.open_files(intent.entities["limit"])
            )
        if intent.name == "files.analyze":
            item = self.files.analyze(intent.entities["path"])
            return self._format_inventory("PHÂN TÍCH FILE", [item] if item else [])
        if intent.name == "files.disk_usage":
            return self._format_disk_usage(self.files.disk_usage())
        if intent.name == "files.directory_usage":
            report = self.files.directory_usage(
                intent.entities["path"], limit=intent.entities["limit"]
            )
            return self._format_directory_usage(report)
        return None

    @staticmethod
    def _format_files(title, results):
        if not results:
            return f"🔎 {title}: không tìm thấy kết quả."
        lines = [f"🔎 **{title}**", ""]
        for index, item in enumerate(results, 1):
            lines.append(f"{index}. 📄 `{item['path']}`")
        return "\n".join(lines)

    @staticmethod
    def _format_inventory(title, results):
        if not results:
            return (
                f"🧹 {title}: không tìm thấy file riêng lẻ phù hợp trong phạm vi quét.\n\n"
                "Jarvis chưa xóa dữ liệu. Hãy dùng `Khu vực chiếm chỗ` hoặc "
                "`Dung lượng thư mục ~/.cache` để xem thư mục nào đang lớn."
            )
        lines = [f"🧹 **{title}**", ""]
        for index, item in enumerate(results, 1):
            processes = ", ".join(item["open_by"][:3]) or "không phát hiện"
            reasons = "; ".join(item["reasons"][:3])
            lines.extend(
                [
                    f"{index}. `{item['path']}`",
                    f"   • Dung lượng: {item['size_text']}",
                    f"   • Quan trọng: {item['importance']} | Đang dùng: {processes}",
                    f"   • Khuyến nghị: {item['recommendation']}",
                    f"   • Lý do: {reasons}",
                ]
            )
        lines.append("")
        lines.append("ℹ️ Báo cáo chỉ đọc; Jarvis chưa xóa bất kỳ file nào.")
        return "\n".join(lines)

    @staticmethod
    def _format_disk_usage(results):
        if not results:
            return "💽 Không đọc được thông tin ổ đĩa."
        lines = ["💽 **DUNG LƯỢNG Ổ ĐĨA**", ""]
        for item in results:
            icon = "🔴" if item["level"] == "critical" else "🟠" if item["level"] == "warning" else "🟢"
            lines.extend(
                [
                    f"{icon} `{item['mount']}` ({item['device']}, {item['filesystem']})",
                    f"   • Đã dùng: {item['used_text']} / {item['total_text']} ({item['percent']:.1f}%)",
                    f"   • Còn trống: {item['free_text']}",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _format_directory_usage(report):
        if report is None:
            return "💽 Không tìm thấy hoặc không đọc được thư mục."
        lines = [f"💽 **DUNG LƯỢNG TRONG `{report['root']}`**", ""]
        for index, item in enumerate(report["items"], 1):
            lines.append(
                f"{index}. `{item['path']}` — {item['size_text']} ({item['file_count']} file)"
            )
        if report["incomplete"]:
            lines.append("")
            lines.append(
                f"⚠️ Báo cáo một phần sau {report['scanned_files']} file do đạt giới hạn quét."
            )
        lines.append("")
        lines.append("ℹ️ Báo cáo chỉ đọc; không có file hoặc phân vùng nào bị thay đổi.")
        return "\n".join(lines)

    @staticmethod
    def _format_duplicates(groups):
        if not groups:
            return "🧬 Không phát hiện nhóm file trùng trong phạm vi quét có giới hạn."
        lines = ["🧬 **FILE TRÙNG NỘI DUNG (CHƯA XÓA)**", ""]
        for index, group in enumerate(groups, 1):
            lines.append(
                f"{index}. {len(group['paths'])} bản × {group['size_text']} "
                f"— có thể thu hồi tối đa {group['wasted_text']}"
            )
            lines.extend(f"   • `{path}`" for path in group["paths"])
        lines.extend(
            ["", "ℹ️ Jarvis so sánh SHA-256 nhưng chưa xóa file; hãy kiểm tra bản cần giữ."]
        )
        return "\n".join(lines)
