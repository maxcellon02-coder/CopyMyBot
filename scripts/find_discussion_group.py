"""
scripts/find_discussion_group.py

Находит ID группы обсуждений, связанной с каналом.

Использование:
    python scripts/find_discussion_group.py
    python scripts/find_discussion_group.py -100XXXXXXXXX
    python scripts/find_discussion_group.py -100XXXXXXXXX --auto-update

--auto-update заменяет ALLOWED_CHAT_IDS в .env на ID группы обсуждений.
"""
import asyncio
import re
import sys
from pathlib import Path

# project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from pyrogram import Client
from pyrogram.enums import ChatType
from app.core.config import settings


async def find(channel_id: int, auto_update: bool) -> None:
    client = Client(
        name="bot_session",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        phone_number=settings.phone,
        workdir="data/sessions",
    )

    try:
        await client.start()
    except Exception as e:
        if "database is locked" in str(e).lower() or "locked" in str(e).lower():
            print("\nОшибка: сессия занята — останови бота (Ctrl+C) и запусти скрипт снова.")
        else:
            print(f"\nОшибка подключения: {e}")
        return

    try:
        print(f"\nПолучаю информацию о чате {channel_id} ...")
        try:
            chat = await client.get_chat(channel_id)
        except Exception as e:
            print(f"Ошибка: {e}")
            print("Убедись что бот-аккаунт является участником этого чата.")
            return

        print(f"\n{'─'*50}")
        print(f"  Название : {chat.title}")
        print(f"  Тип      : {chat.type.name}")
        print(f"  ID       : {chat.id}")

        # Для канала → ищем linked_chat (группа обсуждений)
        # Для группы → ищем linked_chat (связанный канал)
        linked = getattr(chat, "linked_chat", None)

        if chat.type == ChatType.CHANNEL:
            if linked:
                print(f"\n  Группа обсуждений:")
                print(f"    Название : {linked.title}")
                print(f"    ID       : {linked.id}")
                print(f"\n  ► Добавь в .env:")
                print(f"    ALLOWED_CHAT_IDS={linked.id}")
                if auto_update:
                    _patch_env(linked.id)
            else:
                print("\n  У канала нет связанной группы обсуждений.")
                print("  Telegram: настройки канала → Обсуждение → выбери группу.")

        elif chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            print(f"\n  Это уже группа/супергруппа — ID верный для ALLOWED_CHAT_IDS.")
            if linked:
                print(f"\n  Связанный канал (для справки):")
                print(f"    Название : {linked.title}")
                print(f"    ID       : {linked.id}")
            print(f"\n  ► Текущее значение в .env:")
            print(f"    ALLOWED_CHAT_IDS={chat.id}")
            if auto_update:
                _patch_env(chat.id)
        else:
            print(f"\n  Неожиданный тип чата: {chat.type}")

        print(f"{'─'*50}\n")
    finally:
        await client.stop()


def _patch_env(new_id: int) -> None:
    env_path = Path(".env")
    content = env_path.read_text(encoding="utf-8")
    updated = re.sub(
        r"^(ALLOWED_CHAT_IDS\s*=).*$",
        rf"\g<1>{new_id}",
        content,
        flags=re.MULTILINE,
    )
    if updated == content:
        print("\n  Предупреждение: ALLOWED_CHAT_IDS не найден в .env — добавь вручную.")
        return
    env_path.write_text(updated, encoding="utf-8")
    print(f"\n  ✓ .env обновлён: ALLOWED_CHAT_IDS={new_id}")


if __name__ == "__main__":
    auto_update = "--auto-update" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        try:
            target_id = int(args[0])
        except ValueError:
            print(f"Ошибка: ID должен быть числом, получено '{args[0]}'")
            sys.exit(1)
    elif settings.allowed_chat_ids:
        target_id = settings.allowed_chat_ids[0]
        print(f"Использую ID из .env: {target_id}")
    else:
        print("Укажи ID чата: python scripts/find_discussion_group.py -100XXXXXXXXX")
        sys.exit(1)

    asyncio.run(find(target_id, auto_update))
