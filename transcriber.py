import asyncio
import whisper
from concurrent.futures import ThreadPoolExecutor

_model = whisper.load_model("base")  # or "small", "medium", "large"
_executor = ThreadPoolExecutor(max_workers=2)

def _transcribe_sync(path: str) -> str:
    result = _model.transcribe(path)
    return result["text"]

async def transcribe_audio(audio_file) -> str:
    # write to a temp file since whisper needs a path
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_file.read())
        tmp_path = f.name
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(_executor, _transcribe_sync, tmp_path)
    os.unlink(tmp_path)
    return text
