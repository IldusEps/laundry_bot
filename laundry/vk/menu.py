"""Главное меню VK-бота (обычная клавиатура под полем ввода). Подписи — как в Telegram-боте."""
from __future__ import annotations

from .. import services
from ..keyboards import (BTN_ADMIN, BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_REGISTER, BTN_RULES, BTN_SHOWER,
                         BTN_STAROSTA, BTN_SUPPORT)
from . import keyboards as kb

# подпись кнопки -> команда (payload); по подписи узнаём и набранный вручную текст
MENU_COMMANDS = {
    BTN_BOOK: "menu:book",
    BTN_MY: "menu:my",
    BTN_PROFILE: "menu:profile",
    BTN_RULES: "menu:rules",
    BTN_SHOWER: "menu:shower",
    BTN_STAROSTA: "menu:starosta",
    BTN_ADMIN: "menu:admin",
    BTN_REGISTER: "menu:register",
    BTN_SUPPORT: "menu:support",
}


def _b(label: str) -> tuple[str, str]:
    return label, MENU_COMMANDS[label]


def main_menu(user: dict | None) -> str:
    rows: list[list[tuple[str, str]]] = []
    admin = services.is_admin(user)
    if services.is_registered(user):
        rows += [[_b(BTN_BOOK), _b(BTN_MY)], [_b(BTN_PROFILE), _b(BTN_RULES)], [_b(BTN_SHOWER)]]
        if user and user["role"] == "starosta":
            rows.append([_b(BTN_STAROSTA)])
        if not admin:
            rows.append([_b(BTN_SUPPORT)])
    else:
        rows += [[_b(BTN_REGISTER), _b(BTN_RULES)], [_b(BTN_SHOWER)]]
    if admin:
        rows.append([_b(BTN_ADMIN)])
    return kb.menu(rows)
