"""Контекст одного события VK: кто пишет, в какой диалог, какое сообщение редактировать.

Тексты передаются в HTML (как во всём боте) и переводятся в обычный текст VK здесь.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Hashable

from .. import db, render
from .api import VkClient
from .menu import main_menu

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    client: VkClient
    vk_id: int                 # кто нажал / написал
    peer_id: int               # диалог (для личных сообщений совпадает с vk_id)
    text: str = ""             # текст сообщения (для message_new)
    event_id: str | None = None  # для нажатий callback-кнопок (message_event)
    cmid: int | None = None    # conversation_message_id сообщения с нажатой кнопкой
    page: int = 0              # страница длинного списка (из payload)

    @property
    def key(self) -> Hashable:
        """Ключ состояния диалога: id VK и Telegram не пересекаются."""
        return ("vk", self.vk_id)

    @property
    def is_callback(self) -> bool:
        return self.event_id is not None

    def user(self) -> dict | None:
        return db.get_user_by_vk(self.vk_id)

    # ------------------------------------------------------------------ #
    def _photo(self, image: bytes | None) -> str | None:
        return self.client.upload_photo(self.peer_id, image) if image is not None else None

    def send(self, html: str, keyboard: str | None = None, image: bytes | None = None) -> None:
        """Новое сообщение (длинное делится на части в клиенте)."""
        self.client.send(self.peer_id, render.plain(html), keyboard, self._photo(image))

    def edit(self, html: str, keyboard: str | None = None, image: bytes | None = None) -> None:
        """Меняет сообщение с нажатой кнопкой (текст, картинку, кнопки). Не вышло — отправляет новое."""
        text = render.plain(html)
        attachment = self._photo(image)
        if self.cmid is not None and self.client.edit(self.peer_id, self.cmid, text, keyboard, attachment):
            return
        self.client.send(self.peer_id, text, keyboard, attachment)

    def menu(self, html: str = "Главное меню 👇", user: dict | None = None) -> None:
        """Сообщение с главным меню (обычная клавиатура)."""
        self.send(html, main_menu(user if user is not None else self.user()))
