"""Разбор событий Bots Long Poll и вызов обработчиков.

message_new   — текст и кнопки главного меню (обычная клавиатура присылает сообщение с payload);
message_event — нажатие callback-кнопки inline-клавиатуры; на каждое обязательно отвечаем
                messages.sendMessageEventAnswer, иначе у человека крутится индикатор;
message_allow / message_deny — человек разрешил или запретил сообщения сообщества (пишем в лог).

Любая ошибка обработчика ловится здесь: трейсбек — в лог, человеку — вежливое сообщение.
"""
from __future__ import annotations

import logging

from .. import services, states
from . import handlers  # noqa: F401  (регистрирует обработчики в router)
from . import router
from .api import SNACKBAR_MAX, VkClient
from .context import Ctx
from .handlers import common
from .keyboards import parse_payload
from .menu import MENU_COMMANDS

log = logging.getLogger(__name__)

CHAT_PEER_START = 2_000_000_000  # peer_id бесед; бот работает только в личных сообщениях

START_WORDS = {"начать", "старт", "start", "/start"}
MENU_WORDS = {"меню", "menu", "/menu"}
CANCEL_WORDS = {"отмена", "отменить", "cancel", "/cancel"}
HELP_WORDS = {"помощь", "help", "/help"}
ID_WORDS = {"id", "/id", "мой id"}
# Команды Telegram-бота — чтобы привычный ввод работал и здесь
TEXT_COMMANDS = {"/book": "menu:book", "/my": "menu:my", "/profile": "menu:profile", "/rules": "menu:rules",
                 "/admin": "menu:admin"}

ERROR_TEXT = "⚠️ Произошла ошибка, попробуйте ещё раз чуть позже."
ERROR_TOAST = "⚠️ Произошла ошибка, попробуйте ещё раз."


def handle_event(client: VkClient, raw: dict) -> None:
    """raw — событие как пришло от VK: {'type': 'message_new', 'object': {...}, 'group_id': ...}."""
    kind = raw.get("type")
    obj = raw.get("object") or {}
    if kind == "message_new":
        _on_message(client, obj.get("message") or obj)
    elif kind == "message_event":
        _on_button(client, obj)
    elif kind in ("message_allow", "message_deny"):
        log.info("%s: vk_id=%s", kind, obj.get("user_id"))
    else:
        log.debug("Событие %s пропущено", kind)


def run_command(ctx: Ctx, name: str) -> router.Result:
    """Выполняет команду кнопки (bk:…, m:…, adm:…). Ошибку для пользователя возвращает как подсказку."""
    parts = name.split(":")
    handler = router.COMMANDS.get(parts[0])
    if handler is None:
        log.debug("Неизвестная команда %r от vk=%s", name, ctx.vk_id)
        return None
    try:
        return handler(ctx, parts)
    except services.ServiceError as exc:
        return str(exc), True


# --------------------------------------------------------------------------- #
#  Сообщения
# --------------------------------------------------------------------------- #
def _on_message(client: VkClient, msg: dict) -> None:
    from_id, peer_id = msg.get("from_id"), msg.get("peer_id")
    if not from_id or from_id < 0 or not peer_id or peer_id >= CHAT_PEER_START:
        return  # сообщения сообществ и беседы не обрабатываем
    text = (msg.get("text") or "").strip()
    ctx = Ctx(client, vk_id=from_id, peer_id=peer_id, text=text)
    log.debug("сообщение от vk=%s: %r", from_id, text)
    try:
        _route_message(ctx, msg, text)
    except Exception:
        log.exception("Ошибка при обработке сообщения (vk=%s)", from_id)
        try:
            ctx.send(ERROR_TEXT)
        except Exception:  # pragma: no cover
            log.exception("Не удалось сообщить об ошибке (vk=%s)", from_id)


def _route_message(ctx: Ctx, msg: dict, text: str) -> None:
    name, ctx.page = parse_payload(msg.get("payload"))
    word = text.lower()
    if name is None:
        if word in START_WORDS:
            name = "start"
        elif text in MENU_COMMANDS:  # подпись кнопки меню, набранная вручную или со старого клиента
            name = MENU_COMMANDS[text]
        elif word in TEXT_COMMANDS:
            name = TEXT_COMMANDS[word]

    # 1) «Начать» и кнопки главного меню работают всегда и сбрасывают незаконченный ввод
    if name == "start":
        common.start(ctx)
        return
    if name in router.MENU:
        states.clear(ctx.key)
        router.MENU[name](ctx)
        return
    if name is not None:  # текстовая кнопка с командой inline-клавиатуры
        hint = router.toast(run_command(ctx, name))
        if hint:
            ctx.send(hint)
        return
    if word in MENU_WORDS:
        common.show_menu(ctx)
        return
    if word in CANCEL_WORDS:
        common.cancel(ctx)
        return
    if word in HELP_WORDS:
        common.show_help(ctx)
        return
    if word in ID_WORDS:  # нужен администратору, чтобы вписать себя в VK_ADMIN_IDS
        ctx.send(f"Ваш id ВКонтакте: {ctx.vk_id}")
        return

    # 2) ввод в рамках многошагового диалога (регистрация, даты, комнаты)
    state = states.get(ctx.key)
    if state is not None:
        handler = router.STATES.get(state.name)
        if handler is None:
            states.clear(ctx.key)
        elif not text:
            ctx.send("Здесь нужен текст 🙂 Напишите ответ сообщением или «отмена».")
            return
        else:
            handler(ctx, state)
            return

    # 3) всё остальное
    if not text:
        ctx.send("Я понимаю только текст и кнопки меню 🙂")
        return
    common.other_text(ctx)


# --------------------------------------------------------------------------- #
#  Нажатия callback-кнопок
# --------------------------------------------------------------------------- #
def _on_button(client: VkClient, obj: dict) -> None:
    user_id, peer_id, event_id = obj.get("user_id"), obj.get("peer_id"), obj.get("event_id")
    if not user_id or not peer_id or not event_id:
        return
    name, page = parse_payload(obj.get("payload"))
    ctx = Ctx(client, vk_id=user_id, peer_id=peer_id, event_id=event_id,
              cmid=obj.get("conversation_message_id"), page=page)
    log.debug("кнопка от vk=%s: %s (стр. %s)", user_id, name, page)
    hint: str | None = None
    try:
        if peer_id < CHAT_PEER_START and name:
            hint = router.toast(common.start(ctx) if name == "start" else run_command(ctx, name))
    except Exception:
        log.exception("Ошибка при обработке кнопки %r (vk=%s)", name, user_id)
        hint = ERROR_TOAST
    finally:
        if hint and len(hint) > SNACKBAR_MAX:  # в подсказку не влезает — полный текст отдельным сообщением
            client.send(peer_id, hint)
            hint = hint[:SNACKBAR_MAX - 1] + "…"
        client.answer_event(event_id, user_id, peer_id, hint)
