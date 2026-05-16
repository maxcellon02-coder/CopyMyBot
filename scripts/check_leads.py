"""
scripts/check_leads.py — мониторинг Google Таблицы "Maxcellon Заявки".

Запуск:
    python scripts/check_leads.py

Что делает:
  • Каждые 5 минут читает Google Sheet "Maxcellon Заявки"
  • Находит строки где колонка M (Ечим) пустая
  • Отправляет карточку заявки в MANAGER_GROUP_ID
  • Пишет "✅ Юборилди" в колонку M чтобы не отправлять повторно

Требования:
  • config/service_account.json — сервисный аккаунт Google
  • .env — MANAGER_GROUP_ID, TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
  • GOOGLE_DRIVE_FOLDER_ID в .env (ID таблицы) — если пусто, ищет по названию
  • Таблица расшарена на email сервисного аккаунта (редактор)

Колонка M = "Ечим": пустая → новая заявка, "✅ Юборилди" → уже отправлена.
"""

import asyncio
import os
import sys
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
CHECK_INTERVAL  = 5 * 60
CREDS_FILE      = ROOT / "config" / "service_account.json"
DONE_MARK       = "✅ Юборилди"
STATUS_COL      = 13   # колонка M (1-based)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Гибкое определение колонок по заголовку (любой регистр, RU/UZ/EN)
# Реальные заголовки таблицы:
#   Вақт / Дата | Исм Фамилия | Телефон | Компания | Техника | Марка |
#   АКБ тури    | Вольтаж     | Ампер соат | Миқдори | Модель | Изоҳ | Ечим
_COL_ALIASES = {
    "time":      ["вақт / дата", "вақт", "дата", "время", "vaqt", "time", "sana", "timestamp"],
    "name":      ["исм фамилия", "исм", "имя", "фио", "ф.и.о", "ism familiya", "ism", "name"],
    "phone":     ["телефон", "telefon", "тел", "phone", "номер", "raqam"],
    "company":   ["компания", "kompaniya", "company", "организация", "firma", "korxona"],
    "equipment": ["техника", "texnika", "equipment", "тип техники", "mashina", "transport"],
    "brand":     ["марка", "marka", "brand", "брэнд"],
    "battery":   ["акб тури", "акб", "аккумулятор", "akb turi", "akb", "battery", "тип акб", "тип батареи", "batareya"],
    "voltage":   ["вольтаж", "kuchlanish", "voltage", "volt", "вольт", "напряжение"],
    "ah":        ["ампер соат", "ампер-час", "ampere soat", "ah", "sig'im", "ёмкость", "амперчас"],
    "quantity":  ["миқдори", "количество", "miqdori", "miqdor", "quantity", "кол-во", "dona"],
    "model":     ["модель", "model"],
    "notes":     ["изоҳ", "примечание", "izoh", "notes", "comment", "комментарий"],
    "status":    ["ечим", "статус", "status", "holat", "решение"],
}


def _build_col_map(headers: list[str]) -> dict[str, int]:
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


