import discord
from discord.ext import commands
from discord import app_commands
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

bot = commands.Bot(command_prefix="!", intents=intents)
active_sessions: dict[int, dict] = {}


class TranscriptionSink(discord.sinks.WaveSink):
    def __init__(self):
        super().__init__()
        self.user_names: dict[int, str] = {}


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        role = discord.utils.get(interaction.guild.roles, name=ADMIN_ROLE_NAME)
        if role and role in interaction.user.roles:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("You need the Admin role.", ephemeral=True)
        return False
    return app_commands.check(predicate)


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

    await status_msg.edit(content="�� Summarising…")
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


@bot.tree.command(name="transcribe", description="Start transcribing the current voice call")
@is_admin()
async def transcribe(interaction: discord.Interaction):
    if interaction.guild_id in active_sessions:
        await interaction.response.send_message("Already recording.", ephemeral=True)
        return

    voice_state = interaction.user.voice
    if not voice_state or not voice_state.channel:
        await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        return

    vc = await voice_state.channel.connect()
    sink = TranscriptionSink()

    for member in voice_state.channel.members:
        sink.user_names[member.id] = member.display_name

    vc.start_recording(sink, finished_callback=lambda s, vc: None)

    active_sessions[interaction.guild_id] = {
        "voice_client": vc,
        "sink": sink,
        "start_time": datetime.utcnow(),
        "channel": interaction.channel,
    }

    await interaction.response.send_message(
        f"🔴 Recording in **{voice_state.channel.name}**. Use `/stop` when done.",
        ephemeral=True,
    )


@bot.tree.command(name="stop", description="Stop recording and post the summary")
@is_admin()
async def stop(interaction: discord.Interaction):
    if interaction.guild_id not in active_sessions:
        await interaction.response.send_message("No active recording.", ephemeral=True)
        return
    await interaction.response.send_message("⏹ Stopping…", ephemeral=True)
    await finish_recording(interaction.guild_id, channel=interaction.channel)


@bot.tree.command(name="status", description="Check if recording is active")
@is_admin()
async def status(interaction: discord.Interaction):
    session = active_sessions.get(interaction.guild_id)
    if not session:
        await interaction.response.send_message("No active recording.", ephemeral=True)
        return
    duration = datetime.utcnow() - session["start_time"]
    minutes = int(duration.total_seconds() // 60)
    seconds = int(duration.total_seconds() % 60)
    await interaction.response.send_message(
        f"🔴 Recording active — {minutes}m {seconds}s elapsed.", ephemeral=True
    )


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print("Slash commands synced.")


@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id
    session = active_sessions.get(guild_id)
    if not session:
        return
    vc = session["voice_client"]
    if vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
        await finish_recording(guild_id)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
