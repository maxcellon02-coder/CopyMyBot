"""
app/bot/wizard.py — пошаговый подбор АКБ через кнопки клавиатуры.

Шаги (7):
  1. Вольтаж       → ReplyKeyboard
  2. Ампер-час     → ReplyKeyboard
  3. Тип батареи   → ReplyKeyboard (LiFePO4 / PzS / PzB)
  4. Размер/габарит → ReplyKeyboard
  5. Тип техники   → ReplyKeyboard (погрузчик, штабелёр …)
  6. Количество    → ReplyKeyboard
  7. Компания      → свободный ввод текста
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

_wizard_state: dict[int, dict] = {}

TOTAL_STEPS = 7


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
        [KeyboardButton("2V"),  KeyboardButton("6V"),  KeyboardButton("12V")],
        [KeyboardButton("24V"), KeyboardButton("48V"), KeyboardButton("80V")],
        [KeyboardButton("✏️ Boshqa / Другое"), KeyboardButton("Bilmayman / Не знаю")],
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
        [KeyboardButton("400 Ah+"),    KeyboardButton("✏️ Boshqa / Другое")],
        [KeyboardButton("Bilmayman / Не знаю")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_BATTERY_TYPE = ReplyKeyboardMarkup(
    [
        [KeyboardButton("LiFePO4 (litiy / литий)")],
        [KeyboardButton("PzS (kislotali ochiq / кислотные открытые)")],
        [KeyboardButton("PzB (kislotali yopiq / кислотные закрытые)")],
        [KeyboardButton("Bilmayman / Не знаю")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_SIZE = ReplyKeyboardMarkup(
    [
        [KeyboardButton("до 600×400×300 мм"), KeyboardButton("до 800×500×400 мм")],
        [KeyboardButton("до 1000×600×550 мм"), KeyboardButton("более 1000×600×550 мм")],
        [KeyboardButton("Cheklov yo'q / Без ограничений")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_EQUIPMENT = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Pogʻruzchik / Погрузчик"), KeyboardButton("Shtabeler / Штабелёр")],
        [KeyboardButton("Elektr aravacha / Электротележка"), KeyboardButton("Richtrak / Ричтрак")],
        [KeyboardButton("Boshqa texnika / Другая техника")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

_KB_QUANTITY = ReplyKeyboardMarkup(
    [
        [KeyboardButton("1 dona / 1 шт"),  KeyboardButton("2–5 dona / 2–5 шт")],
        [KeyboardButton("6–10 dona / 6–10 шт"), KeyboardButton("10+ dona / 10+ шт")],
        [KeyboardButton("Bilmayman / Не знаю")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
    selective=True,
)

# ── Публичный API ──────────────────────────────────────────────────────────────

def is_in_wizard(user_id: int) -> bool:
    return user_id in _wizard_state


def get_wizard_result(user_id: int) -> Optional[dict]:
    state = _wizard_state.get(user_id)
    if state and state.get("step") == "done":
        return state
    return None


def clear_wizard(user_id: int) -> None:
    _wizard_state.pop(user_id, None)
    _save_wizard_state()


async def start_wizard(client: Client, message: Message, user_name: str) -> None:
    user_id = message.from_user.id
    _wizard_state[user_id] = {"step": "voltage"}
    _save_wizard_state()

    lang = _detect_lang(message.text or "")
    if lang == "ru":
        text = (
            f"Хорошо, {user_name}! Помогу подобрать АКБ под вашу технику 🔋\n\n"
            f"Шаг 1 из {TOTAL_STEPS} — Какое напряжение нужно?"
        )
    else:
        text = (
            f"Yaxshi, {user_name}! АКБ tanlashda yordam beraman 🔋\n\n"
            f"1-qadam ({TOTAL_STEPS} dan) — Qanday kuchlanish kerak?"
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
            reply = (
                f"✅ Напряжение: {text}\n\n"
                f"Шаг 2 из {TOTAL_STEPS} — Какая ёмкость нужна (Ампер-час)?"
            )
        else:
            reply = (
                f"✅ Kuchlanish: {text}\n\n"
                f"2-qadam ({TOTAL_STEPS} dan) — Qancha sig'im kerak (Amper-soat)?"
            )
        await message.reply(reply, reply_markup=_KB_AH)
        return None

    # ── Шаг 2: Ампер-час ──────────────────────────────────────────────────────
    if step == "ah":
        state["ah"] = text
        state["step"] = "battery_type"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Ёмкость: {text}\n\n"
                f"Шаг 3 из {TOTAL_STEPS} — Какой тип батареи предпочтителен?"
            )
        else:
            reply = (
                f"✅ Sig'im: {text}\n\n"
                f"3-qadam ({TOTAL_STEPS} dan) — Qaysi turdagi batareya afzal?"
            )
        await message.reply(reply, reply_markup=_KB_BATTERY_TYPE)
        return None

    # ── Шаг 3: Тип батареи ────────────────────────────────────────────────────
    if step == "battery_type":
        state["battery_type"] = text
        state["step"] = "size"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Тип батареи: {text}\n\n"
                f"Шаг 4 из {TOTAL_STEPS} — Есть ли ограничения по габаритам?\n"
                f"(Длина × Ширина × Высота — выберите ближайший диапазон)"
            )
        else:
            reply = (
                f"✅ Batareya turi: {text}\n\n"
                f"4-qadam ({TOTAL_STEPS} dan) — Gabaritlar bo'yicha cheklov bormi?\n"
                f"(Uzunlik × Kenglik × Balandlik — eng yaqin diapazonni tanlang)"
            )
        await message.reply(reply, reply_markup=_KB_SIZE)
        return None

    # ── Шаг 4: Размер ─────────────────────────────────────────────────────────
    if step == "size":
        state["size"] = text
        state["step"] = "equipment"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Размер: {text}\n\n"
                f"Шаг 5 из {TOTAL_STEPS} — Для какой техники нужна батарея?"
            )
        else:
            reply = (
                f"✅ O'lcham: {text}\n\n"
                f"5-qadam ({TOTAL_STEPS} dan) — Qaysi texnika uchun batareya kerak?"
            )
        await message.reply(reply, reply_markup=_KB_EQUIPMENT)
        return None

    # ── Шаг 5: Тип техники ────────────────────────────────────────────────────
    if step == "equipment":
        state["equipment"] = text
        state["step"] = "quantity"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Техника: {text}\n\n"
                f"Шаг 6 из {TOTAL_STEPS} — Сколько батарей нужно?"
            )
        else:
            reply = (
                f"✅ Texnika: {text}\n\n"
                f"6-qadam ({TOTAL_STEPS} dan) — Nechta batareya kerak?"
            )
        await message.reply(reply, reply_markup=_KB_QUANTITY)
        return None

    # ── Шаг 6: Количество ─────────────────────────────────────────────────────
    if step == "quantity":
        state["quantity"] = text
        state["step"] = "company"
        _save_wizard_state()
        if lang == "ru":
            reply = (
                f"✅ Количество: {text}\n\n"
                f"Шаг 7 из {TOTAL_STEPS} — Напишите название вашей компании\n"
                f"(или напишите «частное лицо» если не от компании)"
            )
        else:
            reply = (
                f"✅ Miqdor: {text}\n\n"
                f"7-qadam ({TOTAL_STEPS} dan) — Kompaniyangiz nomini yozing\n"
                f"(yoki «jismoniy shaxs» deb yozing agar kompaniyasiz bo'lsangiz)"
            )
        await message.reply(reply, reply_markup=ReplyKeyboardRemove(selective=True))
        return None

    # ── Шаг 7: Компания → завершение ──────────────────────────────────────────
    if step == "company":
        state["company"] = text
        state["step"] = "done"
        _save_wizard_state()

        await message.reply(
            "⏳ Analizlayman va sizga mos variantlarni qidiraman..."
            if lang != "ru" else
            "⏳ Анализирую и подбираю подходящие варианты...",
            reply_markup=ReplyKeyboardRemove(selective=True),
        )

        query = _build_rag_query(state)
        logger.info(f"[WIZARD] Done for user={user_id} | query: {query!r}")
        return query

    return None


def build_wizard_context(state: dict) -> str:
    """Формирует контекст о выборе пользователя для Claude."""
    voltage      = state.get("voltage",      "—")
    ah           = state.get("ah",           "—")
    battery_type = state.get("battery_type", "—")
    size         = state.get("size",         "—")
    equipment    = state.get("equipment",    "—")
    quantity     = state.get("quantity",     "—")
    company      = state.get("company",      "—")

    size_note = ""
    no_limit_kw = ("cheklov yo'q", "без ограничений")
    if size and not any(kw in size.lower() for kw in no_limit_kw):
        size_note = f" (отсек под АКБ: максимум {size})"

    return (
        f"[Mijoz tanlovi / Выбор клиента]\n"
        f"Kuchlanish / Напряжение: {voltage}\n"
        f"Sig'im / Ёмкость: {ah}\n"
        f"Batareya turi / Тип батареи: {battery_type}\n"
        f"O'lcham / Размер: {size}{size_note}\n"
        f"Texnika / Техника: {equipment}\n"
        f"Miqdor / Количество: {quantity}\n"
        f"Kompaniya / Компания: {company}\n"
        f"[/Mijoz tanlovi]\n\n"
        f"Важно: подбери модели которые физически помещаются в отсек {size}. "
        f"Сравни с реальными габаритами моделей из базы (Д×Ш×В мм). "
        f"Тип батареи клиента: {battery_type}."
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

    bt = state.get("battery_type", "")
    if bt and "знаю" not in bt and "mayman" not in bt.lower():
        # Извлекаем краткое обозначение (LiFePO4 / PzS / PzB)
        for kw in ("LiFePO4", "PzS", "PzB"):
            if kw.lower() in bt.lower():
                parts.append(kw)
                break

    s = state.get("size", "")
    no_limit_kw = ("cheklov yo'q", "без ограничений")
    if s and not any(kw in s.lower() for kw in no_limit_kw):
        parts.append(f"габариты {s} мм")

    eq = state.get("equipment", "")
    if eq and "другая" not in eq.lower() and "boshqa" not in eq.lower():
        parts.append(eq.split("/")[0].strip())

    return " ".join(parts)
