"""Обёртка над VK API для сообщества: отправка, редактирование, ответы на callback-кнопки, загрузка файлов.

Ошибки VK (нет разрешения на сообщения, сеть) не роняют бота: пишутся в лог, метод возвращает None/False.
Токен в лог не попадает.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import random
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import requests
import vk_api
from PIL import Image
from vk_api.exceptions import ApiError, VkApiError

from .. import config

log = logging.getLogger(__name__)

MAX_TEXT = 4096          # messages.edit — до 4096 символов (send допускает больше, режем одинаково)
SNACKBAR_MAX = 90        # подсказка после нажатия callback-кнопки — короткая строка
RPS = 15                 # лимит сообщества ~20 запросов/с; токен общий у Telegram- и VK-процесса
UPLOAD_TRIES = 2         # серверы загрузки VK иногда отвечают ошибкой — повторяем
UPLOAD_PAUSE = 1.0       # секунд между попытками
UPLOAD_TIMEOUT = 30
SLOW_UPLOAD = 3.0        # секунд: дольше — пишем в лог, чтобы было видно, где бот тормозит
PHOTO_CACHE = 256        # сколько загруженных картинок помним, чтобы не загружать ту же самую повторно
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


def _to_jpeg(image: bytes) -> bytes | None:
    try:
        buf = io.BytesIO()
        Image.open(io.BytesIO(image)).convert("RGB").save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    except (OSError, ValueError):
        return None


class UploadError(Exception):
    """Сервер загрузки VK не принял файл."""


class Uploader:
    """Загрузка файлов в личные сообщения: адрес сервера -> отправка файла -> сохранение.

    Своя вместо vk_api.VkUpload: тот шлёт файл в поле «file0» и без User-Agent, а сервер загрузки
    ждёт поле «photo» / «file» — иначе отвечает не JSON или «photo is undefined».
    """

    def __init__(self, session: Any, http: Any = None):
        self.session = session
        self.http = http or requests.Session()

    def _post(self, url: str, field: str, buf: io.BytesIO) -> dict:
        buf.seek(0)
        response = self.http.post(url, files={field: (buf.name, buf)}, timeout=UPLOAD_TIMEOUT)
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or data.get("error") or data.get(field) in (None, "", "[]"):
            raise UploadError(f"сервер загрузки ответил HTTP {response.status_code}: {response.text[:200]!r}")
        return data

    def _upload(self, server_method: str, params: dict, field: str, buf: io.BytesIO) -> dict:
        for attempt in range(1, UPLOAD_TRIES + 1):
            try:
                url = self.session.method(server_method, params)["upload_url"]
                return self._post(url, field, buf)
            except (UploadError, requests.RequestException) as exc:
                if attempt == UPLOAD_TRIES:
                    raise
                log.debug("Загрузка файла в VK, попытка %s: %s", attempt, exc)
                time.sleep(UPLOAD_PAUSE)
        raise UploadError("не осталось попыток")  # недостижимо: цикл либо возвращает, либо бросает

    def photo_messages(self, photo: io.BytesIO, peer_id: int) -> list[dict]:
        data = self._upload("photos.getMessagesUploadServer", {"peer_id": peer_id}, "photo", photo)
        return self.session.method("photos.saveMessagesPhoto",
                                   {"server": data["server"], "photo": data["photo"], "hash": data["hash"]})

    def document_message(self, doc: io.BytesIO, title: str, peer_id: int) -> Any:
        data = self._upload("docs.getMessagesUploadServer", {"type": "doc", "peer_id": peer_id}, "file", doc)
        return self.session.method("docs.save", {"file": data["file"], "title": title})


class VkClient:
    """session — объект с методом .method(name, values): vk_api.VkApi или имитация в тестах.
    uploader — объект с .photo_messages(photos, peer_id) и .document_message(doc, title, peer_id)."""

    def __init__(self, session: Any, uploader: Any = None):
        self.session = session
        self.uploader = uploader
        self._photos: OrderedDict[str, str] = OrderedDict()   # sha1 картинки -> attachment
        self._photos_lock = threading.Lock()

    @classmethod
    def from_token(cls, token: str) -> "VkClient":
        session = vk_api.VkApiGroup(token=token, api_version=config.VK_API_VERSION)
        session.RPS_DELAY = 1 / RPS
        return cls(session, Uploader(session))

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

    def send_placeholder(self, peer_id: int, text: str) -> int | None:
        """Короткое сообщение «подождите», которое затем заменяется через edit.

        Возвращает conversation_message_id (его отдаёт только вариант messages.send с peer_ids) или None."""
        try:
            result = self.call("messages.send", peer_ids=str(peer_id), random_id=_random_id(), message=text)
            return int(result[0]["conversation_message_id"])
        except (VkApiError, requests.RequestException, LookupError, TypeError, ValueError) as exc:
            log.debug("Сообщение-заглушка не отправлено или без номера (peer_id=%s): %s", peer_id, exc)
            return None

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
        """PNG -> attachment 'photo<owner>_<id>[_<access_key>]'.

        Та же самая картинка второй раз не загружается: расписание без изменений открывают часто, а загрузка —
        самая медленная часть ответа. Если сервер VK не принял PNG, пробуем ту же картинку в JPEG."""
        digest = hashlib.sha1(image).hexdigest()
        with self._photos_lock:
            cached = self._photos.get(digest)
            if cached:
                self._photos.move_to_end(digest)
                return cached
        started = time.monotonic()
        attachment = self._upload_image(peer_id, image, filename)
        if attachment is None:
            jpeg = _to_jpeg(image)
            if jpeg:
                attachment = self._upload_image(peer_id, jpeg, "schedule.jpg")
                if attachment:
                    log.info("Картинка в PNG не принята сервером VK, в JPEG — загружена")
        took = time.monotonic() - started
        if took > SLOW_UPLOAD:
            log.info("Загрузка картинки в VK заняла %.1f с (%d КБ)", took, len(image) // 1024)
        if attachment:
            with self._photos_lock:
                self._photos[digest] = attachment
                while len(self._photos) > PHOTO_CACHE:
                    self._photos.popitem(last=False)
        return attachment

    def photo_cached(self, image: bytes) -> bool:
        """Эта картинка уже загружена — отправится без задержки."""
        with self._photos_lock:
            return hashlib.sha1(image).hexdigest() in self._photos

    def _upload_image(self, peer_id: int, image: bytes, filename: str) -> str | None:
        buf = io.BytesIO(image)
        buf.name = filename
        try:
            photo = self.uploader.photo_messages(buf, peer_id=peer_id)[0]
        except (UploadError, VkApiError, requests.RequestException, KeyError, IndexError, ValueError) as exc:
            log.warning("Не удалось загрузить картинку в VK (peer_id=%s, %s, %d КБ): %s",
                        peer_id, filename, len(image) // 1024, exc)
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
        except (OSError, UploadError, VkApiError, requests.RequestException, KeyError, IndexError, TypeError) as exc:
            log.warning("Не удалось загрузить файл %s в VK: %s", path.name, exc)
            return None
        return f"doc{doc['owner_id']}_{doc['id']}"
