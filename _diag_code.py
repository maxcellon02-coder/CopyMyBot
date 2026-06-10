"""Диагностика: запрашиваем код у Telegram и печатаем способ доставки / ошибку.
Использует отдельную сессию diag_session, чтобы не трогать bot_session."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from pyrogram import Client

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE = os.getenv("TELEGRAM_PHONE")


async def main():
    print(f"PHONE={PHONE}")
    app = Client("diag_session", api_id=API_ID, api_hash=API_HASH, workdir=r"E:\Claude code\mybot\data\sessions")
    await app.connect()
    try:
        sent = await app.send_code(PHONE)
        print(f"RESULT_OK type={sent.type} next_type={sent.next_type}")
    except Exception as e:
        print(f"RESULT_ERROR {type(e).__name__}: {e}")
    finally:
        try:
            await app.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
