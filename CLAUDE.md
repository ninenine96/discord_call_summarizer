# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Start detached (recommended) — kills any existing instance, writes logs to bot.log
./start_bot.sh

# Watch logs (bot.log may contain non-printable bytes from Whisper progress bars)
tail -f bot.log        # may garble terminal
grep -a "" bot.log     # safe for binary content
strings bot.log        # strip non-printable

# Run in foreground (for debugging)
source venv/bin/activate && python -u discord_bot.py
```

Ollama must be running before starting the bot:
```bash
ollama serve
```

## Architecture

The active entrypoint is `discord_bot.py` (root level) — **not** `bot/` or `pyproject.toml`'s `bot.main` entrypoint, which are remnants of an older architecture and no longer exist.

- `discord_bot.py` — all bot logic: slash commands (`/transcribe`, `/stop`, `/status`), voice recording, session state, live transcription loop
- `transcriber.py` — loads OpenAI Whisper (`base` model) and transcribes WAV audio synchronously via a thread pool
- `summariser.py` — posts the transcript to Ollama (`llama3`) via HTTP and returns a summary

**Voice recording flow:**
1. `/transcribe` — bot joins the user's voice channel, creates a `TranscriptionSink` (py-cord `WaveSink` subclass), starts recording per-user audio, stores session in `active_sessions[guild_id]`. Starts a background `live_transcription_loop` task.
2. Every 30 seconds — `live_transcription_loop` wakes, extracts new PCM bytes per user from the sink's BytesIO (skipping the 44-byte WAV header, tracking a per-user byte offset), wraps them in a fresh WAV, transcribes with Whisper, and `print()`s the result to `bot.log`. Lines appear as `[transcript] Name: text`.
3. `/stop` (or all humans leave) — calls `finish_recording()`: cancels the live task, stops recording, transcribes any remaining audio since the last chunk, then sends the full transcript to Ollama and posts a summary embed to the configured Discord channel. Raw transcript is **not** posted to Discord — it's only in `bot.log`.

**PCM extraction detail:** Discord audio is 48 kHz, stereo, 16-bit PCM = 192,000 bytes/second. The sink's `audio_data[user_id].file` is a BytesIO written by Python's `wave` module. The WAV header is always 44 bytes. PCM data starts at byte 44. `getvalue()[44:]` gives all PCM so far; we slice from a tracked offset to get only new bytes since the last chunk.

## Environment

`.env` variables that matter:
- `DISCORD_TOKEN` — required
- `SUMMARY_CHANNEL_ID` — channel where summaries are posted (right-click channel → Copy ID)
- `WHISPER_MODEL_SIZE` — default `base`; `small`/`medium` are more accurate but slower on CPU
- `OLLAMA_HOST` — default `http://localhost:11434`

The `.env` file has a duplicate `SUMMARY_CHANNEL_ID` entry (second one is blank) — python-dotenv uses the first value, so this is harmless but worth knowing.

## Summariser personality

The Ollama system prompt in `summariser.py` is intentionally unhinged — it's a shitposter persona that targets "Melwin" specifically. Edit `SYSTEM_PROMPT` there to change the summary style.

---

## Gotchas and known issues

### DAVE (Discord Audio Video Encryption) — the big one

Discord rolled out mandatory E2E voice encryption (DAVE/MLS protocol) in late 2024. Without proper DAVE support the bot receives audio but every Opus decode fails with `"Error occurred while decoding opus frame."` — Whisper gets silence.

**The fix:** Two venv files are patched in-place. If you ever rebuild the venv (`pip install`, `pip install --force-reinstall`), **you must re-apply these patches**:

#### 1. `venv/lib/python3.12/site-packages/discord/gateway.py`

- Added DAVE opcode constants (21–31): `DAVE_PREPARE_TRANSITION`, `DAVE_EXECUTE_TRANSITION`, `DAVE_TRANSITION_READY`, `DAVE_PREPARE_EPOCH`, `MLS_EXTERNAL_SENDER`, `MLS_KEY_PACKAGE`, `MLS_PROPOSALS`, `MLS_COMMIT_WELCOME`, `MLS_ANNOUNCE_COMMIT_TRANSITION`, `MLS_WELCOME`, `MLS_INVALID_COMMIT_WELCOME`
- Added `send_binary(opcode, data)` — sends raw binary WebSocket frame
- Added `send_transition_ready(transition_id)` — ACKs DAVE transitions
- IDENTIFY payload now includes `"max_dave_protocol_version": getattr(self._connection, "max_dave_protocol_version", 1)` — **this must be 1, not 0**; sending 0 still gets close code 4017
- `received_message()` handles `SESSION_DESCRIPTION` DAVE fields + `DAVE_PREPARE_TRANSITION` / `DAVE_EXECUTE_TRANSITION` / `DAVE_PREPARE_EPOCH` opcodes
- Added `received_binary_message()` for MLS binary frames (opcodes 25–31)
- `poll_event()` routes `WSMsgType.BINARY` frames to `received_binary_message()`
- Fixed crash when `data` is `None` in seq_ack update: `if data and isinstance(data, dict): self.seq_ack = data.get("seq", self.seq_ack)`

#### 2. `venv/lib/python3.12/site-packages/discord/voice_client.py`

