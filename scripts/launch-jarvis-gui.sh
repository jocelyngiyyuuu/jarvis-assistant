#!/usr/bin/env bash
set -u

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
SYSTEM_PYTHON="/usr/bin/python3"
REPAIR_SCRIPT="$PROJECT_DIR/scripts/repair-jarvis-env.sh"

show_error() {
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Jarvis" --text="$1"
    else
        printf 'Jarvis: %s\n' "$1" >&2
    fi
}

if [[ ! -x "$VENV_PYTHON" ]]; then
    if command -v zenity >/dev/null 2>&1 && zenity --question \
        --title="Sửa chữa Jarvis" \
        --text="Môi trường .venv đang thiếu. Bạn có muốn tạo lại môi trường Jarvis ngay bây giờ không?" \
        --ok-label="Tạo lại" --cancel-label="Để sau"; then
        "$REPAIR_SCRIPT" || {
            show_error "Không thể tạo lại .venv. Hãy mở Terminal và chạy scripts/repair-jarvis-env.sh để xem lỗi."
            exit 1
        }
    else
        show_error "Jarvis cần được cài đặt hoặc sửa chữa vì thiếu .venv."
        exit 1
    fi
fi

if [[ ! -x "$SYSTEM_PYTHON" ]]; then
    show_error "Không tìm thấy Python hệ thống của Ubuntu."
    exit 1
fi

if ! "$SYSTEM_PYTHON" -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk"; then
    show_error "Thiếu GTK 4 Python bindings. Cần cài gói python3-gi và gir1.2-gtk-4.0."
    exit 1
fi

# Jarvis nền là chủ sở hữu Discord, TTS và các lịch tác vụ. Khởi động service
# không mở Terminal; khóa một-instance trong jarvis.py ngăn chạy trùng.
if systemctl --user cat jarvis.service >/dev/null 2>&1; then
    systemctl --user start jarvis.service || true
fi

cd "$PROJECT_DIR" || exit 1
exec "$SYSTEM_PYTHON" "$PROJECT_DIR/jarvis_gui.py"
