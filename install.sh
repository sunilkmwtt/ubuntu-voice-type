#!/bin/bash
# Installs Voice Type: local Ubuntu/X11 voice dictation.
#
# What this does:
#   1. Installs required apt packages (asks for sudo).
#   2. Builds an isolated venv + installs this package into it under
#      ~/.local/share/voice-type -- independent of its location that is in authors Github.
#   3. Symlinks the `voice-type` command into ~/.local/bin.
#   4. Registers a GNOME custom keyboard shortcut (Ctrl+Alt+Space), without
#      touching any other custom shortcuts you already have.
set -euo pipefail

REPO_URL="https://github.com/sunilkmwtt/ubuntu-voice-type.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || pwd)"

if [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/voice_type" ]; then
    # Running from an actual checkout (e.g. `git clone` then `./install.sh`).
    REPO_DIR="$SCRIPT_DIR"
else
    # Running standalone (e.g. `curl -fsSL .../install.sh | bash`) -- fetch
    # the source into a throwaway directory. Nothing here depends on this
    # directory afterwards: the venv + package end up under $INSTALL_DIR,
    # so this clone can be safely deleted once the install finishes.
    echo "==> Fetching Voice Type source"
    command -v git >/dev/null 2>&1 || { echo "git is required. Install it first: sudo apt install git" >&2; exit 1; }
    REPO_DIR="$(mktemp -d)"
    trap 'rm -rf "$REPO_DIR"' EXIT
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

INSTALL_DIR="$HOME/.local/share/voice-type"
BIN_DIR="$HOME/.local/bin"
BIN_LINK="$BIN_DIR/voice-type"

SCHEMA_BASE="org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voicetype/"
CUSTOM_URI="${SCHEMA_BASE}.custom-keybinding:${CUSTOM_PATH}"

echo "==> Voice Type installer"

if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    echo "Warning: this looks like a Wayland session. Voice Type v1 targets" >&2
    echo "         X11 only (xdotool paste does not work reliably on Wayland)." >&2
fi

echo "==> Checking for interrupted package installs"
# A previously interrupted apt/dpkg run (power loss, Ctrl+C mid-install,
# etc.) leaves packages half-configured, and every apt-get afterwards fails
# until this is repaired. Safe to run even when nothing is broken.
if ! sudo dpkg --configure -a; then
    echo "" >&2
    echo "ERROR: dpkg has interrupted/broken packages that could not be repaired" >&2
    echo "automatically. Fix this first, then re-run this installer:" >&2
    echo "  sudo dpkg --configure -a" >&2
    exit 1
fi

echo "==> Installing system packages (sudo required)"
# Tolerate failures from unrelated third-party apt repos already configured
# on this machine (e.g. broken PPAs) -- we only need the official Ubuntu
# archive, which `apt-get install` will still use even if `update` reports
# errors elsewhere.
sudo apt-get update || echo "Note: apt-get update reported errors above from some repositories; continuing anyway."
sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-tk \
    python3-gi \
    python3-pyatspi \
    gir1.2-atspi-2.0 \
    at-spi2-core \
    alsa-utils \
    ffmpeg \
    xclip \
    xdotool \
    pulseaudio-utils

echo "==> Enabling accessibility support (needed for caret-position detection)"
# GTK apps (GNOME Text Editor, gedit, GNOME Terminal, ...) only publish caret
# position/color over AT-SPI when this is on. It's the standard system
# accessibility toggle -- harmless to leave on, but note it here since it's
# a system-wide setting change, not something private to this app. Apps
# already running need to be restarted to pick it up; new ones will use it
# automatically.
gsettings set org.gnome.desktop.interface toolkit-accessibility true || true

echo "==> Building virtual environment at $INSTALL_DIR/venv"
mkdir -p "$INSTALL_DIR"
# --system-site-packages: python3-pyatspi/python3-gi are GObject-Introspection
# bindings tied to system libraries -- they can't be pip-installed into an
# isolated venv, so the venv inherits them from the system Python instead.
python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "==> Installing voice-type package"
"$INSTALL_DIR/venv/bin/pip" install "$REPO_DIR"

echo "==> Creating command: $BIN_LINK"
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/venv/bin/voice-type" "$BIN_LINK"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo ""
        echo "Note: $BIN_DIR is not on your PATH."
        echo "Add this to your ~/.bashrc (or ~/.zshrc), then open a new terminal:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        ;;
esac

echo "==> Configuring keyboard shortcut (Ctrl+Alt+Space)"
# Check the exact schemas exist before touching them -- `gsettings` can be
# present (e.g. on a minimal/non-GNOME X11 setup) while the GNOME
# media-keys schemas it needs are not installed, which would otherwise
# fail with a "No such schema" error instead of falling back cleanly.
SCHEMAS_OK=false
if command -v gsettings >/dev/null 2>&1 \
    && gsettings list-schemas | grep -qx "$SCHEMA_BASE" \
    && gsettings list-relocatable-schemas | grep -qx "$KEYBINDING_SCHEMA"; then
    SCHEMAS_OK=true
fi

SHORTCUT_DONE=false
if [ "$SCHEMAS_OK" = true ]; then
    # Wrapped in this `if` (rather than run as plain top-level commands) so
    # that a failure here -- e.g. a transient dbus/gsettings error -- can't
    # abort the whole installer via `set -e`. The package/command install
    # above has already succeeded at this point regardless of what happens
    # to the shortcut.
    if UPDATED_LIST=$(python3 - "$SCHEMA_BASE" "$CUSTOM_PATH" <<'PYEOF'
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

if path not in current:
    current.append(path)

print(repr(current))
PYEOF
        ) \
        && gsettings set "$SCHEMA_BASE" custom-keybindings "$UPDATED_LIST" \
        && gsettings set "$CUSTOM_URI" name "Voice Type" \
        && gsettings set "$CUSTOM_URI" command "$BIN_LINK" \
        && gsettings set "$CUSTOM_URI" binding "<Control><Alt>space"; then
        echo "Shortcut configured: Ctrl+Alt+Space -> voice-type"
        SHORTCUT_DONE=true
    else
        echo "Warning: automatic shortcut registration failed partway through." >&2
    fi
fi

if [ "$SHORTCUT_DONE" != true ]; then
    cat <<EOF
Automatic shortcut setup is unavailable on this system (required GNOME
schema not found, or registration failed). Configure it manually:
Settings > Keyboard > View and Customize Shortcuts > Custom Shortcuts:
  Name:     Voice Type
  Command:  $BIN_LINK
  Shortcut: Ctrl+Alt+Space
Use that exact command path -- it must be the full absolute path shown
above, since GNOME's shortcut dialog does not expand "~".
EOF
fi

echo ""
echo "==> Done."
echo "Press Ctrl+Alt+Space to start dictation, speak, then press it again to"
echo "stop, transcribe, and paste into the focused application."
echo "(If the shortcut doesn't fire right away, log out and back in.)"
echo ""
echo "Note: the status bubble positions itself using the real text-caret"
echo "location where possible (GNOME Text Editor, gedit, GNOME Terminal)."
echo "Already-running apps must be restarted once to pick up accessibility"
echo "support. VS Code and Chrome/Google Docs do not expose caret info to"
echo "the system accessibility API, so the bubble falls back to the active"
echo "window's position there -- see README for details."
