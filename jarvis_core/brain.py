"""Natural-language intent detection without requiring an external AI service."""

import re
from dataclasses import dataclass, field

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
        remember = re.match(r"^(?:ghi nho|nho rang|hay nho)\s+(.+)$", plain)
        if remember:
            content = re.sub(
                r"^(?:ghi nhớ|ghi nho|nhớ rằng|nho rang|hãy nhớ|hay nho)\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip()
            return Intent("memory.remember", 0.99, {"content": content})
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
        if plain in {"xem bo nho", "bo nho", "ban nho gi", "toi da bao ban nho gi"}:
            return Intent("memory.recall", 0.95, {"query": ""})
        if plain in {"tinh trang he thong", "kiem tra he thong", "kiem tra may", "may dang the nao", "system status"}:
            return Intent("system.status", 0.98)
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
