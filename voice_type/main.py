"""Entry point for the `voice-type` command.

Bound to a single global keyboard shortcut: the first press starts
recording, the second press stops it, transcribes, and pastes the result.

    Ctrl+Alt+Space -> voice-type -> [start: sound + "Listening" bubble + arecord]
    Ctrl+Alt+Space -> voice-type -> [stop: sound + bubble -> "Processing"
                                     -> whisper -> clipboard -> paste
                                     -> bubble hidden, or "Could not load"
                                     briefly on failure]
"""

import logging
import time

from . import indicator, paste, recorder, sounds, transcriber

DATA_DIR = recorder.DATA_DIR
LOG_FILE = DATA_DIR / "voice-type.log"

ERROR_DISPLAY_SECONDS = 1.8


def _setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _start() -> None:
    paste.save_active_window()  # preserve focus before the bubble ever appears
    sounds.play_start()
    indicator.show_listening()
    recorder.start_recording()


def _stop() -> None:
    log = logging.getLogger(__name__)

    recorder.stop_recording()
    sounds.play_stop()
    indicator.show_processing()

    try:
        text = transcriber.transcribe(str(recorder.AUDIO_FILE))
        paste.copy_and_paste(text)
    except Exception:
        log.exception("transcription/paste failed")
        indicator.show_error("Could not load")
        time.sleep(ERROR_DISPLAY_SECONDS)
    finally:
        indicator.hide()


def main() -> None:
    _setup_logging()
    log = logging.getLogger(__name__)

    try:
        if recorder.is_recording():
            log.info("toggle: stop")
            _stop()
        else:
            log.info("toggle: start")
            _start()
    except Exception:
        log.exception("voice-type failed")
        raise


if __name__ == "__main__":
    main()
