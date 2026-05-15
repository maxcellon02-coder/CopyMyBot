"""
SQLAlchemy 2.0 ORM models for the analytics database.

Tables:
  conversations  — one row per user conversation session
  messages       — individual messages within a conversation
  leads          — extracted lead contact info
  ingestion_logs — RAG ingestion run history
"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # supports telegram, olx, glotr, prom_uz, bizkim, stroyka_uz, etc.
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_user_id: Mapped[str] = mapped_column(String(200), index=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    language_detected: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    # active | closed | escalated_to_manager
    status: Mapped[str] = mapped_column(String(30), default="active")

    messages: Mapped[List["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )
    leads: Mapped[List["Lead"]] = relationship(
        "Lead", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # user | assistant
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    has_attachment: Mapped[bool] = mapped_column(Boolean, default=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_user_id: Mapped[str] = mapped_column(String(200))
    user_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    product_interest: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_to_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # ID сообщения-карточки в группе менеджеров (для отслеживания ответов)
    manager_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Менеджер, взявший лид в работу
    assigned_to: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="leads")


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(50))   # telegram_channel | google_drive
    source_id: Mapped[str] = mapped_column(String(200))
    chunks_indexed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | success | error
    status: Mapped[str] = mapped_column(String(20), default="running")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
