"""Реестр обработчиков VK-бота.

command('bk') — обработчик команд вида bk:… (payload кнопок, те же строки, что callback_data в Telegram);
сигнатура handler(ctx, parts) -> результат: None, 'подсказка' или ('подсказка', True) — показывается snackbar.
state('reg_surname') — обработчик текста в многошаговом диалоге: handler(ctx, state).
menu('menu:book') — кнопка главного меню: handler(ctx).
"""
from __future__ import annotations

from typing import Any, Callable

from .. import states
from .context import Ctx

Result = Any  # None | str | tuple[str, bool]

COMMANDS: dict[str, Callable[[Ctx, list[str]], Result]] = {}
STATES: dict[str, Callable[[Ctx, states.State], None]] = {}
MENU: dict[str, Callable[[Ctx], None]] = {}


def command(*prefixes: str):
    def decorator(func):
        for prefix in prefixes:
            COMMANDS[prefix] = func
        return func
    return decorator


def state(name: str):
    def decorator(func):
        STATES[name] = func
        return func
    return decorator


def menu(*commands: str):
    def decorator(func):
        for cmd in commands:
            MENU[cmd] = func
        return func
    return decorator


def toast(result: Result) -> str | None:
    """Текст для snackbar из результата обработчика."""
    if isinstance(result, tuple):
        return str(result[0]) if result[0] else None
    return str(result) if result else None
