"""Главное меню (reply-клавиатура)."""
from __future__ import annotations

from telebot import types

from . import services

BTN_BOOK = "📅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_PROFILE = "👤 Профиль"
BTN_RULES = "ℹ️ Правила"
BTN_STAROSTA = "⭐ Панель старосты"
BTN_ADMIN = "🛠 Админ-панель"
BTN_REGISTER = "📝 Регистрация"
BTN_SUPPORT = "✉️ Написать администратору"


def main_menu(user: dict | None, telegram_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    admin = services.is_admin(user or {"telegram_id": telegram_id})
    if services.is_registered(user):
        kb.row(BTN_BOOK, BTN_MY)
        kb.row(BTN_PROFILE, BTN_RULES)
        if user and user["role"] == "starosta":
            kb.row(BTN_STAROSTA)
        if not admin:
            kb.row(BTN_SUPPORT)
    else:
        kb.row(BTN_REGISTER, BTN_RULES)
    if admin:
        kb.row(BTN_ADMIN)
    return kb


def remove() -> types.ReplyKeyboardRemove:
    return types.ReplyKeyboardRemove()
