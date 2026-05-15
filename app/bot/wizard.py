"""
app/bot/wizard.py — пошаговый подбор АКБ через кнопки клавиатуры.

Шаги:
  1. Вольтаж       → ReplyKeyboard (selective=True — видит только этот пользователь)
  2. Ампер-час     → ReplyKeyboard
  3. Размер/габарит → ReplyKeyboard
  4. Вес (опционально, можно пропустить)
  → Запрос в RAG + рекомендация от Claude
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger
from pyrogram import Client
from pyrogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ── Персистентность состояния wizard ──────────────────────────────────────────

_WIZARD_FILE = Path("data/wizard_state.json")

# user_id → {"step": str, "voltage": str, "ah": str, "size": str, "weight": str}
_wizard_state: dict[int, dict] = {}


def _load_wizard_state() -> dict:
    if not _WIZARD_FILE.exists():
        return {}
    try:
        raw = json.loads(_WIZARD_FILE.read_text(encoding="utf-8"))
        return {int(k): v for k, v in raw.items()}
    except Exception:
        return {}


def _save_wizard_state() -> None:
    try:
        _WIZARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _WIZARD_FILE.write_text(
            json.dumps({str(k): v for k, v in _wizard_state.items()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[WIZARD] Could not save state: {e}")


_wizard_state = _load_wizard_state()

# ── Клавиатуры ─────────────────────────────────────────────────────────────────

_KB_VOLTAGE = ReplyKeyboardMarkup(
    [
        [KeyboardButton("12V"),  KeyboardButton("24V"),  KeyboardButton("36V")],
        [KeyboardButton("48V"),  KeyboardButton("60V"),  KeyboardButton("72V")],
        [KeyboardButton("80V"),  KeyboardButton("96V"),  KeyboardButton("Bilmayman / Не знаю")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_AH = ReplyKeyboardMarkup(
    [
        [KeyboardButton("20–40 Ah"),   KeyboardButton("50–80 Ah")],
        [KeyboardButton("100–150 Ah"), KeyboardButton("160–200 Ah")],
        [KeyboardButton("250–300 Ah"), KeyboardButton("350–400 Ah")],
        [KeyboardButton("400 Ah+"),    KeyboardButton("Bilmayman / Не знаю")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_SIZE = ReplyKeyboardMarkup(
    [
        [KeyboardButton("до 600×400×300 мм")],
        [KeyboardButton("до 800×500×400 мм")],
        [KeyboardButton("до 1000×600×550 мм")],
        [KeyboardButton("1000×600×550+ мм")],
        [KeyboardButton("O'lchamni bilmayman / Не знаю размер")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_WEIGHT_SKIP = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Muhim emas / Не важно")],
        [KeyboardButton("До 30 кг"),  KeyboardButton("30–60 кг")],
        [KeyboardButton("60–100 кг"), KeyboardButton("100 кг+")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

# ── Публичный API ──────────────────────────────────────────────────────────────

def is_in_wizard(user_id: int) -> bool:
    return user_id in _wizard_state


def get_wizard_result(user_id: int) -> Optional[dict]:
    """Возвращает результат если wizard завершён (step='done'), иначе None."""
    state = _wizard_state.get(user_id)
    if state and state.get("step") == "done":
        return state
    return None


def clear_wizard(user_id: int) -> None:
    _wizard_state.pop(user_id, None)
    _save_wizard_state()


async def start_wizard(client: Client, message: Message, user_name: str) -> None:
    """Запускает wizard — отправляет первый вопрос с клавиатурой."""
    user_id = message.from_user.id
    _wizard_state[user_id] = {"step": "voltage"}
    _save_wizard_state()

    lang = _detect_lang(message.text or "")
    if lang == "ru":
        text = (
            f"Хорошо, {user_name}! Помогу подобрать АКБ под вашу технику 🔋\n\n"
            f"Шаг 1 из 4 — Выберите напряжение:"
        )
    else:
        text = (
            f"Yaxshi, {user_name}! АКБ tanlashda yordam beraman 🔋\n\n"
            f"1-qadam (4 dan) — Kuchlanishni tanlang:"
        )
    await message.reply(text, reply_markup=_KB_VOLTAGE)
    logger.info(f"[WIZARD] Started for user={user_id}")


async def handle_wizard_step(
    client: Client,
    message: Message,
    user_name: str,
) -> Optional[str]:
    """
    Обрабатывает текущий шаг wizard.
    Возвращает строку-запрос для RAG если wizard завершён, иначе None.
    """
    user_id = message.from_user.id
    state = _wizard_state.get(user_id)
    if not state:
        return None

    text = (message.text or "").strip()
    lang = _detect_lang(text)
    step = state["step"]

    # ── Шаг 1: Вольтаж ────────────────────────────────────────────────────────
    if step == "voltage":
        state["voltage"] = text
        state["step"] = "ah"
        _save_wizard_state()
        if lang == "ru":
            reply = f"Отлично! ✅ Напряжение: {text}\n\nШаг 2 из 4 — Выберите ёмкость (Ампер-час):"
        else:
            reply = f"Ajoyib! ✅ Kuchlanish: {text}\n\n2-qadam (4 dan) — Sig'imni tanlang (Amper-soat):"
        await message.reply(reply, reply_markup=_KB_AH)
        return None

    # ── Шаг 2: Ампер-час ──────────────────────────────────────────────────────
    if step == "ah":
        state["ah"] = text
        state["step"] = "size"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Ёмкость: {text}\n\n"
                f"Шаг 3 из 4 — Размер отсека под АКБ в вашей технике:\n"
                f"(Длина × Ширина × Высота — выберите ближайший диапазон)"
            )
        else:
            reply = (
                f"✅ Sig'im: {text}\n\n"
                f"3-qadam (4 dan) — Texnikangizda АКБ o'rnatiladigan bo'shliq o'lchami:\n"
                f"(Uzunlik × Kenglik × Balandlik — eng yaqin diapazonni tanlang)"
            )
        await message.reply(reply, reply_markup=_KB_SIZE)
        return None

    # ── Шаг 3: Размер ─────────────────────────────────────────────────────────
    if step == "size":
        state["size"] = text
        state["step"] = "weight"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Размер: {text}\n\n"
                f"Шаг 4 из 4 — Есть ли ограничение по весу?\n"
                f"(Если не важно — нажмите «Не важно»)"
            )
        else:
            reply = (
                f"✅ O'lcham: {text}\n\n"
                f"4-qadam (4 dan) — Og'irlik bo'yicha cheklov bormi?\n"
                f"(Muhim bo'lmasa — «Muhim emas» tugmasini bosing)"
            )
        await message.reply(reply, reply_markup=_KB_WEIGHT_SKIP)
        return None

    # ── Шаг 4: Вес (опционально) → завершение ─────────────────────────────────
    if step == "weight":
        state["weight"] = text
        state["step"] = "done"
        _save_wizard_state()

        # Убираем клавиатуру
        await message.reply(
            "⏳ Analizlayman va sizga mos variantlarni qidiraman..."
            if lang != "ru" else
            "⏳ Анализирую и подбираю подходящие варианты...",
            reply_markup=ReplyKeyboardRemove(selective=True),
        )

        # Формируем поисковый запрос для RAG
        query = _build_rag_query(state)
        logger.info(f"[WIZARD] Done for user={user_id} | query: {query!r}")
        return query

    return None


def build_wizard_context(state: dict) -> str:
    """Формирует контекст о выборе пользователя для Claude."""
    voltage = state.get("voltage", "—")
    ah      = state.get("ah",      "—")
    size    = state.get("size",    "—")
    weight  = state.get("weight",  "—")

    size_note = ""
    if size and "bilmayman" not in size.lower() and "не знаю" not in size.lower():
        size_note = f" (отсек под АКБ: максимум {size})"

    return (
        f"[Mijoz tanlovi / Выбор клиента]\n"
        f"Kuchlanish / Напряжение: {voltage}\n"
        f"Sig'im / Ёмкость: {ah}\n"
        f"O'lcham bo'shlig'i / Размер отсека: {size}{size_note}\n"
        f"Og'irlik / Вес: {weight}\n"
        f"[/Mijoz tanlovi]\n\n"
        f"Важно: подбери модели которые физически помещаются в отсек {size}. "
        f"Сравни с реальными габаритами моделей из базы (Д×Ш×В мм)."
    )


# ── Внутренние хелперы ─────────────────────────────────────────────────────────

def _detect_lang(text: str) -> str:
    if not text:
        return "uz"
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    return "ru" if cyrillic / max(len(text), 1) > 0.3 else "uz"


def _build_rag_query(state: dict) -> str:
    parts = ["тяговый аккумулятор АКБ"]
    v = state.get("voltage", "")
    if v and "знаю" not in v and "mayman" not in v:
        parts.append(v)
    a = state.get("ah", "")
    if a and "знаю" not in a and "mayman" not in a:
        parts.append(a)
    s = state.get("size", "")
    if s and "bilmayman" not in s.lower() and "не знаю" not in s.lower():
        parts.append(f"габариты {s} мм")
    w = state.get("weight", "")
    if w and "важно" not in w and "muhim" not in w.lower():
        parts.append(w.split("/")[0].strip())
    return " ".join(parts)
