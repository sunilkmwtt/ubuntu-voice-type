"""Microphone recording via arecord, toggled with a PID file.

Uses fixed filenames (not a new file per recording) so the rest of the
pipeline never has to search for "the latest" recording -- it always reads
the one known current file.
"""

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "voice-type"
AUDIO_FILE = DATA_DIR / "dictation.wav"
PID_FILE = DATA_DIR / "recording.pid"

log = logging.getLogger(__name__)


def is_recording() -> bool:
    """True if a recording is in progress. Cleans up a stale PID file."""
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return False


def start_recording() -> int:
    """Start arecord in the background and record its PID."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_FILE.unlink(missing_ok=True)

    proc = subprocess.Popen(
        [
            "arecord",
            "-D", "default",
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            str(AUDIO_FILE),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    PID_FILE.write_text(str(proc.pid))
    log.info("recording started (pid=%s) -> %s", proc.pid, AUDIO_FILE)
    return proc.pid


def stop_recording(timeout: float = 3.0) -> bool:
    """Stop the active recording gracefully (SIGINT, like Ctrl+C) so arecord
    finalizes the WAV header. Returns True if a recording was stopped."""
    if not PID_FILE.exists():
        return False

    pid = int(PID_FILE.read_text().strip())
    log.info("stopping recording (pid=%s)", pid)

    try:
        os.kill(pid, signal.SIGINT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
    except ProcessLookupError:
        pass
    finally:
        PID_FILE.unlink(missing_ok=True)

    return True
