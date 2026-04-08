import asyncio
import tempfile
import os
import whisper
from concurrent.futures import ThreadPoolExecutor

_model = whisper.load_model("base")
_executor = ThreadPoolExecutor(max_workers=2)


def _transcribe_sync(path: str) -> str:
    result = _model.transcribe(path)
    return result["text"]


async def transcribe_audio(audio_file) -> str:
    """Write audio bytes to a temp file and run Whisper on it."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_file.read())
        tmp_path = f.name
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, _transcribe_sync, tmp_path)
    finally:
        os.unlink(tmp_path)
