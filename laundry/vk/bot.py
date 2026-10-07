"""Цикл Bots Long Poll: получает события и раздаёт их в пул потоков. После сетевых ошибок переподключается."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from vk_api.bot_longpoll import VkBotLongPoll
from vk_api.exceptions import ApiError

from .api import VkClient
from .dispatcher import handle_event

log = logging.getLogger(__name__)

WORKERS = 4
LONGPOLL_WAIT = 25      # секунд ждать события в одном запросе (рекомендация VK)
RETRY_MIN, RETRY_MAX = 2, 60

# Ошибки VK, которые не пройдут сами: что исправить в сообществе (README, раздел «Настройка сообщества»)
SETUP_HINTS = {
    5: "ключ доступа не принят. Проверьте VK_TOKEN в .env — нужен ключ доступа сообщества, целиком.",
    15: "у ключа доступа нет права «Управление сообществом». Создайте новый ключ с правами: управление "
        "сообществом, сообщения сообщества, фотографии, документы — впишите его в VK_TOKEN и перезапустите бота.",
    100: "проверьте VK_GROUP_ID и включите Long Poll API: Управление → Дополнительно → Работа с API → Long Poll API.",
}


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
            except ApiError as exc:  # VK отказал: чаще всего дело в настройках сообщества или ключа
                delay = RETRY_MAX
                hint = SETUP_HINTS.get(exc.code)
                if hint:
                    log.error("Long Poll не запускается (ошибка VK %s): %s Повтор через %s с", exc.code, hint, delay)
                else:
                    log.warning("Long Poll: %s: %s — переподключение через %s с", type(exc).__name__, exc, delay)
                stop.wait(delay)
            except Exception as exc:  # сеть, таймаут, ответ VK не в JSON — переподключаемся, а не падаем
                log.warning("Long Poll: %s: %s — переподключение через %s с", type(exc).__name__, exc, delay)
                stop.wait(delay)
                delay = min(delay * 2, RETRY_MAX)
