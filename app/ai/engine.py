"""
app/ai/engine.py — Claude Sonnet-4.6 + RAG + prompt caching.

Per-message flow:
  1. Extract text — voice→Whisper, document→parser, text→direct
  2. Detect language (ru / uz)
  3. Retrieve RAG context from Qdrant (graceful fallback)
  4. Build Claude messages array with in-memory conversation history
  5. Call Claude API — system prompt cached, RAG context cached per query
  6. Update history
  7. Fire-and-forget CRM lead extraction
  8. Return reply text
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

import anthropic
from loguru import logger
from pyrogram import Client, enums
from pyrogram.types import Message

from app.analytics import tracker
from app.core.config import settings
from app.multimodal.documents import extract_document_text
from app.multimodal.voice import transcribe_voice
from app.rag.retriever import retrieve

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
MAX_HISTORY_TURNS = 6        # user+assistant pairs kept per conversation
SYSTEM_PROMPT_FILE = Path("data/system_prompt.txt")


_DEFAULT_SYSTEM_PROMPT = """\
Ты — вежливый и профессиональный ассистент по продажам компании Maxcellon LLC.

Ты общаешься с клиентами через Telegram. Твои задачи:
1. Отвечать на вопросы о товарах, ценах, наличии и условиях доставки.
2. Помогать клиенту выбрать подходящий товар.
3. Ненавязчиво направлять к покупке.
4. При необходимости сообщить, что передашь вопрос живому менеджеру.

