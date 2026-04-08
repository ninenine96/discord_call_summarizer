"""Summarization service using a local Llama 3 model via Ollama."""

from __future__ import annotations

import logging

import ollama

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a concise meeting-notes assistant.
You will receive a transcript of a Discord voice conversation with speaker labels.
Produce a short summary (3-8 bullet points) capturing:
- Key topics discussed
- Decisions made
- Action items or next steps (if any)
Keep it concise and factual. Use speaker names when attributing statements."""


class SummarizationService:
    def __init__(
        self,
        model: str = "llama3:8b-instruct-q4_K_M",
        ollama_host: str | None = None,
    ) -> None:
        self.model = model
        self.client = ollama.AsyncClient(host=ollama_host)

    async def summarize(self, transcript: str) -> str:
        """Return a bullet-point summary of the transcript."""
        if not transcript.strip():
            return "_No speech detected in this interval._"
        try:
            response = await self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                options={"temperature": 0.3, "num_predict": 512},
            )
            return response["message"]["content"].strip()
        except Exception:
            log.exception("Summarization failed")
            return "_Summarization failed. Check logs._"
