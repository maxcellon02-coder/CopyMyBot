"""
app/crm/lead_form.py — запись заполненной заявки в форму (лист «Maxcellon Заявки»).

Когда бот собрал данные клиента (телефон + параметры АКБ) и поставил тег
[SAVE_LEAD], структурированные поля извлекаются Haiku и дописываются строкой
в тот же лист, что читает scripts/check_leads.py — со СТАТУСОМ пустым.
Дальше check_leads сам назначает менеджера и шлёт карточку в группу
«по установленному порядку» — то есть заявка идёт «через форму».

Горячие лиды без полных данных по-прежнему уходят в группу через
app/crm/extractor.maybe_extract_lead ([NOTIFY_MANAGER]).
"""
import asyncio
import json
from datetime import datetime
from typing import Optional

import anthropic
import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

from app.core.config import settings
from scripts.check_leads import CREDS_FILE, SCOPES, _build_col_map, _open_worksheet

MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You extract a structured sales lead (traction battery order) from a "
    "conversation. Respond ONLY with valid JSON, no markdown, no explanation."
)

_PROMPT = """\
Conversation (Клиент = customer, Бот = assistant):
{conversation}

Extract the customer's battery request. Return JSON:
{{
  "name": "string|null",        // имя клиента
  "phone": "string|null",       // телефон
  "company": "string|null",     // название компании
  "equipment": "string|null",   // тип техники (погрузчик, гольф-кар, ...)
  "brand": "string|null",       // марка/модель техники
  "battery": "string|null",     // тип АКБ: AGM / GEL / кислотный / литий (LiFePO4)
  "voltage": "string|null",     // вольтаж (12V/24V/36V/48V/80V)
  "ah": "string|null",          // ёмкость в Ah
  "size": "string|null",        // размеры Д×Ш×В в мм
  "quantity": "string|null",    // количество, шт
  "model": "string|null",       // модель АКБ если называли (FT48-400 и т.п.)
  "notes": "string|null",       // что важно передать менеджеру
  "ready": true/false           // true ТОЛЬКО если в разговоре есть телефон
}}
Use the customer's language for text values. Null for unknown fields."""

_client: Optional[anthropic.AsyncAnthropic] = None
# conv_id, по которым заявка уже записана — защита от дублей в рамках сессии
_saved_convs: set[int] = set()

# Поля заявки → ключи col_map (scripts.check_leads._COL_ALIASES)
_ROW_FIELDS = (
    "name", "phone", "company", "equipment", "brand", "battery",
    "voltage", "ah", "size", "quantity", "model", "notes",
)


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    return _client


def _append_row_sync(fields: dict) -> None:
    """Синхронная запись строки в лист (вызывается через asyncio.to_thread)."""
    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=SCOPES)
    gc = gspread.authorize(creds)
    ws = _open_worksheet(gc)
    headers = ws.row_values(1)
    col_map = _build_col_map(headers)

    ncols = max(len(headers), (max(col_map.values()) + 1) if col_map else 0, 12)
    row = ["" for _ in range(ncols)]

    def put(key: str, val) -> None:
        idx = col_map.get(key)
        if idx is not None and idx < ncols and val:
            row[idx] = str(val)

    put("time", datetime.now().strftime("%Y-%m-%d %H:%M"))
    for key in _ROW_FIELDS:
        put(key, fields.get(key))

    # Статус/менеджер НЕ заполняем — пустой статус нужен, чтобы check_leads
    # подхватил заявку, назначил менеджера и отправил карточку в группу.
    ws.append_row(row, value_input_option="USER_ENTERED")


async def save_lead_to_sheet(
    *,
    conv_id: int,
    conversation_text: str,
    user_name: Optional[str] = None,
    phone: Optional[str] = None,
    user_tg: Optional[str] = None,
) -> bool:
    """Извлекает поля заявки из разговора и дописывает строку в лист заявок."""
    if conv_id in _saved_convs:
        logger.info(f"[FORM] Заявка по conv={conv_id} уже записана — пропускаю")
        return False
    if not settings.anthropic_key:
        return False

    try:
        resp = await _get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(conversation=conversation_text[:3500]),
            }],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"[FORM] Extraction failed conv={conv_id}: {e}")
        return False

    # Телефон/имя из Telegram, если AI не извлёк
    data["phone"] = data.get("phone") or phone
    data["name"] = data.get("name") or user_name
    if user_tg:
        tg_note = f"TG: {user_tg}"
        data["notes"] = f"{data.get('notes') or ''}  {tg_note}".strip()

    if not data.get("phone"):
        logger.info(f"[FORM] conv={conv_id}: нет телефона — заявка пока не записана")
        return False

    try:
        await asyncio.to_thread(_append_row_sync, data)
        _saved_convs.add(conv_id)
        logger.info(
            f"[FORM] Заявка записана в форму | conv={conv_id} | "
            f"phone={data.get('phone')} | {data.get('voltage')}/{data.get('ah')} "
            f"{data.get('battery')}"
        )
        return True
    except Exception as e:
        logger.error(f"[FORM] Не удалось записать заявку conv={conv_id}: {e}", exc_info=True)
        return False
