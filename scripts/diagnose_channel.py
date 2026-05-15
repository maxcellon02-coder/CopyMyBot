"""
Diagnostic script: inspects the knowledge-base channel and tries to parse every document.
Run ONLY when the main bot is stopped (same session file).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pyrogram import Client
from app.core.config import settings
from app.rag.sources.parser import parse_document

CHANNEL = -1003729116719


async def main():
    client = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir="data/sessions",
    )

    async with client:
        me = await client.get_me()
        print(f"\n✅ Logged in as: {me.first_name} (@{me.username})\n")

        total_msgs = 0
        text_msgs = 0
        docs = []
        photos = 0
        videos = 0

        print(f"📡 Reading channel {CHANNEL}...\n")
        async for msg in client.get_chat_history(CHANNEL, limit=500):
            total_msgs += 1
            if msg.text:
                text_msgs += 1
                print(f"  [msg {msg.id}] TEXT ({len(msg.text)} chars): {msg.text[:80]!r}")
            if msg.photo:
                photos += 1
                cap = (msg.caption or "")[:60]
                print(f"  [msg {msg.id}] PHOTO caption={cap!r}")
            if msg.video:
                videos += 1
                cap = (msg.caption or "")[:60]
                print(f"  [msg {msg.id}] VIDEO caption={cap!r}")
            if msg.document:
                fname = msg.document.file_name or "unnamed"
                size_kb = (msg.document.file_size or 0) // 1024
                docs.append((msg.id, fname, size_kb))
                print(f"  [msg {msg.id}] DOC: {fname!r}  ({size_kb} KB)")

        print(f"\n{'='*60}")
        print(f"TOTAL messages: {total_msgs}")
        print(f"  Text:   {text_msgs}")
        print(f"  Photos: {photos}")
        print(f"  Videos: {videos}")
        print(f"  Docs:   {len(docs)}")
        print(f"{'='*60}\n")

        if not docs:
            print("⚠️  NO documents found in channel!")
            return

        print("📄 Downloading and parsing each document...\n")
        for msg_id, fname, size_kb in docs:
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "?"
            if ext not in ("pdf", "docx", "xlsx", "txt"):
                print(f"  [{msg_id}] SKIP {fname!r} — unsupported extension '{ext}'")
                continue

            print(f"  [{msg_id}] Downloading {fname!r} ({size_kb} KB)...")
            try:
                async for msg in client.get_chat_history(CHANNEL, limit=500):
                    if msg.id == msg_id and msg.document:
                        raw = await client.download_media(msg, in_memory=True)
                        if raw:
                            data = bytes(raw.getbuffer())
                            parsed = parse_document(data, fname)
                            if parsed and parsed.text.strip():
                                words = len(parsed.text.split())
                                print(f"    ✅ Parsed OK — {words} words, parser={parsed.metadata.get('parser','?')}")
                                print(f"    Preview: {parsed.text[:200]!r}")
                            else:
                                print(f"    ❌ Parser returned EMPTY text — likely scanned image PDF")
                        else:
                            print(f"    ❌ download_media returned None")
                        break
            except Exception as e:
                print(f"    ❌ Error: {e}")
            print()


asyncio.run(main())
