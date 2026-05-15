"""
app/multimodal/voice.py — Voice message transcription via OpenAI Whisper.

Flow:
  1. Download OGG/OGA from Telegram to temp file
  2. Run whisper.transcribe() in thread pool (CPU-bound)
  3. Return transcribed text, or None on failure
"""

import asyncio
import tempfile
from functools import lru_cache
from typing import Optional

from loguru import logger
from pyrogram import Client
from pyrogram.types import Message

WHISPER_MODEL = "small"  # tiny/base/small/medium/large


@lru_cache(maxsize=1)
def _load_model():
    import whisper
    logger.info(f"[VOICE] Loading Whisper '{WHISPER_MODEL}' (first call only)")
    return whisper.load_model(WHISPER_MODEL)


async def transcribe_voice(client: Client, message: Message) -> Optional[str]:
    """Download and transcribe voice/audio. Returns text or None."""
    if not (message.voice or message.audio):
        return None

    loop = asyncio.get_event_loop()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = await client.download_media(message, file_name=f"{tmp}/voice.ogg")
            if not path:
                return None

            def _run(p: str) -> str:
                return _load_model().transcribe(p, fp16=False).get("text", "").strip()

            text = await loop.run_in_executor(None, _run, path)
            if text:
                logger.info(f"[VOICE] Transcribed {len(text)} chars | user={message.from_user and message.from_user.id}")
            return text or None

    except Exception as e:
        logger.error(f"[VOICE] Transcription error: {e}")
        return None
