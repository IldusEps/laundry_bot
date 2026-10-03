"""Логирование на сервере: файлы с ротацией + вывод в консоль (journald при запуске через systemd).

logs/bot.log      — всё (INFO и выше по умолчанию)
logs/errors.log   — только ошибки с трейсбеками
logs/actions.log  — журнал действий: записи, отмены, баны, закрытия, роли
"""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import config

FORMAT = "%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s"
ACTIONS_LOGGER = "actions"


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


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(console)
    root.addHandler(_file_handler("bot.log", logging.DEBUG))
    root.addHandler(_file_handler("errors.log", logging.ERROR))

    actions = logging.getLogger(ACTIONS_LOGGER)
    actions.setLevel(logging.INFO)
    actions.addHandler(_file_handler("actions.log", logging.INFO))  # + попадает в bot.log через root

    logging.getLogger("urllib3").setLevel(logging.WARNING)

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
