"""Проверяет и добавляет peer -5143151591 в кеш bot_session."""
import sqlite3, sys, time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "sessions" / "bot_session.session"

conn = sqlite3.connect(str(DB))

print("Tables:", [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")])

# Найти колонки таблицы peers
try:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(peers)")]
    print("Peers columns:", cols)
    print("All peers:")
    for row in conn.execute("SELECT * FROM peers"):
        print(" ", row)
except Exception as e:
    print("peers table error:", e)

# Проверить есть ли нужный peer
CHAT_ID = 5143151591
try:
    found = list(conn.execute("SELECT * FROM peers WHERE id=?", (CHAT_ID,)))
    print(f"\nPeer {CHAT_ID} in cache:", found)

    if not found:
        print(f"Adding peer {CHAT_ID} to cache...")
        # type=2 для basic group в Pyrogram
        conn.execute(
            "INSERT OR REPLACE INTO peers (id, access_hash, type, username, phone_number, last_update_on) "
            "VALUES (?, 0, 2, NULL, NULL, ?)",
            (CHAT_ID, int(time.time()))
        )
        conn.commit()
        print("Done. Peer added.")
    else:
        print("Peer already in cache, checking type...")
        # Update type to 2 (group) just in case
        conn.execute("UPDATE peers SET type=2, access_hash=0 WHERE id=?", (CHAT_ID,))
        conn.commit()
        print("Updated peer type to 2 (group).")
except Exception as e:
    print("Error:", e)

conn.close()
