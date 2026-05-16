"""Быстрый тест: может ли bot_session отправить в MANAGER_GROUP_ID."""
import asyncio, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from pyrogram import Client
from app.core.config import settings

async def main():
    tg = Client("bot_session", api_id=settings.api_id, api_hash=settings.api_hash,
                phone_number=settings.phone, workdir=str(ROOT / "data" / "sessions"))
    await tg.start()
    print(f"Connected. Sending to {settings.manager_group_id}...")
    try:
        msg = await tg.send_message(settings.manager_group_id, "✅ тест check_leads")
        print(f"SUCCESS: msg_id={msg.id}")
    except Exception as e:
        print(f"FAIL: {e}")
        # Попробуем через get_chat
        try:
            chat = await tg.get_chat(settings.manager_group_id)
            print(f"get_chat OK: title={chat.title} id={chat.id} type={chat.type}")
            msg = await tg.send_message(chat.id, "✅ тест через chat.id")
            print(f"SUCCESS via chat.id: msg_id={msg.id}")
        except Exception as e2:
            print(f"FAIL via chat.id: {e2}")
    await tg.stop()

asyncio.run(main())
