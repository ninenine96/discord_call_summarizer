import discord
from discord.ext import commands
import asyncio
import os
from datetime import datetime

from transcriber import transcribe_audio
from summariser import summarise_transcript

from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID", "0"))
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Admin")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = discord.Bot(intents=intents)
active_sessions: dict[int, dict] = {}


class TranscriptionSink(discord.sinks.WaveSink):
    def __init__(self):
        super().__init__()
        self.user_names: dict[int, str] = {}


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


async def finish_recording(guild_id: int, channel=None):
    session = active_sessions.pop(guild_id, None)
    if not session:
        return

    voice_client = session["voice_client"]
    sink = session["sink"]
    start_time = session["start_time"]
    post_channel = channel or bot.get_channel(SUMMARY_CHANNEL_ID)

    voice_client.stop_recording()
    await asyncio.sleep(1)
    await voice_client.disconnect()

    if not post_channel:
        print("No summary channel configured.")
        return

    status_msg = await post_channel.send("⏳ Transcribing audio…")

    transcript_lines = []
    for user_id, audio_data in sink.audio_data.items():
        name = sink.user_names.get(user_id, f"User {user_id}")
        try:
            text = await transcribe_audio(audio_data.file)
            if text.strip():
                transcript_lines.append(f"{name}: {text.strip()}")
        except Exception as e:
            print(f"Transcription failed for {name}: {e}")

    if not transcript_lines:
        await status_msg.edit(content="No speech detected — nothing to summarise.")
        return

    full_transcript = "\n".join(transcript_lines)
    minutes = int((datetime.utcnow() - start_time).total_seconds() // 60)

    await status_msg.edit(content="🧠 Summarising…")
    summary = await summarise_transcript(full_transcript)

    embed = discord.Embed(
        title="📋 Meeting Summary",
        description=summary,
        color=0x5865F2,
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"Call duration: ~{minutes} min • {len(transcript_lines)} speakers")
    await status_msg.edit(content=None, embed=embed)

    thread = await post_channel.create_thread(
        name=f"Transcript – {datetime.utcnow().strftime('%d %b %H:%M')}",
        message=status_msg,
    )
    for i in range(0, len(full_transcript), 1900):
        await thread.send(f"```\n{full_transcript[i:i+1900]}\n```")


@bot.slash_command(name="transcribe", description="Start transcribing the current voice call")
@check_admin()
async def transcribe(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)  # must be first, before any async work

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

    vc = await voice_state.channel.connect()

    # wait up to 10s for connection to stabilise
    for _ in range(10):
        await asyncio.sleep(1)
        if vc.is_connected():
            break

    print(f"Connected: {vc.is_connected()}, channel: {vc.channel}")

    if not vc.is_connected():
        await vc.disconnect(force=True)
        await ctx.followup.send("Could not stabilise voice connection, try again.", ephemeral=True)
        return

    sink = TranscriptionSink()
    for member in voice_state.channel.members:
        sink.user_names[member.id] = member.display_name

    vc.start_recording(sink, lambda s, v: None, ctx.channel)

    active_sessions[ctx.guild_id] = {
        "voice_client": vc,
        "sink": sink,
        "start_time": datetime.utcnow(),
        "channel": ctx.channel,
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