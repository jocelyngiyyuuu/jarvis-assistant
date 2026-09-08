#!/usr/bin/env bash
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    /usr/bin/python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip

if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
    "$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"
else
    printf '%s\n' "Không tìm thấy requirements.txt; .venv đã được tạo nhưng dependency Jarvis cần được cài thủ công." >&2
    exit 2
fi
