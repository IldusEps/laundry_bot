"""Состояния многошаговых диалогов (регистрация, ввод дат и т.п.).

Хранятся в памяти процесса: при перезапуске бота незавершённый ввод просто начинается заново.
Ключ — пользователь мессенджера: Telegram-бот передаёт telegram_id (int), VK-бот — кортеж ('vk', vk_id),
поэтому id из разных мессенджеров не пересекаются, даже если оба бота работают в одном процессе (тесты).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable

_lock = threading.Lock()
_states: dict[Hashable, "State"] = {}

# имя состояния -> функция(message, state) — обработчики Telegram-бота (у VK-бота свой реестр)
HANDLERS: dict[str, Callable[[Any, "State"], None]] = {}


@dataclass
class State:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


def handler(name: str):
    def decorator(func):
        HANDLERS[name] = func
        return func
    return decorator


def set_state(key: Hashable, name: str, **data: Any) -> State:
    with _lock:
        state = State(name, dict(data))
        _states[key] = state
        return state


def get(key: Hashable) -> State | None:
    with _lock:
        return _states.get(key)


def update(key: Hashable, name: str | None = None, **data: Any) -> State | None:
    with _lock:
        state = _states.get(key)
        if state is None:
            return None
        if name:
            state.name = name
        state.data.update(data)
        return state


def clear(key: Hashable) -> State | None:
    with _lock:
        return _states.pop(key, None)
