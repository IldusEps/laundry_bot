"""Обёртка над VK API для сообщества: отправка, редактирование, ответы на callback-кнопки, загрузка файлов.

Ошибки VK (нет разрешения на сообщения, сеть) не роняют бота: пишутся в лог, метод возвращает None/False.
Токен в лог не попадает.
"""
from __future__ import annotations

import io
import json
import logging
import random
from pathlib import Path
from typing import Any

import requests
import vk_api
from vk_api.exceptions import ApiError, VkApiError

from .. import config

log = logging.getLogger(__name__)

MAX_TEXT = 4096          # messages.edit — до 4096 символов (send допускает больше, режем одинаково)
SNACKBAR_MAX = 90        # подсказка после нажатия callback-кнопки — короткая строка
RPS = 15                 # лимит сообщества ~20 запросов/с; токен общий у Telegram- и VK-процесса
NO_PERMISSION = {901: "пользователь не разрешил сообществу писать ему (ошибка 901)",
                 902: "запрет в настройках приватности (ошибка 902)"}


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Делит текст на части до limit символов — по строкам, а слишком длинную строку режет."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def _random_id() -> int:
    return random.getrandbits(31)


class VkClient:
    """session — объект с методом .method(name, values): vk_api.VkApi или имитация в тестах.
    uploader — объект с .photo_messages(photos, peer_id) и .document_message(doc, title, peer_id)."""

    def __init__(self, session: Any, uploader: Any = None):
        self.session = session
        self.uploader = uploader

    @classmethod
    def from_token(cls, token: str) -> "VkClient":
        session = vk_api.VkApiGroup(token=token, api_version=config.VK_API_VERSION)
        session.RPS_DELAY = 1 / RPS
        return cls(session, vk_api.VkUpload(session))

    def call(self, method: str, **params: Any) -> Any:
        return self.session.method(method, params)

    # ------------------------------------------------------------------ #
    def send(self, peer_id: int, text: str, keyboard: str | None = None, attachment: str | None = None) -> int | None:
        """Отправляет сообщение (длинное — частями; клавиатура — у последней части). Возвращает id сообщения."""
        chunks = split_text(text)
        message_id = None
        for i, chunk in enumerate(chunks):
            params: dict[str, Any] = {"peer_id": peer_id, "random_id": _random_id(), "message": chunk,
                                      "dont_parse_links": 1, "disable_mentions": 1}
            if i == 0 and attachment:
                params["attachment"] = attachment
            if i == len(chunks) - 1 and keyboard:
                params["keyboard"] = keyboard
            try:
                message_id = self.call("messages.send", **params)
            except ApiError as exc:
                reason = NO_PERMISSION.get(exc.code, str(exc))
                log.warning("Не удалось отправить сообщение VK peer_id=%s: %s", peer_id, reason)
                return None
            except (VkApiError, requests.RequestException) as exc:
                log.warning("Не удалось отправить сообщение VK peer_id=%s: %s", peer_id, exc)
                return None
        return message_id

    def edit(self, peer_id: int, cmid: int, text: str, keyboard: str | None = None,
             attachment: str | None = None) -> bool:
        """Меняет текст, вложение и клавиатуру сообщения. False — не получилось (старое, удалено, ошибка)."""
        if len(text) > MAX_TEXT:
            return False
        # Сообщение заменяется целиком: чего не передали (кнопки, картинка), того в нём больше нет
        params: dict[str, Any] = {"peer_id": peer_id, "conversation_message_id": cmid, "message": text,
                                  "dont_parse_links": 1, "disable_mentions": 1}
        if keyboard:
            params["keyboard"] = keyboard
        if attachment:
            params["attachment"] = attachment
        try:
            self.call("messages.edit", **params)
            return True
        except (VkApiError, requests.RequestException) as exc:
            log.debug("messages.edit не удался (%s), отправлю новое сообщение", exc)
            return False

    def answer_event(self, event_id: str, user_id: int, peer_id: int, text: str | None = None) -> None:
        """Ответ на нажатие callback-кнопки — обязателен, иначе у человека крутится индикатор."""
        params: dict[str, Any] = {"event_id": event_id, "user_id": user_id, "peer_id": peer_id}
        if text:
            params["event_data"] = json.dumps({"type": "show_snackbar", "text": text[:SNACKBAR_MAX]},
                                              ensure_ascii=False)
        try:
            self.call("messages.sendMessageEventAnswer", **params)
        except (VkApiError, requests.RequestException) as exc:
            log.debug("sendMessageEventAnswer: %s", exc)

    # ------------------------------------------------------------------ #
    def upload_photo(self, peer_id: int, image: bytes, filename: str = "schedule.png") -> str | None:
        """PNG -> attachment 'photo<owner>_<id>[_<access_key>]' для сообщения в этот диалог."""
        buf = io.BytesIO(image)
        buf.name = filename
        try:
            photo = self.uploader.photo_messages(buf, peer_id=peer_id)[0]
        except (VkApiError, requests.RequestException, KeyError, IndexError, ValueError) as exc:
            log.warning("Не удалось загрузить картинку в VK (peer_id=%s): %s", peer_id, exc)
            return None
        key = f"_{photo['access_key']}" if photo.get("access_key") else ""
        return f"photo{photo['owner_id']}_{photo['id']}{key}"

    def upload_document(self, peer_id: int, path: Path, title: str) -> str | None:
        """Файл -> attachment 'doc<owner>_<id>' (нужен доступ ключа к документам)."""
        try:
            with path.open("rb") as fh:
                buf = io.BytesIO(fh.read())
            buf.name = title
            result = self.uploader.document_message(buf, title=title, peer_id=peer_id)
            doc = result["doc"] if "doc" in result else result[0]
        except (OSError, VkApiError, requests.RequestException, KeyError, IndexError, TypeError) as exc:
            log.warning("Не удалось загрузить файл %s в VK: %s", path.name, exc)
            return None
        return f"doc{doc['owner_id']}_{doc['id']}"
