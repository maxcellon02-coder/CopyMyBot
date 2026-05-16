"""
scripts/check_leads.py — мониторинг Google Таблицы "Maxcellon Заявки".

Запуск:
    python scripts/check_leads.py

Что делает:
  • Каждые 5 минут читает Google Sheet "Maxcellon Заявки"
  • Находит строки где колонка M (Ечим) пустая
  • Отправляет карточку заявки в MANAGER_GROUP_ID через отдельную Pyrogram сессию
  • Пишет "✅ Юборилди" в колонку M чтобы не отправлять повторно

Требования:
  • config/service_account.json — сервисный аккаунт Google
  • .env — MANAGER_GROUP_ID, TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
  • GOOGLE_SHEETS_ID в .env (ID таблицы)

Сессия: data/sessions/leads_session  (НЕ трогает bot_session)
"""

import asyncio
import os
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger
from pyrogram import Client, enums

from app.core.config import settings

# ── Настройки ──────────────────────────────────────────────────────────────────
SHEET_NAME      = "Maxcellon Заявки"
CHECK_INTERVAL  = 2 * 60
CREDS_FILE      = ROOT / "config" / "service_account.json"
DONE_MARK       = "✅ Юборилди"
STATUS_COL      = 13              # колонка M (1-based)
SESSION_NAME    = "leads_session"
SESSIONS_DIR    = ROOT / "data" / "sessions"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Реальная структура таблицы (A–L данные, M = Ечим):
_FIXED_COL_MAP: dict[str, int] = {
    "time": 0, "name": 1, "phone": 2, "company": 3,
    "equipment": 4, "brand": 5, "battery": 6, "voltage": 7,
    "ah": 8, "quantity": 9, "model": 10, "notes": 11,
}

_COL_ALIASES = {
    "time":      ["вақт / дата", "вақт", "дата", "время", "vaqt", "time", "sana", "timestamp"],
    "name":      ["исм фамилия", "исм", "имя", "фио", "ф.и.о", "ism familiya", "ism", "name"],
    "phone":     ["телефон", "telefon", "тел", "phone", "номер", "raqam"],
    "company":   ["компания", "kompaniya", "company", "организация", "firma", "korxona"],
    "equipment": ["техника", "texnika", "equipment", "тип техники", "mashina", "transport"],
    "brand":     ["марка", "marka", "brand", "брэнд"],
    "battery":   ["акб тури", "акб", "аккумулятор", "akb turi", "akb", "battery", "тип акб", "batareya"],
    "voltage":   ["вольтаж", "kuchlanish", "voltage", "volt", "вольт", "напряжение"],
    "ah":        ["ампер соат", "ампер-час", "ampere soat", "ah", "sig'im", "ёмкость", "амперчас"],
    "quantity":  ["миқдори", "количество", "miqdori", "miqdor", "quantity", "кол-во", "dona"],
    "model":     ["модель", "model"],
    "notes":     ["изоҳ", "примечание", "izoh", "notes", "comment", "комментарий"],
}


def _ensure_session():
    pass  # bot_session используется напрямую


def _build_col_map(headers: list[str]) -> dict[str, int]:
    col_map: dict[str, int] = {}
    for idx, h in enumerate(headers):
        h_low = h.strip().lower()
        for field, aliases in _COL_ALIASES.items():
            if h_low in aliases and field not in col_map:
                col_map[field] = idx
    if len(col_map) < len(_FIXED_COL_MAP) // 2:
        logger.info("[SHEETS] Заголовки не распознаны — используем фиксированные позиции A–L")
        return dict(_FIXED_COL_MAP)
    return col_map


def _get(row: list[str], col_map: dict, key: str, default: str = "—") -> str:
    idx = col_map.get(key)
    if idx is None or idx >= len(row):
        return default
    val = str(row[idx]).strip()
    return val if val else default


