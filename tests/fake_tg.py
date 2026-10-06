"""Имитация Telegram Bot API для тестов.

Подменяет сетевые методы настоящего экземпляра telebot (laundry.loader.bot) и подаёт ему обновления через
bot.process_new_updates — то есть работают настоящие обработчики, фильтры и их порядок, как у жильцов.
"""
from __future__ import annotations

import html
import itertools
import re
import time
from types import SimpleNamespace
from typing import Any
from unittest import mock

from telebot import types
from telebot.apihelper import ApiTelegramException

import laundry.handlers  # noqa: F401  (регистрирует обработчики Telegram-бота, как это делает main.py)
from laundry.loader import bot

_METHODS = ("send_message", "send_photo", "send_document", "edit_message_text", "edit_message_caption",
            "edit_message_media", "delete_message", "answer_callback_query")


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []           # все отправленные сообщения (как отправлены)
        self.answers: list[dict] = []        # answer_callback_query
        self.documents: list[dict] = []
        self.blocked: set[int] = set()       # кто заблокировал бота
        self._chats: dict[int, list[dict]] = {}
        self._ids = itertools.count(1)
        self._updates = itertools.count(1)
        self._patches: list[Any] = []

    # ------------------------------------------------------------------ #
    def install(self) -> None:
        for name in _METHODS:
            patcher = mock.patch.object(bot, name, getattr(self, "_" + name))
            patcher.start()
            self._patches.append(patcher)
        patcher = mock.patch.object(bot, "threaded", False)  # обработчики выполняются сразу, в этом же потоке
        patcher.start()
        self._patches.append(patcher)

    def uninstall(self) -> None:
        for patcher in reversed(self._patches):
            patcher.stop()
        self._patches.clear()

    # ------------------------------------------------------------------ #
    #  Подменённые методы Bot API
    # ------------------------------------------------------------------ #
    def _store(self, chat_id: int, text: str, markup: Any, photo: bool, silent: Any) -> SimpleNamespace:
        if chat_id in self.blocked:
            raise ApiTelegramException("sendMessage", SimpleNamespace(status_code=403, reason="Forbidden"),
                                       {"error_code": 403, "description": "Forbidden: bot was blocked by the user"})
        message = {"message_id": next(self._ids), "chat_id": chat_id, "text": text, "markup": markup,
                   "photo": photo, "silent": bool(silent), "deleted": False}
        self._chats.setdefault(chat_id, []).append(message)
        self.sent.append(dict(message))
        return SimpleNamespace(message_id=message["message_id"])

    def _send_message(self, chat_id, text, reply_markup=None, disable_notification=None, **_):
        return self._store(chat_id, text, reply_markup, False, disable_notification)

    def _send_photo(self, chat_id, photo, caption=None, reply_markup=None, **_):
        photo.read()
        return self._store(chat_id, caption or "", reply_markup, True, None)

    def _send_document(self, chat_id, document, caption=None, **_):
        self.documents.append({"chat_id": chat_id, "caption": caption, "size": len(document.read())})

    def _find(self, chat_id: int, message_id: int) -> dict:
        for message in self._chats.get(chat_id, []):
            if message["message_id"] == message_id and not message["deleted"]:
                return message
        raise ApiTelegramException("editMessageText", SimpleNamespace(status_code=400, reason="Bad Request"),
                                   {"error_code": 400, "description": "Bad Request: message to edit not found"})

    def _edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, **_):
        self._find(chat_id, message_id).update(text=text, markup=reply_markup)

    def _edit_message_caption(self, caption, chat_id=None, message_id=None, reply_markup=None, **_):
        self._find(chat_id, message_id).update(text=caption, markup=reply_markup)

    def _edit_message_media(self, media, chat_id=None, message_id=None, reply_markup=None, **_):
        self._find(chat_id, message_id).update(text=media.caption, markup=reply_markup, photo=True)

    def _delete_message(self, chat_id, message_id, **_):
        self._find(chat_id, message_id)["deleted"] = True

    def _answer_callback_query(self, callback_query_id, text=None, show_alert=None, **_):
        self.answers.append({"id": callback_query_id, "text": text, "alert": bool(show_alert)})

    # ------------------------------------------------------------------ #
    #  Действия пользователя
    # ------------------------------------------------------------------ #
    @staticmethod
    def _user(uid: int, username: str | None) -> dict:
        user = {"id": uid, "is_bot": False, "first_name": "Тест"}
        if username:
            user["username"] = username
        return user

    def say(self, uid: int, text: str, username: str | None = None) -> None:
        """Пользователь отправил боту текст (команду, кнопку меню или ответ на вопрос)."""
        update = {"update_id": next(self._updates),
                  "message": {"message_id": next(self._ids), "date": int(time.time()),
                              "chat": {"id": uid, "type": "private"}, "from": self._user(uid, username),
                              "text": text}}
        bot.process_new_updates([types.Update.de_json(update)])

    def press(self, uid: int, label: str, username: str | None = None) -> str | None:
        """Нажатие inline-кнопки, подпись которой начинается с label, в последнем сообщении с такой кнопкой.
        Возвращает текст всплывающей подсказки."""
        for message in reversed(self.chat(uid)):
            for button in self.buttons(message):
                if button.text.startswith(label):
                    return self.press_data(uid, button.callback_data, message, username)
        raise AssertionError(f"Нет кнопки «{label}» у пользователя Telegram {uid}. "
                             f"Есть: {[b.text for m in self.chat(uid) for b in self.buttons(m)]}")

    def press_data(self, uid: int, data: str, message: dict | None = None, username: str | None = None) -> str | None:
        """Нажатие кнопки с данной callback_data (в том числе «устаревшей», которой уже нет на экране)."""
        message = message or self.last(uid)
        body: dict = {"message_id": message["message_id"], "date": int(time.time()),
                      "chat": {"id": uid, "type": "private"}}
        shown = html.unescape(re.sub(r"<[^>]+>", "", message["text"]))  # Telegram отдаёт текст уже без тегов
        if message["photo"]:
            body.update(photo=[{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}], caption=shown)
        else:
            body["text"] = shown
        query_id = str(next(self._updates))
        update = {"update_id": int(query_id),
                  "callback_query": {"id": query_id, "from": self._user(uid, username), "chat_instance": "test",
                                     "data": data, "message": body}}
        before = len(self.answers)
        bot.process_new_updates([types.Update.de_json(update)])
        answers = [a for a in self.answers[before:] if a["id"] == query_id]
        if len(answers) != 1:
            raise AssertionError(f"На нажатие {data!r} бот ответил {len(answers)} раз(а), а должен ровно один")
        return answers[0]["text"]

    # ------------------------------------------------------------------ #
    #  Что видит пользователь
    # ------------------------------------------------------------------ #
    def chat(self, uid: int) -> list[dict]:
        return [m for m in self._chats.get(uid, []) if not m["deleted"]]

    def last(self, uid: int) -> dict:
        chat = self.chat(uid)
        if not chat:
            raise AssertionError(f"Бот ничего не написал пользователю Telegram {uid}")
        return chat[-1]

    def texts(self, uid: int) -> list[str]:
        return [m["text"] for m in self.chat(uid)]

    @staticmethod
    def buttons(message: dict) -> list[types.InlineKeyboardButton]:
        markup = message.get("markup")
        if not isinstance(markup, types.InlineKeyboardMarkup):
            return []
        return [button for row in markup.keyboard for button in row]

    @classmethod
    def labels(cls, message: dict) -> list[str]:
        return [b.text for b in cls.buttons(message)]

    def menu_labels(self, uid: int) -> list[str]:
        """Подписи главного меню (последняя присланная reply-клавиатура)."""
        for message in reversed(self.chat(uid)):
            markup = message.get("markup")
            if isinstance(markup, types.ReplyKeyboardMarkup):
                return [(b["text"] if isinstance(b, dict) else b.text) for row in markup.keyboard for b in row]
        return []
