"""Экземпляр бота telebot."""
from __future__ import annotations

import logging
import re

import telebot

from . import config
from .logger import route_telebot_logs

from telebot import apihelper

route_telebot_logs()
log = logging.getLogger("telebot.errors")


def _mask(url: str) -> str:
    """socks5h://user:pass@host:1080 -> socks5h://user:***@host:1080 (пароль в лог не пишем)."""
    return re.sub(r"(//[^:/@]+):[^@]*@", r"\1:***@", url)


if config.PROXY_URL:
    apihelper.proxy = {"https": config.PROXY_URL}
    logging.getLogger(__name__).info("[proxy] using %s", _mask(config.PROXY_URL))
else:
    logging.getLogger(__name__).info("[proxy] not configured, going direct")


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
