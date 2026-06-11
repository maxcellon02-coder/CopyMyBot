"""
app/rag/sources/local_docs.py — индексация локальных документов знаний
в Qdrant: условия гарантии, инструкции по эксплуатации и т.п.

Просто положите файл (.md/.txt) в data/knowledge/ — при следующей индексации
(старт бота или ежечасно) он попадёт в базу знаний бота, и бот сможет отвечать
на конкретные вопросы по нему (например, условия гарантии для AGM/GEL, LiFePO4).

Файлы с «warranty/гарант/kafolat» в имени помечаются doc_type="warranty" —
ретривер отдаёт им приоритет на гарантийные вопросы.
"""
from pathlib import Path
from typing import List

from loguru import logger

from app.rag.chunk import Chunk
from app.rag.sources.parser import chunk_text
from app.rag.vector_store import QdrantStore

KNOWLEDGE_DIR = Path("data/knowledge")
SOURCE_TYPE = "knowledge"
_EXT = (".md", ".txt")


def _doc_type_for(name: str) -> str:
    low = name.lower()
    if any(k in low for k in ("warranty", "garant", "kafolat", "гарант")):
        return "warranty"
    return "knowledge"


class LocalDocsIngester:
    """Индексирует data/knowledge/*.{md,txt} в Qdrant."""

    def __init__(self, store: QdrantStore):
        self.store = store

    async def ingest_all(self) -> int:
        if not KNOWLEDGE_DIR.exists():
            return 0
        files = [p for p in KNOWLEDGE_DIR.iterdir() if p.suffix.lower() in _EXT]
        chunks: List[Chunk] = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"[KNOWLEDGE] Не удалось прочитать {p.name}: {e}")
                continue
            if not text:
                continue
            dt = _doc_type_for(p.name)
            for i, piece in enumerate(chunk_text(text)):
                chunks.append(Chunk(
                    text=piece,
                    source_type=SOURCE_TYPE,
                    source_id=p.name,
                    document_title=p.stem,
                    chunk_index=i,
                    metadata={"doc_type": dt},
                ))
        if chunks:
            await self.store.upsert_chunks(chunks)
        logger.info(
            f"[KNOWLEDGE] Проиндексировано: {len(chunks)} чанков из {len(files)} файл(ов)"
        )
        return len(chunks)
