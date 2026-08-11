"""Composition root for Jarvis core services."""

from pathlib import Path

from .brain import NaturalLanguageRouter
from .file_manager import SmartFileManager
from .local_ai import LocalAI
from .memory import MemoryStore
from .monitoring import SystemMonitor


class JarvisCore:
    def __init__(self, base_dir):
        base_dir = Path(base_dir)
        self.brain = NaturalLanguageRouter()
        self.memory = MemoryStore(base_dir / ".jarvis_data" / "memory.sqlite3")
        self.files = SmartFileManager([Path.home()])
        self.monitor = SystemMonitor(Path.home())
        self.local_ai = LocalAI()

    def handle(self, command):
        intent = self.brain.detect(command)
        if intent.name == "legacy":
            return None
        if intent.name == "memory.remember":
            memory_id = self.memory.remember(intent.entities["content"])
            return f"🧠 Đã ghi nhớ (#{memory_id}): {intent.entities['content']}"
        if intent.name == "memory.recall":
            rows = self.memory.recall(intent.entities.get("query", ""))
            if not rows:
                return "🧠 Tôi chưa có ký ức phù hợp."
            return "🧠 **BỘ NHỚ JARVIS**\n" + "\n".join(
                f"{index}. {row['content']}" for index, row in enumerate(rows, 1)
            )
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
        return None

    @staticmethod
    def _format_files(title, results):
        if not results:
            return f"🔎 {title}: không tìm thấy kết quả."
        lines = [f"🔎 **{title}**", ""]
        for index, item in enumerate(results, 1):
            lines.append(f"{index}. 📄 `{item['path']}`")
        return "\n".join(lines)
