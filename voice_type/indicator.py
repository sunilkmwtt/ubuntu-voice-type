"""Small floating status text, anchored to the real text caret.

Position: obtained from the accessibility tree (AT-SPI) of the active
application -- specifically the focused widget's Text interface, which
exposes the caret's actual on-screen coordinates. This works well for GTK
apps (GNOME Text Editor, gedit, GNOME Terminal) but Electron/Chromium apps
(VS Code, Chrome, Google Docs) do not populate a usable accessibility tree
for a passive client like this one, even though they register on the
AT-SPI bus by name -- their focused-widget/text info stays empty. When
that happens we do NOT guess from the mouse position, and we do NOT scan
the screen for a blinking cursor; we fall back to the bottom-center of the
active monitor instead, and log which mode was used.

Style: plain italic light-grey text, no drawn border -- a subtle
thought-indicator look rather than a notification bubble. Tk can't give a
truly invisible background with fully-opaque text (whole-window "-alpha"
blends everything uniformly), so a low alpha is used just to keep the text
legible without looking like a filled box. The window's corners are
rounded via a static X11 SHAPE mask computed from plain geometry and
applied once before the window is ever shown -- unlike the fully
transparent background attempted earlier (which needed a screenshot-based
mask recomputed after peeking the window open once, and was flaky on this
compositor), this shape never changes and never needs a second reveal, so
it isn't subject to the same timing issue.

Runs as its own subprocess, same as before, so hide() is just "kill the
process." The window never asks for focus and is never interacted with.
"""

import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "voice-type"
PID_FILE = DATA_DIR / "indicator.pid"
TEXT_FILE = DATA_DIR / "indicator_text.txt"

WIDTH, HEIGHT = 108, 24
GAP = 8  # px between the caret and the bubble
POLL_INTERVAL_SECONDS = 0.12

CARET_LOOKUP_ATTEMPTS = 4
CARET_LOOKUP_RETRY_DELAY = 0.06  # seconds; AT-SPI's focus state can lag briefly right after a focus change

TEXT_COLOR = "#cccccc"  # fixed light grey, per design -- no longer caret-color-matched
FONT = ("Ubuntu", 10, "italic")
CORNER_RADIUS = 8

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Caret position + color, via AT-SPI
# --------------------------------------------------------------------------

def _find_focused_in_active_app(desktop):
    """Search only the active application's active frame for a focused
    descendant -- far cheaper and less ambiguous than walking every app."""
    for app in desktop:
        try:
            n = app.childCount
        except Exception:
            continue
        for i in range(n):
            try:
                frame = app.getChildAtIndex(i)
            except Exception:
                continue
            if frame is None:
                continue
            try:
                active = frame.getState().contains(_pyatspi().STATE_ACTIVE)
            except Exception:
                active = False
            if not active:
                continue
            found = _find_focused(frame)
            if found is not None:
                return found
    return None


def _find_focused(node, _depth=0):
    if _depth > 40:  # guard against any unexpectedly deep/cyclic tree
        return None
    try:
        if node.getState().contains(_pyatspi().STATE_FOCUSED):
            return node
    except Exception:
        pass
    try:
        n = node.childCount
    except Exception:
        return None
    for i in range(n):
        try:
            child = node.getChildAtIndex(i)
        except Exception:
            continue
        if child is None:
            continue
        result = _find_focused(child, _depth + 1)
        if result is not None:
            return result
    return None


_pyatspi_module = None


def _pyatspi():
    global _pyatspi_module
    if _pyatspi_module is None:
        import pyatspi

        _pyatspi_module = pyatspi
    return _pyatspi_module


