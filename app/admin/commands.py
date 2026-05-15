"""
app/admin/commands.py — Admin slash commands for bot management.

Commands (private message from ADMIN_USER_IDS only):
  /help          — list all commands
  /status        — active chats, uptime, memory stats
  /leads [N]     — last N leads from DB (default 5)
  /pause         — stop responding to users (DRY_RUN stays, just stops processing)
  /resume        — resume normal operation
  /dry on|off    — toggle DRY_RUN at runtime
  /prompt        — show current system prompt
  /reload_prompt — reload system prompt from data/system_prompt.txt
  /reindex       — trigger immediate RAG re-indexing
  /clear_history — wipe all in-memory conversation histories
"""

import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from app.analytics import tracker
from app.core.config import settings
from app.rag import scheduler as rag_scheduler

# ── Pause flag (read by client.py) ───────────────────────────────────────────

_paused: bool = False
_pause_since: Optional[float] = None
_start_time: float = time.time()


def is_paused() -> bool:
    return _paused


def set_paused(value: bool) -> None:
    global _paused, _pause_since
    _paused = value
    _pause_since = time.time() if value else None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uptime() -> str:
    secs = int(time.time() - _start_time)
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m}m"


async def _is_admin(_, __, message: Message) -> bool:
    return bool(
        message.from_user
        and settings.admin_ids
        and message.from_user.id in settings.admin_ids
    )


_admin_filter = (
    filters.incoming
    & filters.private
    & filters.create(_is_admin)
    & filters.regex(r"^/")
)

# ── Handlers ──────────────────────────────────────────────────────────────────

async def _cmd_help(client: Client, message: Message) -> None:
    await message.reply(
        "**Команды администратора:**\n"
        "/status — статус бота\n"
        "/stats — статистика переговоров и лидов\n"
        "/leads [N] — последние N лидов (по умолч. 5)\n"
        "/pause — поставить бота на паузу\n"
        "/resume — снять с паузы\n"
        "/dry on|off — включить/выключить DRY\\_RUN\n"
        "/prompt — показать системный промпт\n"
        "/reload\\_prompt — перезагрузить промпт из файла\n"
        "/reindex — запустить RAG переиндексацию\n"
        "/rag\\_status — сколько документов в Qdrant\n"
        "/ping\\_manager — тест отправки в группу менеджеров\n"
        "/clear\\_history — очистить историю диалогов в памяти"
    )


async def _cmd_status(client: Client, message: Message) -> None:
    from app.bot.client import _conversations, _handed_off, _contacts, _processing
    from app.ai.engine import _history

    active = sum(1 for (_, ts) in _conversations.values() if time.monotonic() - ts < 1800)
    mode = "PAUSED ⏸" if _paused else ("DRY_RUN 🔇" if settings.dry_run else "LIVE 🟢")
    rag_last = rag_scheduler.last_run
    rag_str = rag_last.strftime("%H:%M %d.%m") if rag_last else "никогда"

    await message.reply(
        f"**Статус бота**\n"
        f"Режим: {mode}\n"
        f"Аптайм: {_uptime()}\n"
        f"Активных диалогов: {active}\n"
        f"На обработке сейчас: {len(_processing)}\n"
        f"Хэндоффов: {len(_handed_off)}\n"
        f"Контактов в памяти: {len(_contacts)}\n"
        f"Историй в памяти: {len(_history)}\n"
        f"RAG последний запуск: {rag_str}"
    )


async def _cmd_leads(client: Client, message: Message) -> None:
    parts = message.text.split()
    n = 5
    if len(parts) > 1 and parts[1].isdigit():
        n = min(int(parts[1]), 20)

    leads = await tracker.get_recent_leads(limit=n)
    if not leads:
        await message.reply("Лидов пока нет.")
        return

    lines = [f"**Последние {len(leads)} лидов:**"]
    for lead in leads:
        lines.append(
            f"\n#{lead.id} | {lead.user_name or '—'} | 📞 {lead.phone or '—'}\n"
            f"  🛍 {lead.product_interest or '—'} | 📍 {lead.location or '—'}\n"
            f"  {lead.notes or ''}"
        )
    await message.reply("\n".join(lines)[:4000])


async def _cmd_pause(client: Client, message: Message) -> None:
    if _paused:
        await message.reply("Уже на паузе.")
        return
    set_paused(True)
    logger.info("[ADMIN] Bot paused by admin")
    await message.reply("⏸ Бот на паузе. Пользователи не получают ответов.")


async def _cmd_resume(client: Client, message: Message) -> None:
    if not _paused:
        await message.reply("Бот уже работает в штатном режиме.")
        return
    set_paused(False)
    logger.info("[ADMIN] Bot resumed by admin")
    await message.reply("▶️ Бот возобновил работу.")


async def _cmd_dry(client: Client, message: Message) -> None:
    parts = message.text.lower().split()
    if len(parts) < 2 or parts[1] not in ("on", "off"):
        current = "on" if settings.dry_run else "off"
        await message.reply(f"Текущий режим: DRY_RUN={current}\nИспользование: /dry on|off")
        return
    settings.dry_run = parts[1] == "on"
    state = "включён 🔇" if settings.dry_run else "выключен 🟢"
    logger.info(f"[ADMIN] DRY_RUN set to {settings.dry_run}")
    await message.reply(f"DRY_RUN {state}")


async def _cmd_prompt(client: Client, message: Message) -> None:
    from app.ai.engine import _get_system_prompt
    prompt = _get_system_prompt()
    await message.reply(f"**Текущий системный промпт:**\n\n{prompt}"[:4000])


