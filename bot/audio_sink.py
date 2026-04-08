"""Audio sink that captures per-user PCM from a Discord voice channel."""

from __future__ import annotations

import io
import struct
import time
import wave
from collections import defaultdict

import discord


class AudioBuffer:
    """Accumulates raw PCM frames for a single user."""

    def __init__(self, user: discord.Member | discord.User) -> None:
        self.user = user
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
    """A discord.py Sink that stores per-user audio buffers.

    Call `harvest()` to retrieve and reset the accumulated audio.
    """

    def __init__(self) -> None:
        super().__init__()
        self.buffers: dict[int, AudioBuffer] = {}

    def write(self, data: bytes, user: int) -> None:  # type: ignore[override]
        if user not in self.buffers:
            member = self.vc.guild.get_member(user) if self.vc else None
            self.buffers[user] = AudioBuffer(member or user)
        self.buffers[user].write(data)

    def harvest(self) -> dict[int, bytes]:
        """Return {user_id: wav_bytes} for all users, then clear buffers."""
        results: dict[int, bytes] = {}
        for uid, buf in self.buffers.items():
            if buf.frames:
                results[uid] = buf.to_wav_bytes()
                buf.clear()
        return results

    def get_user_display_names(self) -> dict[int, str]:
        names: dict[int, str] = {}
        for uid, buf in self.buffers.items():
            if isinstance(buf.user, (discord.Member, discord.User)):
                names[uid] = buf.user.display_name
            else:
                names[uid] = f"User-{uid}"
        return names

    def cleanup(self) -> None:
        self.buffers.clear()
