"""Общие помощники для обработчиков: безопасная отправка, редактирование, логирование ошибок."""
from __future__ import annotations

import functools
import io
import logging
from typing import Any, Callable

from telebot import types
from telebot.apihelper import ApiTelegramException

from .loader import bot

log = logging.getLogger(__name__)
MAX_TEXT = 4000
MAX_CAPTION = 1024


def btn(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text, callback_data=data)


def inline(*rows: list[types.InlineKeyboardButton] | types.InlineKeyboardButton,
           width: int = 1) -> types.InlineKeyboardMarkup:
    """inline([b1, b2], b3) — список = строка кнопок, одиночная кнопка = отдельная строка."""
    kb = types.InlineKeyboardMarkup(row_width=width)
    for row in rows:
        if isinstance(row, list):
            if row:
                kb.row(*row)
        else:
            kb.row(row)
    return kb


def grid_kb(buttons: list[types.InlineKeyboardButton], width: int,
            footer: list[types.InlineKeyboardButton] | None = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=width)
    if buttons:
        kb.add(*buttons)
    for b in footer or []:
        kb.row(b)
    return kb


def _description(exc: ApiTelegramException) -> str:
    return str(getattr(exc, "description", "") or exc)


def send(chat_id: int, text: str, reply_markup: Any = None, silent: bool = False) -> bool:
    """Отправка с перехватом ошибок (пользователь мог заблокировать бота).
    silent=True — сообщение придёт без звука."""
    try:
        bot.send_message(chat_id, text, reply_markup=reply_markup, disable_notification=silent or None)
        return True
    except ApiTelegramException as exc:
        log.warning("Не удалось отправить сообщение chat_id=%s: %s", chat_id, _description(exc))
        return False


def send_long(chat_id: int, text: str, reply_markup: Any = None) -> None:
    """Делит длинный текст по строкам на сообщения до 4000 символов."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_TEXT and current:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current)
    for i, chunk in enumerate(chunks):
        send(chat_id, chunk, reply_markup if i == len(chunks) - 1 else None)


def notify(telegram_id: int | None, text: str, reply_markup: Any = None, silent: bool = False) -> bool:
    if not telegram_id:
        return False
    return send(telegram_id, text, reply_markup, silent=silent)


def _png(data: bytes) -> io.BytesIO:
    buf = io.BytesIO(data)
    buf.name = "schedule.png"
    return buf


def send_photo(chat_id: int, image: bytes, caption: str, reply_markup: Any = None) -> bool:
    try:
        bot.send_photo(chat_id, _png(image), caption=caption, reply_markup=reply_markup)
        return True
    except ApiTelegramException as exc:
        log.warning("Не удалось отправить картинку chat_id=%s: %s", chat_id, _description(exc))
        return False


def _is_photo(call: types.CallbackQuery) -> bool:
    return getattr(call.message, "content_type", "text") == "photo"


def _delete(call: types.CallbackQuery) -> None:
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except ApiTelegramException as exc:
        log.debug("delete_message: %s", _description(exc))


def edit(call: types.CallbackQuery, text: str, reply_markup: Any = None, image: bytes | None = None) -> None:
    """Обновляет сообщение с кнопками. Умеет переключаться между текстом и картинкой:
    у картинки меняется подпись (до 1024 символов) или само изображение.
    Если отредактировать нельзя — старое сообщение удаляется и отправляется новое."""
    chat_id, message_id = call.message.chat.id, call.message.message_id
    try:
        if image is not None:
            if _is_photo(call) and len(text) <= MAX_CAPTION:
                media = types.InputMediaPhoto(_png(image), caption=text, parse_mode="HTML")
                bot.edit_message_media(media, chat_id, message_id, reply_markup=reply_markup)
            else:
                _delete(call)
                send_photo(chat_id, image, text, reply_markup)
            return
        if _is_photo(call):
            if len(text) <= MAX_CAPTION:
                bot.edit_message_caption(text, chat_id, message_id, reply_markup=reply_markup)
            else:
                _delete(call)
                send(chat_id, text, reply_markup)
            return
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except ApiTelegramException as exc:
        if "message is not modified" in _description(exc):
            return
        log.debug("Редактирование не удалось (%s), отправляю новое сообщение", _description(exc))
        if image is not None:
            send_photo(chat_id, image, text, reply_markup)
        else:
            send(chat_id, text, reply_markup)


def answer(call: types.CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    try:
        bot.answer_callback_query(call.id, text, show_alert=alert)
    except ApiTelegramException as exc:
        log.debug("answer_callback_query: %s", _description(exc))


def answer_result(call: types.CallbackQuery, result: Any) -> None:
    """Обработчики кнопок возвращают None, 'тост' или ('текст', True) для всплывающего окна."""
    if isinstance(result, tuple):
        answer(call, result[0], alert=bool(result[1]))
    else:
        answer(call, result)


def safe(func: Callable) -> Callable:
    """Ловит любые исключения обработчика: пишет трейсбек в лог и вежливо сообщает пользователю."""

    @functools.wraps(func)
    def wrapper(update: Any, *args: Any, **kwargs: Any) -> Any:
        user = getattr(update, "from_user", None)
        uid = getattr(user, "id", None)
        if isinstance(update, types.CallbackQuery):
            log.debug("callback от %s: %s", uid, update.data)
        else:
            log.debug("сообщение от %s: %r", uid, getattr(update, "text", None))
        try:
            return func(update, *args, **kwargs)
        except Exception:
            log.exception("Ошибка в обработчике %s (user=%s)", func.__name__, uid)
            try:
                if isinstance(update, types.CallbackQuery):
                    answer(update, "⚠️ Произошла ошибка, попробуйте ещё раз.", alert=True)
                else:
                    send(update.chat.id, "⚠️ Произошла ошибка, попробуйте ещё раз чуть позже.")
            except Exception:  # pragma: no cover
                pass
            return None

    return wrapper
