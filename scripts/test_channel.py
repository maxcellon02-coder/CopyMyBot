import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.core.config import settings
from pyrogram import Client


async def main():
    client = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir="data/sessions",
    )
    await client.start()

    print(f"Monitored channels: {settings.monitored_channels}")
    print()

    for ch in settings.monitored_channels:
        print(f"=== Testing channel: {ch} ===")
        try:
            chat = await client.get_chat(ch)
            print(f"  Name : {chat.title}")
            print(f"  Type : {chat.type}")
            print(f"  ID   : {chat.id}")
        except Exception as e:
            print(f"  get_chat ERROR: {e}")
            await client.stop()
            return

        count = 0
        try:
            async for msg in client.get_chat_history(ch, limit=5):
                count += 1
                text = (msg.text or msg.caption or "").strip()[:80]
                media = "photo" if msg.photo else ("video" if msg.video else ("doc" if msg.document else ""))
                print(f"  [{msg.id}] {media or 'text'}: {repr(text)}")
        except Exception as e:
            print(f"  get_chat_history ERROR: {e}")

        if count == 0:
            print("  (no messages found — channel may be empty)")

    await client.stop()
    print("\nDone.")


asyncio.run(main())
