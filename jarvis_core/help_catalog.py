"""Discoverable Jarvis capabilities shared by every interface."""

from .text import normalize_text


HELP_CATEGORIES = {
    "memory": {
        "title": "Bộ nhớ",
        "icon": "🧠",
        "description": "Lưu, tìm lại, cập nhật và quên thông tin trong bộ nhớ local.",
        "aliases": ("bo nho", "memory", "ghi nho", "nho"),
        "commands": (
            ("Ghi nhớ", "ghi nhớ Tôi thích giao diện tối"),
            ("Ghi nhớ thiết bị", "ghi nhớ thiết bị: Máy chính dùng Ubuntu"),
            ("Nhớ tạm thời", "ghi nhớ tạm thời 2 ngày: kiểm tra bản sao lưu"),
            ("Tìm ký ức", "nhớ lại Ubuntu"),
            ("Xem tất cả", "xem bộ nhớ"),
            ("Cập nhật", "cập nhật mục 1 thành nội dung mới"),
            ("Quên theo ID", "quên mục 1"),
        ),
    },
    "files": {
        "title": "File & ổ đĩa",
        "icon": "📁",
        "description": "Tìm file, đo dung lượng và đánh giá mức quan trọng trước khi dọn.",
        "aliases": ("file", "tep", "o dia", "file va o dia"),
        "commands": (
            ("Tìm file", "tìm file báo cáo"),
            ("File gần đây", "file gần đây"),
            ("File lớn", "file lớn nhất"),
            ("Ổ đĩa", "dung lượng ổ đĩa"),
            ("Dung lượng thư mục", "dung lượng thư mục ~/Downloads"),
            ("File đang dùng", "file đang được sử dụng"),
            ("File trùng", "tìm file trùng"),
            ("Downloads cũ", "downloads cũ"),
            ("Có thể dọn", "quét file có thể dọn"),
            ("Phân tích", "phân tích file ~/.env"),
        ),
    },
    "system": {
        "title": "Hệ thống",
        "icon": "📊",
        "description": "Theo dõi CPU, RAM, ổ đĩa, uptime và dịch vụ Jarvis.",
        "aliases": ("he thong", "system", "cpu", "ram"),
        "commands": (("Tình trạng máy", "tình trạng hệ thống"),),
    },
    "web": {
        "title": "Web & YouTube",
        "icon": "🌐",
        "description": "Mở website, tìm Google và điều khiển phiên YouTube theo profile.",
        "aliases": ("web", "youtube", "google", "trinh duyet"),
        "commands": (
            ("YouTube cá nhân", "mở youtube cá nhân"),
            ("YouTube học", "mở youtube học"),
            ("Tìm video", "tìm youtube nhạc thư giãn"),
            ("Mở video", "mở video 1"),
            ("Dừng video", "dừng video"),
            ("Google", "google thời tiết hôm nay"),
            ("ChatGPT", "mở chatgpt học"),
        ),
    },
    "apps": {
        "title": "Ứng dụng Ubuntu",
        "icon": "🖥️",
        "description": "Mở, đóng ứng dụng và điều khiển các cửa sổ Ubuntu.",
        "aliases": ("ung dung", "app", "ubuntu", "cua so"),
        "commands": (
            ("Mở VS Code", "mở vscode"),
            ("Mở Terminal", "mở terminal"),
            ("Mở Downloads", "mở downloads"),
            ("Hiện desktop", "hiện desktop"),
            ("Đóng Chrome", "tắt chrome"),
        ),
    },
    "audio": {
        "title": "Âm thanh",
        "icon": "🔊",
        "description": "Xem, đặt, tăng giảm âm lượng và điều khiển trạng thái tắt tiếng.",
        "aliases": ("am thanh", "am luong", "audio", "volume"),
        "commands": (
            ("Xem âm lượng", "âm lượng"),
            ("Đặt 50%", "âm lượng 50"),
            ("Tăng 10", "tăng âm lượng 10"),
            ("Giảm 10", "giảm âm lượng 10"),
            ("Tắt tiếng", "tắt tiếng"),
            ("Bật tiếng", "bật tiếng"),
        ),
    },
    "power": {
        "title": "Nguồn & lịch",
        "icon": "🌙",
        "description": "Tắt màn hình, suspend và quản lý lịch sleep sâu trong tiến trình nền.",
        "aliases": ("nguon", "lich", "sleep", "suspend"),
        "commands": (
            ("Tắt màn hình", "sleep"),
            ("Sleep sâu", "sleep sâu"),
            ("Hẹn 30 phút", "sleep sâu sau 30p"),
            ("Hẹn lúc 23:30", "sleep sâu lúc 23:30"),
            ("Xem lịch", "lịch sleep sâu"),
            ("Hủy lịch", "hủy sleep sâu"),
        ),
    },
}


RECENT_UPDATES = (
    (
        "Đồng bộ nhiều kênh",
        "GTK, Discord và Terminal dùng chung lịch sử, phiên YouTube, timer và bộ nhớ.",
        "tình trạng hệ thống",
    ),
    (
        "Quản lý file nhanh",
        "Chỉ mục SQLite tìm file/thư mục nhanh, xem file lớn, gần đây và mở trực tiếp.",
        "file lớn nhất",
    ),
    (
        "Phân tích dung lượng",
        "Xem ổ đĩa, khu vực chiếm chỗ, Downloads cũ và ứng viên dọn dẹp an toàn.",
        "dung lượng thư mục ~/Downloads",
    ),
    (
        "Phát hiện file trùng",
        "Đối chiếu kích thước và SHA-256, chỉ báo cáo chứ chưa tự xóa.",
        "tìm file trùng",
    ),
    (
        "Bộ nhớ tương tác",
        "Ghi nhớ, tìm lại, cập nhật, sao chép và xóa từng ký ức có xác nhận.",
        "help bộ nhớ",
    ),
    (
        "Sleep theo giờ",
        "Đặt Sleep sâu theo khoảng thời gian hoặc giờ cụ thể, đồng bộ giữa GTK và Discord.",
        "sleep sâu lúc 23:30",
    ),
    (
        "Bảo mật Discord",
        "Rate limit, che bí mật, audit và nút xác nhận một lần cho lệnh nguy hiểm.",
        "khám phá chức năng",
    ),
)


def resolve_help_category(value):
    query = normalize_text(value)
    for key, category in HELP_CATEGORIES.items():
        if query == key or query in category["aliases"]:
            return key
    return None


def format_help_overview():
    lines = ["🧰 **JARVIS CÓ THỂ LÀM GÌ?**", ""]
    for key, category in HELP_CATEGORIES.items():
        lines.append(
            f"{category['icon']} **{category['title']}** — {category['description']}"
        )
    lines.extend(
        ["", "Chọn một nhóm trên ứng dụng hoặc nhập `help bộ nhớ`, `help file`, `help hệ thống`…"]
    )
    return "\n".join(lines)


def format_help_category(key):
    category = HELP_CATEGORIES[key]
    lines = [
        f"{category['icon']} **{category['title'].upper()}**",
        category["description"],
        "",
        "Lệnh gợi ý:",
    ]
    lines.extend(f"• `{command}` — {label}" for label, command in category["commands"])
    return "\n".join(lines)


def format_recent_updates():
    lines = ["✨ **JARVIS CÓ GÌ MỚI SAU UPDATE?**", ""]
    for title, description, command in RECENT_UPDATES:
        lines.extend([f"**{title}**", f"• {description}", f"• Thử: `{command}`", ""])
    lines.append("Gõ `help` hoặc `khám phá chức năng` để xem toàn bộ khả năng.")
    return "\n".join(lines)
