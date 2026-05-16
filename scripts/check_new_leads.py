"""
scripts/check_new_leads.py — мониторинг Google Таблицы "Maxcellon Заявки".

Запуск:
    python scripts/check_new_leads.py

Что делает:
  • Каждые 5 минут читает лист Google Sheets "Maxcellon Заявки"
  • Находит строки без отметки "✅" в колонке "Статус"
  • Отправляет красивую карточку в MANAGER_GROUP_ID
  • Проставляет "✅ Отправлено HH:MM" в колонку "Статус"

Требования:
  • config/service_account.json — сервисный аккаунт Google
  • .env — MANAGER_GROUP_ID, TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
  • Таблица расшарена на email сервисного аккаунта (редактор)

Ожидаемые колонки таблицы (порядок гибкий, ищем по заголовку):
  Имя | Телефон | Компания | Техника | Тип АКБ | Вольтаж | Ампер-час | Количество | Время | Статус
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Пути ──────────────────────────────────────────────────────────────────────
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

# ── Настройки ─────────────────────────────────────────────────────────────────
SHEET_NAME      = "Maxcellon Заявки"
CHECK_INTERVAL  = 5 * 60          # секунд между проверками
CREDS_FILE      = ROOT / "config" / "service_account.json"
SCOPES          = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Возможные названия колонок (в любом регистре)
_COL_ALIASES = {
    "name":     ["имя", "ism", "исм", "имя клиента", "ф.и.о", "фио", "name"],
    "phone":    ["телефон", "telefon", "тел", "phone", "номер", "raqam"],
    "company":  ["компания", "kompaniya", "company", "организация", "firma"],
    "equipment":["техника", "texnika", "equipment", "тип техники", "mashina"],
    "batt_type":["тип акб", "тип батареи", "batareya turi", "battery type", "akb turi"],
    "voltage":  ["вольтаж", "kuchlanish", "voltage", "volt", "вольт", "v"],
    "ah":       ["ампер-час", "ampere", "ah", "sig'im", "ёмкость", "амперчас"],
    "quantity": ["количество", "miqdor", "quantity", "кол-во", "dona"],
    "time":     ["время", "vaqt", "time", "дата", "sana", "timestamp"],
    "status":   ["статус", "status", "holat"],
}

PROCESSED_MARK = "✅"


def _build_col_map(headers: list[str]) -> dict[str, int]:
    """Возвращает {field_key: col_index_0based} по заголовкам листа."""
    col_map: dict[str, int] = {}
    for idx, h in enumerate(headers):
        h_low = h.strip().lower()
        for field, aliases in _COL_ALIASES.items():
            if h_low in aliases and field not in col_map:
                col_map[field] = idx
    return col_map


def _get(row: list[str], col_map: dict, key: str, default: str = "—") -> str:
    idx = col_map.get(key)
    if idx is None or idx >= len(row):
        return default
    val = str(row[idx]).strip()
    return val if val else default


def _format_card(row: list[str], col_map: dict, row_num: int) -> str:
    """Формирует HTML-карточку для Telegram."""
    name      = _get(row, col_map, "name")
    phone     = _get(row, col_map, "phone")
    company   = _get(row, col_map, "company")
    equipment = _get(row, col_map, "equipment")
    batt_type = _get(row, col_map, "batt_type")
    voltage   = _get(row, col_map, "voltage")
    ah        = _get(row, col_map, "ah")
    quantity  = _get(row, col_map, "quantity")
    time_str  = _get(row, col_map, "time")

    def e(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return (
        f"🔔 <b>Yangi ariza! / Новая заявка!</b>  <code>#{row_num}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Исм / Имя:</b> {e(name)}\n"
        f"📞 <b>Телефон:</b> {e(phone)}\n"
        f"🏢 <b>Компания:</b> {e(company)}\n"
        f"🚜 <b>Техника:</b> {e(equipment)}\n"
        f"🔋 <b>Тип АКБ:</b> {e(batt_type)}\n"
        f"⚡ <b>Вольтаж:</b> {e(voltage)}\n"
        f"📊 <b>Ампер-соат / Ёмкость:</b> {e(ah)}\n"
        f"📦 <b>Миқдори / Количество:</b> {e(quantity)}\n"
        f"🕐 <b>Вақт / Время:</b> {e(time_str)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"↩️ Ответьте на это сообщение чтобы взять заявку"
    )


async def check_and_notify(tg_client: Client, worksheet) -> int:
    """Проверяет лист и отправляет карточки для новых строк. Возвращает кол-во отправленных."""
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        logger.error("MANAGER_GROUP_ID / NOTIFICATION_CHAT_ID не задан в .env")
        return 0

    all_values = worksheet.get_all_values()
    if len(all_values) < 2:
        logger.info("[SHEETS] Таблица пуста или только заголовок")
        return 0

    headers  = all_values[0]
    col_map  = _build_col_map(headers)

    if "status" not in col_map:
        # Если колонки "Статус" нет — добавляем в конец
        status_col_idx = len(headers)
        worksheet.update_cell(1, status_col_idx + 1, "Статус")
        headers.append("Статус")
        col_map["status"] = status_col_idx
        logger.info(f"[SHEETS] Добавлена колонка 'Статус' (col {status_col_idx + 1})")

    status_col_letter = _col_letter(col_map["status"] + 1)
    sent_count = 0

    for row_idx, row in enumerate(all_values[1:], start=2):   # 1-based, skip header
        status_val = _get(row, col_map, "status", default="")
        if status_val.startswith(PROCESSED_MARK):
            continue   # уже обработана

        # Пропускаем полностью пустые строки
        data_cols = [k for k in col_map if k != "status"]
        if all(_get(row, col_map, k, "") == "" for k in data_cols):
            continue

        # Формируем и отправляем карточку
        card = _format_card(row, col_map, row_idx - 1)
        try:
            await tg_client.send_message(target, card, parse_mode=enums.ParseMode.HTML)
            logger.info(f"[SHEETS] Карточка отправлена | строка {row_idx}")
        except Exception as e:
            logger.error(f"[SHEETS] Ошибка отправки строка {row_idx}: {e}")
            continue

        # Помечаем строку как обработанную
        now_str = datetime.now().strftime("%d.%m %H:%M")
        cell_addr = f"{status_col_letter}{row_idx}"
        try:
            worksheet.update_acell(cell_addr, f"{PROCESSED_MARK} {now_str}")
        except Exception as e:
            logger.warning(f"[SHEETS] Не смог пометить строку {row_idx}: {e}")

        sent_count += 1
        await asyncio.sleep(0.5)   # небольшая пауза между карточками

    return sent_count


def _col_letter(n: int) -> str:
    """Номер колонки (1-based) → буква: 1→A, 26→Z, 27→AA."""
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def _open_worksheet(gc: gspread.Client) -> gspread.Worksheet:
    """Открывает первый лист таблицы SHEET_NAME."""
    try:
        sh = gc.open(SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        raise SystemExit(
            f"Таблица «{SHEET_NAME}» не найдена.\n"
            f"Убедитесь что:\n"
            f"  1. Таблица существует в Google Drive\n"
            f"  2. Расшарена на {gc.auth.service_account_email} (редактор)\n"
            f"  3. Название точно совпадает: «{SHEET_NAME}»"
        )
    return sh.sheet1


async def main():
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")

    if not CREDS_FILE.exists():
        raise SystemExit(
            f"Файл {CREDS_FILE} не найден.\n"
            f"Положите service_account.json в папку config/"
        )

    # Google Sheets клиент
    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=SCOPES)
    gc = gspread.authorize(creds)
    worksheet = _open_worksheet(gc)
    logger.info(f"[SHEETS] Подключено к «{SHEET_NAME}» | лист: {worksheet.title}")

    # Telegram клиент
    tg = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir=str(ROOT / "data" / "sessions"),
    )
    await tg.start()
    logger.info(f"[TG] Подключено | target={settings.manager_group_id or settings.notification_chat_id}")

    logger.info(f"[LOOP] Проверка каждые {CHECK_INTERVAL // 60} мин. Ctrl+C для остановки.")

    try:
        while True:
            try:
                worksheet = _open_worksheet(gc)   # обновляем ссылку на лист
                sent = await check_and_notify(tg, worksheet)
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
