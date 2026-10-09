"""Natural-language intent detection without requiring an external AI service."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .text import normalize_text


@dataclass(frozen=True)
class Intent:
    name: str
    confidence: float
    entities: dict = field(default_factory=dict)


class NaturalLanguageRouter:
    """Map flexible Vietnamese phrases to stable internal intents."""

    _POLITE_PREFIX = re.compile(r"^(jarvis[ ,]+)?(hay|vui long|lam on|giup toi|ban co the)\s+")

    def normalize_command(self, command):
        original = str(command).strip()
        plain = normalize_text(original)
        plain = self._POLITE_PREFIX.sub("", plain).strip()
        aliases = {
            "kiem tra may": "tình trạng hệ thống",
            "may dang the nao": "tình trạng hệ thống",
            "kiem tra he thong": "tình trạng hệ thống",
            "toi da bao ban nho gi": "xem bộ nhớ",
            "ban nho gi": "xem bộ nhớ",
        }
        return aliases.get(plain, original if plain == normalize_text(original) else plain)

    def detect(self, command):
        original = str(command).strip()
        plain = normalize_text(original)

        update = re.match(r"^(?:cap nhat|sua)\s+(?:muc\s+)?#?(\d+)\s+(?:thanh|la)\s+(.+)$", plain)
        if update:
            content = re.sub(
                r"^(?:cập nhật|cap nhat|sửa|sua)\s+(?:mục|muc)?\s*#?\d+\s+(?:thành|thanh|là|la)\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            return Intent(
                "memory.update",
                0.99,
                {"id": int(update.group(1)), "content": content},
            )

        forget_id = re.match(r"^(?:quen|xoa)(?:\s+muc)?\s+#?(\d+)$", plain)
        if forget_id:
            return Intent("memory.forget_id", 0.99, {"id": int(forget_id.group(1))})

        temporary = re.match(
            r"^(?:ghi nho|nho rang|hay nho)\s+tam thoi\s+(\d+)\s*"
            r"(giay|phut|gio|ngay)\s*:?\s+(.+)$",
            plain,
        )
        if temporary:
            multipliers = {"giay": 1, "phut": 60, "gio": 3600, "ngay": 86400}
            prefix = re.compile(
                r"^(?:ghi nhớ|ghi nho|nhớ rằng|nho rang|hãy nhớ|hay nho)\s+"
                r"tạm thời\s+\d+\s*(?:giây|giay|phút|phut|giờ|gio|ngày|ngay)\s*:?\s*",
                re.IGNORECASE,
            )
            return Intent(
                "memory.remember",
                0.99,
                {
                    "content": prefix.sub("", original).strip(),
                    "category": "temporary",
                    "expires_in": int(temporary.group(1)) * multipliers[temporary.group(2)],
                },
            )

        remember = re.match(r"^(?:ghi nho|nho rang|hay nho)\s+(.+)$", plain)
        if remember:
            content = re.sub(
                r"^(?:ghi nhớ|ghi nho|nhớ rằng|nho rang|hãy nhớ|hay nho)\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            categories = {
                "so thich": "preference",
                "ho so": "profile",
                "cong viec": "task",
                "su that": "fact",
                "thiet bi": "device",
            }
            category = "general"
            normalized_content = normalize_text(content)
            for label, value in categories.items():
                if normalized_content.startswith(label + ":"):
                    category = value
                    content = content.split(":", 1)[1].strip()
                    break
            return Intent(
                "memory.remember",
                0.99,
                {"content": content, "category": category},
            )
        forget = re.match(r"^(?:quen|xoa bo nho|xoa ghi nho)\s+(.+)$", plain)
        if forget:
            query = re.sub(
                r"^(?:quên|quen|xóa bộ nhớ|xoa bo nho|xóa ghi nhớ|xoa ghi nho)\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            return Intent("memory.forget", 0.97, {"query": query})
        recall = re.match(r"^(?:nho lai|tim trong bo nho)\s*(.*)$", plain)
        if recall:
            return Intent("memory.recall", 0.95, {"query": recall.group(1).strip()})
        category_recall = re.match(r"^xem bo nho\s+(so thich|ho so|cong viec|su that|thiet bi|tam thoi)$", plain)
        if category_recall:
            categories = {
                "so thich": "preference", "ho so": "profile", "cong viec": "task",
                "su that": "fact", "thiet bi": "device", "tam thoi": "temporary",
            }
            return Intent(
                "memory.recall",
                0.97,
                {"query": "", "category": categories[category_recall.group(1)]},
            )
        if plain in {"xem bo nho", "bo nho", "ban nho gi", "toi da bao ban nho gi"}:
            return Intent("memory.recall", 0.95, {"query": ""})
        if plain in {"tinh trang he thong", "kiem tra he thong", "kiem tra may", "may dang the nao", "system status"}:
            return Intent("system.status", 0.98)
        if plain in {"dung luong o dia", "o dia", "disk usage", "kiem tra o dia"}:
            return Intent("files.disk_usage", 0.99)
        directory_usage = re.match(
            r"^(?:dung luong thu muc|thu muc nao chiem dung luong)(?:\s+(.+))?$",
            plain,
        )
        if directory_usage:
            path = directory_usage.group(1) or str(Path.home())
            original_path = re.sub(
                r"^(?:dung lượng thư mục|dung luong thu muc|thư mục nào chiếm dung lượng|thu muc nao chiem dung luong)\s*",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            return Intent(
                "files.directory_usage",
                0.98,
                {"path": original_path or str(Path.home()), "limit": 15},
            )
        if plain in {"file lon nhat", "file nao chiem nhieu dung luong", "quet file lon"}:
            return Intent("files.largest", 0.98, {"limit": 10})
        if plain in {"file trung", "file trung lap", "tim file trung", "file giong nhau"}:
            return Intent("files.duplicates", 0.98, {"limit": 15})
        if plain in {"downloads cu", "file cu trong downloads", "file tai ve cu"}:
            return Intent("files.stale_downloads", 0.98, {"days": 30, "limit": 20})
        if plain in {"quet file co the don", "file co the xoa", "don dep file", "file rac"}:
            return Intent("files.cleanup_candidates", 0.98, {"limit": 15})
        if plain in {"file dang duoc su dung", "file dang chay", "jarvis dang dung file nao"}:
            return Intent("files.open", 0.98, {"limit": 20})
        analyze_file = re.match(r"^(?:phan tich|kiem tra) file\s+(.+)$", plain)
        if analyze_file:
            path = re.sub(
                r"^(?:phân tích|phan tich|kiểm tra|kiem tra) file\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            return Intent("files.analyze", 0.98, {"path": path})
        recent = re.match(r"^(?:file|tep)(?: moi| gan day)(?: nhat)?(?: (\d+))?$", plain)
        if recent:
            return Intent("files.recent", 0.92, {"limit": int(recent.group(1) or 10)})
        smart = re.match(r"^(?:tim thong minh|tim tep thong minh)\s+(.+)$", plain)
        if smart:
            query = re.sub(
                r"^(?:tìm thông minh|tim thong minh|tìm tệp thông minh|tim tep thong minh)\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            return Intent("files.search", 0.90, {"query": query})
        return Intent("legacy", 0.0, {"command": command})
