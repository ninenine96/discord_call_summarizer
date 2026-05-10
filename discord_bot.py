import discord
from discord.ext import commands
import asyncio
import io
import logging
import os
import wave
from datetime import datetime


from transcriber import transcribe_audio
from summariser import summarise_transcript

from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID", "0"))
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Admin")

# Discord audio: 48 kHz, stereo, 16-bit PCM
WAV_HEADER_BYTES = 44
BYTES_PER_SEC = 48000 * 2 * 2
CHUNK_INTERVAL = 30        # seconds between live-transcript polls
MIN_CHUNK_BYTES = BYTES_PER_SEC * 2  # skip chunk if < 2 s of new audio

if not discord.opus.is_loaded():
    discord.opus.load_opus("libopus.so.0")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = discord.Bot(intents=intents)
active_sessions: dict[int, dict] = {}


def _pcm_to_wav(pcm: bytes) -> io.BytesIO:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf


class TranscriptionSink(discord.sinks.WaveSink):
    def __init__(self):
        super().__init__()
        self.user_names: dict[int, str] = {}
        self._pcm_offsets: dict[int, int] = {}  # PCM bytes already sent to live thread


def check_admin():
    async def predicate(ctx: discord.ApplicationContext) -> bool:
        role = discord.utils.get(ctx.guild.roles, name=ADMIN_ROLE_NAME)
        if role and role in ctx.user.roles:
            return True
        if ctx.user.guild_permissions.administrator:
            return True
        await ctx.respond("You need the Admin role.", ephemeral=True)
        return False
    return commands.check(predicate)


async def _transcribe_new_pcm(sink: TranscriptionSink, user_id: int) -> str | None:
    """Transcribe PCM bytes accumulated since the last call for this user."""
    audio_data = sink.audio_data.get(user_id)
    if not audio_data:
        return None
    all_pcm = audio_data.file.getvalue()[WAV_HEADER_BYTES:]
    offset = sink._pcm_offsets.get(user_id, 0)
    new_pcm = all_pcm[offset:]
    if len(new_pcm) < MIN_CHUNK_BYTES:
        return None
    sink._pcm_offsets[user_id] = offset + len(new_pcm)
    return await transcribe_audio(_pcm_to_wav(new_pcm))


async def live_transcription_loop(guild_id: int):
    try:
        while True:
            await asyncio.sleep(CHUNK_INTERVAL)
            session = active_sessions.get(guild_id)
            if not session:
                break
            sink: TranscriptionSink = session["sink"]
            for user_id in list(sink.audio_data):
                name = sink.user_names.get(user_id, f"User {user_id}")
                try:
                    text = await _transcribe_new_pcm(sink, user_id)
                    if text and text.strip():
                        line = f"{name}: {text.strip()}"
                        session["transcript_lines"].append(line)
                        print(f"[transcript] {line}", flush=True)
                except Exception as e:
                    logging.warning(f"Live transcription failed for {name}: {e}")
    except asyncio.CancelledError:
        pass


