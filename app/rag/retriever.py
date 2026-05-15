"""
RAG retrieval — embeds a user query, searches Qdrant, and returns
the top-k text chunks as a formatted context string for Claude.
"""
from typing import List, Optional

from app.rag.embedder import embed_query
from app.rag.vector_store import QdrantStore

_store: Optional[QdrantStore] = None


def get_store() -> QdrantStore:
    global _store
    if _store is None:
        _store = QdrantStore()
    return _store


async def retrieve_media(query: str, top_k: int = 2) -> list:
    """Ищет медиа-чанки (фото/видео) в Qdrant по запросу. Возвращает список metadata."""
    vector = await embed_query(query)
    results = await get_store().search(vector, top_k=top_k, filter_by={"has_media": True})
    items = []
    for r in results:
        p = r.payload
        if p.get("has_media") and p.get("message_id") and p.get("channel_id"):
            items.append({
                "message_id": int(p["message_id"]),
                "channel_id":  int(p["channel_id"]),
                "media_type":  p.get("media_type", "photo"),
                "caption":     p.get("caption", ""),
                "score":       round(r.score, 3),
            })
    return items


_PRICE_KEYWORDS = (
    "narx", "narxi", "narxlari", "qancha", "baho",
    "цена", "стоим", "прайс", "почём", "сколько",
    "price", "cost",
)


def _is_price_query(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _PRICE_KEYWORDS)


def _fmt_results(results: list) -> str:
    parts: List[str] = []
    for r in results:
        payload = r.payload
        title = payload.get("document_title", "source")
        text = payload.get("text", "")
        score = round(r.score, 3)
        parts.append(f"[{title} | relevance={score}]\n{text}")
    return "\n\n---\n\n".join(parts)


async def retrieve(
    query: str,
    top_k: int = 5,
    source_type: Optional[str] = None,
) -> str:
    """
    Returns a formatted context block for Claude.
    For price queries automatically searches only pricelist (DOCX/XLSX) chunks.
    Falls back to full search if pricelist returns nothing.
    """
    vector = await embed_query(query)

    if _is_price_query(query):
        # First try: pricelist documents only
        results = await get_store().search(
            vector, top_k=top_k, filter_by={"doc_type": "pricelist"}
        )
        if results:
            return _fmt_results(results)
        # Fallback: unrestricted search (for cases where doc_type not yet indexed)

    filters = {"source_type": source_type} if source_type else None
    results = await get_store().search(vector, top_k=top_k, filter_by=filters)
    if not results:
        return ""
    return _fmt_results(results)
