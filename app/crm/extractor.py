"""
app/crm/extractor.py — AI lead extraction + manager group notification.

After each bot reply:
  1. Claude Haiku analyses the conversation and extracts lead info + key quotes
  2. If buying intent detected → saves Lead row in DB
  3. Sends a formatted lead card to MANAGER_GROUP_ID
  4. Forwards any photos the client sent during the conversation
  5. Stores manager_message_id for assignment tracking (see crm/monitor.py)
"""

import asyncio
import json
from typing import Optional

import anthropic
from loguru import logger

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

# In-memory map: manager_group message_id → lead_id (for assignment tracking)
_lead_by_msg_id: dict[int, int] = {}

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    return _client


def get_lead_id_by_msg(message_id: int) -> Optional[int]:
    """Called by monitor.py to look up which lead a manager replied to."""
    return _lead_by_msg_id.get(message_id)


async def maybe_extract_lead(
    *,
    conv_id: int,
    platform: str,
    external_user_id: str,
    user_name: Optional[str],
    phone: Optional[str],
    conversation_text: str,
    tg_client=None,
    photos: Optional[list] = None,   # list of (source_chat_id, message_id)
) -> None:
    """
    Fire-and-forget lead extraction.
    Call via asyncio.create_task() — does not block the reply flow.
    """
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
            logger.warning(f"[CRM] Empty response from model for conv={conv_id}")
            return
        # strip possible markdown code fences the model may add
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[CRM] JSON parse error conv={conv_id}: {e} | raw={raw!r:.100}")
        return
    except Exception as e:
        logger.warning(f"[CRM] Extraction failed conv={conv_id}: {e}")
        return

    if not data.get("has_intent"):
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
) -> None:
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        return

    level = data.get("intent_level", "browsing")
    emoji = _INTENT_EMOJI.get(level, "📋")
    label = _INTENT_LABEL.get(level, level)

    # Key quotes block
    quotes = data.get("key_quotes") or []
    quotes_block = ""
    if quotes:
        lines = "\n".join(f"  • «{q}»" for q in quotes[:3])
        quotes_block = f"\n\n💬 **Из переговоров:**\n{lines}"

    card = (
        f"{emoji} **Лид #{lead_id}** — {label}\n"
        f"{'─' * 28}\n"
        f"👤 {user_name or '—'}\n"
        f"📞 {phone or data.get('phone') or '—'}\n"
        f"🛍 {data.get('product_interest') or '—'}\n"
        f"📍 {data.get('location') or '—'}\n"
        f"📝 {data.get('notes') or '—'}"
        f"{quotes_block}\n\n"
        f"↩️ Ответьте на это сообщение чтобы взять в работу"
    )

    try:
        sent = await tg_client.send_message(target, card)
        # Store message_id for assignment tracking
        _lead_by_msg_id[sent.id] = lead_id
        await tracker.mark_lead_sent(lead_id, manager_message_id=sent.id)
        logger.info(f"[CRM] Card sent | lead={lead_id} msg={sent.id} target={target}")
    except Exception as e:
        logger.warning(f"[CRM] Failed to send card: {e}")
        return

    # Forward photos (up to 5)
    if photos and tg_client:
        for source_chat_id, msg_id in photos[-5:]:
            try:
                await tg_client.copy_message(
                    chat_id=target,
                    from_chat_id=source_chat_id,
                    message_id=msg_id,
                )
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"[CRM] Photo forward failed: {e}")
