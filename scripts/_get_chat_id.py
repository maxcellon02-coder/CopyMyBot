import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import httpx

token = os.getenv("NOTIFICATION_BOT_TOKEN")
print("Пиши в группу 'Сотув ва техник булим' — жду 60 сек...")
print("-" * 50)

offset = 0
seen = set()

# Сначала сбросим старые апдейты
with httpx.Client(timeout=10) as c:
    r = c.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"offset": -1})
    updates = r.json().get("result", [])
    if updates:
        offset = updates[-1]["update_id"] + 1

for _ in range(30):
    with httpx.Client(timeout=10) as c:
        r = c.get(f"https://api.telegram.org/bot{token}/getUpdates",
                  params={"offset": offset, "timeout": 3})
    updates = r.json().get("result", [])
    for u in updates:
        offset = u["update_id"] + 1
        msg = u.get("message") or u.get("channel_post")
        if msg:
            chat = msg["chat"]
            cid = chat["id"]
            if cid not in seen:
                seen.add(cid)
                print(f"Chat ID : {cid}")
                print(f"Title   : {chat.get('title', '(личка)')}")
                print(f"Type    : {chat.get('type')}")
                print("-" * 50)
    if not updates:
        time.sleep(2)
