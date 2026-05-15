"""
Full re-ingestion from Telegram channel into Qdrant.
Run ONLY when the main bot is stopped.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()  # must be before any app/ imports

from loguru import logger
from pyrogram import Client
from app.core.config import settings
from app.rag.retriever import get_store
from app.rag.vector_store import COLLECTION
from app.rag.sources.telegram import TelegramIngester


async def main():
    # 1. Connect to Qdrant and clear old data
    store = get_store()
    qdrant = store._client_()
    try:
        await qdrant.delete_collection(COLLECTION)
        logger.info("Old collection deleted")
    except Exception:
        pass
    await store.ensure_collection()
    logger.info("Fresh collection created")

    # 2. Reset state so everything is re-read
    state_file = Path("data/tg_ingest_state.json")
    state_file.write_text("{}", encoding="utf-8")
    logger.info("Ingestion state reset")

    # 3. Connect Pyrogram and ingest
    client = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir="data/sessions",
    )

    async with client:
        me = await client.get_me()
        logger.info(f"Logged in as @{me.username}")

        ingester = TelegramIngester(client, store)
        total = await ingester.ingest_all()
        logger.info(f"DONE — {total} chunks indexed into Qdrant")

    # Show final count (outside client context)
    info = await qdrant.get_collection(COLLECTION)
    logger.info(f"Qdrant points total: {info.points_count}")


asyncio.run(main())
