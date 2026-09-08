"""Ranked local file discovery for Jarvis."""

import os
import hashlib
import heapq
import shutil
import subprocess
import time
from pathlib import Path

from .text import normalize_text


DEFAULT_SKIPS = {
    ".cache", ".git", ".npm", ".cargo", ".rustup", ".venv",
    "__pycache__", "node_modules", "Trash",
}

INVENTORY_SKIPS = {".git", ".Trash", "Trash", "lost+found"}
IMPORTANT_NAMES = {
    ".env", ".gitignore", "pyproject.toml", "requirements.txt", "package.json",
    "package-lock.json", "poetry.lock", "cargo.toml", "cargo.lock",
}
IMPORTANT_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".crt", ".kdbx",
    ".doc", ".docx", ".odt", ".xls", ".xlsx", ".pdf",
}
CACHE_PARTS = {
    ".cache", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build", ".gradle", "target",
}
PSEUDO_FILESYSTEMS = {
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs", "debugfs",
    "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs", "mqueue", "overlay",
    "proc", "pstore", "securityfs", "squashfs", "sysfs", "tmpfs", "tracefs",
    "fuse.portal", "fuse.gvfsd-fuse",
}


class SmartFileManager:
    def __init__(self, roots=None, skip_names=None):
        self.roots = [Path(root).expanduser() for root in (roots or [Path.home()])]
        self.skip_names = set(skip_names or DEFAULT_SKIPS)

    def _walk(self):
        for search_root in self.roots:
            for root, dirs, files in os.walk(search_root, topdown=True, onerror=lambda _e: None):
                dirs[:] = [name for name in dirs if name not in self.skip_names]
                root_path = Path(root)
                for name in dirs:
                    yield root_path / name, "folder"
                for name in files:
                    yield root_path / name, "file"

    def search(self, query, item_type="any", limit=20):
        terms = normalize_text(query).split()
        if not terms:
            return []
        matches = []
        now = time.time()
        for path, kind in self._walk():
            if item_type != "any" and kind != item_type:
                continue
            name = normalize_text(path.name)
            full = normalize_text(path)
            if not all(term in full for term in terms):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            exact = int(name == " ".join(terms))
            prefix = int(name.startswith(" ".join(terms)))
            name_hits = sum(term in name for term in terms)
            age_days = max(0.0, (now - stat.st_mtime) / 86400)
            score = exact * 100 + prefix * 40 + name_hits * 10 + 10 / (1 + age_days)
            matches.append({"path": path, "type": kind, "score": score, "modified": stat.st_mtime})
        matches.sort(key=lambda item: (-item["score"], -item["modified"], str(item["path"])))
        return matches[:max(1, min(int(limit), 100))]

    def recent(self, limit=10, item_type="file"):
        results = []
        for path, kind in self._walk():
            if item_type != "any" and kind != item_type:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            results.append({"path": path, "type": kind, "modified": stat.st_mtime})
        results.sort(key=lambda item: item["modified"], reverse=True)
        return results[:max(1, min(int(limit), 100))]

    def _inventory_files(self, max_files=20000, time_budget=8.0):
        """Quét có giới hạn để không làm Jarvis treo trên home directory lớn."""
        started = time.monotonic()
        scanned = 0
        for search_root in self.roots:
            for root, dirs, files in os.walk(
                search_root, topdown=True, onerror=lambda _error: None
            ):
                dirs[:] = [
                    name for name in dirs
                    if name not in INVENTORY_SKIPS and not (Path(root) / name).is_symlink()
                ]
                root_path = Path(root)
                for name in files:
                    if scanned >= max_files or time.monotonic() - started >= time_budget:
                        return
                    scanned += 1
                    path = root_path / name
                    if path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    yield path, stat

    @staticmethod
    def _format_size(size):
        size = float(size)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if size < 1024 or unit == "TiB":
                return f"{size:.1f} {unit}"
            size /= 1024

    @staticmethod
    def _open_file_owners():
        """Ánh xạ file đang mở từ /proc mà không phụ thuộc lsof."""
        owners = {}
        proc = Path("/proc")
        try:
            processes = list(proc.iterdir())
        except OSError:
            return owners
        for process_dir in processes:
            if not process_dir.name.isdigit():
                continue
            try:
                command = (process_dir / "comm").read_text(errors="replace").strip()
                descriptors = list((process_dir / "fd").iterdir())
            except OSError:
                continue
            for descriptor in descriptors:
                try:
                    target = descriptor.resolve(strict=True)
                except OSError:
                    continue
                if not target.is_file():
                    continue
                owners.setdefault(str(target), set()).add(
                    f"{command or 'process'} (PID {process_dir.name})"
                )
        return owners

    @staticmethod
    def _git_state(path):
        """Trả về trạng thái Git cho số ít file đã lọt vào báo cáo cuối."""
        repository = None
        for parent in (path.parent, *path.parents):
            if (parent / ".git").exists():
                repository = parent
                break
        if repository is None:
            return {"repository": None, "tracked": False, "modified": False}
        try:
            relative = path.relative_to(repository)
            tracked = subprocess.run(
                ["git", "-C", str(repository), "ls-files", "--error-unmatch", "--", str(relative)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            ).returncode == 0
            status = subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain", "--", str(relative)],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            ).stdout.strip()
            return {"repository": repository, "tracked": tracked, "modified": bool(status)}
        except (OSError, subprocess.SubprocessError, ValueError):
            return {"repository": repository, "tracked": False, "modified": False}

    def analyze(self, path, open_owners=None):
        path = Path(path).expanduser().resolve()
        try:
            stat = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None

        owners = (open_owners or self._open_file_owners()).get(str(path), set())
        git = self._git_state(path)
        normalized_parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        suffix = path.suffix.lower()
        age_days = max(0.0, (time.time() - stat.st_mtime) / 86400)
        reasons = []
        cleanup_score = 0
        importance = "normal"

        if owners:
            importance = "critical"
            reasons.append("đang được process sử dụng")
        if git["tracked"] or git["modified"]:
            importance = "critical" if git["modified"] else "high"
            reasons.append("được Git theo dõi" if git["tracked"] else "có thay đổi Git chưa lưu")
        if name in IMPORTANT_NAMES or suffix in IMPORTANT_SUFFIXES:
            importance = "high" if importance == "normal" else importance
            reasons.append("thuộc loại dữ liệu/cấu hình quan trọng")

        if normalized_parts & CACHE_PARTS:
            cleanup_score += 70
            reasons.append("nằm trong cache/build có thể tạo lại")
        if suffix in {".tmp", ".temp", ".cache", ".pyc"}:
            cleanup_score += 50
            reasons.append("là file tạm hoặc bytecode")
        if suffix == ".log" and age_days >= 7:
            cleanup_score += 35
            reasons.append("là log cũ hơn 7 ngày")
        if "trash" in normalized_parts:
            cleanup_score += 80
            reasons.append("nằm trong thùng rác")
        if owners or git["tracked"] or git["modified"] or importance in {"critical", "high"}:
            cleanup_score = 0

        recommendation = "không xóa" if importance in {"critical", "high"} else "giữ lại"
        if cleanup_score >= 50:
            recommendation = "ứng viên dọn dẹp — cần xác nhận"
        elif cleanup_score > 0:
            recommendation = "xem xét thủ công"

        return {
            "path": path,
            "size": stat.st_size,
            "size_text": self._format_size(stat.st_size),
            "modified": stat.st_mtime,
            "age_days": age_days,
            "open_by": sorted(owners),
            "git": git,
            "importance": importance,
            "cleanup_score": cleanup_score,
            "recommendation": recommendation,
            "reasons": reasons or ["chưa có dấu hiệu đặc biệt"],
        }

    def largest(self, limit=10, minimum_size=1024 * 1024):
        """Giữ top-N trong lúc quét để không bao giờ trả rỗng chỉ vì ngưỡng size."""
        limit = max(1, min(int(limit), 30))
        top = []
        fallback = []
        for path, stat in self._inventory_files(max_files=100000, time_budget=15.0):
            target = top if stat.st_size >= minimum_size else fallback
            item = (stat.st_size, str(path), path)
            if len(target) < limit:
                heapq.heappush(target, item)
            elif item[:2] > target[0][:2]:
                heapq.heapreplace(target, item)
        candidates = top or fallback
        candidates.sort(reverse=True)
        owners = self._open_file_owners()
        return [
            self.analyze(path, owners)
            for _size, _path_text, path in candidates[:limit]
        ]

    def stale_files(self, path=None, days=30, limit=20, minimum_size=1024 * 1024):
        """Báo cáo file cũ, ưu tiên Downloads; chỉ đọc và không suy diễn là an toàn để xóa."""
        root = Path(path or (Path.home() / "Downloads")).expanduser().resolve()
        if not root.is_dir():
            return []
        cutoff = time.time() - max(1, int(days)) * 86400
        candidates = []
        for current, dirs, files in os.walk(root, topdown=True, onerror=lambda _e: None):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in files:
                candidate = Path(current) / name
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                if stat.st_mtime <= cutoff and stat.st_size >= minimum_size:
                    candidates.append((stat.st_size, candidate))
        candidates.sort(reverse=True, key=lambda item: item[0])
        owners = self._open_file_owners()
        return [
            self.analyze(candidate, owners)
            for _size, candidate in candidates[:max(1, min(int(limit), 50))]
        ]

    @staticmethod
    def _sha256(path, chunk_size=1024 * 1024):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest()

    def duplicate_files(self, limit=20, minimum_size=1024 * 1024):
        """Tìm file trùng bằng size rồi SHA-256; giới hạn để tránh khóa máy quá lâu."""
        by_size = {}
        for path, stat in self._inventory_files(max_files=60000, time_budget=10.0):
            if stat.st_size >= minimum_size:
                by_size.setdefault(stat.st_size, []).append(path)
        by_hash = {}
        hashed = 0
        hash_started = time.monotonic()
        for size, paths in sorted(by_size.items(), reverse=True):
            if len(paths) < 2:
                continue
            for path in paths[:20]:
                if hashed >= 200 or time.monotonic() - hash_started >= 12.0:
                    break
                hashed += 1
                try:
                    digest = self._sha256(path)
                except OSError:
                    continue
                by_hash.setdefault((size, digest), []).append(path)
            if hashed >= 200 or time.monotonic() - hash_started >= 12.0:
                break
        groups = []
        for (size, _digest), paths in by_hash.items():
            if len(paths) > 1:
                groups.append(
                    {
                        "size": size,
                        "size_text": self._format_size(size),
                        "wasted": size * (len(paths) - 1),
                        "wasted_text": self._format_size(size * (len(paths) - 1)),
                        "paths": paths,
                    }
                )
        groups.sort(key=lambda item: item["wasted"], reverse=True)
        return groups[:max(1, min(int(limit), 30))]

    def cleanup_candidates(self, limit=15, minimum_size=1024 * 1024):
        preliminary = []
        for path, stat in self._inventory_files():
            parts = {part.lower() for part in path.parts}
            suffix = path.suffix.lower()
            looks_cleanable = bool(parts & CACHE_PARTS) or suffix in {
                ".tmp", ".temp", ".cache", ".pyc", ".log"
            }
            if looks_cleanable and stat.st_size >= minimum_size:
                preliminary.append((stat.st_size, path))
        preliminary.sort(reverse=True, key=lambda item: item[0])
        owners = self._open_file_owners()
        analyzed = [self.analyze(path, owners) for _size, path in preliminary[:100]]
        safe_candidates = [item for item in analyzed if item and item["cleanup_score"] > 0]
        safe_candidates.sort(key=lambda item: (-item["cleanup_score"], -item["size"]))
        return safe_candidates[:max(1, min(int(limit), 30))]

    def open_files(self, limit=20):
        owners = self._open_file_owners()
        results = []
        roots = [root.resolve() for root in self.roots]
        for path_text in owners:
            path = Path(path_text)
            try:
                if not any(path.is_relative_to(root) for root in roots):
                    continue
            except (OSError, ValueError):
                continue
            analyzed = self.analyze(path, owners)
            if analyzed:
                results.append(analyzed)
        results.sort(key=lambda item: (-item["size"], str(item["path"])))
        return results[:max(1, min(int(limit), 50))]

    @staticmethod
    def disk_usage():
        """Dung lượng các filesystem thật đang được mount, không gồm pseudo FS."""
        mounts = []
        seen_devices = set()
        try:
            lines = Path("/proc/self/mounts").read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            fields = line.split()
            if len(fields) < 3:
                continue
            device, mount_text, filesystem = fields[:3]
            mount_text = (
                mount_text.replace("\\040", " ")
                .replace("\\011", "\t")
                .replace("\\134", "\\")
            )
            mount = Path(mount_text)
            # /dev/loop* là image Snap read-only và luôn báo 100%, không phải
            # phân vùng người dùng bị đầy. Bind mount cùng thiết bị cũng chỉ
            # được hiển thị một lần.
            if (
                filesystem in PSEUDO_FILESYSTEMS
                or device.startswith("/dev/loop")
                or device in seen_devices
                or str(mount).startswith("/run/snap")
            ):
                continue
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            if usage.total <= 0:
                continue
            seen_devices.add(device)
            percent = (usage.used / usage.total * 100) if usage.total else 0.0
            mounts.append(
                {
                    "device": device,
                    "mount": mount,
                    "filesystem": filesystem,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "total_text": SmartFileManager._format_size(usage.total),
                    "used_text": SmartFileManager._format_size(usage.used),
                    "free_text": SmartFileManager._format_size(usage.free),
                    "percent": percent,
                    "level": "critical" if percent >= 95 else "warning" if percent >= 85 else "normal",
                }
            )
        mounts.sort(key=lambda item: (-item["percent"], str(item["mount"])))
        return mounts

    @staticmethod
    def directory_usage(path, limit=15, max_files=50000, time_budget=10.0):
        """Xếp hạng dung lượng con trực tiếp; không đi theo symlink/filesystem khác."""
        root = Path(path).expanduser().resolve()
        try:
            root_stat = root.stat()
            children = list(root.iterdir())
        except OSError:
            return None
        if not root.is_dir():
            return None

        started = time.monotonic()
        scanned = 0
        incomplete = False
        results = []
        for child in children:
            if child.is_symlink():
                continue
            size = 0
            file_count = 0
            if child.is_file():
                try:
                    size = child.stat().st_size
                    file_count = 1
                except OSError:
                    continue
            elif child.is_dir():
                for current, dirs, files in os.walk(
                    child, topdown=True, onerror=lambda _error: None
                ):
                    if scanned >= max_files or time.monotonic() - started >= time_budget:
                        incomplete = True
                        break
                    current_path = Path(current)
                    kept_dirs = []
                    for name in dirs:
                        candidate = current_path / name
                        try:
                            if not candidate.is_symlink() and candidate.stat().st_dev == root_stat.st_dev:
                                kept_dirs.append(name)
                        except OSError:
                            continue
                    dirs[:] = kept_dirs
                    for name in files:
                        if scanned >= max_files or time.monotonic() - started >= time_budget:
                            incomplete = True
                            break
                        scanned += 1
                        candidate = current_path / name
                        try:
                            if not candidate.is_symlink() and candidate.stat().st_dev == root_stat.st_dev:
                                size += candidate.stat().st_size
                                file_count += 1
                        except OSError:
                            continue
                    if incomplete:
                        break
            results.append(
                {
                    "path": child,
                    "size": size,
                    "size_text": SmartFileManager._format_size(size),
                    "file_count": file_count,
                }
            )
            if incomplete:
                break

        results.sort(key=lambda item: (-item["size"], str(item["path"])))
        return {
            "root": root,
            "items": results[:max(1, min(int(limit), 50))],
            "scanned_files": scanned,
            "incomplete": incomplete,
        }
