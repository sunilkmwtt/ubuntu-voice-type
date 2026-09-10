"""Local speech-to-text using OpenAI Whisper.

Loads the model and transcribes in-process (no CLI subprocess, no
intermediate output file). The model is loaded fresh on every call --
see README "Performance notes" for why, and how a future daemon could
avoid it.
"""

import logging

log = logging.getLogger(__name__)

MODEL_NAME = "base.en"


def transcribe(audio_path: str) -> str:
    """Transcribe an audio file to text. Returns an empty string on failure."""
    import whisper  # imported lazily: this is the slow import (torch et al.)

    log.info("loading whisper model '%s'", MODEL_NAME)
    model = whisper.load_model(MODEL_NAME)

    log.info("transcribing %s", audio_path)
    result = model.transcribe(audio_path, language="en", fp16=False)

    text = result.get("text", "").strip()
    log.info("transcription: %r", text)
    return text
