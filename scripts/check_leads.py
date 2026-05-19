"""
scripts/check_leads.py — мониторинг Google Таблицы "Maxcellon Заявки".

Запуск:
    python scripts/check_leads.py

Что делает:
  • Каждые 2 минуты читает Google Sheet "Maxcellon Заявки"
  • Находит строки где колонка M (Ечим) пустая
  • Определяет менеджера по реферальной ссылке (колонка "Источник") → пишет в N (МЕНЕЖЕР)
  • Если реферальная ссылка пустая → пишет "БОШКА" в колонку N
  • Отправляет карточку заявки в MANAGER_GROUP_ID через отдельную Pyrogram сессию
  • Пишет "✅ Юборилди" в колонку M чтобы не отправлять повторно

Требования:
  • config/service_account.json — сервисный аккаунт Google
  • .env — MANAGER_GROUP_ID, GOOGLE_SHEETS_ID, TELEGRAM_API_*
  • MANAGERS_MAP в .env — маппинг реферал→менеджер (формат: "код1:Имя1,код2:Имя2")

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
from app.crm.dispatcher import get_manager_by_name, get_next_manager

# ── Настройки ──────────────────────────────────────────────────────────────────
SHEET_NAME      = "Maxcellon Заявки"
CHECK_INTERVAL  = 2 * 60
CREDS_FILE      = ROOT / "config" / "service_account.json"
DONE_MARK       = "✅ Юборилди"
DEFAULT_MANAGER = "БОШКА"        # если реферальная ссылка пустая
# Колонки определяются динамически по заголовкам (см. _find_service_cols)
_STATUS_HEADER  = "Ечим"         # заголовок колонки-статуса (создаётся если нет)
_MANAGER_HEADER = "Менежер"      # заголовок колонки менеджера
SESSION_NAME    = "leads_session"
SESSIONS_DIR    = ROOT / "data" / "sessions"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Реальная структура таблицы (A–L данные, M = Ечим, N = МЕНЕЖЕР):
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
    "size":      ["ўлчамлари", "o'lchamlari", "размер", "gabarity", "size"],
    "charging":  ["зарядлаш", "zaryadlash", "зарядка", "charging"],
    "notes":     ["изоҳ", "примечание", "izoh", "notes", "comment", "комментарий"],
    "referral":  ["источник", "манба", "реферал", "ref", "referral", "source", "utm_source",
                  "ким юборди", "кто привел", "откуда", "canal", "канал"],
}


def _load_managers_map() -> dict[str, str]:
    """Загружает маппинг реферальный_код → имя_менеджера из MANAGERS_MAP в .env.

    Формат в .env:
        MANAGERS_MAP=акром:Акром,бекзод:Бекзод,sardor:Сардор
    """
    raw = os.getenv("MANAGERS_MAP", "").strip()
    result: dict[str, str] = {}
    if not raw:
        return result
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            key, _, name = pair.partition(":")
            result[key.strip().lower()] = name.strip()
    return result


def _find_service_cols(
    worksheet: "gspread.Worksheet", headers: list[str]
) -> tuple[int, int]:
    """Возвращает (status_col_1based, manager_col_1based).

    Ищет по заголовкам. Если нет — создаёт новые колонки в конце листа.
    """
    h_low = [h.strip().lower() for h in headers]
    total = len(headers)

    # Ищем колонку менеджера
    mgr_idx = next(
        (i for i, h in enumerate(h_low) if h in ("менежер", "менеджер", "manager")),
        None,
    )
    if mgr_idx is None:
        mgr_idx = total
        try:
            worksheet.update_cell(1, mgr_idx + 1, _MANAGER_HEADER)
        except Exception:
            pass
        logger.info(f"[SHEETS] Создана колонка «{_MANAGER_HEADER}» → {mgr_idx + 1}")
    else:
        logger.info(f"[SHEETS] Найдена колонка «{_MANAGER_HEADER}» → {mgr_idx + 1}")

    # Ищем колонку статуса (Ечим / Статус)
    status_idx = next(
        (i for i, h in enumerate(h_low)
         if h in ("ечим", "статус", "status", "holat", "sent", "отправлено")),
        None,
    )
    if status_idx is None:
        status_idx = max(total, mgr_idx + 1)
        try:
            worksheet.update_cell(1, status_idx + 1, _STATUS_HEADER)
        except Exception:
            pass
        logger.info(f"[SHEETS] Создана колонка «{_STATUS_HEADER}» → {status_idx + 1}")
    else:
        logger.info(f"[SHEETS] Найдена колонка «{_STATUS_HEADER}» → {status_idx + 1}")

    return status_idx + 1, mgr_idx + 1  # возвращаем 1-based


def _detect_manager(referral_val: str, managers_map: dict[str, str]) -> str:
    """Возвращает имя менеджера по значению реферального поля.

    Логика:
      - пусто → DEFAULT_MANAGER ("БОШКА")
      - есть в маппинге → берём имя из маппинга
      - нет в маппинге, но значение не пустое → используем значение как есть
    """
    val = referral_val.strip()
    if not val:
        return DEFAULT_MANAGER
    mapped = managers_map.get(val.lower())
    return mapped if mapped else val


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


def _format_card(row: list[str], col_map: dict, row_num: int, manager: str) -> str:
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
        f"{notes_line}\n"
        f"👨‍💼 <b>Менежер:</b> {_e(manager)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def _pick_worksheet(sh: gspread.Spreadsheet) -> gspread.Worksheet:
    """Выбирает нужный лист: сначала по имени, потом по наибольшему кол-ву строк."""
    worksheets = sh.worksheets()
    all_titles = [w.title for w in worksheets]
    logger.info(f"[SHEETS] Листы в таблице: {all_titles}")

    # 1. Ищем по точному имени
    for ws in worksheets:
        if ws.title.strip().lower() == SHEET_NAME.strip().lower():
            logger.info(f"[SHEETS] Использую лист: «{ws.title}»")
            return ws

    # 2. Ищем лист с наибольшим кол-вом строк (ответы формы)
    best = max(worksheets, key=lambda w: w.row_count)
    logger.info(f"[SHEETS] Лист «{SHEET_NAME}» не найден — использую лист с макс. строками: «{best.title}»")
    return best


def _open_worksheet(gc: gspread.Client) -> gspread.Worksheet:
    sheet_id = (
        os.getenv("GOOGLE_SHEETS_ID", "").strip()
        or os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    )
    if sheet_id:
        try:
            sh = gc.open_by_key(sheet_id)
            logger.info(f"[SHEETS] Открыта по ID: {sheet_id[:20]}...")
            return _pick_worksheet(sh)
        except Exception as e:
            logger.warning(f"[SHEETS] Не удалось открыть по ID: {e} — пробую по названию")
    try:
        sh = gc.open(SHEET_NAME)
        logger.info(f"[SHEETS] Открыта по названию: «{SHEET_NAME}»")
        return _pick_worksheet(sh)
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

    managers_map = _load_managers_map()

    worksheet = _open_worksheet(gc)
    all_values = worksheet.get_all_values()
    if len(all_values) < 2:
        logger.info("[SHEETS] Таблица пуста или только заголовок")
        return 0

    headers = all_values[0]
    col_map = _build_col_map(headers)
    status_col_1, manager_col_1 = _find_service_cols(worksheet, headers)
    status_idx = status_col_1 - 1  # 0-based

    logger.info(f"[SHEETS] Заголовки ({len(headers)} кол.): {headers}")
    logger.info(f"[SHEETS] Всего строк данных: {len(all_values) - 1} | статус={status_col_1} | менежер={manager_col_1}")

    def _col_letter(n: int) -> str:
        result = ""
        while n:
            n, r = divmod(n - 1, 26)
            result = chr(65 + r) + result
        return result

    status_letter  = _col_letter(status_col_1)
    manager_letter = _col_letter(manager_col_1)

    sent_count = 0
    for row_idx, row in enumerate(all_values[1:], start=2):
        status_val = row[status_idx].strip() if status_idx < len(row) else ""
        if status_val == DONE_MARK:
            continue
        if all(_get(row, col_map, k, "") == "" for k in _FIXED_COL_MAP):
            continue

        # Определяем менеджера
        referral_val = _get(row, col_map, "referral", default="")
        if referral_val == "—":
            referral_val = ""
        manager_name = _detect_manager(referral_val, managers_map)

        # Ищем @username менеджера
        if manager_name == DEFAULT_MANAGER:
            # Нет реф. ссылки → назначаем по очереди (round-robin)
            mgr_obj = get_next_manager()
        else:
            # Есть имя → ищем в активных менеджерах
            mgr_obj = get_manager_by_name(manager_name)
            if mgr_obj is None:
                # Менеджер больше не активен (напр. Jahongir) → round-robin
                logger.info(f"[LEADS] Менежер «{manager_name}» не активен → round-robin")
                mgr_obj = get_next_manager()

        if mgr_obj:
            manager_name = mgr_obj["name"]
            manager_mention = mgr_obj["username"]
        else:
            manager_mention = ""

        manager_display = f"{manager_name} ({manager_mention})" if manager_mention else manager_name

        # Помечаем ДО отправки — защита от дублей при параллельных запусках
        try:
            worksheet.update_acell(f"{status_letter}{row_idx}", DONE_MARK)
        except Exception as e:
            logger.warning(f"[SHEETS] Не смог пометить строку {row_idx}: {e} — пропускаем")
            continue

        # Записываем имя менеджера в колонку МЕНЕЖЕР
        try:
            worksheet.update_acell(f"{manager_letter}{row_idx}", manager_name)
            logger.info(f"[SHEETS] Менежер записан | строка {row_idx} → {manager_display}")
        except Exception as e:
            logger.warning(f"[SHEETS] Не смог записать менежера строка {row_idx}: {e}")

        card = _format_card(row, col_map, row_idx - 1, manager_display)
        # Добавляем @mention отдельной строкой чтобы Telegram создал уведомление
        mention_line = f"\n{manager_mention} — сизнинг клиент! 👆" if manager_mention else ""
        try:
            await tg.send_message(target, card + mention_line, parse_mode=enums.ParseMode.HTML)
            logger.info(f"[SHEETS] Карточка отправлена | строка {row_idx} | менежер={manager_display}")
        except Exception as e:
            logger.error(f"[SHEETS] Ошибка отправки строка {row_idx}: {e} — откатываем метку")
            try:
                worksheet.update_acell(f"{status_letter}{row_idx}", "")
                worksheet.update_acell(f"{manager_letter}{row_idx}", "")
            except Exception:
                pass
            continue

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


async def run_leads_checker():
    """Фоновая задача для запуска из main.py: мониторинг Google Таблицы."""
    if not CREDS_FILE.exists():
        logger.error(f"[LEADS] {CREDS_FILE} не найден — мониторинг заявок отключён")
        return

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
    logger.info(f"[LEADS] Подключено | сессия={SESSION_NAME}")
    logger.info(f"[LEADS] Проверка каждые {CHECK_INTERVAL // 60} мин.")

    try:
        while True:
            try:
                sent = await check_and_notify(tg, gc)
                if sent:
                    logger.info(f"[LEADS] Отправлено {sent} новых заявок")
                else:
                    logger.info("[LEADS] Новых заявок нет")
            except gspread.exceptions.APIError as e:
                logger.warning(f"[LEADS] API ошибка: {e} — пропускаем итерацию")
            except Exception as e:
                logger.error(f"[LEADS] Ошибка: {e}", exc_info=True)
            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await tg.stop()
        logger.info("[LEADS] Отключено")


if __name__ == "__main__":
    asyncio.run(main())
