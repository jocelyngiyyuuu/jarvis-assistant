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
            ("Lịch sử trò chuyện", "lịch sử trò chuyện"),
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
    "desktop": {
        "title": "Màn hình & chuột",
        "icon": "🖥️",
        "description": "Xem màn hình, chuyển cửa sổ và điều khiển chuột XWayland có xác nhận.",
        "aliases": ("man hinh", "desktop", "chuot", "monitor"),
        "commands": (
            ("Trạng thái màn hình", "trạng thái màn hình"),
            ("Chụp màn hình", "chụp màn hình"),
            ("Mở Zalo trên màn hình phụ", "mở zalo trên màn hình phụ"),
            ("Chuyển cửa sổ hiện tại", "chuyển cửa sổ hiện tại sang màn hình phụ"),
            ("Nhớ vị trí Zalo", "nhớ zalo mở trên màn hình phụ"),
            ("Di chuột", "di chuột đến 500, 300"),
            ("Bấm chuột", "bấm chuột tại 500, 300"),
            ("Nhấn theo tên", "nhấn nút Gửi"),
            ("Cuộn", "cuộn xuống 3"),
        ),
    },
    "web": {
        "title": "Web & Nhạc",
        "icon": "🌐",
        "description": "Mở website, tìm Google và điều khiển YouTube hoặc SoundCloud.",
        "aliases": ("web", "youtube", "soundcloud", "google", "trinh duyet"),
        "commands": (
            ("YouTube cá nhân", "mở youtube cá nhân"),
            ("YouTube học", "mở youtube học"),
            ("Đóng YouTube", "tắt youtube"),
            ("Tìm video", "tìm youtube nhạc thư giãn"),
            ("Mở video", "mở video 1"),
            ("Dừng video", "dừng video"),
            ("Video đang phát", "youtube đang phát gì"),
            ("Mở SoundCloud", "mở soundcloud"),
            ("Tìm SoundCloud", "tìm soundcloud nhạc thư giãn"),
            ("Mở bài SoundCloud", "mở bài soundcloud 1"),
            ("Dừng SoundCloud", "dừng soundcloud"),
            ("Bài SoundCloud đang phát", "soundcloud đang phát gì"),
            ("Đóng SoundCloud", "tắt soundcloud"),
            ("Google", "google thời tiết hôm nay"),
            ("Thời tiết hôm nay theo vị trí", "thời tiết hôm nay thế nào"),
            ("Dự báo tuần này", "thời tiết tuần này thế nào"),
            ("Dự báo tuần tới", "thời tiết tuần tới thế nào"),
            ("ChatGPT", "mở chatgpt học"),
            ("Đóng ChatGPT học", "tắt chatgpt học"),
            ("Đóng Gmail học", "tắt gmail học"),
            ("Đóng Drive học", "tắt drive học"),
            ("Đóng Calendar học", "tắt calendar học"),
            ("Mở GitHub mặc định Profile 1", "mở github"),
            ("Đóng GitHub mặc định Profile 1", "tắt github"),
        ),
    },
    "gmail": {
        "title": "Gmail",
        "icon": "📧",
        "description": "Mở Gmail, tóm tắt cục bộ, nêu việc cần làm, đánh dấu đã đọc và báo thư mới qua Discord.",
        "aliases": ("gmail", "email", "mail", "thu dien tu"),
        "commands": (
            ("Mở Gmail Jarvis", "mở gmail"),
            ("Tóm tắt thư chưa đọc và đánh dấu đã đọc", "tóm tắt gmail"),
            ("Tóm tắt thư chưa đọc", "tóm tắt gmail chưa đọc"),
            ("Kiểm tra thư mới", "gmail mới"),
            ("Tóm tắt thư rác có ý nghĩa", "tóm tắt thư rác"),
            ("Đóng đúng tab Gmail", "tắt gmail"),
        ),
    },
    "zalo": {
        "title": "Zalo công việc",
        "icon": "💬",
        "description": "Mở Zalo học, tóm tắt theo ngày/nhóm, hỏi sự kiện, soạn và hẹn gửi có xác nhận Discord.",
        "aliases": ("zalo", "zalo cong viec", "tin nhan", "nhan zalo"),
        "commands": (
            ("Mở Zalo mặc định Chrome học", "mở zalo"),
            ("Đóng đúng tab Zalo", "tắt zalo"),
            ("Tóm tắt tối đa 20 nhóm", "tóm tắt zalo công việc"),
            ("Tóm tắt hôm nay", "tóm tắt zalo hôm nay"),
            ("Tóm tắt hôm qua", "tóm tắt zalo hôm qua"),
            ("Tóm tắt theo ngày", "tóm tắt zalo ngày 12/08/2026"),
            ("Tóm tắt một nhóm", "tóm tắt zalo nhóm PRF193 hôm nay"),
            ("Hỏi sự kiện trong nhóm", "hỏi zalo nhóm PRF193 hôm nay về khảo sát"),
            ("Soạn bản nháp", "soạn zalo cho Minh: nội dung cần gửi"),
            ("Soạn mặc định Zalo học", "soạn tin nhắn cho Minh với nội dung test"),
            ("Soạn theo thẻ", "soạn zalo thẻ Trả lời sau cho Minh: nội dung cần gửi"),
            ("Gửi ngay có xác nhận", "gửi tin nhắn cho Minh với nội dung test"),
            ("Hẹn theo giờ", "nhắn zalo cho Minh lúc 20:30: nội dung cần gửi"),
            ("Hẹn theo ngày giờ", "nhắn zalo cho Minh lúc 19:30 ngày 13/08/2026: nội dung cần gửi"),
            ("Hẹn sau một khoảng", "nhắn zalo cho Minh sau 2h nữa: nội dung cần gửi"),
            ("Thời gian ở đầu câu", "sau 1h15p hãy nhắn zalo cho Suri với nội dung ra phơi đồ với a2"),
            ("Mặc định Zalo khi hẹn", "sau 10s gửi tin nhắn cho Minh với nội dung test"),
            ("Hẹn theo thẻ Gia đình", "nhắn zalo thẻ Gia đình cho Minh lúc 20:30: nội dung cần gửi"),
            ("Hẹn thẻ Trả lời sau", "nhắn zalo thẻ Trả lời sau cho Minh sau 30p nữa: nội dung cần gửi"),
        ),
    },
    "apps": {
        "title": "Ứng dụng Ubuntu",
        "icon": "🖥️",
        "description": "Mở, đóng ứng dụng và điều khiển các cửa sổ Ubuntu.",
        "aliases": ("ung dung", "app", "ubuntu", "cua so"),
        "commands": (
            ("Mở VS Code", "mở vscode"),
            ("Đóng VS Code", "tắt vscode"),
            ("Mở Terminal", "mở terminal"),
            ("Đóng Terminal Jarvis vừa mở", "tắt terminal"),
            ("Mở System Monitor", "mở system monitor"),
            ("Đóng System Monitor Jarvis vừa mở", "tắt system monitor"),
            ("Mở Downloads", "mở downloads"),
            ("Đóng thư mục Jarvis vừa mở", "tắt downloads"),
            ("Hiện desktop", "hiện desktop"),
            ("Đóng Chrome", "tắt chrome"),
            ("Đóng mọi tab/app GUI, kể cả của bạn", "tắt tất cả"),
        ),
    },
    "audio": {
        "title": "Âm thanh",
        "icon": "🔊",
        "description": "Điều khiển âm lượng và kích hoạt lệnh giọng nói bằng vỗ/búng tay.",
        "aliases": ("am thanh", "am luong", "audio", "volume", "giong noi"),
        "commands": (
            ("Kích hoạt giọng nói", "vỗ tay 2 lần hoặc búng tay 1 lần rồi nói lệnh"),
            ("Trạng thái cảm biến", "trạng thái cảm biến âm thanh"),
            ("Tạm tắt giọng nói", "tắt giọng nói tạm thời"),
            ("Bật lại giọng nói", "bật giọng nói"),
            ("Tắt vỗ tay bật màn hình", "tắt vỗ tay"),
            ("Bật vỗ tay bật màn hình", "bật vỗ tay"),
            ("Xem âm lượng", "âm lượng"),
            ("Đặt 50%", "âm lượng 50"),
            ("Tăng 10", "tăng âm lượng 10"),
            ("Giảm 10", "giảm âm lượng 10"),
            ("Tắt tiếng", "tắt tiếng"),
            ("Bật tiếng", "bật tiếng"),
            ("Xem âm lượng PC", "âm lượng PC hiện tại"),
            ("Tăng âm lượng PC", "tăng âm lượng PC 10"),
            ("Giảm âm lượng PC", "giảm âm lượng PC 10"),
            ("Đặt âm lượng PC", "âm lượng PC 50"),
            ("Tắt tiếng PC", "tắt tiếng PC"),
            ("Bật tiếng PC", "bật tiếng PC"),
        ),
    },
    "power": {
        "title": "Nguồn & lịch",
        "icon": "🌙",
        "description": "Tắt màn hình, quản lý Sleep sâu và tra cứu lịch âm Việt Nam local.",
        "aliases": ("nguon", "lich", "sleep", "suspend", "lich am", "am lich"),
        "commands": (
            ("Bật PC qua mạng", "bật PC"),
            ("Tắt PC qua SSH", "tắt PC"),
            ("Tắt màn hình", "sleep"),
            ("Đánh thức bằng vỗ/búng", "sleep rồi vỗ 2 lần hoặc búng 1 lần"),
            ("Sleep sâu", "sleep sâu"),
            ("Hẹn 30 phút", "sleep sâu sau 30p"),
            ("Hẹn lúc 23:30", "sleep sâu lúc 23:30"),
            ("Xem lịch", "lịch sleep sâu"),
            ("Hủy lịch", "hủy sleep sâu"),
            ("Âm lịch hôm nay", "hôm nay là ngày mấy âm lịch"),
            ("Rằm sắp tới", "rằm lịch âm là ngày nào"),
            ("Mùng một sắp tới", "mùng 1 âm lịch là ngày nào"),
            ("Đổi dương sang âm", "17/02/2026 là ngày âm lịch nào"),
            ("Đổi âm sang dương", "ngày 1 tháng 1 năm 2026 âm lịch là ngày dương nào"),
            ("Nhắc sau 30 phút", "nhắc tôi sau 30p kiểm tra đơn hàng"),
            ("Nhắc thời lượng ghép", "nhắc tôi sau 1h15p đi phơi đồ"),
            ("Tạo nhắc tự nhiên", "tạo nhắc nhở phơi đồ sau 1h15p"),
            ("Nhắc với thời gian trước", "sau 1h15p hãy nhắc tôi đi phơi đồ"),
            ("Nhắc theo ngày giờ", "nhắc tôi lúc 19:30 ngày 13/08/2026: gọi khách hàng"),
            ("Xem lịch nhắc", "lịch nhắc"),
            ("Chọn lịch cần hủy", "hủy lịch nhắc"),
        ),
    },
}