async def finish_recording(guild_id: int, channel=None):
    session = active_sessions.pop(guild_id, None)
    if not session:
        return

    voice_client = session["voice_client"]
    sink = session["sink"]
    start_time = session["start_time"]
    live_task = session.get("live_task")
    transcript_lines: list[str] = session.get("transcript_lines", [])
    post_channel = channel or bot.get_channel(SUMMARY_CHANNEL_ID)

    if live_task:
        live_task.cancel()

    voice_client.stop_recording()
    await asyncio.sleep(1)
    await voice_client.disconnect()

    if not post_channel:
        logging.warning("No summary channel configured.")
        return

    status_msg = await post_channel.send("⏳ Finishing transcription…")

    # Transcribe any audio recorded after the last live chunk
    for user_id in list(sink.audio_data):
        name = sink.user_names.get(user_id, f"User {user_id}")
        try:
            text = await _transcribe_new_pcm(sink, user_id)
            if text and text.strip():
                line = f"{name}: {text.strip()}"
                transcript_lines.append(line)
                logging.info(f"[transcript] {line}")
        except Exception as e:
            logging.warning(f"Final transcription failed for {name}: {e}")

    if not transcript_lines:
        await status_msg.edit(content="No speech detected — nothing to summarise.")
        return

    full_transcript = "\n".join(transcript_lines)
    minutes = int((datetime.utcnow() - start_time).total_seconds() // 60)
    speaker_count = len({line.split(":")[0] for line in transcript_lines})

    await status_msg.edit(content="🧠 Summarising…")
    summary = await summarise_transcript(full_transcript)

    embed = discord.Embed(
        title="📋 Meeting Summary",
        description=summary,
        color=0x5865F2,
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"Call duration: ~{minutes} min • {speaker_count} speakers")
    await status_msg.edit(content=None, embed=embed)



@bot.slash_command(name="transcribe", description="Start transcribing the current voice call")
@check_admin()
async def transcribe(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)

    if not ctx.guild_id:
        await ctx.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    if ctx.guild_id in active_sessions:
        await ctx.followup.send("Already recording.", ephemeral=True)
        return

    voice_state = ctx.user.voice
    if not voice_state or not voice_state.channel:
        await ctx.followup.send("Join a voice channel first.", ephemeral=True)
        return

    print(f"[transcribe] Connecting to {voice_state.channel.name}...")
    try:
        vc = await voice_state.channel.connect(reconnect=False)
    except Exception as e:
        print(f"[transcribe] connect() failed: {e}")
        await ctx.followup.send("Failed to connect to voice channel.", ephemeral=True)
        return

    print(f"[transcribe] connected, is_connected={vc.is_connected()}")
    if not vc.is_connected():
        vc._connected.set()

    sink = TranscriptionSink()
    for member in voice_state.channel.members:
        sink.user_names[member.id] = member.display_name

    try:
        async def _done(sink, channel):
            pass
        vc.start_recording(sink, _done, ctx.channel)
    except Exception as e:
        print(f"[transcribe] start_recording failed: {e}")
        await vc.disconnect(force=True)
        await ctx.followup.send(f"Failed to start recording: {e}", ephemeral=True)
        return

    live_task = asyncio.create_task(live_transcription_loop(ctx.guild_id))

    active_sessions[ctx.guild_id] = {
        "voice_client": vc,
        "sink": sink,
        "start_time": datetime.utcnow(),
        "channel": ctx.channel,
        "live_task": live_task,
        "transcript_lines": [],
    }

    await ctx.followup.send(
        f"🔴 Recording in **{voice_state.channel.name}**. Use `/stop` when done.",
        ephemeral=True,
    )


@bot.slash_command(name="stop", description="Stop recording and post the summary")
@check_admin()
async def stop(ctx: discord.ApplicationContext):
    if ctx.guild_id not in active_sessions:
        await ctx.respond("No active recording.", ephemeral=True)
        return
    await ctx.respond("⏹ Stopping…", ephemeral=True)
    await finish_recording(ctx.guild_id, channel=ctx.channel)


@bot.slash_command(name="status", description="Check if recording is active")
@check_admin()
async def status(ctx: discord.ApplicationContext):
    session = active_sessions.get(ctx.guild_id)
    if not session:
        await ctx.respond("No active recording.", ephemeral=True)
        return
    duration = datetime.utcnow() - session["start_time"]
    minutes = int(duration.total_seconds() // 60)
    seconds = int(duration.total_seconds() % 60)
    await ctx.respond(
        f"🔴 Recording active — {minutes}m {seconds}s elapsed.", ephemeral=True
    )


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    for guild in bot.guilds:
        if guild.voice_client:
            await guild.voice_client.disconnect(force=True)
            print(f"Cleaned up stale voice connection in {guild.name}")


@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id
    session = active_sessions.get(guild_id)
    if not session:
        return
    vc = session["voice_client"]
    if vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
        await finish_recording(guild_id)


bot.run(DISCORD_TOKEN)
