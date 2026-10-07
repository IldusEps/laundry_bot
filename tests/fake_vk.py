"""Имитация VK API для тестов (вместо vk_api.VkApi и vk_api.VkUpload).

Запоминает отправленные и отредактированные сообщения, ответы на message_event, загруженные фото и файлы.
Проверяет то же, что проверил бы VK: лимиты клавиатур, длину текста и подсказки, формат payload.
Нарушение — запись в violations (тест падает в tearDown) и AssertionError на месте.

Лимиты здесь записаны числами отдельно от laundry/vk/keyboards.py — это независимая проверка.
"""
from __future__ import annotations

import json
from typing import Any

from vk_api.exceptions import ApiError

MAX_TEXT = 4096
SNACKBAR_MAX = 90
LIMITS = {True: (6, 10), False: (10, 40)}   # inline -> (рядов, кнопок всего)
MAX_ROW, MAX_LABEL, MAX_PAYLOAD = 5, 40, 255
COLORS = {"primary", "secondary", "negative", "positive"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FakeVk:
    def __init__(self, group_id: int = 77):
        self.group_id = group_id
        self.calls: list[tuple[str, dict]] = []     # все вызовы API по порядку
        self.sent: list[dict] = []                  # messages.send
        self.edits: list[dict] = []                 # messages.edit
        self.answers: list[dict] = []               # messages.sendMessageEventAnswer
        self.photos: list[dict] = []                # загруженные картинки
        self.docs: list[dict] = []                  # загруженные файлы
        self.violations: list[str] = []
        self.blocked: set[int] = set()              # кому сообщество писать не может (ошибка 901)
        self.fail_edits = False                     # True — любое редактирование «не удаётся» (ошибка 909)
        self._dialogs: dict[int, list[dict]] = {}   # peer_id -> сообщения в текущем виде
        self._ids = 0

    # ------------------------------------------------------------------ #
    #  Интерфейс vk_api.VkApi / VkUpload
    # ------------------------------------------------------------------ #
    def method(self, name: str, values: dict | None = None, **_: Any) -> Any:
        values = dict(values or {})
        self.calls.append((name, values))
        handler = getattr(self, "_" + name.replace(".", "_"), None)
        if handler is None:
            self._violation(f"неизвестный метод API: {name}")
        return handler(values)

    def photo_messages(self, photos: Any, peer_id: int | None = None) -> list[dict]:
        data = photos.read()
        if not data.startswith(PNG_SIGNATURE):
            self._violation("в фото загружен не PNG")
        if not peer_id:
            self._violation("photo_messages без peer_id")
        self._ids += 1
        photo = {"owner_id": -self.group_id, "id": self._ids, "access_key": "key", "peer_id": peer_id,
                 "size": len(data)}
        self.photos.append(photo)
        return [photo]

    def document_message(self, doc: Any, title: str | None = None, tags: Any = None,
                         peer_id: int | None = None) -> dict:
        self._ids += 1
        item = {"owner_id": -self.group_id, "id": self._ids, "title": title, "peer_id": peer_id,
                "size": len(doc.read())}
        self.docs.append(item)
        return {"type": "doc", "doc": item}

    # ------------------------------------------------------------------ #
    #  Методы API
    # ------------------------------------------------------------------ #
    def _messages_send(self, v: dict) -> int:
        many = "peer_ids" in v                      # с peer_ids VK отвечает списком с conversation_message_id
        peer_id = int(v["peer_ids"]) if many else v.get("peer_id")
        if not peer_id or "random_id" not in v:
            self._violation("messages.send без peer_id или random_id")
        if peer_id in self.blocked:
            raise self._error("messages.send", v, 901, "Can't send messages for users without permission")
        text, attachment = v.get("message", ""), v.get("attachment")
        if not text and not attachment:
            self._violation("пустое сообщение")
        self._check_text(text)
        keyboard = self._check_keyboard(v.get("keyboard"))
        self._ids += 1
        dialog = self._dialogs.setdefault(peer_id, [])
        message = {"id": self._ids, "peer_id": peer_id, "cmid": len(dialog) + 1, "text": text,
                   "keyboard": keyboard, "attachment": attachment, "edits": 0}
        dialog.append(message)
        self.sent.append(dict(message))
        if many:
            return [{"peer_id": peer_id, "message_id": message["id"], "conversation_message_id": message["cmid"]}]
        return message["id"]

    def _messages_edit(self, v: dict) -> int:
        peer_id, cmid = v.get("peer_id"), v.get("conversation_message_id")
        message = next((m for m in self._dialogs.get(peer_id, []) if m["cmid"] == cmid), None)
        if message is None or self.fail_edits:
            raise self._error("messages.edit", v, 909, "Can't edit this message, because it's too old")
        text, attachment = v.get("message", ""), v.get("attachment")
        if not text and not attachment:
            self._violation("редактирование в пустое сообщение")
        self._check_text(text)
        keyboard = self._check_keyboard(v.get("keyboard"))
        if keyboard is not None and not keyboard.get("inline"):
            self._violation("при редактировании можно менять только inline-клавиатуру")
        # как в VK: сообщение заменяется целиком — без attachment картинка исчезает
        message.update(text=text, attachment=attachment, edits=message["edits"] + 1,
                       keyboard=keyboard if keyboard and keyboard["buttons"] else None)
        self.edits.append(dict(message))
        return 1

    def _messages_sendMessageEventAnswer(self, v: dict) -> int:
        if not v.get("event_id") or not v.get("user_id") or not v.get("peer_id"):
            self._violation("sendMessageEventAnswer без event_id / user_id / peer_id")
        text = None
        if v.get("event_data"):
            data = json.loads(v["event_data"])
            if data.get("type") != "show_snackbar" or not data.get("text"):
                self._violation(f"неверный event_data: {data}")
            text = data["text"]
            if len(text) > SNACKBAR_MAX:
                self._violation(f"подсказка длиннее {SNACKBAR_MAX} символов: {text!r}")
        self.answers.append({"event_id": v["event_id"], "user_id": v["user_id"], "text": text})
        return 1

    def _groups_getById(self, v: dict) -> dict:
        return {"groups": [{"id": self.group_id, "name": "Тестовое сообщество"}]}

    def _users_get(self, v: dict) -> list[dict]:
        ids = [int(x) for x in str(v.get("user_ids", "")).split(",") if x]
        return [{"id": i, "first_name": "Имя", "last_name": f"Фамилия{i}", "screen_name": f"id{i}"} for i in ids]

    # ------------------------------------------------------------------ #
    #  Проверки
    # ------------------------------------------------------------------ #
    def _violation(self, text: str) -> None:
        self.violations.append(text)
        raise AssertionError("VK API: " + text)

    def _error(self, method: str, values: dict, code: int, msg: str) -> ApiError:
        return ApiError(self, method, values, False, {"error_code": code, "error_msg": msg})

    def _check_text(self, text: str) -> None:
        if len(text) > MAX_TEXT:
            self._violation(f"текст длиннее {MAX_TEXT} символов ({len(text)})")
        if "<b>" in text or "</" in text or "&lt;" in text or "&amp;" in text:
            self._violation(f"в текст VK попала HTML-разметка: {text[:80]!r}")

    def _check_keyboard(self, raw: str | None) -> dict | None:
        if raw is None:
            return None
        try:
            keyboard = json.loads(raw)
        except ValueError:
            self._violation(f"клавиатура — не JSON: {raw!r}")
        inline = bool(keyboard.get("inline"))
        rows = keyboard.get("buttons")
        if not isinstance(rows, list):
            self._violation("в клавиатуре нет списка buttons")
        max_rows, max_buttons = LIMITS[inline]
        total = sum(len(r) for r in rows)
        if len(rows) > max_rows or total > max_buttons:
            self._violation(f"клавиатура (inline={inline}): рядов {len(rows)}, кнопок {total} — "
                            f"лимит {max_rows} и {max_buttons}")
        for row in rows:
            if not 1 <= len(row) <= MAX_ROW:
                self._violation(f"в ряду {len(row)} кнопок (допустимо 1–{MAX_ROW})")
            for button in row:
                action = button.get("action", {})
                label, payload = action.get("label", ""), action.get("payload")
                if action.get("type") != ("callback" if inline else "text"):
                    self._violation(f"кнопка «{label}»: тип {action.get('type')!r} не подходит (inline={inline})")
                if not label or len(label) > MAX_LABEL:
                    self._violation(f"подпись кнопки пустая или длиннее {MAX_LABEL} символов: {label!r}")
                if button.get("color") not in COLORS:
                    self._violation(f"кнопка «{label}»: неизвестный цвет {button.get('color')!r}")
                if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PAYLOAD:
                    self._violation(f"кнопка «{label}»: payload не строка или длиннее {MAX_PAYLOAD} байт")
                try:
                    if not isinstance(json.loads(payload), dict):
                        raise ValueError
                except ValueError:
                    self._violation(f"кнопка «{label}»: payload — не JSON-объект: {payload!r}")
        return keyboard

    # ------------------------------------------------------------------ #
    #  Удобства для тестов
    # ------------------------------------------------------------------ #
    def dialog(self, peer_id: int) -> list[dict]:
        """Сообщения, которые сейчас видит человек (с учётом редактирований)."""
        return self._dialogs.get(peer_id, [])

    def last(self, peer_id: int) -> dict:
        dialog = self.dialog(peer_id)
        if not dialog:
            raise AssertionError(f"Бот ничего не написал пользователю {peer_id}")
        return dialog[-1]

    def texts(self, peer_id: int) -> list[str]:
        return [m["text"] for m in self.dialog(peer_id)]

    @staticmethod
    def buttons(message: dict) -> list[dict]:
        """Кнопки сообщения: [{'label', 'command', 'page', 'payload'}]."""
        out = []
        for row in (message.get("keyboard") or {}).get("buttons", []):
            for button in row:
                payload = json.loads(button["action"]["payload"])
                out.append({"label": button["action"]["label"], "command": payload.get("c"),
                            "page": payload.get("p", 0), "payload": payload})
        return out

    @classmethod
    def labels(cls, message: dict) -> list[str]:
        return [b["label"] for b in cls.buttons(message)]
