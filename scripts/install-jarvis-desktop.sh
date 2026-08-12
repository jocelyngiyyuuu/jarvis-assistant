#!/usr/bin/env bash
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APPLICATIONS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPLICATIONS_DIR/jarvis.desktop"

mkdir -p "$APPLICATIONS_DIR"
sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/packaging/jarvis.desktop.in" > "$DESKTOP_FILE"
chmod 755 "$PROJECT_DIR/scripts/launch-jarvis-gui.sh" "$PROJECT_DIR/scripts/repair-jarvis-env.sh"
chmod 644 "$DESKTOP_FILE"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPLICATIONS_DIR" || true
printf 'Đã cài biểu tượng Jarvis: %s\n' "$DESKTOP_FILE"
