"""
app/media/smm_cache.py — локальный кэш медиафайлов SAPO SMM.

Источник: SQLite-база платформы SMM (вкладка «Медиа») — таблица `media`
с полями url / kind / folder / caption / doc_text. Файлы лежат локально в
`smm/static/...`. Имена файлов — хеши, поэтому привязка «модель → файл»
берётся из folder (для фото: `LEOCH/LFP/Photos/FT48-500`) и caption (для
датащитов: `LB-LFeLi-FT48-400(48V400Ah)(...).pdf`).

Кэш строится в `data/media_cache.json` и используется ботом: при ответе с
тегом [MEDIA_SEND: модель] / [SEND_DOC: модель] движок ищет здесь нужный
файл и отправляет его клиенту.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.config import settings

CACHE_FILE = Path("data/media_cache.json")

# ── Извлечение кода модели ──────────────────────────────────────────────────
# FT48-400, FT80-205, LDC12-120, LDC6-265-GC2, LDC8-165-GC8, MKS-GP48150, DT1275
_MODEL_RE = re.compile(
    r"(FT\d{2,3}-\d{2,4}"
    r"|LDC\d{1,2}-\d{2,4}(?:-GC\d)?"
    r"|MKS-?GP?\d+"
    r"|DT-?[A-Z]?\d{2,4})",
    re.IGNORECASE,
)

# Вольтаж/ёмкость из подписи: (48V400Ah), 48V-400, 25.6V200Ah, 6V210Ah
_VAH_RE = re.compile(r"(\d{1,3})\s*V[\s\-(]*?(\d{2,4})\s*Ah", re.IGNORECASE)
# Вольтаж/ёмкость прямо из кода модели: LDC6-210 → 6V/210Ah, FT48-400 → 48V/400Ah
_MODEL_VA_RE = re.compile(r"(?:LDC|FT)(\d{1,3})-(\d{2,4})", re.IGNORECASE)

# Папки, которые НЕ отправляем клиенту (внутренние/брендовые)
_EXCLUDE_FOLDER = ("логотип", "brandbook", "лого", "партнер", "к партнерам", "_debug")


# Кириллические буквы-двойники → латиница (каталог пишет «LDС6-245» с кир. «С»)
_CYR2LAT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I",
})


def _translit(s: str) -> str:
    return s.translate(_CYR2LAT)


def _norm_model(s: str) -> str:
    """FT48-400 / ft48 400 / FT48–400 / LDС6-245(кир.) → нормальный латинский код."""
    s = _translit(s.strip().upper().replace("–", "-").replace("—", "-"))
    s = re.sub(r"\s+", "", s)
    return s


def _extract_models(*texts: str) -> list[str]:
    found: list[str] = []
    for t in texts:
        if not t:
            continue
        for m in _MODEL_RE.findall(_translit(t)):
            code = _norm_model(m if isinstance(m, str) else m[0])
            if code not in found:
                found.append(code)
    return found


def _category(folder: str, kind: str) -> str:
    f = (folder or "").lower()
    if "datasheet" in f:
        return "datasheet"
    if "сертификат" in f or "certificate" in f:
        return "certificate"
    if "каталог" in f or "catalog" in f:
        return "catalog"
    if any(x in f for x in _EXCLUDE_FOLDER):
        return "brand"
    if kind == "video":
        return "video"
    if kind == "document":
        return "doc"
    return "photo"


def _abs_path(url: str, media_root: str) -> Optional[str]:
    if not url:
        return None
    rel = url.lstrip("/")
    p = os.path.join(media_root, rel)
    return p if os.path.exists(p) else None


# ── Построение кэша из smm.db ───────────────────────────────────────────────

def build_cache(
    db_path: Optional[str] = None,
    media_root: Optional[str] = None,
    write: bool = True,
) -> dict:
    """Читает smm.db и строит индекс модель→файлы. Возвращает структуру кэша."""
    db_path = db_path or settings.smm_db_path
    media_root = media_root or settings.smm_media_root

    if not db_path or not os.path.exists(db_path):
        logger.warning(f"[MEDIA] smm.db не найдена: {db_path}")
        return {"models": {}, "items": []}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    models: dict[str, dict] = {}
    items: list[dict] = []
    skipped = 0

    for row in conn.execute(
        "SELECT url, kind, folder, caption, doc_text FROM media"
    ):
        path = _abs_path(row["url"], media_root)
        if not path:
            skipped += 1
            continue
        folder = row["folder"] or ""
        caption = row["caption"] or ""
        kind = row["kind"] or "image"
        cat = _category(folder, kind)

        codes = _extract_models(caption, folder)
        # Вольтаж/ёмкость (для поиска по «6v 210», «48v 400»)
        vah = _VAH_RE.search(caption) or _VAH_RE.search(folder)
        volt, ah = (vah.group(1), vah.group(2)) if vah else (None, None)
        # Если в тексте нет — извлекаем из кода модели (LDC6-210 → 6V/210Ah)
        if not volt and codes:
            for c in codes:
                mv = _MODEL_VA_RE.match(c)
                if mv:
                    volt, ah = mv.group(1), mv.group(2)
                    break

        entry = {
            "path": path,
            "kind": kind,
            "category": cat,
            "folder": folder,
            "caption": caption,
            "models": codes,
            "volt": volt,
            "ah": ah,
        }
        items.append(entry)

        for code in codes:
            slot = models.setdefault(
                code,
                {"photo": [], "video": [], "datasheet": [], "certificate": [], "catalog": []},
            )
            bucket = cat if cat in slot else "photo"
            if path not in slot[bucket]:
                slot[bucket].append(path)

    conn.close()
    cache = {"models": models, "items": items}

    if write:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(
                json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[MEDIA] Не удалось записать кэш: {e}")

    logger.info(
        f"[MEDIA] Кэш построен: {len(models)} модель(ей), "
        f"{len(items)} файл(ов), пропущено {skipped}"
    )
    return cache


# ── Ленивая загрузка ────────────────────────────────────────────────────────

_cache: Optional[dict] = None


def get_cache(force_rebuild: bool = False) -> dict:
    global _cache
    if _cache is not None and not force_rebuild:
        return _cache
    if not force_rebuild and CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return _cache
        except Exception as e:
            logger.warning(f"[MEDIA] Кэш повреждён, пересобираю: {e}")
    _cache = build_cache()
    return _cache


def reload_cache() -> dict:
    """Пересобрать кэш из smm.db (для /команды или планировщика)."""
    return get_cache(force_rebuild=True)


# ── Поиск ───────────────────────────────────────────────────────────────────

# want="photo" → фото/видео; want="doc" → датащит, затем сертификат (там тоже specs)
_WANT_BUCKETS = {
    "photo": ("photo", "video"),
    "doc": ("datasheet", "certificate", "catalog"),
    "any": ("photo", "video", "datasheet", "certificate", "catalog"),
}


def lookup(query: str, want: str = "photo", limit: int = 5) -> list[str]:
    """Возвращает список путей к файлам по модели/запросу.

    want: 'photo' (фото+видео) | 'doc' (датащит/сертификат) | 'any'.
    """
    cache = get_cache()
    buckets = _WANT_BUCKETS.get(want, _WANT_BUCKETS["any"])
    models = cache.get("models", {})

    # 1) Точный код модели в запросе
    codes = _extract_models(query)
    paths: list[str] = []
    for code in codes:
        slot = models.get(code)
        if not slot:
            # частичное совпадение (FT48 → FT48-400)
            slot = _merge_slots(models, code)
        if slot:
            for b in buckets:
                for p in slot.get(b, []):
                    if p not in paths:
                        paths.append(p)
    if paths:
        return paths[:limit]

    # 2) Поиск по вольтажу/ёмкости («48v 400»)
    vah = _VAH_RE.search(query) or re.search(r"(\d{2,3})\s*[vв].*?(\d{2,4})", query, re.I)
    if vah:
        volt, ah = vah.group(1), vah.group(2)
        for it in cache.get("items", []):
            if it["category"] in buckets and it.get("volt") == volt and it.get("ah") == ah:
                if it["path"] not in paths:
                    paths.append(it["path"])
    if paths:
        return paths[:limit]

    # 3) Ключевые слова по folder/caption (бренд, линейка)
    q = query.lower().strip()
    if len(q) >= 3:
        for it in cache.get("items", []):
            if it["category"] not in buckets:
                continue
            hay = (it["folder"] + " " + it["caption"]).lower()
            if q in hay:
                if it["path"] not in paths:
                    paths.append(it["path"])
    return paths[:limit]


def _merge_slots(models: dict, prefix: str) -> Optional[dict]:
    """Объединяет слоты моделей, начинающихся с prefix (FT48 → FT48-400, FT48-460…)."""
    merged: dict = {}
    hit = False
    for code, slot in models.items():
        if code.startswith(prefix):
            hit = True
            for b, paths in slot.items():
                merged.setdefault(b, [])
                for p in paths:
                    if p not in merged[b]:
                        merged[b].append(p)
    return merged if hit else None
