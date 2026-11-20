#!/usr/bin/env bash
# Install DreamON for the current user: the GNOME Shell extension and the companion app.
#   ./install.sh            both
#   ./install.sh extension  extension only
#   ./install.sh app        app only
set -euo pipefail

UUID="dreamon@sgakhil.github.io"
APP_ID="io.github.sgakhil.DreamON"
SRC="$(cd "$(dirname "$0")" && pwd)"
WHAT="${1:-all}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

install_extension() {
    local dest="$DATA/gnome-shell/extensions/$UUID"
    mkdir -p "$dest/schemas" "$dest/icons"
    cp "$SRC"/metadata.json "$SRC"/extension.js "$SRC"/prefs.js "$SRC"/stylesheet.css "$dest/"
    cp "$SRC"/icons/dreamon-symbolic.svg "$dest/icons/"
    cp "$SRC"/schemas/*.gschema.xml "$dest/schemas/"
    glib-compile-schemas "$dest/schemas"
    echo "Extension installed in $dest"
}

install_app() {
    local appdir="$DATA/dreamon-app"
    local bin="$HOME/.local/bin/dreamon-app"
    mkdir -p "$appdir" "$HOME/.local/bin" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
    cp "$SRC"/app/dreamon_app.py "$SRC"/app/enso.py "$SRC"/app/stats.py "$SRC"/app/zotero.py "$appdir/"
    printf '#!/bin/sh\nexec python3 "%s/dreamon_app.py" "$@"\n' "$appdir" > "$bin"
    chmod +x "$bin"
    sed "s|@EXEC@|$bin|" "$SRC/app/$APP_ID.desktop" > "$DATA/applications/$APP_ID.desktop"
    cp "$SRC/icons/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
    gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
    update-desktop-database -q "$DATA/applications" 2>/dev/null || true
    echo "App installed: open DreamON from the app grid, or run dreamon-app"
}

case "$WHAT" in
    extension) install_extension ;;
    app) install_app ;;
    all) install_extension; install_app ;;
    *) echo "usage: $0 [extension|app|all]"; exit 1 ;;
esac

if [ "$WHAT" != app ]; then
    if gnome-extensions info "$UUID" >/dev/null 2>&1; then
        gnome-extensions enable "$UUID" || true
        echo "Log out and back in to load the new extension code."
    else
        echo "Log out and back in (Wayland can't reload the shell), then run:"
        echo "  gnome-extensions enable $UUID"
    fi
fi
