"""
_relogin_call.py — ручной релогин bot_session с возможностью запросить
повторную доставку кода (resend → звонок/SMS). Показывает, КАКИМ способом
Telegram шлёт код (app / sms / call / email / fragment).

Запуск (в видимом окне):
    .venv\Scripts\python.exe _relogin_call.py
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE = os.getenv("TELEGRAM_PHONE")


async def main():
    app = Client(
        "bot_session",
        api_id=API_ID,
        api_hash=API_HASH,
        workdir="data/sessions",
    )
    await app.connect()

    print(f"\n>>> Raqam: {PHONE}")
    sent = await app.send_code(PHONE)
    print(f">>> Kod yuborildi.  USUL (type) = {sent.type}")
    print(f">>> Keyingi usul   (next_type)  = {sent.next_type}")
    phone_code_hash = sent.phone_code_hash

    # Если код не пришёл — пробуем resend (часто это звонок).
    ans = input(
        "\nKod kelmadimi? Qayta yuborishni (qo'ng'iroq/SMS) sinab ko'ramizmi? [ha/yo'q]: "
    ).strip().lower()
    while ans in ("ha", "h", "y", "yes", "da", "ҳа", "да"):
        try:
            sent = await app.resend_code(PHONE, phone_code_hash)
            phone_code_hash = sent.phone_code_hash
            print(f">>> Qayta yuborildi. USUL = {sent.type} | next = {sent.next_type}")
        except Exception as e:
            print(f">>> resend XATO: {type(e).__name__}: {e}")
            break
        ans = input("Yana bir marta qayta yuboramizmi? [ha/yo'q]: ").strip().lower()

    code = input("\n>>> Kodni kiriting: ").strip()
    try:
        await app.sign_in(PHONE, phone_code_hash, code)
    except SessionPasswordNeeded:
        pw = input(">>> 2FA bulut paroli: ")
        await app.check_password(pw)

    me = await app.get_me()
    print(f"\n=== MUVAFFAQIYAT! Kirildi: {me.first_name} (@{me.username}) id={me.id} ===")
    print("=== Sessiya saqlandi: data/sessions/bot_session.session ===")
    await app.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
