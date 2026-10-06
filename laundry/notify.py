"""Уведомления пользователям — туда, где у них есть аккаунт: Telegram, VK или оба (после привязки).

Работает из обоих процессов. Из Telegram-процесса в VK сообщения уходят через VK API по токену сообщества,
из VK-процесса в Telegram — через Bot API (без polling). Если токена платформы нет (BOT_TOKEN / VK_TOKEN
пустые), отправка туда пропускается.

Текст — HTML, как во всём боте (для VK переводится в обычный текст). Кнопки передаются абстрактно:
[[(подпись, команда), …], …] — команда та же, что callback_data в Telegram и payload в VK.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Iterable, Sequence

from . import config, db, render, services

log = logging.getLogger(__name__)

Buttons = Sequence[Sequence[tuple[str, str]]]

_lock = threading.Lock()
_vk_client: Any = None


def set_vk_client(client: Any) -> None:
    """VK-процесс передаёт сюда свой клиент (общий лимит запросов); тесты — имитацию."""
    global _vk_client
    _vk_client = client


def _vk() -> Any:
    global _vk_client
    with _lock:
        if _vk_client is None:
            from .vk.api import VkClient  # noqa: PLC0415  (vk_api нужен, только если VK настроен)
            _vk_client = VkClient.from_token(config.VK_TOKEN)
        return _vk_client


def _send_tg(user: dict, text: str, buttons: Buttons | None, silent: bool, menu: bool) -> bool:
    from . import utils  # noqa: PLC0415  (в VK-процессе Telegram-бот подгружается только при отправке)
    if menu:
        from .keyboards import main_menu  # noqa: PLC0415
        markup = main_menu(user, user["telegram_id"])
    elif buttons:
        markup = utils.inline(*[[utils.btn(label, data) for label, data in row] for row in buttons])
    else:
        markup = None
    return utils.send(user["telegram_id"], text, markup, silent=silent)


def _send_vk(user: dict, text: str, buttons: Buttons | None, menu: bool) -> bool:
    from .vk import keyboards as vk_kb  # noqa: PLC0415
    from .vk.menu import main_menu  # noqa: PLC0415
    keyboard = main_menu(user) if menu else vk_kb.inline(buttons) if buttons else None
    return _vk().send(user["vk_id"], render.plain(text), keyboard) is not None


def send_to_user(user: dict | None, text: str, buttons: Buttons | None = None, silent: bool = False,
                 menu: bool = False) -> bool:
    """Отправляет уведомление во все мессенджеры пользователя. True — дошло хотя бы в один.

    user — строка users или любая строка с telegram_id/vk_id (например, запись из db.get_booking).
    silent=True — в Telegram без звука; в VK тихих сообщений нет, такие уведомления туда не отправляются.
    menu=True — вместе с текстом прислать главное меню (оно меняется после смены роли или комнаты);
    для этого user должен быть полной строкой users.
    """
    if not user:
        return False
    delivered = False
    if user.get("telegram_id") and config.BOT_TOKEN:
        try:
            delivered |= _send_tg(user, text, buttons, silent, menu)
        except Exception:  # сеть, прокси — не мешаем основному действию
            log.warning("Уведомление в Telegram не отправлено (telegram_id=%s)", user["telegram_id"], exc_info=True)
    if user.get("vk_id") and config.VK_TOKEN and not silent:
        try:
            delivered |= _send_vk(user, text, buttons, menu)
        except Exception:
            log.warning("Уведомление в VK не отправлено (vk_id=%s)", user["vk_id"], exc_info=True)
    return delivered


def send_many(users: Iterable[dict], text: str, buttons: Buttons | None = None, silent: bool = False,
              exclude_id: int | None = None) -> int:
    """Рассылка нескольким пользователям (каждому один раз). Возвращает, скольким дошло.
    Пауза между запросами к VK — в клиенте VK API (лимит сообщества)."""
    seen: set[int] = set() if exclude_id is None else {exclude_id}
    delivered = 0
    for user in users:
        key = user.get("user_id") or user.get("id")
        if key in seen:
            continue
        seen.add(key)
        delivered += send_to_user(user, text, buttons, silent=silent)
    return delivered


# --------------------------------------------------------------------------- #
#  Кому что сообщать (одинаково для обоих ботов)
# --------------------------------------------------------------------------- #
def new_user(user: dict, place: str) -> None:
    """Тихое уведомление администраторам и старостам этажа о новом жильце (в VK не уходит — см. silent)."""
    send_many([*db.admins(), *db.starostas(user["floor"])], render.new_user_text(user, place),
              silent=True, exclude_id=user["id"])


def starosta_request(user: dict) -> None:
    """Заявка на роль старосты — администраторам, с кнопками решения."""
    send_many(db.admins(), render.starosta_request_text(user, db.starostas(user["floor"])),
              render.starosta_request_buttons(user))


def change_request(req: dict) -> None:
    """Заявка на изменение данных — старостам старого и нового этажа (или администраторам)."""
    send_many(services.change_approvers(req), render.change_request_text(req), render.change_buttons(req))


def support_message(user: dict, text: str) -> int:
    """Сообщение жильца администраторам с кнопкой «Ответить». Возвращает, скольким дошло."""
    return send_many(db.admins(), render.support_text(user, text), [[("↩️ Ответить", f"adm:rp:{user['id']}")]])
