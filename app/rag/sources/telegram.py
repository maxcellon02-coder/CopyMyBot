"""
Ingests text messages and document attachments from Telegram channels/groups.
Tracks the last-seen message ID per source in data/tg_ingest_state.json so
subsequent runs only process new content (incremental ingestion).
"""
import json
from pathlib import Path
from typing import List

from loguru import logger
from pyrogram import Client

from app.core.config import settings
from app.rag.chunk import Chunk
from app.rag.sources.parser import chunk_text, parse_document
from app.rag.vector_store import QdrantStore

STATE_FILE = Path("data/tg_ingest_state.json")
HISTORY_LIMIT = 2000        # max messages per source on first run


def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


class TelegramIngester:
    def __init__(self, client: Client, store: QdrantStore):
        self.client = client
        self.store = store
        self.state = _load_state()

    async def ingest_all(self) -> int:
        total = 0
        for source in settings.monitored_channels:
            try:
                n = await self._ingest_source(source)
                total += n
            except Exception as e:
                logger.error(f"TG ingestion failed for '{source}': {e}")
        _save_state(self.state)
        return total

    async def _ingest_source(self, source: str) -> int:
        last_id: int = self.state.get(str(source), 0)
        newest_id: int = last_id
        chunks: List[Chunk] = []

        # Числовые ID (в т.ч. отрицательные) передаём как int, иначе Pyrogram не резолвит
        peer = int(source) if source.lstrip("-").isdigit() else source

        async for msg in self.client.get_chat_history(peer, limit=HISTORY_LIMIT):
            if msg.id <= last_id:
                break
            newest_id = max(newest_id, msg.id)

            # Plain text messages
            if msg.text:
                for i, piece in enumerate(chunk_text(msg.text)):
                    chunks.append(Chunk(
                        text=piece,
                        source_type="telegram_channel",
                        source_id=str(source),
                        document_title=f"msg_{msg.id}",
                        chunk_index=i,
                        metadata={"message_id": msg.id, "date": str(msg.date)},
                    ))

            # Фото — сохраняем подпись + message_id для последующей пересылки
            if msg.photo:
                caption = (msg.caption or "").strip()
                photo_text = f"[FOTO] {caption}" if caption else "[FOTO] Mahsulot fotosi"
                chunks.append(Chunk(
                    text=photo_text,
                    source_type="telegram_channel",
                    source_id=str(source),
                    document_title=f"photo_{msg.id}",
                    chunk_index=0,
                    metadata={
                        "message_id": msg.id,
                        "media_type": "photo",
                        "channel_id": str(source),
                        "has_media": True,
                        "caption": caption,
                        "date": str(msg.date),
                    },
                ))

            # Видео — аналогично
            if msg.video:
                caption = (msg.caption or "").strip()
                video_text = f"[VIDEO] {caption}" if caption else "[VIDEO] Mahsulot videosi"
                chunks.append(Chunk(
                    text=video_text,
                    source_type="telegram_channel",
                    source_id=str(source),
                    document_title=f"video_{msg.id}",
                    chunk_index=0,
                    metadata={
                        "message_id": msg.id,
                        "media_type": "video",
                        "channel_id": str(source),
                        "has_media": True,
                        "caption": caption,
                        "date": str(msg.date),
                    },
                ))

            # Document attachments (PDF / DOCX / XLSX)
            if msg.document:
                fname = msg.document.file_name or "attachment"
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                logger.info(f"[TG] msg={msg.id} document: '{fname}' ext='{ext}'")
                if ext in ("pdf", "docx", "xlsx"):
                    # pricelist = commercial offer docs; datasheet = technical PDFs
                    doc_type = "pricelist" if ext in ("docx", "xlsx") else "datasheet"
                    try:
                        raw = await self.client.download_media(msg, in_memory=True)
                        if raw:
                            file_bytes = bytes(raw.getbuffer())
                            logger.info(f"[TG] Downloaded '{fname}': {len(file_bytes)} bytes")
                            parsed = parse_document(file_bytes, fname)
                            if parsed and parsed.text.strip():
                                added = 0
                                for i, piece in enumerate(chunk_text(parsed.text)):
                                    chunks.append(Chunk(
                                        text=piece,
                                        source_type="telegram_channel",
                                        source_id=str(source),
                                        document_title=fname,
                                        chunk_index=i,
                                        metadata={"message_id": msg.id, "doc_type": doc_type},
                                    ))
                                    added += 1
                                logger.info(f"[TG] '{fname}' ({doc_type}) → {added} chunks added")
                            else:
                                logger.warning(f"[TG] '{fname}' — parser returned empty text (msg={msg.id})")
                        else:
                            logger.warning(f"[TG] download_media returned None for msg={msg.id}")
                    except Exception as e:
                        logger.error(f"[TG] Could not process attachment '{fname}' in {source}/{msg.id}: {e}", exc_info=True)
                else:
                    logger.debug(f"[TG] Skipping unsupported file type: '{fname}'")

        if chunks:
            await self.store.upsert_chunks(chunks)

        self.state[str(source)] = newest_id
        logger.info(f"[TG] {source}: {len(chunks)} new chunks (up to msg {newest_id})")
        return len(chunks)
