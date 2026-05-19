"""
app/bot/client.py — Pyrogram userbot: приём и маршрутизация сообщений.

Жизнь одного входящего сообщения:
  1. Pyrogram видит сообщение → проверяет фильтры (пускать или нет?)
  2. Контакт-гейт: если номер неизвестен → запрос контакта, удаление сообщения
  3. Проверяем: не обрабатываем ли мы уже этого пользователя?
  4. Проверяем: не превышен ли лимит одновременных диалогов?
  5. Случайная пауза 2–5 сек (имитация человека, снижает риск бана)
  6. Получаем/открываем диалог в аналитике
  7. Передаём сообщение в AI движок → получаем текст ответа
  8. DRY_RUN=true?  → только пишем в лог, ничего не отправляем
     DRY_RUN=false? → отправляем ответ в чат
  9. Дублируем ответ в NOTIFICATION_CHAT_ID (группа мониторинга)
 10. Записываем оба сообщения в аналитику
"""

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from pyrogram import Client, filters
from pyrogram.enums import ChatAction, ChatMemberStatus, ChatType
from pyrogram.handlers import MessageHandler
from pyrogram.types import ForceReply, Message

from app.admin.commands import is_paused, register_admin_handlers
from app.ai.engine import clear_history, generate_reply, get_history
from app.analytics import tracker
from app.bot.wizard import (
    build_wizard_context,
    clear_wizard,
    get_wizard_result,
    handle_wizard_step,
    is_in_wizard,
    start_wizard,
)
from app.core.config import settings
from app.crm.monitor import register_monitor_handler


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 1: СОСТОЯНИЕ — что бот «помнит» пока работает
# ═══════════════════════════════════════════════════════════════════════════════

_CONV_TIMEOUT_SEC  = 30 * 60
_ADMIN_CACHE_TTL   = 3600
_HANDOFF_FILE      = Path("data/handoffs.json")
_CONTACTS_FILE     = Path("data/contacts.json")
_NAMES_FILE        = Path("data/names.json")

_TARGET_GROUP_ID = -1003985668441  # группа, где требуется контакт-гейт

_semaphore:        Optional[asyncio.Semaphore]               = None
_processing:       set[tuple[int, int]]                      = set()
_conversations:    dict[tuple[int, int], tuple[int, float]]  = {}
_admin_cache:      dict[int, tuple[bool, float]]             = {}
_bot_id:           Optional[int]                             = None
_awaiting_contact: set[int]                                  = set()  # ждут контакт
_awaiting_name:    set[int]                                  = set()  # ждут ввод имени
_user_admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}     # (chat_id, user_id) → (is_admin, ts)
_conv_photos:      dict[tuple[int, int], list]               = {}     # conv_key → [(chat_id, msg_id), ...]


# ── Контакты: user_id → номер телефона ───────────────────────────────────────

