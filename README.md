# Discord Call Summarizer

A Discord bot that joins voice channels, listens to conversations, transcribes the audio using local Whisper, and posts periodic AI-generated summaries using a quantized Llama 3 model via Ollama. **Fully local — no cloud API keys needed.**

## How it works

1. A user invokes `!join` while in a voice channel
2. The bot connects and starts recording all participants
3. Every N minutes (default: 5), it:
   - Harvests the recorded audio per user
   - Transcribes each user's audio locally with faster-whisper (CTranslate2)
   - Combines the transcripts with speaker labels
   - Sends the transcript to a quantized Llama 3 model (via Ollama) for summarization
   - Posts the summary as an embed in the designated text channel
4. `!leave` stops recording, posts a final summary, and disconnects

## Setup

### Prerequisites

- Python 3.10+
- A Discord bot token with **Voice** and **Message Content** intents enabled
- [Ollama](https://ollama.com/) installed and running
- FFmpeg installed on your system (`sudo apt install ffmpeg` or `brew install ffmpeg`)

### Install Ollama & pull the model

```bash
# Install Ollama (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull quantized Llama 3 8B
ollama pull llama3:8b-instruct-q4_K_M
```

### Installation

```bash
cd discord_call_summarizer
pip install -e .
```

### Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | Yes | — | Discord bot token |
| `OLLAMA_HOST` | No | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | No | `llama3:8b-instruct-q4_K_M` | Ollama model for summarization |
| `WHISPER_MODEL_SIZE` | No | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`) |
| `WHISPER_DEVICE` | No | `cpu` | Device for Whisper inference (`cpu` or `cuda`) |
| `WHISPER_COMPUTE_TYPE` | No | `int8` | Compute type (`int8`, `float16`, `float32`) |
| `SUMMARY_CHANNEL_ID` | No | — | Text channel ID for summaries (defaults to command channel) |
| `SUMMARY_INTERVAL` | No | `300` | Seconds between summaries |

### Discord Bot Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot
3. Enable these **Privileged Gateway Intents**:
   - Message Content Intent
   - Server Members Intent (optional, for display names)
4. Generate an invite URL with these **Bot Permissions**:
   - Connect
   - Speak
   - Send Messages
   - Embed Links
5. Invite the bot to your server

### Run

```bash
# Make sure Ollama is running
ollama serve

# In another terminal
python -m bot.main
```

## Commands

| Command | Description |
|---|---|
| `!join` | Join your voice channel and start recording |
| `!leave` | Stop recording, post final summary, and disconnect |
| `!summarize` | Immediately generate a summary of the conversation so far |
| `!status` | Check if the bot is currently recording |

## Architecture

```
bot/
├── __init__.py
├── main.py            # Bot setup, commands, summary loop
├── audio_sink.py      # Custom discord.py Sink for per-user audio capture
├── transcription.py   # Local Whisper transcription (faster-whisper / CTranslate2)
└── summarization.py   # Llama 3 summarization via Ollama
```

## Resource Usage

- **Whisper base** (int8): ~150 MB RAM, runs well on CPU
- **Llama 3 8B Q4_K_M**: ~4.5 GB RAM via Ollama
- For faster transcription on NVIDIA GPUs, set `WHISPER_DEVICE=cuda` and `WHISPER_COMPUTE_TYPE=float16`
