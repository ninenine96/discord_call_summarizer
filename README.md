# Discord Call Summarizer

A Discord bot that joins voice channels, records conversations, transcribes audio locally with Whisper, and posts AI-generated summaries via Ollama. **Fully local — no cloud API keys needed.**

## How it works

1. An admin uses `/transcribe` while in a voice channel
2. The bot joins and records all participants as separate audio streams
3. Every 30 seconds, audio is transcribed incrementally and logged to `bot.log`
4. An admin uses `/stop` (or everyone leaves) to end the session
5. The bot transcribes any remaining audio, sends the full transcript to Ollama, and posts a summary embed to the configured Discord channel

Raw transcripts are logged locally to `bot.log` (as `[transcript] Name: text` lines) — they are not posted to Discord.

## Prerequisites

- Python 3.10+
- A Discord bot token with **Message Content** and **Voice State** intents enabled
- [Ollama](https://ollama.com/) installed with `llama3` pulled
- `libopus` installed (`sudo apt install libopus0`)

## Setup

### 1. Install Ollama and pull the model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3
```

### 2. Clone and install dependencies

```bash
git clone <repo>
cd discord_call_summarizer
python -m venv venv
source venv/bin/activate

# IMPORTANT: install py-cord, NOT discord.py — they share the same namespace
pip install "py-cord[voice]" openai-whisper python-dotenv aiohttp davey
```

> **After any `pip install --force-reinstall` or venv rebuild**, three venv files must be re-patched — see CLAUDE.md for the exact changes. Discord requires DAVE (E2E voice encryption) support; without the patches, the bot receives audio but decodes silence and produces no transcript.

### 3. Configure `.env`

```env
DISCORD_TOKEN=your_bot_token_here
SUMMARY_CHANNEL_ID=123456789012345678   # right-click channel → Copy ID
ADMIN_ROLE_NAME=Admin                   # role that can use /transcribe and /stop
WHISPER_MODEL_SIZE=base                 # tiny/base/small/medium — base is the sweet spot on CPU
OLLAMA_HOST=http://localhost:11434
```

### 4. Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot
3. Enable **Privileged Gateway Intents**: Message Content Intent, Server Members Intent
4. Generate an invite URL with permissions: Connect, Speak, Send Messages, Embed Links
5. Invite the bot to your server

### 5. Run

```bash
# Start Ollama first
ollama serve

# Start the bot (detached, restarts any existing instance, logs to bot.log)
./start_bot.sh

# Watch transcript lines as they come in
grep -a "\[transcript\]" bot.log
```

## Commands

All commands require the `Admin` role (or server administrator permissions).

| Command | Description |
|---|---|
| `/transcribe` | Join your current voice channel and start recording |
| `/stop` | Stop recording and post the summary to Discord |
| `/status` | Check recording status and elapsed time |

## Architecture

- `discord_bot.py` — all bot logic (slash commands, voice recording, 30s live transcription loop, summary posting)
- `transcriber.py` — OpenAI Whisper transcription (runs in thread pool to avoid blocking the event loop)
- `summariser.py` — Ollama HTTP summarization; contains the system prompt (currently a shitposter persona targeting Melwin — edit `SYSTEM_PROMPT` to change style)
- `start_bot.sh` — process management: graceful stop → wait → start with unbuffered logging

## Known Issues

**Whisper FP16 warning** — `UserWarning: FP16 is not supported on CPU; using FP32 instead` is harmless. Whisper auto-falls back to FP32 on CPU.

**bot.log may garble your terminal** — Whisper's progress bars write non-printable bytes. Use `grep -a "" bot.log` instead of `cat` or `tail -f`.

**One decode error on session start** — The very first audio packet arrives before Discord's SSRC→user mapping is ready, so it gets dropped. This is expected and doesn't affect transcription quality.

## Resource Usage

- **Whisper base**: ~150 MB RAM, ~10–30s per speaker per chunk on CPU
- **Llama 3 (8B)**: ~4–5 GB RAM via Ollama