def _load_contacts() -> dict[int, str]:
    if not _CONTACTS_FILE.exists():
        return {}
    try:
        raw = json.loads(_CONTACTS_FILE.read_text(encoding="utf-8"))
        return {int(k): v for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Could not load contacts: {e}")
        return {}


def _save_contacts() -> None:
    try:
        _CONTACTS_FILE.write_text(
            json.dumps({str(k): v for k, v in _contacts.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Could not save contacts: {e}")


_contacts: dict[int, str] = _load_contacts()


# ── Имена пользователей: user_id → имя ───────────────────────────────────────

def _load_names() -> dict[int, str]:
    if not _NAMES_FILE.exists():
        return {}
    try:
        raw = json.loads(_NAMES_FILE.read_text(encoding="utf-8"))
        return {int(k): v for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Could not load names: {e}")
        return {}


def _save_names() -> None:
    try:
        _NAMES_FILE.write_text(
            json.dumps({str(k): v for k, v in _user_names.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Could not save names: {e}")


_user_names: dict[int, str] = _load_names()


# ── Определение языка по тексту ───────────────────────────────────────────────

def _detect_lang(text: str) -> str:
    if not text:
        return 'uz'
    cyrillic = sum(1 for c in text if 'Ѐ' <= c <= 'ӿ')
    return 'ru' if cyrillic / len(text) > 0.3 else 'uz'


# ── Хэндоффы: (chat_id, user_id) → (имя_менеджера, unix_timestamp) ───────────

def _load_handoffs() -> dict[tuple[int, int], tuple[str, float]]:
    if not _HANDOFF_FILE.exists():
        return {}
    try:
        raw = json.loads(_HANDOFF_FILE.read_text(encoding="utf-8"))
        now = time.time()
        result: dict[tuple[int, int], tuple[str, float]] = {}
        for key, entry in raw.items():
            chat_id, user_id = map(int, key.split(":"))
            if now - entry["ts"] < _CONV_TIMEOUT_SEC:
                result[(chat_id, user_id)] = (entry["mgr"], entry["ts"])
        if result:
            logger.info(f"Restored {len(result)} active handoff(s) from disk")
        return result
    except Exception as e:
        logger.warning(f"Could not load handoffs from disk: {e}")
        return {}


def _save_handoffs() -> None:
    try:
        raw = {
            f"{chat_id}:{user_id}": {"mgr": mgr, "ts": ts}
            for (chat_id, user_id), (mgr, ts) in _handed_off.items()
        }
        _HANDOFF_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"Could not save handoffs to disk: {e}")


_handed_off: dict[tuple[int, int], tuple[str, float]] = _load_handoffs()


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_chats)
    return _semaphore


async def _get_bot_id(client: Client) -> int:
    global _bot_id
    if _bot_id is None:
        me = await client.get_me()
        _bot_id = me.id
        logger.debug(f"Bot account ID: {_bot_id}")
    return _bot_id


async def _get_is_admin(client: Client, chat_id: int) -> bool:
    now = time.monotonic()
    if chat_id in _admin_cache:
        is_admin, ts = _admin_cache[chat_id]
        if now - ts < _ADMIN_CACHE_TTL:
            return is_admin
    try:
        bot_id = await _get_bot_id(client)
        member = await client.get_chat_member(chat_id, bot_id)
        is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as e:
        logger.debug(f"Admin status check failed for chat {chat_id}: {e}")
        is_admin = False
    _admin_cache[chat_id] = (is_admin, now)
    logger.debug(f"Admin status in {chat_id}: {is_admin}")
    return is_admin


async def _get_user_is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    now = time.monotonic()
    key = (chat_id, user_id)
    if key in _user_admin_cache:
        is_admin, ts = _user_admin_cache[key]
        if now - ts < _ADMIN_CACHE_TTL:
            return is_admin
    try:
        member = await client.get_chat_member(chat_id, user_id)
        is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        is_admin = False
    _user_admin_cache[key] = (is_admin, now)
    return is_admin


def _detect_human_takeover(message: Message, bot_id: int) -> tuple[bool, str]:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return False, ""
    rply_user = message.reply_to_message.from_user
    if rply_user.id == bot_id or rply_user.id == message.from_user.id:
        return False, ""
    if settings.manager_user_ids and rply_user.id not in settings.manager_user_ids:
        return False, ""
    name = f"@{rply_user.username}" if rply_user.username else (rply_user.first_name or str(rply_user.id))
    return True, name


def _build_conv_summary(conv_id: int | None, max_turns: int = 5) -> str:
    """Собирает последние max_turns обменов из истории в читаемый текст."""
    if conv_id is None:
        return ""
    history = get_history(conv_id)
    if not history:
        return ""
    tail = history[-(max_turns * 2):]
    lines: list[str] = []
    for msg in tail:
        role = msg.get("role", "")
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if len(content) > 200:
            content = content[:200] + "…"
        prefix = "👤 Клиент" if role == "user" else "🤖 Бот"
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


async def _notify_handoff(client: Client, chat_id: int, user, mgr_name: str,
                          conv_id: int | None = None):
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        return
    try:
        client_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)
        summary = _build_conv_summary(conv_id)
        summary_block = f"\n\n📋 <b>Суть разговора:</b>\n{summary}" if summary else ""
        text = (
            f"🤝 <b>Менеджер {mgr_name} продолжает переговоры</b>\n"
            f"👤 Клиент: {client_name} (<code>{user.id}</code>)\n"
            f"💬 Чат: <code>{chat_id}</code>"
            f"{summary_block}"
        )
        await client.send_message(target, text, parse_mode="html")
    except Exception as e:
        logger.warning(f"Failed to send handoff notification: {e}")


async def _manager_intervened_during_delay(
    client: Client, message: Message
) -> tuple[bool, str]:
    bot_id = await _get_bot_id(client)
    try:
        async for msg in client.get_chat_history(message.chat.id, limit=10):
            if msg.id <= message.id:
                break
            if not msg.from_user or msg.from_user.id == bot_id:
                continue
            uid = msg.from_user.id
            in_manager_list = bool(settings.manager_user_ids) and uid in settings.manager_user_ids
            is_group_admin = await _get_user_is_admin(client, message.chat.id, uid)
            if in_manager_list or is_group_admin:
                mgr = msg.from_user
                name = f"@{mgr.username}" if mgr.username else (mgr.first_name or str(mgr.id))
                return True, name
    except Exception as e:
        logger.debug(f"Manager delay-check failed for chat {message.chat.id}: {e}")
    return False, ""




# ── Обработка введённого имени ────────────────────────────────────────────────

async def _handle_name_input(client: Client, message: Message, user) -> None:
    name = (message.text or "").strip()
    if not name or len(name) < 2:
        await message.reply(
            "Iltimos, ismingizni kiriting 🙏",
            reply_markup=ForceReply(),
        )
        return

    _user_names[user.id] = name
    _save_names()
    _awaiting_name.discard(user.id)
    logger.info(f"[ONBOARD] Name saved: user={user.id} name={name!r}")

    # Приветствие — дальше Claude сам ведёт разговор естественно
    lang = _detect_lang(name)
    if lang == "ru":
        greeting = (
            f"Приятно познакомиться, {name}! 😊\n"
            f"Расскажите, что вас интересует — помогу подобрать нужную АКБ."
        )
    else:
        greeting = (
            f"Tanishganimdan xursandman, {name}! 😊\n"
            f"Nimaga qiziqasiz — kerakli АКБni tanlashda yordam beraman."
        )
    await message.reply(greeting)


# ── Обработчик новых участников группы ───────────────────────────────────────

async def on_new_member(client: Client, message: Message) -> None:
    if not message.new_chat_members:
        return
    for user in message.new_chat_members:
        if user.is_bot:
            continue
        bot_id = await _get_bot_id(client)
        if user.id == bot_id:
            continue

        first_name = user.first_name or "Do'st"

        # Приветствие в группе (видят все)
        try:
            await message.reply(
                f"Assalomu alaykum, {first_name}! 👋\n"
                f"Maxcellon guruhiga xush kelibsiz!\n\n"
                f"Sizga qanday yordam bera olaman? 🙂"
            )
            logger.info(f"[ONBOARD] Group welcome sent for user={user.id}")
        except Exception as e:
            logger.warning(f"[ONBOARD] Could not send group welcome for user={user.id}: {e}")

        # DM: запрос имени (если ещё не зарегистрирован)
        if user.id not in _contacts:
            _awaiting_name.add(user.id)
            try:
                await client.send_message(
                    user.id,
                    f"Assalomu alaykum, {first_name}! 👋\n"
                    f"Maxcellon guruhiga xush kelibsiz!\n\n"
                    f"Men — Maxcellon savdo yordamchisiman.\n"
                    f"Mahsulotlar, narxlar va yetkazib berish bo'yicha savollaringizga javob beraman.\n\n"
                    f"Avval tanishib olaylik — ismingizni kiriting:",
                    reply_markup=ForceReply(),
                )
                logger.info(f"[ONBOARD] DM name-request sent to user={user.id}")
            except Exception as e:
                logger.warning(f"[ONBOARD] Could not DM new member user={user.id}: {e}")


# ── Индикатор «печатает…» ─────────────────────────────────────────────────────

async def _typing_loop(client: Client, chat_id: int, stop: asyncio.Event) -> None:
    """Отправляет ChatAction.TYPING каждые 4 с пока stop не выставлен."""
    while not stop.is_set():
        try:
            await client.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=4)
        except asyncio.TimeoutError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 2: ФИЛЬТРЫ — «кому отвечать, кому нет»
# ═══════════════════════════════════════════════════════════════════════════════

async def _is_allowed_chat(_, __, message: Message) -> bool:
    chat_type = message.chat.type
    if chat_type == ChatType.PRIVATE:
        return settings.allow_private_chats
    if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not settings.allowed_chat_ids:
            return True
        return message.chat.id in settings.allowed_chat_ids
    return False


async def _is_not_ignored(_, __, message: Message) -> bool:
    if message.from_user:
        return message.from_user.id not in settings.ignored_user_ids
    # sender_chat = пользователь пишет "от имени канала" — пропускаем в on_message
    return message.sender_chat is not None


async def _is_not_slash_command(_, __, message: Message) -> bool:
    """Slash-команды (/что-угодно) никогда не доходят до Claude.
    Для админов их обрабатывает admin handler (group=-3).
    Для всех остальных — тихое игнорирование, без ответа.
    """
    if message.text and message.text.lstrip().startswith("/"):
        return False
    return True


_content_filter = filters.text | filters.voice | filters.document | filters.audio | filters.photo
_chat_filter    = filters.create(_is_allowed_chat)
_user_filter    = filters.create(_is_not_ignored)
_no_cmd_filter  = filters.create(_is_not_slash_command)

MAIN_FILTER = (
    filters.incoming
    & ~filters.me
    & _content_filter
    & _chat_filter
    & _user_filter
    & _no_cmd_filter
)


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 3: ДИАЛОГИ — открытие и отслеживание
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_or_open_conversation(conv_key: tuple[int, int], message: Message) -> int:
    now = time.monotonic()

    if conv_key in _conversations:
        conv_id, last_seen = _conversations[conv_key]
        if now - last_seen < _CONV_TIMEOUT_SEC:
            _conversations[conv_key] = (conv_id, now)
            return conv_id
        await tracker.close_conversation(conv_id)
        clear_history(conv_id)
        _conv_photos.pop(conv_key, None)
        logger.debug(f"Conversation {conv_id} closed by timeout")

    user = message.from_user
    conv_id = await tracker.open_conversation(
        platform="telegram",
        external_user_id=str(user.id),
        user_name=user.first_name or user.username,
    )
    _conversations[conv_key] = (conv_id, now)
    logger.debug(f"New conversation {conv_id} opened for user {user.id}")
    return conv_id


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 4: УВЕДОМЛЕНИЕ — дублирование в группу мониторинга
# ═══════════════════════════════════════════════════════════════════════════════

async def _mirror_to_notification_chat(client: Client, message: Message, reply_text: str):
    if not settings.notification_chat_id:
        return
    try:
        user = message.from_user
        chat_title = getattr(message.chat, "title", None) or "Личка"
        preview = reply_text[:300] + ("…" if len(reply_text) > 300 else "")
        text = (
            f"💬 **{chat_title}**\n"
            f"👤 {user.first_name or ''} {user.last_name or ''} (`{user.id}`)\n"
            f"📨 Вопрос: {(message.text or '[вложение]')[:200]}\n"
            f"🤖 Ответ: {preview}"
        )
        await client.send_message(settings.notification_chat_id, text)
    except Exception as e:
        logger.warning(f"Failed to mirror to notification chat: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 5: ОСНОВНОЙ ОБРАБОТЧИК — сердце бота
# ═══════════════════════════════════════════════════════════════════════════════

async def _process_message(client: Client, message: Message, handler_start: float = 0.0):
    if not handler_start:
        handler_start = time.time()

    user = message.from_user
    chat_id = message.chat.id
    conv_key = (chat_id, user.id)
    is_private = message.chat.type == ChatType.PRIVATE

    # ── Личка: сохранённые контакты — с ними общается сам пользователь ──────────
    if is_private and user.id in _contacts:
        logger.debug(f"[PRIVATE] {user.id}: saved contact — skipped (handled manually)")
        return

    if not is_private:
        # ── Шаг 0: не-админ групп — только логируем (интент-детект в Step 2) ──
        is_admin = await _get_is_admin(client, chat_id)
        if not is_admin:
            logger.debug(f"[MEMBER-ONLY] {user.id}@{chat_id}: not admin — logging only")
            return

        bot_id = await _get_bot_id(client)

        # ── Игнорируем сообщения от администраторов группы ───────────────────
        if await _get_user_is_admin(client, chat_id, user.id):
            logger.debug(f"[ADMIN-SKIP] user={user.id} is group admin — ignoring")
            return

        # ── Запрос имени: только если неизвестен ─────────────────────────────
        if user.id not in _user_names and user.id not in _awaiting_name:
            fn = user.first_name or ""
            greeting = f"Assalomu alaykum{', ' + fn if fn else ''}! 👋 Guruhimizga xush kelibsiz!\n" \
                       f"Sizga qanday murojaat qilsam bo'ladi? Ismingizni yozing 😊"
            _awaiting_name.add(user.id)
            try:
                await message.reply(greeting)
            except Exception as e:
                logger.warning(f"[ONBOARD] Group reply failed for user={user.id}: {e}")
            return

        # ── Шаг 0а: диалог уже ведёт менеджер — молчим ───────────────────────
        if conv_key in _handed_off:
            mgr_name, handed_off_ts = _handed_off[conv_key]
            age_sec = time.time() - handed_off_ts
            if age_sec < _CONV_TIMEOUT_SEC:
                logger.debug(
                    f"[HANDOFF] {user.id}@{chat_id} → {mgr_name} "
                    f"({age_sec/60:.0f} min ago) — skipping"
                )
                return
            logger.info(f"[HANDOFF] Expired ({age_sec/60:.0f} min) for {user.id}@{chat_id} — bot resumes")
            del _handed_off[conv_key]
            _save_handoffs()

        # ── Шаг 0б: клиент отвечает на сообщение менеджера или группового админа
        taken, mgr_name = _detect_human_takeover(message, bot_id)
        if not taken and message.reply_to_message and message.reply_to_message.from_user:
            rply_user = message.reply_to_message.from_user
            if rply_user.id != bot_id and rply_user.id != user.id:
                if await _get_user_is_admin(client, chat_id, rply_user.id):
                    mgr_name = (
                        f"@{rply_user.username}" if rply_user.username
                        else (rply_user.first_name or str(rply_user.id))
                    )
                    taken = True
        if taken:
            _handed_off[conv_key] = (mgr_name, time.time())
            _save_handoffs()
            logger.info(f"[HANDOFF] Reply-chain: {user.id}@{chat_id} → {mgr_name}")
            existing = _conversations.get(conv_key)
            conv_id_taken = existing[0] if existing else None
            await _notify_handoff(client, chat_id, user, mgr_name, conv_id=conv_id_taken)
            if existing:
                _conversations.pop(conv_key)
                await tracker.close_conversation(conv_id_taken, status="handed_off")
                clear_history(conv_id_taken)
            return

    t_checks_done = time.time()

    # ── Шаг A: задержка 2–5 сек ──────────────────────────────────────────────
    delay = random.uniform(settings.reply_delay_min, settings.reply_delay_max)
    logger.debug(f"Sleeping {delay:.1f}s before replying to {user.id}")
    await asyncio.sleep(delay)
    t_after_delay = time.time()

    # ── Шаг A.1: менеджер вмешался во время задержки? ────────────────────────
    if not is_private:
        intervened, mgr_name = await _manager_intervened_during_delay(client, message)
        if intervened:
            _handed_off[conv_key] = (mgr_name, time.time())
            _save_handoffs()
            logger.info(f"[HANDOFF] During-delay: {user.id}@{chat_id} → {mgr_name}")
            existing_delay = _conversations.get(conv_key)
            conv_id_delay = existing_delay[0] if existing_delay else None
            await _notify_handoff(client, chat_id, user, mgr_name, conv_id=conv_id_delay)
            if existing_delay:
                _conversations.pop(conv_key)
                await tracker.close_conversation(conv_id_delay, status="handed_off")
                clear_history(conv_id_delay)
            return

    # ── Шаг B: открыть/найти диалог в аналитике ──────────────────────────────
    conv_id = await _get_or_open_conversation(conv_key, message)

    # ── Шаг C: записать входящее сообщение ───────────────────────────────────
    has_attachment = bool(message.voice or message.document or message.audio)
    await tracker.record_message(
        conv_id=conv_id,
        role="user",
        content=message.text or f"[attachment: {message.document and message.document.file_name or 'voice/audio'}]",
        has_attachment=has_attachment,
    )

    # ── Шаг D: AI движок → текст ответа (с индикатором «печатает…») ─────────
    _stop_typing = asyncio.Event()
    _typing_task = asyncio.create_task(_typing_loop(client, chat_id, _stop_typing))
    t_ai_start = time.time()
    try:
        reply_text = await generate_reply(
            message=message,
            conv_id=conv_id,
            tg_client=client,
            photos=_conv_photos.get(conv_key, []),
            client_name=_user_names.get(user.id),
        )
    except Exception as e:
        logger.error(f"AI engine error for conv {conv_id}: {e}")
        reply_text = "Извините, произошла техническая ошибка. Попробуйте позже."
    finally:
        _stop_typing.set()
        _typing_task.cancel()
        try:
            await _typing_task
        except asyncio.CancelledError:
            pass
    t_ai_done = time.time()

    # ── Шаг E: тайминги ──────────────────────────────────────────────────────
    total = t_ai_done - handler_start
    tg_ts = message.date.timestamp() if message.date else handler_start
    e2e   = t_ai_done - tg_ts
    logger.info(
        f"[TIMING] user={user.id} | "
        f"delivery={handler_start - tg_ts:.1f}s | "
        f"checks={t_checks_done - handler_start:.2f}s | "
        f"delay={delay:.1f}s | "
        f"ai={t_ai_done - t_ai_start:.2f}s | "
        f"total={total:.1f}s | e2e={e2e:.1f}s"
    )

    # ── Шаг F: DRY_RUN — тихий режим ─────────────────────────────────────────
    if settings.dry_run:
        u_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"
        u_at = f" @{user.username}" if user.username else ""
        logger.info(
            f"[DRY_RUN] chat={chat_id} user={user.id} ({u_name}{u_at}) "
            f"conv={conv_id} | would send: {reply_text[:120]}"
        )
        return

    # ── Шаг G: отправить ответ ────────────────────────────────────────────────
    await message.reply(reply_text)
    logger.info(f"Replied | chat={chat_id} user={user.id} conv={conv_id} | {len(reply_text)} chars")

    # ── Шаг H: зеркало и аналитика ───────────────────────────────────────────
    await _mirror_to_notification_chat(client, message, reply_text)
    await tracker.record_message(conv_id=conv_id, role="assistant", content=reply_text)


async def _handle_wizard_complete(
    client: Client, message: Message, user, rag_query: str
) -> None:
    """Wizard завершён: строим контекст, вызываем Claude, отправляем результат."""
    user_name = _user_names.get(user.id, user.first_name or "Mijoz")
    wizard_state = get_wizard_result(user.id)
    wizard_ctx = build_wizard_context(wizard_state) if wizard_state else ""

    override_text = (
        f"{wizard_ctx}\n\n"
        f"Iltimos, shu parametrlarga mos АКБ modellarini bazadan topib tavsiya qiling."
    )

    conv_key = (message.chat.id, user.id)
    conv_id = await _get_or_open_conversation(conv_key, message)
    await tracker.record_message(conv_id=conv_id, role="user", content=override_text)

    _stop_typing = asyncio.Event()
    _typing_task = asyncio.create_task(_typing_loop(client, message.chat.id, _stop_typing))
    try:
        reply_text = await generate_reply(
            message=message,
            conv_id=conv_id,
            tg_client=client,
            photos=_conv_photos.get(conv_key, []),
            client_name=user_name,
            override_text=override_text,
        )
    except Exception as e:
        logger.error(f"[WIZARD] AI error for user={user.id}: {e}", exc_info=True)
        lang = _detect_lang(message.text or "")
        reply_text = (
            "Kechirasiz, texnik xato yuz berdi. Iltimos, qayta urinib ko'ring."
            if lang != "ru" else
            "Извините, произошла техническая ошибка. Попробуйте позже."
        )
    finally:
        _stop_typing.set()
        _typing_task.cancel()
        try:
            await _typing_task
        except asyncio.CancelledError:
            pass

    # Сохраняем лид с данными квалификации
    if wizard_state and settings.database_url:
        try:
            phone = _contacts.get(user.id)
            await tracker.save_lead(
                conv_id=conv_id,
                platform="telegram",
                external_user_id=str(user.id),
                user_name=user_name,
                phone=phone,
                battery_voltage=wizard_state.get("voltage"),
                battery_ah=wizard_state.get("ah"),
                battery_type_pref=wizard_state.get("battery_type"),
                size_info=wizard_state.get("size"),
                equipment_type=wizard_state.get("equipment"),
                quantity_needed=wizard_state.get("quantity"),
                company_name=wizard_state.get("company"),
            )
            logger.info(f"[WIZARD] Lead saved for user={user.id}")
        except Exception as e:
            logger.warning(f"[WIZARD] Could not save lead for user={user.id}: {e}")

    clear_wizard(user.id)

    if settings.dry_run:
        logger.info(f"[DRY_RUN][WIZARD] user={user.id}: {reply_text[:120]}")
        return

    await message.reply(reply_text)
    logger.info(f"[WIZARD] Reply sent | user={user.id} | {len(reply_text)} chars")

    await _mirror_to_notification_chat(client, message, reply_text)
    await tracker.record_message(conv_id=conv_id, role="assistant", content=reply_text)


async def on_message(client: Client, message: Message):
    # Пользователь написал "от имени канала/группы" — from_user отсутствует.
    # Отвечаем с просьбой написать в личку и выходим (user_id неизвестен).
    if not message.from_user:
        if message.sender_chat:
            sc = message.sender_chat
            logger.debug(
                f"[ANON] sender_chat={sc.id} ({sc.title!r}) in chat={message.chat.id} — silently ignored"
            )
        return

    handler_start = time.time()
    tg_ts = message.date.timestamp() if message.date else handler_start
    delivery_lag = handler_start - tg_ts
    if delivery_lag > 10:
        logger.warning(f"[TIMING] High delivery lag: {delivery_lag:.1f}s (TG created → Pyrogram handler)")
    else:
        logger.debug(f"[TIMING] Delivery lag: {delivery_lag*1000:.0f}ms")

    user = message.from_user
    chat_id = message.chat.id
    conv_key = (chat_id, user.id)

    if is_paused():
        logger.debug(f"[PAUSED] Skipping message from {user.id}")
        return

    # Онбординг: ожидаем имя — ловим и в личке, и в группе
    if user.id in _awaiting_name and message.text:
        await _handle_name_input(client, message, user)
        return

    # Wizard: пошаговый подбор АКБ
    if is_in_wizard(user.id) and message.text:
        user_name = _user_names.get(user.id, user.first_name or "Mijoz")
        rag_query = await handle_wizard_step(client, message, user_name)
        if rag_query is None:
            return  # промежуточный шаг — следующая клавиатура уже отправлена
        # Последний шаг — нужен Claude; запускаем через семафор
        if conv_key in _processing:
            logger.debug(f"[WIZARD] Already processing {user.id}, skipping")
            return
        sem = _get_semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            logger.warning(f"[WIZARD] Concurrency limit reached for user={user.id}")
            return
        _processing.add(conv_key)
        try:
            await _handle_wizard_complete(client, message, user, rag_query)
        except Exception as e:
            logger.error(f"[WIZARD] Unhandled error for user={user.id}: {e}", exc_info=True)
        finally:
            _processing.discard(conv_key)
            sem.release()
        return

    # Сохраняем фото для последующей отправки с карточкой лида
    if message.photo:
        photos = _conv_photos.setdefault(conv_key, [])
        photos.append((message.chat.id, message.id))
        if len(photos) > 5:
            _conv_photos[conv_key] = photos[-5:]

    if conv_key in _processing:
        logger.debug(f"Already processing {user.id}, skipping duplicate message")
        return

    sem = _get_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.1)
    except asyncio.TimeoutError:
        logger.warning(
            f"Concurrency limit ({settings.max_concurrent_chats}) reached, "
            f"skipping message from user {user.id}"
        )
        return

    _processing.add(conv_key)
    try:
        await _process_message(client, message, handler_start)
    except Exception as e:
        logger.error(f"Unhandled error processing message from {user.id}: {e}", exc_info=True)
    finally:
        _processing.discard(conv_key)
        sem.release()


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 6: ОБРАБОТЧИК КОНТАКТОВ
# ═══════════════════════════════════════════════════════════════════════════════

async def on_contact_received(client: Client, message: Message) -> None:
    """
    Обрабатывает входящее сообщение с контактом.
    Срабатывает в личке (после нажатия кнопки) или в группе (если поделились вручную).
    Приоритет group=-2 — обрабатывается раньше дебаг-хэндлера и основного.
    """
    contact = message.contact
    if not contact:
        return

    phone = contact.phone_number
    # contact.user_id — Telegram ID человека в контакте (None если не TG-пользователь)
    user_id = contact.user_id or (message.from_user.id if message.from_user else None)
    if not user_id:
        logger.warning(f"[CONTACT] Received contact without user_id: phone={phone}")
        return

    _contacts[user_id] = phone
    _save_contacts()
    _awaiting_contact.discard(user_id)

    logger.info(f"[CONTACT] Received: user={user_id} phone={phone}")
    print(f"[CONTACT] New phone: user_id={user_id}  phone={phone}", flush=True)

    try:
        await message.reply(
            "Rahmat! Kontaktingiz qabul qilindi. ✅\n"
            "Endi savollaringizga javob bera olamiz!\n\n"
            "Спасибо! Контакт получен. ✅\n"
            "Теперь мы можем ответить на ваши вопросы!"
        )
    except Exception as e:
        logger.warning(f"[CONTACT] Failed to reply after contact received: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 7: ОТЛАДОЧНЫЙ ОБРАБОТЧИК
# ═══════════════════════════════════════════════════════════════════════════════

async def _debug_incoming(client: Client, message: Message) -> None:
    """
    Логирует ВСЕ входящие до любой фильтрации (group=-1).
    Убери строку add_handler в create_client() когда отладка закончена.
    """
    chat_id   = message.chat.id
    chat_type = message.chat.type

    if message.text:
        content = f"text={message.text[:50]!r}"
    elif message.voice:
        content = "voice"
    elif message.document:
        content = f"doc={getattr(message.document, 'file_name', '?')}"
    elif message.audio:
        content = "audio"
    elif message.contact:
        content = f"contact=+{message.contact.phone_number}"
    else:
        content = "other"

    if message.from_user:
        u = message.from_user
        name = f"{u.first_name or ''} {u.last_name or ''}".strip() or "—"
        uname = f" @{u.username}" if u.username else ""
        user_part = f"user={u.id} ({name}{uname})"
    elif message.sender_chat:
        sc = message.sender_chat
        user_part = f"sender_chat={sc.id} ({sc.title or '—'}) [as channel]"
    else:
        user_part = "no_user (channel_post)"

    now_ts = time.time()
    tg_ts  = message.date.timestamp() if message.date else now_ts
    lag    = now_ts - tg_ts
    lag_str = f"lag={lag:.1f}s" if lag > 1 else f"lag={lag*1000:.0f}ms"

    if chat_type == ChatType.CHANNEL:
        verdict = "✗ channel type (ignored)"
    elif chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not message.from_user and not message.sender_chat:
            verdict = "✗ channel post in group (no sender) → ignored"
        elif not message.from_user and message.sender_chat:
            verdict = "⚠ anonymous (posted as channel) → will ask to DM"
        elif not settings.allowed_chat_ids or chat_id in settings.allowed_chat_ids:
            verdict = "✓ in whitelist → will process"
        else:
            verdict = f"✗ NOT in whitelist {settings.allowed_chat_ids}"
    elif chat_type == ChatType.PRIVATE:
        verdict = "✓ private allowed" if settings.allow_private_chats else "✗ private disabled"
    else:
        verdict = f"✗ unknown type {chat_type}"

    logger.info(
        f"[ALL_MSG] chat={chat_id} ({chat_type.name}) {user_part} | {content} | {lag_str} | {verdict}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# БЛОК 8: ФАБРИКА КЛИЕНТА — создание и настройка Pyrogram
# ═══════════════════════════════════════════════════════════════════════════════

def create_client() -> Client:
    """
    Создаёт Pyrogram Client (userbot) и регистрирует обработчики.
    Вызывается один раз из main.py при старте.

    Порядок приоритетов (group):
      -2  on_contact_received  — контакты (самый высокий приоритет)
      -1  _debug_incoming      — лог всех входящих
       0  on_message           — основная логика
    """
    client = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir="data/sessions",
    )

    register_admin_handlers(client)
    register_monitor_handler(client)
    client.add_handler(
        MessageHandler(
            on_new_member,
            filters=filters.new_chat_members & filters.incoming & _chat_filter,
        ),
        group=-3,
    )
    client.add_handler(
        MessageHandler(on_contact_received, filters=filters.contact & filters.incoming),
        group=-2,
    )
    client.add_handler(MessageHandler(_debug_incoming, filters=filters.incoming), group=-1)
    client.add_handler(MessageHandler(on_message, filters=MAIN_FILTER), group=0)

    mode = "DRY_RUN (тихий режим)" if settings.dry_run else "LIVE (отвечает реально)"
    logger.info(f"Pyrogram client configured | mode={mode}")
    logger.info(f"Max concurrent chats: {settings.max_concurrent_chats}")
    logger.info(f"Reply delay: {settings.reply_delay_min}–{settings.reply_delay_max}s")
    logger.info(f"Private chats: {'enabled' if settings.allow_private_chats else 'disabled'}")
    logger.info(f"Contacts loaded: {len(_contacts)} known user(s)")

    if settings.allowed_chat_ids:
        logger.info(f"Whitelist: {len(settings.allowed_chat_ids)} chat(s) — {settings.allowed_chat_ids}")
    else:
        logger.warning("ALLOWED_CHAT_IDS not set — bot responds in ALL groups! Set it in .env for production.")

    return client
