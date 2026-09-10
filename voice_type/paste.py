"""Clipboard copy + simulated paste into whichever window has focus.

xdotool sends a normal Ctrl+V to the focused X11 window, so this works in
any application that supports pasting (editors, browsers, terminals, Slack,
etc.) without those applications needing to know anything about dictation.
"""

import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".local" / "share" / "voice-type"
ACTIVE_WINDOW_FILE = DATA_DIR / "active_window.id"


def save_active_window() -> None:
    """Record whichever window has focus right now (called before the
    status bubble is shown), so it can be restored just before pasting --
    a safety net against focus ever drifting during dictation."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, check=True, timeout=1,
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE_WINDOW_FILE.write_text(result.stdout.strip())
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("could not capture active window: %s", exc)


def _restore_active_window() -> None:
    if not ACTIVE_WINDOW_FILE.exists():
        return
    window_id = ACTIVE_WINDOW_FILE.read_text().strip()
    ACTIVE_WINDOW_FILE.unlink(missing_ok=True)
    if not window_id:
        return
    try:
        subprocess.run(
            ["xdotool", "windowactivate", "--sync", window_id],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("could not restore active window %s: %s", window_id, exc)


def copy_to_clipboard(text: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"),
        check=True,
    )


def paste_at_cursor(delay: float = 0.3) -> None:
    """Restore focus to the original window, then send Ctrl+V."""
    _restore_active_window()
    time.sleep(delay)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True)


def copy_and_paste(text: str) -> None:
    if not text:
        log.info("empty transcription, nothing to paste")
        return
    copy_to_clipboard(text)
    paste_at_cursor()