Правила:
- Отвечай на том языке, на котором пишет клиент (русский, узбекский, английский).
- Используй только информацию из базы знаний. Не придумывай цены и характеристики.
- Если информации нет — честно скажи и предложи уточнить у менеджера.
- Будь кратким: 2–4 предложения, без лишних списков.
- Не упоминай конкурентов негативно.\
"""

# ── Anthropic async client (lazy init) ───────────────────────────────────────

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_key)
    return _client


# ── System prompt — reads from file every time so edits apply without restart ──

_cached_system_prompt: Optional[str] = None  # set by /reload_prompt or on first read


def _get_system_prompt() -> str:
    global _cached_system_prompt
    if SYSTEM_PROMPT_FILE.exists():
        try:
            text = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
            if text != _cached_system_prompt:
                logger.info(f"[AI] System prompt (re)loaded from {SYSTEM_PROMPT_FILE}")
                _cached_system_prompt = text
            return _cached_system_prompt
        except Exception as e:
            logger.warning(f"[AI] Cannot read system prompt file: {e} — using cached/default")
    if _cached_system_prompt is None:
        _cached_system_prompt = _DEFAULT_SYSTEM_PROMPT
    return _cached_system_prompt


# ── In-memory conversation history ───────────────────────────────────────────
# conv_id → [{"role": "user"|"assistant", "content": str}, ...]

_history: dict[int, list[dict]] = {}


def clear_history(conv_id: int) -> None:
    """Call when a conversation is closed or handed off."""
    _history.pop(conv_id, None)


# ── Language detection ────────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """Cyrillic-heavy → ru; Latin-heavy → uz (default in UZ context)."""
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    latin = sum(1 for c in text if "a" <= c.lower() <= "z")
    return "ru" if cyrillic >= latin else "uz"


# ── Canned replies ────────────────────────────────────────────────────────────

def _fallback(lang: str, name: str) -> str:
    if lang == "uz":
        return f"Kechirasiz, {name}, texnik nosozlik. Iltimos, bir ozdan keyin qayta yozing."
    return f"Извините, {name}, произошла техническая ошибка. Попробуйте написать через минуту."


def _voice_reply(lang: str, name: str) -> str:
    if lang == "uz":
        return f"Salom, {name}! Hozircha ovozli xabarlarni qayta ishlay olmayman — iltimos, matn yozing."
    return f"Здравствуйте, {name}! Пока не могу обрабатывать голосовые — напишите текстом, пожалуйста."


def _doc_reply(lang: str, name: str) -> str:
    if lang == "uz":
        return f"Salom, {name}! Hujjat uchun rahmat. Savolingizni matn orqali yozing."
    return f"Здравствуйте, {name}! Документ получен. Напишите ваш вопрос текстом."


def _photo_reply(lang: str, name: str) -> str:
    if lang == "uz":
        return f"Salom, {name}! Rasmingiz uchun rahmat 📷\nSavolingizni matn orqali yozing 😊"
    return f"Здравствуйте, {name}! Фото получено 📷\nНапишите ваш вопрос текстом 😊"


# ── Main entry point ──────────────────────────────────────────────────────────

async def _send_media_for_model(tg_client: Client, chat_id: int, model_query: str) -> None:
    """Ищет фото/видео модели в Qdrant и пересылает из канала в чат."""
    from app.rag.retriever import retrieve_media
    try:
        items = await retrieve_media(model_query, top_k=1)
        if not items:
            logger.info(f"[MEDIA] No media found for: {model_query!r}")
            return
        item = items[0]
        await tg_client.forward_messages(
            chat_id=chat_id,
            from_chat_id=item["channel_id"],
            message_ids=item["message_id"],
        )
        logger.info(f"[MEDIA] Forwarded {item['media_type']} msg={item['message_id']} score={item['score']}")
    except Exception as e:
        logger.warning(f"[MEDIA] Could not forward media for {model_query!r}: {e}")




async def generate_reply(
    message: Message,
    conv_id: int,
    tg_client: Optional[Client] = None,
    photos: Optional[list] = None,
    client_name: Optional[str] = None,
    override_text: Optional[str] = None,
) -> str:
    user = message.from_user
    user_name = client_name or ((user.first_name or "Mijoz") if user else "Mijoz")
    user_text = override_text or (message.text or message.caption or "").strip()
    voice_echo: Optional[str] = None   # set when voice was transcribed

    # ── Multimodal: voice → Whisper, document → parser ────────────────────────
    if not override_text and not user_text:
        if (message.voice or message.audio) and tg_client:
            transcribed = await transcribe_voice(tg_client, message)
            if transcribed:
                user_text = transcribed          # clean text to Claude
                voice_echo = transcribed         # will be echoed back to client
            else:
                return _voice_reply("ru", user_name)
        elif message.document and tg_client:
            extracted = await extract_document_text(tg_client, message)
            if extracted:
                user_text = f"[Документ «{message.document.file_name}»]:\n{extracted}"
            else:
                return _doc_reply("ru", user_name)
        elif message.photo:
            hist = _history.get(conv_id, [])
            past = " ".join(t["content"] for t in hist if t["role"] == "user")[-300:]
            lang = _detect_language(past) if past else "uz"
            return _photo_reply(lang, user_name)
        else:
            return _fallback("ru", user_name)

    lang = _detect_language(user_text)

    try:
        await tracker.update_language(conv_id, lang)
    except Exception:
        pass

    # ── RAG retrieval ─────────────────────────────────────────────────────────
    rag_context = ""
    try:
        rag_context = await retrieve(user_text, top_k=4)
    except Exception as e:
        logger.warning(f"[RAG] Skipped for conv={conv_id}: {e}")

    # ── Build messages: history + current turn ────────────────────────────────
    history = _history.get(conv_id, [])
    messages: list[dict] = [
        {"role": t["role"], "content": t["content"]}
        for t in history[-(MAX_HISTORY_TURNS * 2):]
    ]

    if rag_context:
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"[База знаний]\n{rag_context}\n[/База знаний]",
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": user_text},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})

    # ── Claude API call ───────────────────────────────────────────────────────
    try:
        response = await _get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": _get_system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        reply_text = response.content[0].text.strip()

        # [NOTIFY_MANAGER] — убираем тег, ставим флаг для CRM
        force_notify = False
        if "[NOTIFY_MANAGER]" in reply_text:
            reply_text = reply_text.replace("[NOTIFY_MANAGER]", "").strip()
            force_notify = True

        # [MEDIA_SEND: model] — убираем тег, пересылаем медиа из канала
        media_match = re.search(r'\[MEDIA_SEND:\s*([^\]]+)\]', reply_text)
        if media_match and tg_client:
            model_query = media_match.group(1).strip()
            reply_text = re.sub(r'\[MEDIA_SEND:\s*[^\]]+\]', '', reply_text).strip()
            asyncio.create_task(_send_media_for_model(tg_client, message.chat.id, model_query))

        u = response.usage
        logger.info(
            f"[AI] conv={conv_id} lang={lang} | "
            f"in={u.input_tokens} out={u.output_tokens} | "
            f"cache_created={getattr(u, 'cache_creation_input_tokens', 0)} "
            f"cache_read={getattr(u, 'cache_read_input_tokens', 0)}"
        )

    except anthropic.APIError as e:
        logger.error(f"[AI] Claude API error conv={conv_id}: {e}")
        return _fallback(lang, user_name)
    except Exception as e:
        logger.error(f"[AI] Unexpected error conv={conv_id}: {e}", exc_info=True)
        return _fallback(lang, user_name)

    # ── Voice echo: показываем клиенту что бот расслышал ─────────────────────
    if voice_echo:
        reply_text = f"🎤 _{voice_echo}_\n\n{reply_text}"

    # ── Update in-memory history (plain text only) ────────────────────────────
    hist = _history.setdefault(conv_id, [])
    hist.append({"role": "user", "content": user_text})
    hist.append({"role": "assistant", "content": reply_text})
    if len(hist) > MAX_HISTORY_TURNS * 2:
        _history[conv_id] = hist[-(MAX_HISTORY_TURNS * 2):]

    # ── CRM: fire-and-forget lead extraction ──────────────────────────────────
    from app.crm.extractor import maybe_extract_lead
    convo_text = "\n".join(
        f"{'Клиент' if t['role'] == 'user' else 'Бот'}: {t['content']}"
        for t in hist[-10:]
    )
    phone = None
    if user and hasattr(user, "phone_number"):
        phone = user.phone_number
    asyncio.create_task(maybe_extract_lead(
        conv_id=conv_id,
        platform="telegram",
        external_user_id=str(user.id) if user else "unknown",
        user_name=user_name,
        phone=phone,
        conversation_text=convo_text,
        tg_client=tg_client,
        photos=photos or [],
        force_notify=force_notify,
        chat_id=message.chat.id if message else None,
        message_id=message.id if message else None,
        user_id=user.id if user else None,
        user_questions=[t["content"] for t in hist if t["role"] == "user"][-6:],
        user_tg=f"@{user.username}" if user and user.username else None,
        chat_title=getattr(message.chat, "title", None) if message else "Личка",
    ))

    return reply_text
