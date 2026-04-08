"""Discord Call Summarizer — main bot entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from bot.audio_sink import CallRecorderSink
from bot.summarization import SummarizationService
from bot.transcription import TranscriptionService

load_dotenv()

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)
log.info("Log level set to %s", LOG_LEVEL)

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
SUMMARY_CHANNEL_ID = int(os.environ.get("SUMMARY_CHANNEL_ID", "0"))
SUMMARY_INTERVAL = int(os.environ.get("SUMMARY_INTERVAL", "300"))  # seconds

# Ollama / model configuration
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3:8b-instruct-q4_K_M")
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")


# ── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Shared services — all local, no cloud API keys needed
transcriber = TranscriptionService(
    model_size=WHISPER_MODEL_SIZE,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE_TYPE,
)
summarizer = SummarizationService(
    model=OLLAMA_MODEL,
    ollama_host=OLLAMA_HOST,
)

# Active session state (one session at a time per guild, keyed by guild id)
active_sessions: dict[int, dict] = {}


# ── Helper: process one summary cycle ────────────────────────────────────────

async def _process_summary(guild_id: int) -> None:
    """Harvest audio, transcribe each user, summarize, and post."""
    session = active_sessions.get(guild_id)
    if not session:
        return

    sink: CallRecorderSink = session["sink"]
    text_channel: discord.TextChannel = session["text_channel"]

    log.info("[guild=%s] Starting summary cycle", guild_id)

    user_names = sink.get_user_display_names()
    audio_chunks = sink.harvest()

    if not audio_chunks:
        log.info("[guild=%s] No audio captured this interval", guild_id)
        return

    log.info(
        "[guild=%s] Harvested audio from %d user(s): %s",
        guild_id, len(audio_chunks),
        ", ".join(f"{user_names.get(uid, uid)}" for uid in audio_chunks),
    )

    # Transcribe each user in parallel
    async def _transcribe_user(uid: int, wav: bytes) -> str:
        name = user_names.get(uid, f"User-{uid}")
        log.info("[guild=%s] Transcribing %s (%d bytes)", guild_id, name, len(wav))
        text = await transcriber.transcribe(wav)
        if text:
            log.info("[guild=%s] %s transcript: %d chars", guild_id, name, len(text))
            return f"**{name}**: {text}"
        log.warning("[guild=%s] %s returned empty transcript", guild_id, name)
        return ""

    results = await asyncio.gather(
        *[_transcribe_user(uid, wav) for uid, wav in audio_chunks.items()]
    )
    transcript_parts = [r for r in results if r]

    if not transcript_parts:
        log.warning("[guild=%s] All transcriptions empty — skipping summary", guild_id)
        return

    full_transcript = "\n".join(transcript_parts)
    log.info(
        "[guild=%s] Full transcript (%d chars, %d speaker(s)):\n%s",
        guild_id, len(full_transcript), len(transcript_parts),
        full_transcript[:500],
    )

    # Summarize
    log.info("[guild=%s] Requesting summarization…", guild_id)
    summary = await summarizer.summarize(full_transcript)

    # Post to text channel
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    embed = discord.Embed(
        title=f"Call Summary — {now}",
        description=summary,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Discord Call Summarizer")
    await text_channel.send(embed=embed)
    log.info("[guild=%s] Posted summary to #%s (%d chars)", guild_id, text_channel.name, len(summary))


# ── Background task: periodic summaries ──────────────────────────────────────

@tasks.loop(seconds=SUMMARY_INTERVAL)
async def summary_loop() -> None:
    for guild_id in list(active_sessions):
        try:
            await _process_summary(guild_id)
        except Exception:
            log.exception("Error in summary loop for guild %s", guild_id)


# ── Bot events ───────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    log.info("Connected to %d guild(s): %s", len(bot.guilds), ", ".join(g.name for g in bot.guilds))
    log.info("Summary interval: %ds, summary channel: %s", SUMMARY_INTERVAL, SUMMARY_CHANNEL_ID or "(command channel)")
    if not summary_loop.is_running():
        summary_loop.start()
        log.info("Summary loop started")


# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="join")
async def join_voice(ctx: commands.Context) -> None:
    """Join your current voice channel and start recording."""
    log.info("!join invoked by %s in guild %s", ctx.author, ctx.guild.name)

    if not ctx.author.voice or not ctx.author.voice.channel:
        log.warning("!join failed — %s is not in a voice channel", ctx.author)
        await ctx.send("You need to be in a voice channel first.")
        return

    voice_channel = ctx.author.voice.channel
    guild_id = ctx.guild.id

    # If already connected in this guild, inform the user
    if guild_id in active_sessions:
        log.warning("!join rejected — already recording in guild %s", guild_id)
        await ctx.send("Already recording in a voice channel. Use `!leave` first.")
        return

    # Determine the text channel for summaries
    if SUMMARY_CHANNEL_ID:
        text_channel = bot.get_channel(SUMMARY_CHANNEL_ID)
        if text_channel is None:
            text_channel = ctx.channel
    else:
        text_channel = ctx.channel

    # Connect — cls=None lets pycord use its default VoiceClient
    log.info("Connecting to voice channel '%s' (id=%s)", voice_channel.name, voice_channel.id)
    vc: discord.VoiceClient = await voice_channel.connect(cls=discord.VoiceClient)
    log.info("Voice client connected")

    # Start recording with our custom sink
    sink = CallRecorderSink()
    vc.start_recording(sink, _recording_finished_callback, ctx.channel)
    log.info("Recording started with CallRecorderSink")

    active_sessions[guild_id] = {
        "vc": vc,
        "sink": sink,
        "text_channel": text_channel,
        "voice_channel": voice_channel,
    }

    await ctx.send(
        f"Joined **{voice_channel.name}** and recording. "
        f"Summaries will be posted to <#{text_channel.id}> every "
        f"{SUMMARY_INTERVAL // 60} min."
    )
    log.info("Joined %s in guild %s", voice_channel.name, guild_id)


async def _recording_finished_callback(
    sink: discord.sinks.Sink, channel: discord.TextChannel, *args
) -> None:
    """Called when recording stops (e.g. bot disconnects).

    If the recording was stopped unexpectedly (not via !leave),
    clean up the session so a fresh !join works.
    """
    log.info("Recording finished callback triggered.")
    # Find the guild this sink belonged to and clean up if still tracked
    for guild_id, session in list(active_sessions.items()):
        if session["sink"] is sink:
            log.warning(
                "Recording ended unexpectedly for guild %s — cleaning up.",
                guild_id,
            )
            active_sessions.pop(guild_id, None)
            break


@bot.command(name="leave")
async def leave_voice(ctx: commands.Context) -> None:
    """Stop recording, post a final summary, and disconnect."""
    log.info("!leave invoked by %s in guild %s", ctx.author, ctx.guild.name)
    guild_id = ctx.guild.id
    session = active_sessions.pop(guild_id, None)

    if not session:
        log.warning("!leave but no active session in guild %s", guild_id)
        await ctx.send("I'm not in a voice channel.")
        return

    vc: discord.VoiceClient = session["vc"]
    log.info("[guild=%s] Stopping recording and disconnecting", guild_id)

    # Stop recording first — this triggers the finished callback,
    # but we already popped the session so it won't double-clean.
    try:
        vc.stop_recording()
    except Exception:
        log.exception("Error stopping recording")

    await ctx.send("Processing final summary...")

    # Re-add session temporarily so _process_summary can read it
    active_sessions[guild_id] = session
    try:
        await _process_summary(guild_id)
    except Exception:
        log.exception("Error processing final summary")
    finally:
        active_sessions.pop(guild_id, None)

    # Clean up sink
    session["sink"].cleanup()

    # Disconnect — force=True ensures the websocket is closed cleanly
    try:
        await vc.disconnect(force=True)
    except Exception:
        log.exception("Error disconnecting voice client")
    await ctx.send("Disconnected and posted final summary.")
    log.info("Left voice in guild %s", guild_id)


@bot.command(name="summarize")
async def summarize_now(ctx: commands.Context) -> None:
    """Immediately generate and post a summary of the current conversation."""
    guild_id = ctx.guild.id
    if guild_id not in active_sessions:
        await ctx.send("I'm not recording in any channel. Use `!join` first.")
        return

    log.info("!summarize invoked by %s in guild %s", ctx.author, ctx.guild.name)
    await ctx.send("Generating summary...")
    try:
        await _process_summary(guild_id)
    except Exception:
        log.exception("Error in on-demand summary for guild %s", guild_id)
        await ctx.send("Something went wrong. Check the logs.")


@bot.command(name="status")
async def status(ctx: commands.Context) -> None:
    """Show whether the bot is currently recording."""
    guild_id = ctx.guild.id
    session = active_sessions.get(guild_id)
    if session:
        vc: discord.VoiceClient = session["vc"]
        channel_name = vc.channel.name if vc.channel else "unknown"
        users = len(session["sink"].buffers)
        await ctx.send(
            f"Recording in **{channel_name}** — "
            f"{users} user(s) detected so far."
        )
    else:
        await ctx.send("Not currently recording.")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    log.info(
        "Starting bot — Whisper=%s/%s/%s, Ollama=%s@%s, interval=%ds",
        WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
        OLLAMA_MODEL, OLLAMA_HOST, SUMMARY_INTERVAL,
    )
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
