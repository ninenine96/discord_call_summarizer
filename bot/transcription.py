"""Transcription service using faster-whisper (local CTranslate2 Whisper)."""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel

log = logging.getLogger(__name__)


class TranscriptionService:
    """Runs OpenAI Whisper locally via faster-whisper (CTranslate2).

    The model is downloaded once on first use and cached automatically.
    Runs inference in a thread pool to avoid blocking the event loop.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        log.info(
            "Loading Whisper model '%s' (device=%s, compute=%s)…",
            model_size, device, compute_type,
        )
        self.model = WhisperModel(
            model_size, device=device, compute_type=compute_type,
        )
        log.info("Whisper model loaded.")

    def _transcribe_sync(self, wav_bytes: bytes) -> str:
        """Blocking transcription — called in a thread."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            segments, _info = self.model.transcribe(
                tmp.name, beam_size=5, vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments)

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio bytes and return the text."""
        try:
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                None, self._transcribe_sync, wav_bytes
            )
            return text.strip()
        except Exception:
            log.exception("Transcription failed")
            return ""
