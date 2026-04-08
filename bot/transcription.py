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
        import time as _time

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            t0 = _time.monotonic()
            segments, info = self.model.transcribe(
                tmp.name, beam_size=5, vad_filter=True,
            )
            parts = []
            for seg in segments:
                log.debug(
                    "  segment [%.1fs → %.1fs]: %s",
                    seg.start, seg.end, seg.text.strip(),
                )
                parts.append(seg.text.strip())
            elapsed = _time.monotonic() - t0
            text = " ".join(parts)
            log.info(
                "Transcribed %.1fs audio → %d chars in %.2fs (lang=%s, prob=%.2f)",
                info.duration, len(text), elapsed,
                info.language, info.language_probability,
            )
            return text

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio bytes and return the text."""
        log.info("Transcription requested for %d bytes of WAV audio", len(wav_bytes))
        try:
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                None, self._transcribe_sync, wav_bytes
            )
            if text:
                log.info("Transcription result (%d chars): %.100s…", len(text), text)
            else:
                log.warning("Transcription returned empty text")
            return text.strip()
        except Exception:
            log.exception("Transcription failed for %d bytes of audio", len(wav_bytes))
            return ""
