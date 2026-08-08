#!/usr/bin/env bash
# Install Apricot Studio for the current user only.
#
# Everything lands under ~/.local, so nothing touches the rpm-ostree base image
# and the install survives Bazzite updates. Run with --uninstall to remove it.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"

LAUNCHER="$BIN/apricot-studio"
ENTRY="$APPS/io.github.raulblideran.ApricotStudio.desktop"
ICON="$ICONS/io.github.raulblideran.ApricotStudio.svg"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$LAUNCHER" "$ENTRY" "$ICON"
    update-desktop-database "$APPS" 2>/dev/null || true
    echo "Removed Apricot Studio."
    exit 0
fi

for tool in ffmpeg ffprobe python3; do
    command -v "$tool" >/dev/null || { echo "error: $tool not found" >&2; exit 1; }
done
python3 -c 'import PyQt6.QtMultimediaWidgets' 2>/dev/null || {
    echo "error: PyQt6 with QtMultimedia is required" >&2; exit 1; }

mkdir -p "$BIN" "$APPS" "$ICONS"

cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec python3 "$SRC/apricot.py" "\$@"
EOF
chmod +x "$LAUNCHER"

sed "s|@EXEC@|$LAUNCHER|g" "$SRC/io.github.raulblideran.ApricotStudio.desktop" > "$ENTRY"
chmod +x "$ENTRY"
cp "$SRC/io.github.raulblideran.ApricotStudio.svg" "$ICON"

update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Installed Apricot Studio."
echo "  launcher : $LAUNCHER"
echo "  menu     : $ENTRY"
echo
echo "Launch it from the app menu, or right-click a video in Dolphin -> Open With."
case ":$PATH:" in
    *":$BIN:"*) echo "You can also just run: apricot-studio /path/to/video.mp4" ;;
    *) echo "Note: $BIN is not on your PATH, so run it as: $LAUNCHER video.mp4" ;;
esac
