#!/usr/bin/env bash
# Install TExporT for the current user: Ubuntu 22.04+ (apt) or Arch / Omarchy (pacman).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/.local/share/texport/app"
BIN="$HOME/.local/bin"

echo "== System packages (needs your password)"
if command -v pacman >/dev/null 2>&1; then
  # Arch / Omarchy (Hyprland): grim captures the screen, ydotool turns the wheel for auto-scroll
  # -Syu: sync the package database first (a fresh install has none) and stay fully upgraded, as Arch requires
  sudo pacman -Syu --needed --noconfirm tesseract tesseract-data-eng python python-gobject pyside6 \
    gstreamer gst-plugins-base gst-plugin-pipewire grim ydotool xdotool
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user enable --now ydotool 2>/dev/null \
      || echo "Note: start the ydotool service for auto-scroll (see README → Omarchy / Hyprland)."
  fi
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -q
  sudo apt-get install -y tesseract-ocr tesseract-ocr-eng python3-gi \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-pipewire \
    xdotool    # auto-scroll on X11 sessions (Wayland uses the remote-interaction portal instead)
  echo "== Qt for Python (PySide6)"
  if ! python3 -c "import PySide6.QtWidgets" 2>/dev/null; then
    sudo apt-get install -y python3-pyside6.qtwidgets python3-pyside6.qtgui python3-pyside6.qtcore 2>/dev/null \
    || python3 -m pip install --user PySide6-Essentials 2>/dev/null \
    || python3 -m pip install --user --break-system-packages PySide6-Essentials \
    || echo "PySide6 not installed — TExporT will fall back to PyQt6 if present (fine for local testing)."
  fi
else
  echo "Unsupported package manager. Install: tesseract (+English data), Python 3 with PySide6 and"
  echo "PyGObject, GStreamer with the PipeWire plugin; plus grim+ydotool (Hyprland) or xdotool (X11)."
fi

echo "== Copying app"
mkdir -p "$APP" "$BIN" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
rm -rf "$APP/texport"
cp -r "$HERE/texport" "$APP/"
cp "$HERE/snap/gui/texport.png" "$HOME/.local/share/icons/hicolor/256x256/apps/texport.png"

cat > "$BIN/texport" <<EOF
#!/bin/sh
PYTHONPATH="$APP\${PYTHONPATH:+:\$PYTHONPATH}" exec /usr/bin/python3 -m texport "\$@"
EOF
chmod +x "$BIN/texport"

sed -e "s|^Exec=.*|Exec=$BIN/texport|" -e "s|^Icon=.*|Icon=texport|" \
  "$HERE/snap/gui/texport.desktop" > "$HOME/.local/share/applications/texport.desktop"

# Retire the earlier Convo Capture launcher (its saved captures are imported on first run).
rm -f "$HOME/.local/share/applications/convo-capture.desktop"

echo
echo "Installed. Open 'TExporT' from the app menu, or run: $BIN/texport"
