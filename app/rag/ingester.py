"""
Top-level ingestion orchestrator.
Calls all source ingesters and returns a stats dict.
Called by the scheduler (hourly) and by the admin manual-trigger command.
"""
from typing import Optional

from loguru import logger

from app.core.config import settings
from app.rag.vector_store import QdrantStore


async def run_full_ingestion(tg_client=None) -> dict:
    """
    Ingest from all configured sources.
    tg_client: live Pyrogram Client instance (required for Telegram ingestion).
    Returns: {"telegram": int, "gdrive": int, "errors": list[str]}
    """
    store = QdrantStore()
    await store.ensure_collection()

    stats: dict = {"telegram": 0, "gdrive": 0, "catalog": 0, "knowledge": 0, "errors": []}

    # Каталог «Master Data Base» (Google Sheet) — основной источник знаний
    if settings.catalog_sheet_id:
        from app.rag.sources.sheets import SheetCatalogIngester
        try:
            n = await SheetCatalogIngester(store).ingest_all()
            stats["catalog"] = n
        except Exception as e:
            logger.error(f"Catalog ingestion error: {e}")
            stats["errors"].append(f"catalog: {e}")

    # Локальные документы знаний (data/knowledge/*.md|txt): гарантия, инструкции
    from app.rag.sources.local_docs import LocalDocsIngester
    try:
        n = await LocalDocsIngester(store).ingest_all()
        stats["knowledge"] = n
    except Exception as e:
        logger.error(f"Knowledge docs ingestion error: {e}")
        stats["errors"].append(f"knowledge: {e}")

    # Telegram channels + groups
    if tg_client and settings.monitored_channels:
        from app.rag.sources.telegram import TelegramIngester
        try:
            n = await TelegramIngester(tg_client, store).ingest_all()
            stats["telegram"] = n
        except Exception as e:
            logger.error(f"Telegram ingestion error: {e}")
            stats["errors"].append(f"telegram: {e}")

    # Google Drive
    if settings.drive_folder_id and settings.service_account_json:
        from app.rag.sources.gdrive import GoogleDriveIngester
        try:
            n = await GoogleDriveIngester(store).ingest_all()
            stats["gdrive"] = n
        except Exception as e:
            logger.error(f"Google Drive ingestion error: {e}")
            stats["errors"].append(f"gdrive: {e}")

    total = stats["telegram"] + stats["gdrive"] + stats["catalog"] + stats["knowledge"]
    logger.info(f"Ingestion complete — total {total} chunks | {stats}")
    return stats