def _e(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_card(row: list[str], col_map: dict, row_num: int) -> str:
    time_str  = _get(row, col_map, "time")
    name      = _get(row, col_map, "name")
    phone     = _get(row, col_map, "phone")
    company   = _get(row, col_map, "company")
    equipment = _get(row, col_map, "equipment")
    brand     = _get(row, col_map, "brand")
    battery   = _get(row, col_map, "battery")
    voltage   = _get(row, col_map, "voltage")
    ah        = _get(row, col_map, "ah")
    quantity  = _get(row, col_map, "quantity")
    model     = _get(row, col_map, "model")
    notes     = _get(row, col_map, "notes")

    notes_line = f"\n💬 <b>Изоҳ / Примечание:</b> {_e(notes)}" if notes != "—" else ""

    return (
        f"🔔 <b>Янги ариза! / Новая заявка!</b>  <code>#{row_num}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🕐 <b>Вақт / Дата:</b> {_e(time_str)}\n"
        f"👤 <b>Исм Фамилия:</b> {_e(name)}\n"
        f"📞 <b>Телефон:</b> {_e(phone)}\n"
        f"🏢 <b>Компания:</b> {_e(company)}\n"
        f"🚜 <b>Техника:</b> {_e(equipment)}\n"
        f"🏷 <b>Марка:</b> {_e(brand)}\n"
        f"🔋 <b>АКБ тури:</b> {_e(battery)}\n"
        f"⚡ <b>Вольтаж:</b> {_e(voltage)}\n"
        f"📊 <b>Ампер соат:</b> {_e(ah)}\n"
        f"📦 <b>Миқдори / Количество:</b> {_e(quantity)}\n"
        f"🔩 <b>Модель:</b> {_e(model)}"
        f"{notes_line}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"↩️ Ответьте на это сообщение чтобы взять заявку"
    )


def _open_worksheet(gc: gspread.Client) -> gspread.Worksheet:
    sheet_id = (
        os.getenv("GOOGLE_SHEETS_ID", "").strip()
        or os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    )
    if sheet_id:
        try:
            sh = gc.open_by_key(sheet_id)
            logger.info(f"[SHEETS] Открыта по ID: {sheet_id[:20]}...")
            return sh.sheet1
        except Exception as e:
            logger.warning(f"[SHEETS] Не удалось открыть по ID: {e} — пробую по названию")
    try:
        sh = gc.open(SHEET_NAME)
        logger.info(f"[SHEETS] Открыта по названию: «{SHEET_NAME}»")
        return sh.sheet1
    except gspread.SpreadsheetNotFound:
        raise SystemExit(
            f"Таблица «{SHEET_NAME}» не найдена.\n"
            f"Убедитесь что таблица расшарена и задан GOOGLE_SHEETS_ID в .env"
        )


async def check_and_notify(tg: Client, gc: gspread.Client) -> int:
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        logger.error("MANAGER_GROUP_ID не задан в .env")
        return 0

    worksheet = _open_worksheet(gc)
    all_values = worksheet.get_all_values()
    if len(all_values) < 2:
        logger.info("[SHEETS] Таблица пуста или только заголовок")
        return 0

    col_map   = _build_col_map(all_values[0])
    status_idx = STATUS_COL - 1   # M = индекс 12

    sent_count = 0
    for row_idx, row in enumerate(all_values[1:], start=2):
        status_val = row[status_idx].strip() if status_idx < len(row) else ""
        if status_val:
            continue
        if all(_get(row, col_map, k, "") == "" for k in _FIXED_COL_MAP):
            continue

        card = _format_card(row, col_map, row_idx - 1)
        try:
            await tg.send_message(target, card, parse_mode=enums.ParseMode.HTML)
            logger.info(f"[SHEETS] Карточка отправлена | строка {row_idx}")
        except Exception as e:
            logger.error(f"[SHEETS] Ошибка отправки строка {row_idx}: {e}")
            continue

        try:
            worksheet.update_acell(f"M{row_idx}", DONE_MARK)
        except Exception as e:
            logger.warning(f"[SHEETS] Не смог пометить строку {row_idx}: {e}")

        sent_count += 1
        await asyncio.sleep(0.5)

    return sent_count


async def main():
    logger.remove()
    logger.add(
        sys.stderr, level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )

    if not CREDS_FILE.exists():
        raise SystemExit(f"Файл {CREDS_FILE} не найден. Положите service_account.json в config/")

    _ensure_session()

    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=SCOPES)
    gc = gspread.authorize(creds)

    tg = Client(
        name=SESSION_NAME,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir=str(SESSIONS_DIR),
    )
    await tg.start()
    logger.info(f"[TG] Подключено | сессия={SESSION_NAME} | target={settings.manager_group_id}")
    logger.info(f"[LOOP] Проверка каждые {CHECK_INTERVAL // 60} мин. Ctrl+C для остановки.")

    try:
        while True:
            try:
                sent = await check_and_notify(tg, gc)
                if sent:
                    logger.info(f"[LOOP] Отправлено {sent} новых заявок")
                else:
                    logger.info("[LOOP] Новых заявок нет")
            except gspread.exceptions.APIError as e:
                logger.warning(f"[SHEETS] API ошибка: {e} — пропускаем итерацию")
            except Exception as e:
                logger.error(f"[LOOP] Ошибка: {e}", exc_info=True)
            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await tg.stop()
        logger.info("[TG] Отключено")


if __name__ == "__main__":
    asyncio.run(main())
