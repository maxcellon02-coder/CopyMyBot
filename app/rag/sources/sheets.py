"""
app/rag/sources/sheets.py — индексация каталога «Master Data Base» (Google Sheet)
в Qdrant как основной источник знаний бота.

Каждая строка таблицы = один товар = один чанк с человекочитаемым описанием
(модель, тип, напряжение, ёмкость, цена, наличие, применение, техника...).
Идемпотентно: при повторном запуске точки перезаписываются по Артикулу.

Доступ: тот же service_account.json, что и у мониторинга заявок
(config/service_account.json), таблица расшарена на этот аккаунт.
"""
import asyncio
import re
from pathlib import Path
from typing import List

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

from app.core.config import settings
from app.rag.chunk import Chunk
from app.rag.vector_store import QdrantStore

ROOT = Path(__file__).resolve().parents[3]
CREDS_FILE = ROOT / "config" / "service_account.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

SOURCE_TYPE = "catalog"

# Кол-во штук из артикула: «0002-2хLDС6-245» → 2 (для сборки нужного вольтажа)
_ARTICLE_QTY_RE = re.compile(r"-\s*(\d+)\s*[xхХX]")


def _article_qty(article: str) -> str:
    m = _ARTICLE_QTY_RE.search(article or "")
    return m.group(1) if m else ""


def _v(row: dict, *keys: str) -> str:
    """Первое непустое значение по списку возможных имён колонок."""
    for k in keys:
        val = str(row.get(k, "")).strip()
        if val:
            return val
    return ""


def _product_text(row: dict) -> str:
    """Собирает читаемое описание товара для эмбеддинга/контекста Claude."""
    name = _v(row, "Название_Общее", "Название")
    model = _v(row, "Модель")
    article = _v(row, "Артикуль", "Артикул")
    category = _v(row, "Категория товара")
    btype = _v(row, "Тип_АКБ")
    brand = _v(row, "Бренд")
    volt = _v(row, "Напряжение_V")
    ah = _v(row, "Емкость_Ah")
    apps = _v(row, "Области применения")
    tech = _v(row, "Техники")
    life = _v(row, "Срок службы")
    cycles = _v(row, "Цикл жизни")
    warranty = _v(row, "Срок гарантии")
    worktime = _v(row, "Рабочее время")
    charge = _v(row, "Время зарядки")
    price_r = _v(row, "Цена_Розница")
    price_w = _v(row, "Цена_Опт")
    cur = _v(row, "Валюта") or "UZS"
    min_opt = _v(row, "Минимальный_Заказ_Оптом")
    avail = _v(row, "Наличие")
    city = _v(row, "Город")
    keywords = _v(row, "Ключевые_Слова")

    lines: List[str] = []
    if name:
        lines.append(name)
    head = []
    if model:
        head.append(f"Модель: {model}")
    if article:
        head.append(f"Артикул: {article}")
    if head:
        lines.append(" | ".join(head))
    spec = []
    if category:
        spec.append(f"Категория: {category}")
    if btype:
        spec.append(f"Тип АКБ: {btype}")
    if brand:
        spec.append(f"Бренд: {brand}")
    if spec:
        lines.append(" | ".join(spec))
    vah = []
    if volt:
        vah.append(f"Напряжение: {volt}V")
    if ah:
        vah.append(f"Ёмкость: {ah}Ah")
    if vah:
        lines.append(" | ".join(vah))
    # Сборка из артикула: N штук базовой модели последовательно = нужный вольтаж
    qty = _article_qty(article)
    if qty and qty != "1" and model:
        lines.append(
            f"Сборка: {qty} шт. модели {model} последовательно = {volt}V "
            f"(цена указана за комплект из {qty} шт.)"
        )
    if apps:
        lines.append(f"Применение: {apps}")
    if tech:
        lines.append(f"Техника: {tech}")
    extra = []
    if life:
        extra.append(f"Срок службы: {life}")
    if cycles:
        extra.append(f"Циклы: {cycles}")
    if warranty:
        extra.append(f"Гарантия: {warranty}")
    if worktime:
        extra.append(f"Рабочее время: {worktime}")
    if charge:
        extra.append(f"Время зарядки: {charge}")
    if extra:
        lines.append(" | ".join(extra))
    price_bits = []
    if price_r:
        price_bits.append(f"Цена розница: {price_r} {cur}")
    if price_w:
        price_bits.append(f"опт: {price_w} {cur}")
    if min_opt:
        price_bits.append(f"мин. опт. заказ: {min_opt}")
    if price_bits:
        lines.append(" | ".join(price_bits))
    if avail:
        lines.append(f"Наличие: {avail}")
    if city:
        lines.append(f"Город: {city}")
    if keywords:
        lines.append(f"Ключевые слова: {keywords}")
    return "\n".join(lines)


def _fetch_rows() -> list[dict]:
    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(settings.catalog_sheet_id)
    ws = sh.worksheet(settings.catalog_worksheet)
    return ws.get_all_records()


class SheetCatalogIngester:
    """Индексирует каталог «Master Data Base» в Qdrant."""

    def __init__(self, store: QdrantStore):
        self.store = store

    async def ingest_all(self) -> int:
        if not CREDS_FILE.exists():
            logger.warning(f"[CATALOG] {CREDS_FILE} не найден — каталог пропущен")
            return 0
        if not settings.catalog_sheet_id:
            return 0

        rows = await asyncio.to_thread(_fetch_rows)
        chunks: List[Chunk] = []
        for i, row in enumerate(rows):
            text = _product_text(row)
            if not text.strip():
                continue
            article = _v(row, "Артикуль", "Артикул") or f"row{i + 2}"
            model = _v(row, "Модель")
            chunks.append(Chunk(
                text=text,
                source_type=SOURCE_TYPE,
                source_id=article,
                document_title=model or _v(row, "Название_Общее") or article,
                chunk_index=0,
                metadata={
                    "doc_type": "catalog",
                    "article": article,
                    "model": model,
                    "brand": _v(row, "Бренд"),
                    "battery_type": _v(row, "Тип_АКБ"),
                    "voltage": _v(row, "Напряжение_V"),
                    "ah": _v(row, "Емкость_Ah"),
                    "set_qty": _article_qty(article),
                    "price_retail": _v(row, "Цена_Розница"),
                    "price_wholesale": _v(row, "Цена_Опт"),
                    "currency": _v(row, "Валюта") or "UZS",
                    "availability": _v(row, "Наличие"),
                },
            ))

        if chunks:
            await self.store.upsert_chunks(chunks)
        logger.info(f"[CATALOG] Проиндексировано товаров: {len(chunks)} из {len(rows)}")
        return len(chunks)
