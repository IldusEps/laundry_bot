"""Текстовые сообщения. Подключается ПОСЛЕДНИМ: порядок обработчиков в telebot важен.

1) кнопки главного меню (всегда работают и сбрасывают незаконченный ввод);
2) ввод в рамках многошагового диалога (регистрация, даты, комнаты);
3) всё остальное.
"""
from __future__ import annotations

from telebot import types

from .. import db, services, states
from ..keyboards import (BTN_ADMIN, BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_REGISTER, BTN_RULES, BTN_SHOWER,
                         BTN_STAROSTA, BTN_SUPPORT, main_menu)
from ..loader import bot
from ..utils import safe, send
from . import admin, booking, common, manage

MENU = {
    BTN_BOOK: booking.cmd_book,
    BTN_MY: booking.cmd_my,
    BTN_PROFILE: common.cmd_profile,
    BTN_RULES: common.cmd_rules,
    BTN_SHOWER: common.cmd_shower,
    BTN_STAROSTA: manage.cmd_panel,
    BTN_ADMIN: admin.cmd_panel,
    BTN_REGISTER: common.cmd_register,
    BTN_SUPPORT: common.cmd_support,
}


@bot.message_handler(func=lambda m: m.text in MENU, content_types=["text"])
@safe
def on_menu_button(message: types.Message) -> None:
    states.clear(message.from_user.id)
    MENU[message.text](message)


@bot.message_handler(func=lambda m: states.get(m.from_user.id) is not None, content_types=["text"])
@safe
def on_state_input(message: types.Message) -> None:
    state = states.get(message.from_user.id)
    handler = states.HANDLERS.get(state.name) if state else None
    if handler is None:
        states.clear(message.from_user.id)
        return
    handler(message, state)


@bot.message_handler(content_types=["text"])
@safe
def on_other_text(message: types.Message) -> None:
    uid = message.from_user.id
    user = db.get_user(uid)
    if not services.is_registered(user) and not services.is_admin(user):
        common.start_registration(message.chat.id, uid)
        return
    send(message.chat.id, "Не понял 🤔 Воспользуйтесь кнопками меню.", main_menu(user, uid))


@bot.message_handler(content_types=["photo", "sticker", "voice", "video", "document", "audio",
                                    "animation", "video_note", "location", "contact"])
@safe
def on_non_text(message: types.Message) -> None:
    send(message.chat.id, "Я понимаю только текст и кнопки меню 🙂")
