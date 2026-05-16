"""Диагностика: сырой запрос к группе через Pyrogram raw API."""
import asyncio, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from pyrogram import Client, raw
from app.core.config import settings

CHAT_ID = abs(settings.manager_group_id)  # 5143151591

async def main():
    tg = Client("bot_session", api_id=settings.api_id, api_hash=settings.api_hash,
                phone_number=settings.phone, workdir=str(ROOT / "data" / "sessions"))
    await tg.start()
    print(f"Connected. Testing group id={settings.manager_group_id}")

    # 1. GetFullChat — сырой запрос, не через resolve_peer
    try:
        full = await tg.invoke(raw.functions.messages.GetFullChat(chat_id=CHAT_ID))
        chat = full.chats[0] if full.chats else None
        print(f"\nGetFullChat OK:")
        print(f"  title      = {getattr(chat, 'title', '?')}")
        print(f"  id         = {getattr(chat, 'id', '?')}")
        print(f"  deactivated= {getattr(chat, 'deactivated', '?')}")
        print(f"  migrated_to= {getattr(chat, 'migrated_to', None)}")
        migrated = getattr(chat, 'migrated_to', None)
        if migrated:
            print(f"\n  *** GROUP MIGRATED to channel_id={migrated.channel_id} ***")
    except Exception as e:
        print(f"GetFullChat failed: {e}")

    # 2. Попытка отправить напрямую через InputPeerChat
    try:
        import random
        peer = raw.types.InputPeerChat(chat_id=CHAT_ID)
        await tg.invoke(raw.functions.messages.SendMessage(
            peer=peer,
            message="✅ raw send test",
            random_id=random.randint(-2**63, 2**63-1),
            no_webpage=True,
        ))
        print("\nRaw SendMessage: SUCCESS")
    except Exception as e:
        print(f"\nRaw SendMessage failed: {e}")

    await tg.stop()

asyncio.run(main())
