"""Audio sink that captures per-user PCM from a Discord voice channel."""

from __future__ import annotations

import io
import logging
import time
import wave

import discord

log = logging.getLogger(__name__)


class AudioBuffer:
    """Accumulates raw PCM frames for a single user."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.last_packet_time: float = time.time()

    def write(self, pcm: bytes) -> None:
        self.frames.append(pcm)
        self.last_packet_time = time.time()

    def to_wav_bytes(self) -> bytes:
        """Return a WAV file as bytes from accumulated PCM frames."""
        pcm_data = b"".join(self.frames)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(2)  # discord sends stereo
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(48000)  # 48 kHz
            wf.writeframes(pcm_data)
        return buf.getvalue()

    def duration_seconds(self) -> float:
        total_bytes = sum(len(f) for f in self.frames)
        # stereo, 16-bit, 48kHz  →  192000 bytes/sec
        return total_bytes / 192000

    def clear(self) -> None:
        self.frames.clear()


class CallRecorderSink(discord.sinks.Sink):
    """A pycord Sink that stores per-user audio buffers.

    IMPORTANT: write() is called from the voice-receive thread.
    It must never do guild/member lookups or anything that touches
    the event loop — doing so crashes the receiver silently and
    causes the bot to disconnect after ~10-15 s.

    Member resolution is deferred to harvest-time on the main thread.
    """

    def __init__(self) -> None:
        super().__init__()
        self.buffers: dict[int, AudioBuffer] = {}

    def write(self, data: bytes, user: int) -> None:  # type: ignore[override]
        try:
            if user not in self.buffers:
                self.buffers[user] = AudioBuffer()
                log.debug("New audio buffer created for user %s", user)
            self.buffers[user].write(data)
        except Exception:
            log.exception("Error in sink.write for user %s", user)

    def harvest(self) -> dict[int, bytes]:
        """Return {user_id: wav_bytes} for all users, then clear buffers."""
        results: dict[int, bytes] = {}
        for uid, buf in self.buffers.items():
            if buf.frames:
                duration = buf.duration_seconds()
                results[uid] = buf.to_wav_bytes()
                log.info(
                    "Harvested %.1fs of audio from user %s (%d frames, %d bytes WAV)",
                    duration, uid, len(buf.frames), len(results[uid]),
                )
                buf.clear()
        if not results:
            log.debug("Harvest called but no audio frames found in any buffer")
        else:
            log.info("Harvested audio from %d user(s)", len(results))
        return results

    def get_user_display_names(self) -> dict[int, str]:
        """Resolve display names on the main thread (safe to access guild)."""
        names: dict[int, str] = {}
        guild = self.vc.guild if self.vc else None
        for uid in self.buffers:
            member = guild.get_member(uid) if guild else None
            if member:
                names[uid] = member.display_name
            else:
                log.debug("Could not resolve member for uid %s, using fallback", uid)
                names[uid] = f"User-{uid}"
        log.debug("Resolved display names: %s", names)
        return names

    def cleanup(self) -> None:
        user_count = len(self.buffers)
        self.buffers.clear()
        log.info("Sink cleaned up — cleared %d user buffer(s)", user_count)
