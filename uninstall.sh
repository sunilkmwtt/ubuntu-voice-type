#!/bin/bash
# Removes everything install.sh created: the venv/app data, the `voice-type`
# command, and the Ctrl+Alt+Space shortcut. Does not touch apt packages or
# any other GNOME shortcuts.
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/voice-type"
BIN_LINK="$HOME/.local/bin/voice-type"

SCHEMA_BASE="org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicetype/"
CUSTOM_URI="${SCHEMA_BASE}.custom-keybinding:${CUSTOM_PATH}"

echo "==> Uninstalling Voice Type"

# Stop an in-progress recording, if any, before removing its files.
if [ -f "$INSTALL_DIR/recording.pid" ]; then
    kill -INT "$(cat "$INSTALL_DIR/recording.pid")" 2>/dev/null || true
fi
if [ -f "$INSTALL_DIR/indicator.pid" ]; then
    kill -TERM "$(cat "$INSTALL_DIR/indicator.pid")" 2>/dev/null || true
fi

if command -v gsettings >/dev/null 2>&1; then
    echo "==> Removing keyboard shortcut"
    UPDATED_LIST=$(python3 - "$SCHEMA_BASE" "$CUSTOM_PATH" <<'PYEOF'
import ast
import subprocess
import sys

schema, path = sys.argv[1], sys.argv[2]
out = subprocess.run(
    ["gsettings", "get", schema, "custom-keybindings"],
    capture_output=True, text=True, check=True,
).stdout.strip()

try:
    current = ast.literal_eval(out)
except (ValueError, SyntaxError):
    current = []

print(repr([p for p in current if p != path]))
PYEOF
    )
    gsettings set "$SCHEMA_BASE" custom-keybindings "$UPDATED_LIST"
    gsettings reset-recursively "$CUSTOM_URI" 2>/dev/null || true
fi

echo "==> Removing command: $BIN_LINK"
rm -f "$BIN_LINK"

echo "==> Removing $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

echo "==> Voice Type uninstalled. (apt packages were left in place.)"
