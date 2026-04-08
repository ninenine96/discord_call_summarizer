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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

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
    user_names = sink.get_user_display_names()
    audio_chunks = sink.harvest()

    if not audio_chunks:
        log.info("No audio captured this interval for guild %s", guild_id)
        return

    # Transcribe each user in parallel
    transcript_parts: list[str] = []

    async def _transcribe_user(uid: int, wav: bytes) -> str:
        name = user_names.get(uid, f"User-{uid}")
        text = await transcriber.transcribe(wav)
        if text:
            return f"**{name}**: {text}"
        return ""

    results = await asyncio.gather(
        *[_transcribe_user(uid, wav) for uid, wav in audio_chunks.items()]
    )
    transcript_parts = [r for r in results if r]

    if not transcript_parts:
        log.info("All transcriptions empty for guild %s", guild_id)
        return

    full_transcript = "\n".join(transcript_parts)
    log.info(
        "Transcript for guild %s (%d chars):\n%s",
        guild_id,
        len(full_transcript),
        full_transcript[:500],
    )

    # Summarize
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
    log.info("Posted summary to #%s in guild %s", text_channel.name, guild_id)


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
    if not summary_loop.is_running():
        summary_loop.start()


# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="join")
async def join_voice(ctx: commands.Context) -> None:
    """Join your current voice channel and start recording."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel first.")
        return

    voice_channel = ctx.author.voice.channel
    guild_id = ctx.guild.id

    # If already connected in this guild, inform the user
    if guild_id in active_sessions:
        await ctx.send("Already recording in a voice channel. Use `!leave` first.")
        return

    # Determine the text channel for summaries
    if SUMMARY_CHANNEL_ID:
        text_channel = bot.get_channel(SUMMARY_CHANNEL_ID)
        if text_channel is None:
            text_channel = ctx.channel
    else:
        text_channel = ctx.channel

    # Connect
    vc: discord.VoiceClient = await voice_channel.connect()

    # Start recording with our custom sink
    sink = CallRecorderSink()
    vc.start_recording(sink, _recording_finished_callback, ctx.channel)

    active_sessions[guild_id] = {
        "vc": vc,
        "sink": sink,
        "text_channel": text_channel,
    }

    await ctx.send(
        f"Joined **{voice_channel.name}** and recording. "
        f"Summaries will be posted to <#{text_channel.id}> every "
        f"{SUMMARY_INTERVAL // 60} min."
    )
    log.info("Joined %s in guild %s", voice_channel.name, guild_id)


async def _recording_finished_callback(
    sink: discord.sinks.Sink, channel: discord.TextChannel
) -> None:
    """Called when recording stops (e.g. bot disconnects)."""
    log.info("Recording finished callback triggered.")


@bot.command(name="leave")
async def leave_voice(ctx: commands.Context) -> None:
    """Stop recording, post a final summary, and disconnect."""
    guild_id = ctx.guild.id
    session = active_sessions.pop(guild_id, None)

    if not session:
        await ctx.send("I'm not in a voice channel.")
        return

    vc: discord.VoiceClient = session["vc"]

    # Stop recording and process final summary
    vc.stop_recording()
    await ctx.send("Processing final summary...")

    try:
        await _process_summary(guild_id)
    except Exception:
        log.exception("Error processing final summary")

    # Re-pop in case the loop re-added (it shouldn't, but be safe)
    active_sessions.pop(guild_id, None)

    # Clean up sink
    session["sink"].cleanup()

    await vc.disconnect()
    await ctx.send("Disconnected and posted final summary.")
    log.info("Left voice in guild %s", guild_id)


@bot.command(name="summarize")
async def summarize_now(ctx: commands.Context) -> None:
    """Immediately generate and post a summary of the current conversation."""
    guild_id = ctx.guild.id
    if guild_id not in active_sessions:
        await ctx.send("I'm not recording in any channel. Use `!join` first.")
        return

    await ctx.send("Generating summary...")
    try:
        await _process_summary(guild_id)
    except Exception:
        log.exception("Error in on-demand summary")
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
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
