"""Цикл Bots Long Poll: получает события и раздаёт их в пул потоков. После сетевых ошибок переподключается."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from vk_api.bot_longpoll import VkBotLongPoll

from .api import VkClient
from .dispatcher import handle_event

log = logging.getLogger(__name__)

WORKERS = 4
LONGPOLL_WAIT = 25      # секунд ждать события в одном запросе (рекомендация VK)
RETRY_MIN, RETRY_MAX = 2, 60


def _safe_handle(client: VkClient, raw: dict) -> None:
    try:
        handle_event(client, raw)
    except Exception:  # обработчики ловят свои ошибки сами; это страховка, чтобы поток пула не умер молча
        log.exception("Необработанная ошибка события %s", raw.get("type"))


def run(client: VkClient, group_id: int, stop: threading.Event | None = None) -> None:
    """Работает, пока не установлен stop (или пока процесс не остановят)."""
    stop = stop or threading.Event()
    delay = RETRY_MIN
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="vk") as pool:
        while not stop.is_set():
            try:
                longpoll = VkBotLongPoll(client.session, group_id, wait=LONGPOLL_WAIT)
                log.info("Long Poll подключён, жду события")
                while not stop.is_set():
                    for event in longpoll.check():
                        pool.submit(_safe_handle, client, event.raw)
                    delay = RETRY_MIN
            except Exception as exc:  # сеть, таймаут, ответ VK не в JSON — переподключаемся, а не падаем
                log.warning("Long Poll: %s: %s — переподключение через %s с", type(exc).__name__, exc, delay)
                stop.wait(delay)
                delay = min(delay * 2, RETRY_MAX)
