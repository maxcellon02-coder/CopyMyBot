import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import httpx

token = os.getenv("NOTIFICATION_BOT_TOKEN")
print("Жду сообщения в группе... Напиши что-нибудь в группе менеджеров!")
offset = 0
for _ in range(30):
    with httpx.Client(timeout=10) as c:
        r = c.get(f"https://api.telegram.org/bot{token}/getUpdates",
                  params={"offset": offset, "timeout": 5})
    updates = r.json().get("result", [])
    for u in updates:
        offset = u["update_id"] + 1
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member")
        if msg:
            chat = msg.get("chat") or msg.get("new_chat_member", {})
            if chat and "id" in chat:
                print(f"Chat ID : {chat['id']}")
                print(f"Title   : {chat.get('title')}")
                print(f"Type    : {chat.get('type')}")
                sys.exit(0)
    time.sleep(2)

print("Ничего не получено за 60 сек.")