- `__init__` initializes: `self.dave_session = None`, `self.dave_protocol_version = 0`, `self.dave_pending_transitions = {}`
- Added `max_dave_protocol_version` property — returns `davey.DAVE_PROTOCOL_VERSION` (= 1)
- Added `reinit_dave_session(ws=None)` — creates a `davey.DaveSession`, calls `set_passthrough_mode(False)`, sends `MLS_KEY_PACKAGE` to Discord
- Added `_execute_dave_transition(transition_id)` — processes buffered MLS proposals/welcome and completes epoch transition
- Added `_recover_from_invalid_dave_commit(transition_id)` — fallback for `MLS_INVALID_COMMIT_WELCOME`

#### 3. `venv/lib/python3.12/site-packages/discord/sinks/core.py`

In `RawData.__init__`, after SRTP decryption, DAVE decrypt is applied. The version that ships with py-cord 2.7.2 has **two bugs** that make DAVE decryption always fail silently:

**Bug 1 — wrong first argument:** `dave.decrypt(self.ssrc, ...)` passes the audio stream SSRC, but `davey.DaveSession.decrypt(user_id, media_type, packet)` expects the Discord **user_id**. The SSRC→user_id mapping lives in `ws.ssrc_map[ssrc]["user_id"]`.

**Bug 2 — wrong media_type type:** `dave.decrypt(..., 0, ...)` passes a raw `int`, but davey requires a `davey.MediaType` enum value. `0` raises `TypeError: argument 'media_type': 'int' object cannot be converted to 'MediaType'`. The correct value is `davey.MediaType.audio`.

The corrected block:
```python
dave = getattr(self.client, "dave_session", None)
if dave is not None and self.decrypted_data:
    try:
        import davey as _davey
        ssrc_info = getattr(self.client, "ws", None)
        ssrc_info = ssrc_info.ssrc_map.get(self.ssrc, {}) if ssrc_info else {}
        uid = ssrc_info.get("user_id")
        if uid is not None:
            decrypted = dave.decrypt(uid, _davey.MediaType.audio, self.decrypted_data)
            if decrypted is not None:
                self.decrypted_data = decrypted
            else:
                self.decrypted_data = None
    except Exception:
        pass
```

**Why `uid is not None` guard:** The very first audio packet arrives before Discord has sent the speaking event that populates `ssrc_map`. Without the guard, we'd fall back to passing the SSRC as user_id, DAVE decrypt would fail, and the still-DAVE-encrypted payload would reach Opus → one error log. With the guard, unknown-SSRC packets are dropped cleanly.

**Library used:** `davey==0.1.5` (already in venv) — Python MLS implementation for Discord. Key APIs: `DaveSession`, `decrypt()`, `process_proposals()`, `process_welcome()`, `process_commit()`, `set_external_sender()`, `get_serialized_key_package()`, `set_passthrough_mode()`, `MediaType.audio`, `MediaType.video`.

### Opus must be loaded explicitly

py-cord's auto-load (`_load_default()` via `ctypes.util.find_library("opus")`) silently fails on this system. Without explicitly loading it, `discord.opus.is_loaded()` returns `False` and every frame fails to decode. The fix is at the top of `discord_bot.py`:

```python
if not discord.opus.is_loaded():
    discord.opus.load_opus("libopus.so.0")
```

The library is installed at `/lib/x86_64-linux-gnu/libopus.so.0`. Check with `ldconfig -p | grep opus`.

### `logging.info()` is silently dropped — use `print()`

Python's root logger defaults to `WARNING` level. Calls to `logging.info()` produce no output in `bot.log`. Use `print(..., flush=True)` for anything that needs to appear in the log. The `flush=True` is required because `start_bot.sh` uses `nohup` with stdout redirect — without it, output gets buffered and may never appear.

### py-cord vs discord.py — they share the same namespace

Both `py-cord` and `discord.py` install as the `discord` package. If both are installed, they conflict and break in confusing ways (e.g., `AttributeError: module 'discord' has no attribute 'Bot'`). Always ensure only one is installed:
```bash
pip uninstall discord.py -y
pip install "py-cord[voice]" --force-reinstall
```
Use `pip show py-cord` and `pip show discord.py` to check which are installed.

### Multiple bot processes

If `start_bot.sh` doesn't kill the old process correctly, you can end up with multiple bots in the same voice channel — one records, the other responds to commands saying "already recording". Check with:
```bash
pgrep -a -f "discord_bot.py"
```
The `start_bot.sh` script matches on `"discord_bot.py"` (not `"python discord_bot.py"`) because the actual process name from `pgrep` is the script filename. It sends SIGTERM first (gives Discord time to cleanly disconnect), waits 4s, then SIGKILL.

### `reconnect=False` in `voice_state.channel.connect()`

The bot connects with `reconnect=False`. With `reconnect=True` (the default), a failed connection attempt retries with exponential backoff up to ~15 seconds before raising — causing Discord to time out the interaction ("Interaction has already been acknowledged" / error 40060). Fast-fail with `reconnect=False` lets the bot respond with a proper error message instead.

### `_connected` is a `threading.Event`, not asyncio

py-cord's `VoiceClient._connected` is a `threading.Event`. If you try to `await vc._connected.wait()` in async code, it deadlocks the event loop. The workaround is to manually set it if `is_connected()` is False after connect: `vc._connected.set()`.

### bot.log contains binary data

Whisper prints progress bars using carriage returns and ANSI codes, which produce non-printable bytes in the log. `tail -f bot.log` may garble your terminal. Use `grep -a "" bot.log` or `strings bot.log` to read safely.

### `-u` flag is required for nohup logging

Without `python -u`, Python buffers stdout and nothing gets written to `bot.log` until the process exits. The `start_bot.sh` script uses `python -u discord_bot.py` for unbuffered output.
