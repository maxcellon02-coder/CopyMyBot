"""
High-level analytics API used by the bot handlers and CRM module.

Usage pattern:
    conv_id = await tracker.open_conversation("telegram", str(user.id), user.first_name)
    await tracker.record_message(conv_id, "user", text)
    await tracker.record_message(conv_id, "assistant", reply, tokens_used=120)
    await tracker.save_lead(conv_id, platform="telegram", ...)
    await tracker.close_conversation(conv_id)
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update

from app.analytics.db import get_session
from app.analytics.models import Conversation, IngestionLog, Lead, Message


async def open_conversation(
    platform: str,
    external_user_id: str,
    user_name: Optional[str] = None,
    language: Optional[str] = None,
) -> int:
    """Create a new conversation row. Returns the conversation id."""
    async with get_session() as s:
        conv = Conversation(
            platform=platform,
            external_user_id=external_user_id,
            user_name=user_name,
            language_detected=language,
        )
        s.add(conv)
        await s.flush()
        return conv.id


async def update_language(conv_id: int, language: str):
    async with get_session() as s:
        await s.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(language_detected=language)
        )


async def record_message(
    conv_id: int,
    role: str,
    content: str,
    tokens_used: Optional[int] = None,
    has_attachment: bool = False,
):
    """Append a message and bump conversation counters."""
    async with get_session() as s:
        s.add(Message(
            conversation_id=conv_id,
            role=role,
            content=content,
            tokens_used=tokens_used,
            has_attachment=has_attachment,
        ))
        await s.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(
                message_count=Conversation.message_count + 1,
                last_message_at=datetime.now(timezone.utc),
            )
        )


async def close_conversation(conv_id: int, status: str = "closed"):
    async with get_session() as s:
        await s.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(status=status)
        )


async def save_lead(
    conv_id: int,
    platform: str,
    external_user_id: str,
    user_name: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    product_interest: Optional[str] = None,
    location: Optional[str] = None,
    notes: Optional[str] = None,
    battery_voltage: Optional[str] = None,
    battery_ah: Optional[str] = None,
    battery_type_pref: Optional[str] = None,
    size_info: Optional[str] = None,
    equipment_type: Optional[str] = None,
    quantity_needed: Optional[str] = None,
    company_name: Optional[str] = None,
) -> int:
    """Save extracted lead info. Returns lead id."""
    async with get_session() as s:
        lead = Lead(
            conversation_id=conv_id,
            platform=platform,
            external_user_id=external_user_id,
            user_name=user_name,
            phone=phone,
            email=email,
            product_interest=product_interest,
            location=location,
            notes=notes,
            battery_voltage=battery_voltage,
            battery_ah=battery_ah,
            battery_type_pref=battery_type_pref,
            size_info=size_info,
            equipment_type=equipment_type,
            quantity_needed=quantity_needed,
            company_name=company_name,
        )
        s.add(lead)
        await s.flush()
        return lead.id


async def mark_lead_sent(lead_id: int, manager_message_id: Optional[int] = None):
    values: dict = {"sent_to_manager": True, "sent_at": datetime.now(timezone.utc)}
    if manager_message_id is not None:
        values["manager_message_id"] = manager_message_id
    async with get_session() as s:
        await s.execute(update(Lead).where(Lead.id == lead_id).values(**values))


async def assign_lead(lead_id: int, manager_name: str) -> None:
    """Mark a lead as taken by a manager."""
    async with get_session() as s:
        await s.execute(
            update(Lead)
            .where(Lead.id == lead_id)
            .values(assigned_to=manager_name, assigned_at=datetime.now(timezone.utc))
        )


async def get_lead_by_manager_msg(manager_message_id: int) -> Optional[int]:
    """Return lead_id for the given manager group message_id, or None."""
    async with get_session() as s:
        row = await s.execute(
            select(Lead.id).where(Lead.manager_message_id == manager_message_id)
        )
        result = row.scalar_one_or_none()
        return result


async def get_stats() -> dict:
    """Return summary counts for the admin /stats command."""
    async with get_session() as s:
        total_convs = (await s.execute(select(func.count(Conversation.id)))).scalar_one()
        active_convs = (await s.execute(
            select(func.count(Conversation.id)).where(Conversation.status == "active")
        )).scalar_one()
        handed_off = (await s.execute(
            select(func.count(Conversation.id)).where(Conversation.status == "handed_off")
        )).scalar_one()
        total_leads = (await s.execute(select(func.count(Lead.id)))).scalar_one()
        assigned_leads = (await s.execute(
            select(func.count(Lead.id)).where(Lead.assigned_to.isnot(None))
        )).scalar_one()
        leads_by_level = {}
        for level in ("browsing", "interested", "ready_to_buy"):
            count = (await s.execute(
                select(func.count(Lead.id)).where(Lead.notes.like(f"[{level}]%"))
            )).scalar_one()
            leads_by_level[level] = count
    return {
        "total_conversations": total_convs,
        "active_conversations": active_convs,
        "handed_off": handed_off,
        "total_leads": total_leads,
        "assigned_leads": assigned_leads,
        "unassigned_leads": total_leads - assigned_leads,
        "leads_by_level": leads_by_level,
    }


# ── ingestion log helpers ─────────────────────────────────────────────────────

async def log_ingestion_start(source_type: str, source_id: str) -> int:
    async with get_session() as s:
        entry = IngestionLog(source_type=source_type, source_id=source_id, status="running")
        s.add(entry)
        await s.flush()
        return entry.id


async def log_ingestion_done(log_id: int, chunks: int):
    async with get_session() as s:
        await s.execute(
            update(IngestionLog)
            .where(IngestionLog.id == log_id)
            .values(
                chunks_indexed=chunks,
                finished_at=datetime.now(timezone.utc),
                status="success",
            )
        )


async def log_ingestion_error(log_id: int, error: str):
    async with get_session() as s:
        await s.execute(
            update(IngestionLog)
            .where(IngestionLog.id == log_id)
            .values(
                finished_at=datetime.now(timezone.utc),
                status="error",
                error_message=error[:2000],
            )
        )


# ── simple dashboard queries ──────────────────────────────────────────────────

async def count_conversations_by_platform() -> dict:
    async with get_session() as s:
        rows = await s.execute(
            select(Conversation.platform, Conversation.id)
        )
        counts: dict = {}
        for platform, _ in rows:
            counts[platform] = counts.get(platform, 0) + 1
        return counts


async def get_recent_leads(limit: int = 50) -> list:
    async with get_session() as s:
        result = await s.execute(
            select(Lead).order_by(Lead.extracted_at.desc()).limit(limit)
        )
        return result.scalars().all()
