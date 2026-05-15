"""
Joins a private channel by invite link and prints its numeric ID.
Run once: python scripts/join_and_get_id.py <invite_link>
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from pyrogram import Client
from app.core.config import settings


async def main():
    invite = sys.argv[1] if len(sys.argv) > 1 else input("Invite link: ").strip()

    client = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir="data/sessions",
    )

    await client.start()
    try:
        # Try to get chat info first (already a member?)
        try:
            chat = await client.get_chat(invite)
            print(f"Already a member: {chat.title!r}")
            print(f"Channel ID: {chat.id}")
            return
        except Exception:
            pass

        # Join via invite link
        chat = await client.join_chat(invite)
        print(f"Joined: {chat.title!r}")
        print(f"Channel ID: {chat.id}")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