def _parse_fg_color(attrs) -> str | None:
    for attr in attrs:
        if attr.startswith("fg-color:"):
            try:
                r, g, b = (int(v) for v in attr.split(":", 1)[1].split(","))
                # AT-SPI reports 16-bit-per-channel values.
                return "#%02x%02x%02x" % (r // 257, g // 257, b // 257)
            except (ValueError, TypeError):
                return None
    return None


def get_caret_geometry():
    """Returns (x, y, width, height, fg_color_or_None) in real screen
    coordinates for the focused text caret, or None if it can't be
    determined (accessibility not available, or the focused app doesn't
    expose usable caret info -- notably Electron/Chromium apps).

    Retries a few times with a short delay: right after a focus change
    (e.g. the user just clicked into a field and immediately hit the
    shortcut), AT-SPI's own focused/active state can briefly lag behind
    reality, causing a lookup that would otherwise succeed to report
    nothing focused. Confirmed intermittent in practice, not a one-off."""
    for attempt in range(1, CARET_LOOKUP_ATTEMPTS + 1):
        result = _query_caret_once()
        if result is not None:
            return result
        if attempt < CARET_LOOKUP_ATTEMPTS:
            time.sleep(CARET_LOOKUP_RETRY_DELAY)
    return None


def _query_caret_once():
    try:
        pyatspi = _pyatspi()
    except ImportError as exc:
        log.warning("pyatspi not available, cannot use AT-SPI caret detection: %s", exc)
        return None

    try:
        desktop = pyatspi.Registry.getDesktop(0)
    except Exception as exc:
        log.warning("AT-SPI desktop unavailable: %s", exc)
        return None

    focused = _find_focused_in_active_app(desktop)
    if focused is None:
        log.info("AT-SPI: no focused accessible object found in the active app")
        return None

    try:
        text_iface = focused.queryText()
    except NotImplementedError:
        log.info("AT-SPI: focused object has no Text interface (role=%s)", focused.getRoleName())
        return None
    except Exception as exc:
        log.warning("AT-SPI: queryText() failed: %s", exc)
        return None

    try:
        offset = text_iface.caretOffset
        x, y, w, h = text_iface.getCharacterExtents(offset, pyatspi.DESKTOP_COORDS)
    except Exception as exc:
        log.warning("AT-SPI: caret extents query failed: %s", exc)
        return None

    if h <= 0:
        log.info("AT-SPI: caret extents degenerate (h=%s), treating as unavailable", h)
        return None

    fg_color = None
    try:
        attrs = text_iface.getAttributeRun(offset, True)[0]
        fg_color = _parse_fg_color(attrs)
    except Exception as exc:
        log.debug("AT-SPI: text attribute run unavailable: %s", exc)

    return x, y, w, h, fg_color


# --------------------------------------------------------------------------
# Monitor-aware placement
# --------------------------------------------------------------------------

_MONITOR_RE = re.compile(r"(\d+)x(\d+)\+(\d+)\+(\d+)")


def _get_monitors():
    """List of (x, y, width, height) for each connected+active monitor."""
    monitors = []
    try:
        result = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, check=True, timeout=1,
        )
        for line in result.stdout.splitlines():
            if " connected" not in line:
                continue
            match = _MONITOR_RE.search(line)
            if match:
                w, h, x, y = (int(v) for v in match.groups())
                monitors.append((x, y, w, h))
    except Exception as exc:
        log.warning("could not read monitor layout via xrandr: %s", exc)
    return monitors


def _get_screen_size():
    try:
        result = subprocess.run(
            ["xdotool", "getdisplaygeometry"],
            capture_output=True, text=True, check=True, timeout=1,
        )
        width, height = result.stdout.strip().split()
        return int(width), int(height)
    except Exception as exc:
        log.warning("could not read screen geometry, assuming 1920x1080: %s", exc)
        return 1920, 1080


def _monitor_bounds_for_point(px, py):
    for (mx, my, mw, mh) in _get_monitors():
        if mx <= px < mx + mw and my <= py < my + mh:
            return mx, my, mw, mh
    screen_w, screen_h = _get_screen_size()
    return 0, 0, screen_w, screen_h


