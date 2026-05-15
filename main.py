"""
main.py — точка входа.

Порядок запуска:
  1. Читаем .env
  2. Настраиваем логи (терминал + файл logs/bot.log)
  3. Создаём таблицы в PostgreSQL (если ещё нет)
  4. Создаём Pyrogram клиент, регистрируем обработчики
  5. Запускаем RAG-планировщик (фоновая задача, каждый час)
  6. Запускаем клиент — он держит соединение пока не нажать Ctrl+C
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pyrogram import idle

# ── загрузка .env до любых импортов из app/ ───────────────────────────────────
load_dotenv()

from app.analytics.db import init_db
from app.bot.client import create_client
from app.core.config import settings
from app.rag.scheduler import run_scheduler, set_client


def _configure_logging():
    """
    Пишем логи одновременно в:
      - терминал (цветной, читаемый)
      - logs/bot.log (полный архив, ротация по 10 МБ, хранить 7 дней)
    """
    Path("logs").mkdir(exist_ok=True)

    logger.remove()  # убираем дефолтный loguru обработчик

    # Терминал
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # Файл
    logger.add(
        "logs/bot.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} — {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


async def main():
    _configure_logging()

    logger.info("=" * 60)
    logger.info("Telegram AI Bot — запуск")
    logger.info(f"DRY_RUN  : {settings.dry_run}")
    logger.info(f"PRIVATE  : {settings.allow_private_chats}")
    logger.info(f"MAX_CHATS: {settings.max_concurrent_chats}")
    logger.info("=" * 60)

    # Создаём таблицы в PostgreSQL (безопасно, если уже существуют — пропустит)
    if settings.database_url:
        await init_db()
    else:
        logger.warning("DATABASE_URL не задан — аналитика отключена")

    # Создаём Pyrogram клиент (сессия + регистрация обработчиков)
    client = create_client()

    # Запускаем соединение с Telegram
    # При первом запуске Pyrogram попросит ввести код из SMS/Telegram
    await client.start()
    logger.info("Telegram соединение установлено")

    # Передаём клиент в RAG-планировщик (нужен для чтения каналов)
    set_client(client)

    # Запускаем RAG-планировщик как фоновую задачу
    scheduler_task = asyncio.create_task(run_scheduler())
    logger.info("RAG-планировщик запущен (обновление каждый час)")

    # Ждём — бот работает пока не нажать Ctrl+C
    logger.info("Бот запущен и слушает сообщения. Ctrl+C для остановки.")
    await idle()

    # Остановка
    logger.info("Получен сигнал остановки...")
    scheduler_task.cancel()
    await client.stop()
    logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
