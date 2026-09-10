"""Short start/stop beep cues.

Rather than rely on a system sound theme file (whose actual character --
chime, drip, click -- varies by theme and is easy to get wrong), we
synthesize two plain sine-wave beeps ourselves: higher-pitched for start,
lower-pitched for stop. This needs no bundled audio asset, just a couple
dozen lines of stdlib code, and the two are cached as small WAV files after
the first run.
"""

import logging
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".local" / "share" / "voice-type"
SOUND_DIR = DATA_DIR / "sounds"

SAMPLE_RATE = 44100
DURATION_SECONDS = 0.15
VOLUME = 0.4

START_FREQUENCY_HZ = 880  # higher beep, "recording started"
STOP_FREQUENCY_HZ = 440  # lower beep, "recording stopped"

START_FILE = SOUND_DIR / "start_beep.wav"
STOP_FILE = SOUND_DIR / "stop_beep.wav"


def _generate_beep(path: Path, frequency: float) -> None:
    n_samples = int(SAMPLE_RATE * DURATION_SECONDS)
    fade_samples = max(1, int(SAMPLE_RATE * 0.01))  # 10ms fade in/out, avoids a click

    frames = bytearray()
    for i in range(n_samples):
        envelope = min(1.0, i / fade_samples, (n_samples - i) / fade_samples)
        sample = VOLUME * envelope * math.sin(2 * math.pi * frequency * i / SAMPLE_RATE)
        frames += struct.pack("<h", int(sample * 32767))

    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(bytes(frames))


def _ensure_beep_files() -> None:
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    if not START_FILE.exists():
        _generate_beep(START_FILE, START_FREQUENCY_HZ)
    if not STOP_FILE.exists():
        _generate_beep(STOP_FILE, STOP_FREQUENCY_HZ)


def _play(path: Path) -> None:
    player = shutil.which("paplay") or shutil.which("aplay")
    if not player:
        log.warning("no audio player (paplay/aplay) found, skipping sound")
        return
    try:
        subprocess.Popen(
            [player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError as exc:
        log.warning("failed to play sound %s: %s", path, exc)


def play_start() -> None:
    _ensure_beep_files()
    _play(START_FILE)


def play_stop() -> None:
    _ensure_beep_files()
    _play(STOP_FILE)
