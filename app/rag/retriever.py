"""
RAG retrieval — embeds a user query, searches Qdrant, and returns
the top-k text chunks as a formatted context string for Claude.
"""
import re
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


# ── Структурный поиск по каталогу: точный матч напряжения/ёмкости ─────────────
_VOLT_RE = re.compile(r"(\d{1,3}(?:\.\d)?)\s*[vв]", re.IGNORECASE)       # 6V, 48V, 80в, 25.6V
_AH_RE = re.compile(r"(\d{2,4})\s*(?:ah|а[/.\s]?ч|ампер|а·ч)", re.IGNORECASE)  # 400Ah, 245 а/ч


def _parse_specs(query: str) -> tuple[Optional[str], Optional[str]]:
    volt = _VOLT_RE.search(query)
    ah = _AH_RE.search(query)
    return (volt.group(1) if volt else None, ah.group(1) if ah else None)


async def _catalog_by_specs(vector: list, query: str, top_k: int) -> Optional[list]:
    """Если в запросе есть V/Ah — фильтруем каталог по ним (точный матч).

    Пробуем V+Ah, затем Ah, затем V — первый непустой результат.
    """
    volt, ah = _parse_specs(query)
    if not (volt or ah):
        return None
    attempts: List[dict] = []
    if volt and ah:
        attempts.append({"voltage": volt, "ah": ah})
    if ah:
        attempts.append({"ah": ah})
    if volt:
        attempts.append({"voltage": volt})
    for extra in attempts:
        flt = {"doc_type": "catalog", **extra}
        results = await get_store().search(vector, top_k=top_k, filter_by=flt)
        if results:
            return results
    return None


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

    # 0) Точный матч каталога по напряжению/ёмкости (если есть в запросе)
    structured = await _catalog_by_specs(vector, query, top_k)
    if structured:
        return _fmt_results(structured)

    if _is_price_query(query):
        # 1) Каталог «Master Data Base» — основной источник со структурными ценами
        results = await get_store().search(
            vector, top_k=top_k, filter_by={"doc_type": "catalog"}
        )
        if results:
            return _fmt_results(results)
        # 2) Прайс-листы (DOCX/XLSX из канала)
        results = await get_store().search(
            vector, top_k=top_k, filter_by={"doc_type": "pricelist"}
        )
        if results:
            return _fmt_results(results)
        # 3) Fallback: неограниченный поиск

    filters = {"source_type": source_type} if source_type else None
    results = await get_store().search(vector, top_k=top_k, filter_by=filters)
    if not results:
        return ""
    return _fmt_results(results)
