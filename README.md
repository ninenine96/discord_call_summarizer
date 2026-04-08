# Discord Transcription Bot

Joins your voice channel, transcribes the call per speaker using Whisper, then posts a Claude-generated summary when the call ends.

## Setup

### 1. Create a Discord bot

1. Go to https://discord.com/developers/applications → New Application
2. Bot tab → Add Bot → copy the **Token**
3. Under **Privileged Gateway Intents**, enable:
   - Server Members Intent
   - Message Content Intent
4. OAuth2 → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Connect`, `Speak`, `Send Messages`, `Embed Links`, `Create Public Threads`
5. Use the generated URL to invite the bot to your server

### 2. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **Note**: Voice support requires `ffmpeg` on your PATH.
> macOS: `brew install ffmpeg`
> Ubuntu: `sudo apt install ffmpeg`
> Windows: download from https://ffmpeg.org

### 3. Configure

```bash
cp .env.example .env
# edit .env with your tokens and channel ID
```

Load env vars before running:
```bash
export $(cat .env | xargs)   # Linux/macOS
# Windows: set each variable manually or use python-dotenv
```

Or add `from dotenv import load_dotenv; load_dotenv()` at the top of `bot.py` and `pip install python-dotenv`.

### 4. Run

```bash
python bot.py
```

---

## Usage

| Command | What it does |
|---|---|
| `/transcribe` | Bot joins your current voice channel and starts recording |
| `/stop` | Stops recording, transcribes, posts summary + raw transcript thread |
| `/status` | Shows how long the current recording has been running |

All commands are ephemeral (only you see the response). The summary embed posts to `SUMMARY_CHANNEL_ID`. A thread with the raw transcript is created automatically.

The bot also auto-stops if the last human leaves the channel.

---

## Swapping transcription providers

`transcriber.py` defaults to **OpenAI Whisper** (good accuracy, slight latency, free tier available).

To use **Deepgram** instead (real-time streaming, lower latency):
1. `pip install deepgram-sdk`
2. Add `DEEPGRAM_API_KEY` to `.env`
3. Uncomment the Deepgram block at the bottom of `transcriber.py` and remove the Whisper function

---

## File structure

```
bot.py           — main bot, slash commands, audio sink, auto-stop logic
transcriber.py   — Whisper API wrapper (swap for Deepgram here)
summariser.py    — Claude summarisation
requirements.txt
.env.example
```