"""Логирование на сервере: файлы с ротацией + вывод в консоль (journald при запуске через systemd).

logs/bot.log      — всё (INFO и выше по умолчанию)
logs/errors.log   — только ошибки с трейсбеками
logs/actions.log  — журнал действий: записи, отмены, баны, закрытия, роли

VK-бот — отдельный процесс, он пишет в свои файлы с префиксом: vk_bot.log, vk_errors.log, vk_actions.log
(два процесса не должны ротировать один и тот же файл).
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import config

FORMAT = "%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s"
ACTIONS_LOGGER = "actions"
LOG_FILES = ("bot.log", "errors.log", "actions.log")
VK_PREFIX = "vk_"


def _file_handler(filename: str, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        config.LOG_DIR / filename,
        maxBytes=config.LOG_MAX_MB * 1024 * 1024,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMAT))
    return handler


def setup_logging(prefix: str = "") -> None:
    """prefix — приставка к именам файлов: у VK-процесса 'vk_' (vk_bot.log и т.д.), у Telegram — пусто."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    bot_log, errors_log, actions_log = (prefix + name for name in LOG_FILES)

    root = logging.getLogger()
    actions = logging.getLogger(ACTIONS_LOGGER)
    for target in (root, actions):  # повторная настройка: прежние файлы закрываем, строки не дублируются
        for handler in target.handlers[:]:
            target.removeHandler(handler)
            handler.close()
    root.setLevel(config.LOG_LEVEL)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(console)
    root.addHandler(_file_handler(bot_log, logging.DEBUG))
    root.addHandler(_file_handler(errors_log, logging.ERROR))

    actions.setLevel(logging.INFO)
    actions.addHandler(_file_handler(actions_log, logging.INFO))  # + попадает в bot.log через root

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("vk_api").setLevel(logging.WARNING)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("uncaught").critical("Необработанное исключение", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        logging.getLogger("uncaught").critical(
            "Необработанное исключение в потоке %s", args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def route_telebot_logs() -> None:
    """telebot при импорте вешает свой обработчик на stderr — перенаправляем его логи в наши файлы."""
    tb_logger = logging.getLogger("TeleBot")
    tb_logger.handlers.clear()
    tb_logger.propagate = True
    tb_logger.setLevel(logging.WARNING)
