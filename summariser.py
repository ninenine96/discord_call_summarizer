import asyncio
from concurrent.futures import ThreadPoolExecutor
import requests

_executor = ThreadPoolExecutor(max_workers=2)

SYSTEM_PROMPT = """You are a concise meeting assistant. Given a transcript, produce:
**Key points** — main topics (bullets)
**Decisions made** — conclusions reached (bullets or None)
**Action items** — tasks with owner if mentioned (bullets or None)"""

def _ollama_sync(transcript: str) -> str:
    response = requests.post("http://localhost:11434/api/generate", json={
        "model": "llama3",
        "prompt": f"{SYSTEM_PROMPT}\n\nTranscript:\n{transcript}",
        "stream": False,
    })
    return response.json()["response"]

async def summarise_transcript(transcript: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _ollama_sync, transcript)