async def _cmd_reload_prompt(client: Client, message: Message) -> None:
    import app.ai.engine as engine_mod
    engine_mod._cached_system_prompt = None  # force re-read on next call
    new_prompt = engine_mod._get_system_prompt()
    logger.info("[ADMIN] System prompt reloaded")
    await message.reply(f"Промпт перезагружен ({len(new_prompt)} символов).")


async def _cmd_reindex(client: Client, message: Message) -> None:
    await message.reply(
        "🔄 Запускаю полную переиндексацию базы знаний...\n"
        "Старые данные будут удалены и загружены заново из канала."
    )
    logger.info("[ADMIN] Full reindex triggered by admin")
    try:
        stats = await rag_scheduler.force_full_reindex()
        tg = stats.get("telegram", 0)
        gd = stats.get("gdrive", 0)
        errors = stats.get("errors", [])
        total = tg + gd
        lines = [f"✅ Переиндексация завершена!\n\nЧанков добавлено: **{total}**"]
        if tg:
            lines.append(f"  • Telegram-канал: {tg}")
        if gd:
            lines.append(f"  • Google Drive: {gd}")
        if errors:
            lines.append(f"\n⚠️ Ошибки:\n" + "\n".join(f"  • {e}" for e in errors))
        await message.reply("\n".join(lines))
    except Exception as e:
        logger.error(f"[ADMIN] Reindex failed: {e}", exc_info=True)
        await message.reply(f"❌ Ошибка переиндексации: {e}")


async def _cmd_stats(client: Client, message: Message) -> None:
    stats = await tracker.get_stats()
    lvl = stats["leads_by_level"]
    await message.reply(
        f"**📊 Статистика**\n\n"
        f"**Переговоры:**\n"
        f"  Всего: {stats['total_conversations']}\n"
        f"  Активных: {stats['active_conversations']}\n"
        f"  Передано менеджерам: {stats['handed_off']}\n\n"
        f"**Лиды:**\n"
        f"  Всего: {stats['total_leads']}\n"
        f"  Взято в работу: {stats['assigned_leads']}\n"
        f"  Ждут: {stats['unassigned_leads']}\n\n"
        f"**По уровню интереса:**\n"
        f"  👀 Смотрит: {lvl.get('browsing', 0)}\n"
        f"  🔥 Интересуется: {lvl.get('interested', 0)}\n"
        f"  💰 Готов купить: {lvl.get('ready_to_buy', 0)}"
    )


async def _cmd_clear_history(client: Client, message: Message) -> None:
    from app.ai.engine import _history
    count = len(_history)
    _history.clear()
    logger.info(f"[ADMIN] Cleared {count} conversation histories")
    await message.reply(f"Очищено {count} историй диалогов.")


async def _cmd_ping_manager(client: Client, message: Message) -> None:
    """Тест отправки сообщения в группу менеджеров."""
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        await message.reply("❌ MANAGER_GROUP_ID не задан в .env")
        return
    await message.reply(f"Отправляю тестовое сообщение в группу `{target}`...")
    try:
        await client.send_message(
            target,
            "✅ Тест уведомления от бота — соединение работает!\n"
            f"Время: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}",
        )
        await message.reply(f"✅ Успешно! Сообщение отправлено в `{target}`")
        logger.info(f"[ADMIN] ping_manager: OK → {target}")
    except Exception as e:
        await message.reply(
            f"❌ Ошибка отправки в `{target}`:\n`{e}`\n\n"
            f"Убедись что бот (+998550555552) добавлен в эту группу."
        )
        logger.error(f"[ADMIN] ping_manager: FAILED → {target}: {e}")


async def _cmd_rag_status(client: Client, message: Message) -> None:
    """Показывает сколько документов в Qdrant и последние чанки."""
    try:
        from app.rag.retriever import get_store
        from app.rag.vector_store import COLLECTION
        store = get_store()
        qdrant = store._client_()
        info = await qdrant.get_collection(COLLECTION)
        count = info.points_count
        await message.reply(
            f"**RAG / Qdrant статус**\n"
            f"Коллекция: `{COLLECTION}`\n"
            f"Чанков в базе: **{count}**\n\n"
            f"Если 0 — запусти `/reindex` после добавления файлов в канал."
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка Qdrant: `{e}`")


# ── Router ────────────────────────────────────────────────────────────────────

_ROUTES = {
    "/help": _cmd_help,
    "/status": _cmd_status,
    "/stats": _cmd_stats,
    "/leads": _cmd_leads,
    "/pause": _cmd_pause,
    "/resume": _cmd_resume,
    "/dry": _cmd_dry,
    "/prompt": _cmd_prompt,
    "/reload_prompt": _cmd_reload_prompt,
    "/reindex": _cmd_reindex,
    "/clear_history": _cmd_clear_history,
    "/ping_manager": _cmd_ping_manager,
    "/rag_status": _cmd_rag_status,
}


async def _dispatch(client: Client, message: Message) -> None:
    cmd = (message.text or "").split()[0].lower().split("@")[0]
    handler = _ROUTES.get(cmd)
    if handler:
        try:
            await handler(client, message)
        except Exception as e:
            logger.error(f"[ADMIN] Command {cmd} error: {e}", exc_info=True)
            await message.reply(f"Ошибка выполнения команды: {e}")
    else:
        await message.reply(f"Неизвестная команда. Напишите /help.")


def register_admin_handlers(client: Client) -> None:
    """Call from create_client() to attach admin handler."""
    client.add_handler(MessageHandler(_dispatch, filters=_admin_filter), group=-3)
    logger.info(f"Admin commands registered for {len(settings.admin_ids)} admin(s)")
