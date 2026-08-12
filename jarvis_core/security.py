"""Remote-control security policy and audit log for Jarvis."""

import json
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .text import normalize_text


SAFE_WHEN_LOCKED = {
    "help", "tro giup", "kham pha chuc nang", "tinh trang he thong",
    "kiem tra he thong", "system status",
}


def classify_remote_command(command):
    plain = normalize_text(command)
    # Hẹn gửi chỉ tạo lịch; tới hạn mới hiển thị nút xác nhận gửi riêng.
    if re.match(
        r"sau\s+(?:\d+\s*(?:h|p|s|gio|phut|giay)\s*)+"
        r"(?:nua\s+)?(?:hay\s+)?(?:gui|nhan)(?:\s+tin nhan)?(?:\s+zalo)?\s+cho\s+",
        plain,
    ):
        return "normal"
    permanently_forbidden = (
        "xoa vinh vien", "rm -rf", "private key", "api key", "discord token",
        "doc .env", "mo .env", "cat .env", "mat khau",
    )
    if any(pattern in plain for pattern in permanently_forbidden):
        return "forbidden"
    dangerous = (
        "sleep sau", "ngu sau", "suspend", "huy sleep sau", "shutdown",
        "tat may", "khoi dong lai", "reboot", "xoa file", "dua vao thung rac",
    )
    if re.search(
        r"(?:gui|nhan)\s+(?:tin nhan\s+)?(?:zalo\s+)?cho\s+.+\s+(?:voi\s+)?noi dung\s+.+",
        plain,
    ):
        return "confirm"
    if any(pattern in plain for pattern in dangerous):
        return "confirm"
    sensitive = ("xem bo nho", "nho lai", "phan tich file")
    if any(pattern in plain for pattern in sensitive):
        return "sensitive"
    return "normal"


def redact_sensitive(text):
    value = str(text)
    patterns = (
        r"(?i)(discord_token\s*[=:]\s*)\S+",
        r"(?i)(api[_ -]?key\s*[=:]\s*)\S+",
        r"(?i)(password\s*[=:]\s*)\S+",
        r"(?i)(secret\s*[=:]\s*)\S+",
        r"(?i)(token\s*[=:]\s*)[A-Za-z0-9._-]{12,}",
    )
    for pattern in patterns:
        value = re.sub(pattern, r"\1••••••••", value)
    return value


class RemoteSecurity:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = data_dir / "security.json"
        self.database_path = data_dir / "security.sqlite3"
        self._lock = threading.Lock()
        self._requests = defaultdict(deque)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    user_id TEXT,
                    channel_id TEXT,
                    command TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        if not self.settings_path.exists():
            self._write_settings({"discord_enabled": True, "dangerous_enabled": True})

    def _read_settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"discord_enabled": True, "dangerous_enabled": True}

    def _write_settings(self, settings):
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.settings_path)

    def settings(self):
        with self._lock:
            return self._read_settings()

    def set_discord_enabled(self, enabled):
        with self._lock:
            settings = self._read_settings()
            settings["discord_enabled"] = bool(enabled)
            self._write_settings(settings)

    def set_dangerous_enabled(self, enabled):
        with self._lock:
            settings = self._read_settings()
            settings["dangerous_enabled"] = bool(enabled)
            self._write_settings(settings)

    def is_allowed_when_locked(self, command):
        plain = normalize_text(command)
        return plain in SAFE_WHEN_LOCKED or plain.startswith("help ")

    def rate_limit(self, user_id, limit=5, window=10):
        now = time.monotonic()
        bucket = self._requests[str(user_id)]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def audit(self, source, command, risk, outcome, user_id=None, channel_id=None):
        safe_command = redact_sensitive(command)[:1000]
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO security_events(source,user_id,channel_id,command,risk,outcome,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    source, str(user_id or ""), str(channel_id or ""), safe_command,
                    risk, outcome, datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()

    def recent_events(self, limit=30):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM security_events ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_discord_channel_id(self):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT channel_id FROM security_events "
                "WHERE source='discord' AND channel_id<>'' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            return int(row["channel_id"])
        except (TypeError, ValueError):
            return None