RECENT_UPDATES = (
    (
        "Hẹn gửi tin mặc định qua Zalo học",
        "Câu 'sau 10s gửi tin nhắn cho...' không cần nói Zalo/profile và được lưu thành lịch gửi.",
        "sau 10s gửi tin nhắn cho Minh với nội dung test",
    ),
    (
        "Mở Zalo mặc định bằng Chrome học",
        "Các lệnh mở/vào Zalo không cần nói profile; chỉ dùng Chrome cá nhân khi bạn yêu cầu rõ.",
        "mở zalo",
    ),
    (
        "Hẹn nhắn Zalo với thời gian ở đầu câu",
        "Hiểu dạng 'sau 1h15p hãy nhắn Zalo cho...' và lưu lịch ngay cả khi tab Zalo chưa mở.",
        "sau 1h15p hãy nhắn zalo cho Suri với nội dung ra phơi đồ với a2",
    ),
    (
        "Câu nhắc tự nhiên với thời gian ở đầu",
        "Hiểu trực tiếp dạng 'sau 1h15p hãy nhắc tôi...' và tạo lịch mà không cần cú pháp cố định.",
        "sau 1h15p hãy nhắc tôi đi phơi đồ",
    ),
    (
        "Chọn lịch nhắc cần hủy",
        "Lệnh hủy hiển thị ID, thời gian, loại, người nhận và nội dung trước khi bạn chọn; không mặc định lịch #1.",
        "hủy lịch nhắc",
    ),
    (
        "Thời lượng nhắc việc linh hoạt",
        "Hiểu thời lượng ghép 1h15p/1h 15p và câu tự nhiên 'tạo nhắc nhở ... sau ...' mà không đẩy sang AI.",
        "tạo nhắc nhở phơi đồ sau 1h15p",
    ),
    (
        "Xác minh gửi Zalo",
        "Chỉ báo đã gửi khi ô soạn trống và bong bóng chứa đúng nội dung thực sự xuất hiện; không còn báo thành công chỉ vì đã nhấn phím.",
        "gửi tin nhắn cho Minh với nội dung test",
    ),
    (
        "Mặc định nhắn tin bằng Zalo học",
        "Lệnh soạn/nhắn/gửi cho một người không cần nói lại nền tảng; Jarvis dùng Zalo Web trong Chrome học.",
        "soạn tin nhắn cho Minh với nội dung test",
    ),
    (
        "Phiên chat mới khi mở ứng dụng",
        "GTK không tải lại bong bóng cũ; lịch sử vẫn được lưu local và chỉ hiện khi bạn yêu cầu.",
        "lịch sử trò chuyện",
    ),
    (
        "Gửi Zalo an toàn",
        "Hiểu câu gửi tin tự nhiên, tìm đúng người và chỉ gửi sau khi chủ tài khoản bấm nút xác nhận Discord.",
        "gửi tin nhắn cho Minh với nội dung test",
    ),
    (
        "Tìm liên hệ theo thẻ Zalo",
        "Giới hạn người nhận trong thẻ Trả lời sau hoặc Gia đình để tránh nhầm người trùng tên.",
        "soạn zalo thẻ Trả lời sau cho Minh: nội dung cần gửi",
    ),
    (
        "Hẹn gửi Zalo",
        "Hẹn theo giờ, ngày giờ hoặc sau một khoảng; tới hạn Discord hiển thị bản xem trước và nút xác nhận 60 giây.",
        "nhắn zalo cho Minh sau 2h nữa: nội dung cần gửi",
    ),
    (
        "Zalo theo ngày, nhóm và sự kiện",
        "Tóm tắt đúng ngày, chọn một nhóm, giữ liên kết và trả lời câu hỏi kèm bằng chứng từ tin nhắn.",
        "hỏi zalo nhóm PRF193 hôm nay về khảo sát",
    ),
    (
        "Nhắc việc chủ động qua Discord",
        "Lưu lịch bền vững, nhắc theo khoảng thời gian hoặc ngày giờ, xem và hủy từng lịch.",
        "nhắc tôi sau 30p kiểm tra công việc",
    ),
    (
        "Tổng hợp Zalo công việc",
        "Đọc cục bộ tối đa 20 nhóm Công việc, tóm tắt, liệt kê việc cần làm và giữ lại các liên kết trên GTK/Discord.",
        "tóm tắt zalo công việc",
    ),
    (
        "Xác nhận lệnh gõ sai",
        "Trả lời đúng/phải/ok để chạy lệnh Jarvis vừa gợi ý; lệnh nguy hiểm vẫn giữ lớp xác nhận Discord.",
        "mở vscod",
    ),
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
        ["", "Chọn một nhóm trên ứng dụng hoặc nhập `help bộ nhớ`, `help file`, `help zalo`, `help lịch`…"]
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


def format_category_choices():
    """Generate category guidance from the same catalog used by GTK/Discord."""
    return ", ".join(category["title"] for category in HELP_CATEGORIES.values())
