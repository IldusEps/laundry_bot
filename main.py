"""Точка входа Telegram-бота: python main.py  (VK-бот — отдельный процесс: python vk_main.py)"""
from __future__ import annotations

import logging

from laundry.logger import setup_logging

setup_logging()

from laundry import config  # noqa: E402

if not config.BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN — заполните файл .env (токен от @BotFather)")

from telebot import types  # noqa: E402

from laundry import db  # noqa: E402
from laundry import schedule as sched  # noqa: E402
from laundry.loader import bot  # noqa: E402
import laundry.handlers  # noqa: E402,F401  (регистрирует обработчики)

log = logging.getLogger("main")

COMMANDS = [
    types.BotCommand("start", "Главное меню"),
    types.BotCommand("book", "Записаться на стирку"),
    types.BotCommand("my", "Мои записи"),
    types.BotCommand("profile", "Профиль"),
    types.BotCommand("rules", "Правила"),
    types.BotCommand("cancel", "Отменить текущее действие"),
    types.BotCommand("help", "Помощь"),
]


def main() -> None:
    log.info("Запуск бота. Часовой пояс: %s, этажей: %s, админов: %d",
             config.TIMEZONE, config.MAX_FLOOR, len(config.ADMIN_IDS))
    db.init_db()
    db.sync_admins(config.ADMIN_IDS, config.VK_ADMIN_IDS, sched.now())
    if not config.ADMIN_IDS:
        log.warning("ADMIN_IDS пуст — в Telegram нет администратора")
    if not config.VK_TOKEN:
        log.info("VK_TOKEN не задан — уведомления в VK отправляться не будут")
    try:
        bot.set_my_commands(COMMANDS)
    except Exception:  # не критично
        log.warning("Не удалось установить список команд", exc_info=True)
    me = bot.get_me()
    log.info("Бот @%s запущен, начинаю polling", me.username)
    try:
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
    finally:
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Остановлено пользователем (Ctrl+C)")
    except Exception:
        log.critical("Бот упал при запуске", exc_info=True)
        raise
