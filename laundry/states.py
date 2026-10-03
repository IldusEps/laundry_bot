"""Состояния многошаговых диалогов (регистрация, ввод дат и т.п.).

Хранятся в памяти процесса: при перезапуске бота незавершённый ввод просто начинается заново.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

_lock = threading.Lock()
_states: dict[int, "State"] = {}

# имя состояния -> функция(message, state)
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


def set_state(user_id: int, name: str, **data: Any) -> State:
    with _lock:
        state = State(name, dict(data))
        _states[user_id] = state
        return state


def get(user_id: int) -> State | None:
    with _lock:
        return _states.get(user_id)


def update(user_id: int, name: str | None = None, **data: Any) -> State | None:
    with _lock:
        state = _states.get(user_id)
        if state is None:
            return None
        if name:
            state.name = name
        state.data.update(data)
        return state


def clear(user_id: int) -> State | None:
    with _lock:
        return _states.pop(user_id, None)