def _e(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_card(row: list[str], col_map: dict, row_num: int) -> str:
    name      = _get(row, col_map, "name")
    phone     = _get(row, col_map, "phone")
    company   = _get(row, col_map, "company")
    equipment = _get(row, col_map, "equipment")
    brand     = _get(row, col_map, "brand")
    battery   = _get(row, col_map, "battery")
    voltage   = _get(row, col_map, "voltage")
    ah        = _get(row, col_map, "ah")
    quantity  = _get(row, col_map, "quantity")
    time_str  = _get(row, col_map, "time")

    return (
        f"🔔 <b>Янги ариза! / Новая заявка!</b>  <code>#{row_num}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Исм / Имя:</b> {_e(name)}\n"
        f"📞 <b>Телефон:</b> {_e(phone)}\n"
        f"🏢 <b>Компания:</b> {_e(company)}\n"
        f"🚜 <b>Техника:</b> {_e(equipment)}\n"
        f"🏷 <b>Марка:</b> {_e(brand)}\n"
        f"🔋 <b>АКБ:</b> {_e(battery)}\n"
        f"⚡ <b>Вольтаж:</b> {_e(voltage)}\n"
        f"📊 <b>Ампер-соат:</b> {_e(ah)}\n"
        f"📦 <b>Миқдори / Количество:</b> {_e(quantity)}\n"
        f"🕐 <b>Вақт / Время:</b> {_e(time_str)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"↩️ Ответьте на это сообщение чтобы взять заявку"
    )


def _open_worksheet(gc: gspread.Client) -> gspread.Worksheet:
    sheet_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if sheet_id:
        try:
            sh = gc.open_by_key(sheet_id)
            logger.info(f"[SHEETS] Открыта по ID: {sheet_id}")
            return sh.sheet1
        except Exception as e:
            logger.warning(f"[SHEETS] Не удалось открыть по ID ({sheet_id}): {e} — пробую по названию")

    try:
        sh = gc.open(SHEET_NAME)
        logger.info(f"[SHEETS] Открыта по названию: «{SHEET_NAME}»")
        return sh.sheet1
    except gspread.SpreadsheetNotFound:
        raise SystemExit(
            f"Таблица «{SHEET_NAME}» не найдена.\n"
            f"Убедитесь что:\n"
            f"  1. Таблица существует в Google Drive\n"
            f"  2. Расшарена на {gc.auth.service_account_email} (редактор)\n"
            f"  3. Либо задан GOOGLE_DRIVE_FOLDER_ID в .env"
        )


async def check_and_notify(tg_client: Client, worksheet: gspread.Worksheet) -> int:
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        logger.error("MANAGER_GROUP_ID / NOTIFICATION_CHAT_ID не задан в .env")
        return 0

    all_values = worksheet.get_all_values()
    if len(all_values) < 2:
        logger.info("[SHEETS] Таблица пуста или только заголовок")
        return 0

    headers = all_values[0]
    col_map = _build_col_map(headers)

    # Определяем индекс колонки статуса: по заголовку или колонка M (13-я, idx=12)
    if "status" in col_map:
        status_idx = col_map["status"]
    else:
        status_idx = STATUS_COL - 1   # M = индекс 12

    sent_count = 0

    for row_idx, row in enumerate(all_values[1:], start=2):
        # Получаем значение статуса (колонка M или найденная по заголовку)
        status_val = row[status_idx].strip() if status_idx < len(row) else ""
        if status_val:
            continue   # уже обработана (не пустая)

        # Пропускаем полностью пустые строки
        data_keys = [k for k in col_map if k != "status"]
        if all(_get(row, col_map, k, "") == "" for k in data_keys):
            continue

        card = _format_card(row, col_map, row_idx - 1)
        try:
            await tg_client.send_message(target, card, parse_mode=enums.ParseMode.HTML)
            logger.info(f"[SHEETS] Карточка отправлена | строка {row_idx}")
        except Exception as e:
            logger.error(f"[SHEETS] Ошибка отправки строка {row_idx}: {e}")
            continue

        # Помечаем строку — пишем в колонку M (или найденную колонку статуса)
        col_letter = _col_letter(status_idx + 1)
        try:
            worksheet.update_acell(f"{col_letter}{row_idx}", DONE_MARK)
        except Exception as e:
            logger.warning(f"[SHEETS] Не смог пометить строку {row_idx}: {e}")

        sent_count += 1
        await asyncio.sleep(0.5)

    return sent_count


def _col_letter(n: int) -> str:
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


async def main():
    logger.remove()
    logger.add(
        sys.stderr, level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )

    if not CREDS_FILE.exists():
        raise SystemExit(
            f"Файл {CREDS_FILE} не найден.\n"
            f"Положите service_account.json в папку config/"
        )

    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=SCOPES)
    gc = gspread.authorize(creds)
    worksheet = _open_worksheet(gc)
    logger.info(f"[SHEETS] Лист: «{worksheet.title}» | статус в колонке M (Ечим)")

    tg = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir=str(ROOT / "data" / "sessions"),
    )
    await tg.start()
    logger.info(
        f"[TG] Подключено | target={settings.manager_group_id or settings.notification_chat_id}"
    )
    logger.info(f"[LOOP] Проверка каждые {CHECK_INTERVAL // 60} мин. Ctrl+C для остановки.")

    try:
        while True:
            try:
                worksheet = _open_worksheet(gc)
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
