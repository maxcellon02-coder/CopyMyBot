"""Centralised config — reads from .env."""
import os
from dataclasses import dataclass, field
from typing import List


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes")


def _int(key: str, default: int = 0) -> int:
    val = os.getenv(key, "").strip()
    return int(val) if val else default


@dataclass
class Settings:
    # ── Telegram credentials ──────────────────────────────────────────────────
    api_id: int = _int("TELEGRAM_API_ID", 0)
    api_hash: str = os.getenv("TELEGRAM_API_HASH", "")
    phone: str = os.getenv("TELEGRAM_PHONE", "")

    # ── Where the bot operates ────────────────────────────────────────────────
    # If true, bot also replies in Direct Messages (личка)
    allow_private_chats: bool = _bool("ALLOW_PRIVATE_CHATS", False)
    # Comma-separated Telegram user IDs the bot will NEVER reply to
    ignored_user_ids: List[int] = field(
        default_factory=lambda: [
            int(i.strip())
            for i in os.getenv("IGNORED_USER_IDS", "").split(",")
            if i.strip()
        ]
    )

    # ── Behaviour flags ───────────────────────────────────────────────────────
    # DRY_RUN=true → bot sees messages and logs replies but sends NOTHING
    dry_run: bool = _bool("DRY_RUN", False)
    # Max conversations processed at the same time
    max_concurrent_chats: int = _int("MAX_CONCURRENT_CHATS", 25)
    # Random reply delay range (seconds) — looks human
    reply_delay_min: float = float(os.getenv("REPLY_DELAY_MIN", "2.0"))
    reply_delay_max: float = float(os.getenv("REPLY_DELAY_MAX", "5.0"))

    # ── Notifications ─────────────────────────────────────────────────────────
    # Group/channel where every bot reply is mirrored (0 = disabled)
    notification_chat_id: int = _int("NOTIFICATION_CHAT_ID", 0)
    # Group where CRM lead summaries are sent
    manager_group_id: int = _int("MANAGER_GROUP_ID", 0)

    # ── Whitelist of chats where the bot is allowed to respond ───────────────
    # If empty — bot responds in ALL groups (dangerous, use only for testing)
    # If set — bot ONLY responds in these chat IDs (recommended for production)
    allowed_chat_ids: List[int] = field(
        default_factory=lambda: [
            int(i.strip())
            for i in os.getenv("ALLOWED_CHAT_IDS", "").split(",")
            if i.strip()
        ]
    )

    # ── RAG knowledge sources ─────────────────────────────────────────────────
    monitored_channels: List[str] = field(
        default_factory=lambda: [
            c.strip()
            for c in os.getenv("MONITORED_CHANNELS", "").split(",")
            if c.strip()
        ]
    )

    # ── AI ────────────────────────────────────────────────────────────────────
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Google Drive ──────────────────────────────────────────────────────────
    drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    service_account_json: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")

    # ── Analytics DB ──────────────────────────────────────────────────────────
    database_url: str = os.getenv("DATABASE_URL", "")

    # ── Admin ─────────────────────────────────────────────────────────────────
    admin_ids: List[int] = field(
        default_factory=lambda: [
            int(i.strip())
            for i in os.getenv("ADMIN_USER_IDS", "").split(",")
            if i.strip()
        ]
    )

    # ── Managers (live humans who can take over conversations) ─────────────────
    # When a user in this list replies to a customer, the bot backs off.
    # If empty — any unknown third party replying triggers a handoff.
    manager_user_ids: List[int] = field(
        default_factory=lambda: [
            int(i.strip())
            for i in os.getenv("MANAGER_USER_IDS", "").split(",")
            if i.strip()
        ]
    )


settings = Settings()
