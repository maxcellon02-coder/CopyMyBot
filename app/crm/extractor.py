"""
app/crm/extractor.py — AI lead extraction + единственная карточка менеджеру.

После каждого ответа бота:
  1. Claude Haiku анализирует разговор — извлекает намерение и цитаты
  2. Если есть намерение купить ИЛИ force_notify=True → сохраняет лид + отправляет карточку
  3. Пересылает фото клиента (если есть)
  4. Хранит manager_message_id для отслеживания назначений (см. crm/monitor.py)
"""

import asyncio
import json
from typing import Optional

import anthropic
from loguru import logger
from pyrogram import enums

from app.analytics import tracker
from app.core.config import settings

MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You are a CRM assistant. Extract lead information from a sales conversation. "
    "Respond ONLY with valid JSON, no explanation, no markdown."
)

_PROMPT = """\
Conversation:
{conversation}

Return JSON:
{{
  "has_intent": true/false,
  "intent_level": "browsing" | "interested" | "ready_to_buy",
  "product_interest": "string or null",
  "location": "string or null",
  "name": "string or null",
  "phone": "string or null",
  "notes": "string or null",
  "key_quotes": ["up to 3 short client quotes that show intent, in original language"]
}}
has_intent=true only when user shows real interest in purchasing something."""

_INTENT_EMOJI = {"browsing": "👀", "interested": "🔥", "ready_to_buy": "💰"}
_INTENT_LABEL = {"browsing": "Смотрит", "interested": "Интересуется", "ready_to_buy": "Готов купить"}

# manager_group message_id → lead_id (для отслеживания назначений)
_lead_by_msg_id: dict[int, int] = {}

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    return _client


def get_lead_id_by_msg(message_id: int) -> Optional[int]:
    return _lead_by_msg_id.get(message_id)


def _html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _conv_link(chat_id: Optional[int], message_id: Optional[int], user_id: Optional[int]) -> str:
    """Ссылка на переписку: для группы — прямая, для лички — на профиль."""
    if chat_id and message_id and chat_id < 0:
        # Группа / супергруппа
        if chat_id < -1_000_000_000_000:
            peer = abs(chat_id) - 1_000_000_000_000
        else:
            peer = abs(chat_id)
        return f"https://t.me/c/{peer}/{message_id}"
    if user_id:
        return f"tg://user?id={user_id}"
    return ""


async def maybe_extract_lead(
    *,
    conv_id: int,
    platform: str,
    external_user_id: str,
    user_name: Optional[str],
    phone: Optional[str],
    conversation_text: str,
    tg_client=None,
    photos: Optional[list] = None,
    force_notify: bool = False,
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
    user_id: Optional[int] = None,
    user_questions: Optional[list] = None,
    user_tg: Optional[str] = None,
    chat_title: Optional[str] = None,
) -> None:
    if not settings.anthropic_key:
        return

    try:
        response = await _get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(conversation=conversation_text[:3000]),
            }],
        )
        raw = response.content[0].text.strip()
        if not raw:
            return
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[CRM] JSON parse error conv={conv_id}: {e}")
        return
    except Exception as e:
        logger.warning(f"[CRM] Extraction failed conv={conv_id}: {e}")
        return

    # Отправляем карточку если есть намерение ИЛИ force_notify (Claude решил уведомить)
    if not data.get("has_intent") and not force_notify:
        return

    level = data.get("intent_level", "browsing")
    lead_id = await tracker.save_lead(
        conv_id=conv_id,
        platform=platform,
        external_user_id=external_user_id,
        user_name=user_name or data.get("name"),
        phone=phone or data.get("phone"),
        product_interest=data.get("product_interest"),
        location=data.get("location"),
        notes=f"[{level}] {data.get('notes') or ''}".strip("[] "),
    )
    logger.info(
        f"[CRM] Lead #{lead_id} | conv={conv_id} | "
        f"intent={level} | product={data.get('product_interest')}"
    )

    if tg_client:
        await _send_card(
            tg_client=tg_client,
            lead_id=lead_id,
            conv_id=conv_id,
            user_name=user_name or data.get("name"),
            phone=phone or data.get("phone"),
            data=data,
            photos=photos or [],
            chat_id=chat_id,
            message_id=message_id,
            user_id=user_id,
            user_questions=user_questions or [],
            user_tg=user_tg,
            chat_title=chat_title or "Личка",
        )


async def _send_card(
    *,
    tg_client,
    lead_id: int,
    conv_id: int,
    user_name: Optional[str],
    phone: Optional[str],
    data: dict,
    photos: list,
    chat_id: Optional[int],
    message_id: Optional[int],
    user_id: Optional[int],
    user_questions: list,
    user_tg: Optional[str],
    chat_title: str,
) -> None:
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        return

    level = data.get("intent_level", "browsing")
    emoji = _INTENT_EMOJI.get(level, "📋")
    label = _INTENT_LABEL.get(level, level)

    # Ссылка на переписку
    link = _conv_link(chat_id, message_id, user_id)
    if link:
        link_line = f'\n🔗 <a href="{link}">Открыть переписку →</a>'
    else:
        link_line = ""

    # Имя и @username
    name_str = _html(user_name or "—")
    tg_str = f"  {_html(user_tg)}" if user_tg else ""

    # Вопросы клиента
    if user_questions:
        q_lines = "\n".join(f"  • {_html(q[:200])}" for q in user_questions[-5:])
        questions_block = f"\n\n❓ <b>Вопросы клиента:</b>\n{q_lines}"
    else:
        questions_block = ""

    # Ключевые цитаты от AI
    quotes = data.get("key_quotes") or []
    if quotes:
        q_lines = "\n".join(f"  • «{_html(q)}»" for q in quotes[:3])
        quotes_block = f"\n\n💬 <b>Ключевые фразы:</b>\n{q_lines}"
    else:
        quotes_block = ""

    card = (
        f"{emoji} <b>Лид #{lead_id}</b> — {label}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>{name_str}</b>{tg_str}\n"
        f"🆔 <code>{user_id or '—'}</code>  |  💬 {_html(chat_title)}"
        f"{link_line}\n\n"
        f"🛍 {_html(data.get('product_interest') or '—')}\n"
        f"📞 {_html(phone or data.get('phone') or '—')}\n"
        f"📍 {_html(data.get('location') or '—')}"
        f"{questions_block}"
        f"{quotes_block}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"↩️ Ответьте на это сообщение чтобы взять лид <b>#{lead_id}</b>"
    )

    try:
        sent = await tg_client.send_message(target, card, parse_mode=enums.ParseMode.HTML)
        _lead_by_msg_id[sent.id] = lead_id
        await tracker.mark_lead_sent(lead_id, manager_message_id=sent.id)
        logger.info(f"[CRM] Card sent | lead={lead_id} msg={sent.id} target={target}")
    except Exception as e:
        logger.warning(f"[CRM] Failed to send card: {e}")
        return

    # Пересылаем фото клиента (до 5 штук)
    for source_chat_id, msg_id in (photos or [])[-5:]:
        try:
            await tg_client.copy_message(
                chat_id=target,
                from_chat_id=source_chat_id,
                message_id=msg_id,
            )
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.debug(f"[CRM] Photo forward failed: {e}")
