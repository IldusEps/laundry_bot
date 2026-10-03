"""Экземпляр бота telebot."""
from __future__ import annotations

import logging

import telebot

from . import config
from .logger import route_telebot_logs

route_telebot_logs()
log = logging.getLogger("telebot.errors")


class _LogExceptionHandler(telebot.ExceptionHandler):
    """Не даёт исключению в обработчике уронить polling, а пишет его в лог."""

    def handle(self, exception):  # noqa: D401
        log.error("Исключение в обработчике telebot: %s", exception, exc_info=exception)
        return True


bot = telebot.TeleBot(
    config.BOT_TOKEN,
    parse_mode="HTML",
    num_threads=4,
    exception_handler=_LogExceptionHandler(),
)