def _bubble_position(caret_x, caret_y, caret_w, caret_h):
    """Top-left corner for the bubble: centered above the caret with a
    small gap, dropping below it if there isn't room above, clamped to the
    monitor that actually contains the caret."""
    mon_x, mon_y, mon_w, mon_h = _monitor_bounds_for_point(caret_x, caret_y)

    x = caret_x + caret_w // 2 - WIDTH // 2
    y = caret_y - HEIGHT - GAP
    if y < mon_y:
        y = caret_y + caret_h + GAP  # not enough room above -> place below instead

    x = max(mon_x, min(x, mon_x + mon_w - WIDTH))
    y = max(mon_y, min(y, mon_y + mon_h - HEIGHT))
    return x, y


FALLBACK_BOTTOM_MARGIN = 60  # px above the bottom edge, per spec's 50-70 range


def _fallback_position():
    """Bottom-center of the active monitor, used only when AT-SPI can't
    supply a real caret position. Deliberately NOT mouse-based and NOT a
    screen scan for a blinking cursor -- "active monitor" is determined
    from the focused window's location instead."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
            capture_output=True, text=True, check=True, timeout=1,
        )
        values = dict(
            line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line
        )
        win_x, win_y = int(values["X"]), int(values["Y"])
    except Exception as exc:
        log.warning("fallback: could not read active window geometry: %s", exc)
        win_x, win_y = None, None

    if win_x is None:
        screen_w, screen_h = _get_screen_size()
        mon_x, mon_y, mon_w, mon_h = 0, 0, screen_w, screen_h
    else:
        mon_x, mon_y, mon_w, mon_h = _monitor_bounds_for_point(win_x, win_y)

    x = mon_x + mon_w // 2 - WIDTH // 2
    y = mon_y + mon_h - FALLBACK_BOTTOM_MARGIN - HEIGHT
    return x, y


# --------------------------------------------------------------------------
# Public API (called from the main voice-type process)
# --------------------------------------------------------------------------

def _write_text(text: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TEXT_FILE.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(TEXT_FILE)  # atomic, so the poller never reads a half-written file


def _read_text() -> str:
    try:
        return TEXT_FILE.read_text()
    except OSError:
        return ""


def show_listening() -> None:
    """Create the bubble, anchored to the real text caret when possible."""
    caret = get_caret_geometry()
    if caret is not None:
        caret_x, caret_y, caret_w, caret_h, _fg_color = caret
        x, y = _bubble_position(caret_x, caret_y, caret_w, caret_h)
        log.info("indicator: using real caret position (%s, %s)", caret_x, caret_y)
    else:
        x, y = _fallback_position()
        log.warning(
            "indicator: caret position unavailable via accessibility API "
            "(common for VS Code / Chrome / Google Docs); placing indicator "
            "at the bottom-center of the active monitor instead"
        )

    _write_text("Listening....")
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_type.indicator", "--run", str(x), str(y), str(WIDTH), str(HEIGHT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    PID_FILE.write_text(str(proc.pid))


def show_processing() -> None:
    """Update the existing bubble -- no new position/color, just new text."""
    _write_text("Processing....")


def show_error(message: str) -> None:
    """Update the existing bubble to show a short error message."""
    _write_text(message)


def hide() -> None:
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError):
        pass
    finally:
        PID_FILE.unlink(missing_ok=True)
        TEXT_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Subprocess side: draw the bubble (single persistent window, text updated
# in place -- no recreation needed since this uses whole-window alpha, not
# a shape mask, so there's no first-reveal timing sensitivity to work around)
# --------------------------------------------------------------------------

def _rounded_rect_mask(width: int, height: int, radius: int):
    """2D bool grid: True where a pixel lies inside a rounded rectangle.
    Pure geometry, no rendering involved -- this is what makes it safe to
    apply before the window is ever shown (see _apply_rounded_corners)."""
    r2 = radius * radius
    rows = []
    for yy in range(height):
        row = []
        for xx in range(width):
            cx = radius if xx < radius else (width - radius - 1 if xx >= width - radius else None)
            cy = radius if yy < radius else (height - radius - 1 if yy >= height - radius else None)
            if cx is None or cy is None:
                inside = True  # not in a corner region at all
            else:
                inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= r2
            row.append(inside)
        rows.append(row)
    return rows


def _pack_bitmap(mask_rows, width: int, height: int, scanline_pad_bits: int) -> bytes:
    """Pack a 2D bool mask into X11's XYBitmap wire format: LSB-first bits
    per byte, each scanline padded to `scanline_pad_bits` (PIL-style byte-only
    padding silently mismatches the server's expected scanline length for
    widths that aren't a multiple of 32, causing a BadLength error)."""
    pad_bytes = scanline_pad_bits // 8
    row_bytes = (width + 7) // 8
    padded_row_bytes = ((row_bytes + pad_bytes - 1) // pad_bytes) * pad_bytes

    out = bytearray(padded_row_bytes * height)
    for yy in range(height):
        row = mask_rows[yy]
        row_offset = yy * padded_row_bytes
        for xx in range(width):
            if row[xx]:
                out[row_offset + xx // 8] |= 1 << (xx % 8)
    return bytes(out)


def _apply_rounded_corners(root, width: int, height: int) -> None:
    """Clip the window to a rounded rectangle via the X11 SHAPE extension.

    Unlike the fully-transparent-background attempt (which needed a
    screenshot-based mask recomputed after a first opaque reveal, and was
    found unreliable on this compositor -- see git history), this shape is
    static and computed purely from geometry, and is applied once while the
    window is still withdrawn, before it's ever shown. That "set shape
    before first reveal, no second reveal" pattern was the one case that
    worked reliably in testing, since there's no compositor-timing race to
    hit. Best-effort: falls back to square corners if anything goes wrong.
    """
    try:
        from Xlib.display import Display
        from Xlib.ext import shape as xshape
        from Xlib import X

        display = Display()
        xwin = display.create_resource_object("window", root.winfo_id())
        mask_rows = _rounded_rect_mask(width, height, CORNER_RADIUS)
        scanline_pad = display.display.info.bitmap_format_scanline_pad
        packed = _pack_bitmap(mask_rows, width, height, scanline_pad)

        pixmap = xwin.create_pixmap(width, height, 1)
        gc = pixmap.create_gc(foreground=1, background=0)
        pixmap.put_image(gc, 0, 0, width, height, X.XYBitmap, 1, 0, packed)
        display.sync()
        xwin.shape_mask(xshape.SO.Set, xshape.SK.Bounding, 0, 0, pixmap)
        display.sync()
    except Exception as exc:
        log.warning("rounded-corner shaping failed, using square corners: %s", exc)


def _run_window(x: int, y: int, width: int, height: int) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()  # stay hidden until the rounded-corner shape is applied
    root.overrideredirect(True)  # no title bar / border / taskbar entry
    root.attributes("-topmost", True)
    try:
        root.attributes("-type", "notification")  # hint: not a normal app window
    except tk.TclError:
        pass
    try:
        # Whole-window translucency -- the only transparency vanilla Tk can
        # do reliably on this compositor (a true see-through background was
        # tested via the X11 SHAPE extension and found to work only ~50% of
        # the time here even with retries). Kept low so this reads as
        # floating text, not a filled box -- just enough tint for the fixed
        # light-grey text to stay legible.
        root.attributes("-alpha", 0.45)
    except tk.TclError:
        pass
    root.geometry(f"{width}x{height}+{x}+{y}")

    bg = "#000000"
    root.configure(bg=bg)
    label = tk.Label(root, text=_read_text(), fg=TEXT_COLOR, bg=bg, font=FONT)
    label.pack(fill="both", expand=True)

    _apply_rounded_corners(root, width, height)
    root.deiconify()

    def poll() -> None:
        label.config(text=_read_text())
        root.after(int(POLL_INTERVAL_SECONDS * 1000), poll)

    root.after(int(POLL_INTERVAL_SECONDS * 1000), poll)
    root.mainloop()


if __name__ == "__main__":
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        _x, _y, _w, _h = (int(v) for v in sys.argv[i + 1:i + 5])
        _run_window(_x, _y, _w, _h)
