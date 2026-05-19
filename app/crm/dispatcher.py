"""app/crm/dispatcher.py — Round-robin manager assignment."""

import json
import os
from pathlib import Path

_RR_FILE = Path("data/rr_state.json")


def _load_managers() -> list[dict]:
    """Returns [{name, username}] from MANAGERS_CONFIG env var.

    Format in .env:
        MANAGERS_CONFIG=Feruz:@Maxcellon_sales_closer,Jahongir:@jahongir_tg,...
    """
    raw = os.getenv("MANAGERS_CONFIG", "").strip()
    managers = []
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        name, _, username = pair.partition(":")
        username = username.strip()
        if not username.startswith("@"):
            username = "@" + username
        managers.append({"name": name.strip(), "username": username})
    return managers


def get_manager_by_name(name: str) -> dict | None:
    """Find manager by name (case-insensitive). Returns {name, username} or None."""
    name_low = name.strip().lower()
    for m in _load_managers():
        if m["name"].lower() == name_low:
            return m
    return None


def get_next_manager() -> dict | None:
    """Returns next manager in round-robin rotation and advances the counter."""
    managers = _load_managers()
    if not managers:
        return None

    try:
        state = json.loads(_RR_FILE.read_text(encoding="utf-8")) if _RR_FILE.exists() else {}
    except Exception:
        state = {}

    idx = int(state.get("next", 0)) % len(managers)
    manager = managers[idx]

    state["next"] = (idx + 1) % len(managers)
    _RR_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    return manager


def get_lead_bot_usernames() -> set[str]:
    """Returns set of lowercase usernames of lead-forwarding bots (without @)."""
    raw = os.getenv("LEAD_BOT_USERNAMES", "").strip()
    return {u.strip().lower().lstrip("@") for u in raw.split(",") if u.strip()}
