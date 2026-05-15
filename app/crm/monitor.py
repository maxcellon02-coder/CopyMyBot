"""
app/crm/monitor.py — Monitors manager group for lead assignment.

When a manager replies to a lead card message → records who took the lead.

Handler is registered in create_client() with group=-4 (highest priority).
"""

from loguru import logger
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from app.analytics import tracker
from app.core.config import settings
from app.crm.extractor import get_lead_id_by_msg


async def _in_manager_group(_, __, message: Message) -> bool:
    target = settings.manager_group_id or settings.notification_chat_id
    return bool(target and message.chat.id == target)


async def on_manager_reply(client: Client, message: Message) -> None:
    """
    Fires when anyone sends a message in the manager group.
    If it's a reply to a known lead card → assign the lead.
    """
    if not message.reply_to_message:
        return

    replied_msg_id = message.reply_to_message.id
    lead_id = get_lead_id_by_msg(replied_msg_id)
    if lead_id is None:
        # Not a reply to a known lead card — try DB lookup (covers bot restarts)
        lead_id = await tracker.get_lead_by_manager_msg(replied_msg_id)
    if lead_id is None:
        return

    sender = message.from_user
    if not sender:
        return

    manager_name = (
        f"@{sender.username}" if sender.username
        else f"{sender.first_name or ''} {sender.last_name or ''}".strip()
        or str(sender.id)
    )

    await tracker.assign_lead(lead_id, manager_name)
    logger.info(f"[MONITOR] Lead #{lead_id} assigned to {manager_name}")

    try:
        await message.reply(f"✅ Лид #{lead_id} закреплён за {manager_name}")
    except Exception as e:
        logger.debug(f"[MONITOR] Could not confirm assignment: {e}")


def register_monitor_handler(client: Client) -> None:
    """Call from create_client() after bot starts."""
    target = settings.manager_group_id or settings.notification_chat_id
    if not target:
        logger.warning("[MONITOR] MANAGER_GROUP_ID and NOTIFICATION_CHAT_ID not set — assignment tracking disabled")
        return

    monitor_filter = (
        filters.incoming
        & filters.reply
        & filters.create(_in_manager_group)
    )
    client.add_handler(MessageHandler(on_manager_reply, filters=monitor_filter), group=-4)
    logger.info(f"[MONITOR] Manager group monitor active for chat {target}")
