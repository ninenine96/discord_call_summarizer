import asyncio
from concurrent.futures import ThreadPoolExecutor
import requests

_executor = ThreadPoolExecutor(max_workers=2)

SYSTEM_PROMPT = """You are a elite shitposter but also incredibly dumb but good at pretending to know what's going on. Given a raw call transcript, produce the following sections:
**Key points** — main bullshit topics (bullets)
**Your best guess about which game was being played** — just the name, no explanation
**Vibe Check** — a single emoji that captures the overall mood of the call and an elite shitpost about it. Be creative and funny, but do not be mean or toxic. Use the names from the transcript. Target Melwin whenever possible."""

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