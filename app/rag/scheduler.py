"""
RAG ingestion scheduler.
- Runs a full ingestion automatically every hour.
- Exposes trigger_manual() so admin commands can fire an immediate re-index.
- Call set_client() once on bot startup to pass the live Pyrogram client.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from app.rag.ingester import run_full_ingestion

_INTERVAL_SECONDS = 3600  # 1 hour

_tg_client = None
_manual_trigger = asyncio.Event()
last_run: Optional[datetime] = None
last_stats: Optional[dict] = None


def set_client(client):
    """Call once on bot startup with the live Pyrogram client."""
    global _tg_client
    _tg_client = client


async def trigger_manual():
    """Incremental re-index: only new messages since last run."""
    logger.info("Manual RAG re-index triggered by admin")
    _manual_trigger.set()


async def force_full_reindex() -> dict:
    """
    Full reindex: wipes Qdrant collection + state, re-reads entire channel.
    Safe to call while bot is running — takes a few seconds.
    Returns ingestion stats dict.
    """
    import json
    from pathlib import Path
    from app.rag.vector_store import QdrantStore, COLLECTION

    logger.info("[REINDEX] Starting FULL reindex (wipe + rebuild)...")

    store = QdrantStore()
    qdrant = store._client_()

    # Wipe and recreate collection
    try:
        await qdrant.delete_collection(COLLECTION)
        logger.info("[REINDEX] Old collection wiped")
    except Exception as e:
        logger.warning(f"[REINDEX] Could not delete collection: {e}")
    await store.ensure_collection()

    # Reset incremental state
    state_file = Path("data/tg_ingest_state.json")
    state_file.write_text("{}", encoding="utf-8")
    logger.info("[REINDEX] Ingest state reset")

    # Run fresh ingestion
    stats = await run_full_ingestion(tg_client=_tg_client)
    global last_run, last_stats
    from datetime import datetime, timezone
    last_run = datetime.now(timezone.utc)
    last_stats = stats
    logger.info(f"[REINDEX] Done — {stats}")
    return stats


async def run_scheduler():
    """
    Long-running background coroutine. Launch with asyncio.create_task().
    Sleeps for 1 hour between runs, but wakes early on manual trigger.
    """
    global last_run, last_stats
    logger.info("RAG scheduler started (interval: 1 hour)")

    while True:
        try:
            logger.info("Starting ingestion run")
            last_stats = await run_full_ingestion(tg_client=_tg_client)
            last_run = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Scheduled ingestion failed: {e}")

        _manual_trigger.clear()
        try:
            await asyncio.wait_for(_manual_trigger.wait(), timeout=_INTERVAL_SECONDS)
            logger.info("Waking early: manual trigger received")
        except asyncio.TimeoutError:
            pass  # normal hourly wake-up
