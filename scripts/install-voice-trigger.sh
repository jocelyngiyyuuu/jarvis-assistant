#!/usr/bin/env bash
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
MODEL_ROOT="$PROJECT_DIR/.jarvis_data/models"
MODEL_NAME="vosk-model-small-vn-0.4"
MODEL_DIR="$MODEL_ROOT/$MODEL_NAME"
MODEL_URL="https://alphacephei.com/vosk/models/$MODEL_NAME.zip"

"$PROJECT_DIR/.venv/bin/python" -m pip install "vosk==0.3.45"

if [ -d "$MODEL_DIR/am" ] && [ -d "$MODEL_DIR/conf" ]; then
    printf '%s\n' "Model giọng nói đã có: $MODEL_DIR"
    exit 0
fi

mkdir -p "$MODEL_ROOT"
TEMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

curl --fail --location --proto '=https' --tlsv1.2 \
    --output "$TEMP_DIR/model.zip" "$MODEL_URL"
unzip -q "$TEMP_DIR/model.zip" -d "$TEMP_DIR/unpacked"

if [ ! -d "$TEMP_DIR/unpacked/$MODEL_NAME/am" ] || \
   [ ! -d "$TEMP_DIR/unpacked/$MODEL_NAME/conf" ]; then
    printf '%s\n' "Gói model không có cấu trúc Vosk hợp lệ; không cài đặt." >&2
    exit 2
fi

mv "$TEMP_DIR/unpacked/$MODEL_NAME" "$MODEL_DIR"
printf '%s\n' "Đã cài model giọng nói tiếng Việt: $MODEL_DIR"
