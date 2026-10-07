"""Старт, регистрация, профиль, правила, служебные команды."""
from __future__ import annotations

import logging

from telebot import types

from .. import db, notify, render, services, shower, states
from .. import schedule as sched
from ..keyboards import main_menu, remove
from ..loader import bot
from ..render import change_label, esc
from ..utils import answer_result, btn, edit, inline, safe, send, send_photo

log = logging.getLogger(__name__)


def show_menu(chat_id: int, telegram_id: int, text: str = "Главное меню 👇") -> None:
    send(chat_id, text, main_menu(db.get_user(telegram_id), telegram_id))


def start_registration(chat_id: int, telegram_id: int, mode: str = "new") -> None:
    states.set_state(telegram_id, "reg_surname", mode=mode)
    if mode == "change":
        send(chat_id, "✏️ <b>Заявка на изменение данных</b>\n"
                      "Изменения вступят в силу после того, как их подтвердит староста этажа.\n\n"
                      "Введите фамилию (можно прежнюю):\n\n/cancel — отменить")
    else:
        hint = ("\n\n🔗 Уже записываетесь через бота ВКонтакте? Вместо фамилии отправьте код привязки "
                "из профиля VK-бота — аккаунты станут общими." if services.platform_configured("vk") else "")
        send(chat_id, "✏️ Введите вашу <b>фамилию</b>:" + hint, remove())


# --------------------------------------------------------------------------- #
#  Команды
# --------------------------------------------------------------------------- #
@bot.message_handler(commands=["start"])
@safe
def cmd_start(message: types.Message) -> None:
    uid = message.from_user.id
    states.clear(uid)
    user = db.get_user(uid)
    if services.is_registered(user):
        send(message.chat.id, f"👋 С возвращением, {esc(user['surname'])}!", main_menu(user, uid))
        return
    if services.is_admin(user):
        send(message.chat.id,
             "👋 Вы вошли как <b>администратор</b>.\n\n"
             "Если вы сами живёте в общежитии и хотите записываться на стирку — нажмите «📝 Регистрация».",
             main_menu(user, uid))
        return
    send(message.chat.id,
         "👋 Привет! Это бот записи на стирку в общежитии.\n\n"
         "Для начала короткая регистрация: фамилия и номер комнаты.")
    start_registration(message.chat.id, uid)


@bot.message_handler(commands=["cancel"])
@safe
def cmd_cancel(message: types.Message) -> None:
    uid = message.from_user.id
    had_state = states.clear(uid) is not None
    user = db.get_user(uid)
    if not services.is_registered(user) and not services.is_admin(user):
        send(message.chat.id, "Без регистрации бот не работает — давайте закончим её.")
        start_registration(message.chat.id, uid)
        return
    show_menu(message.chat.id, uid, "Действие отменено." if had_state else "Главное меню 👇")


@bot.message_handler(commands=["menu"])
@safe
def cmd_menu(message: types.Message) -> None:
    states.clear(message.from_user.id)
    show_menu(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["help"])
@safe
def cmd_help(message: types.Message) -> None:
    send(message.chat.id, render.help_text())


@bot.message_handler(commands=["rules"])
@safe
def cmd_rules_command(message: types.Message) -> None:
    cmd_rules(message)


@bot.message_handler(commands=["profile"])
@safe
def cmd_profile_command(message: types.Message) -> None:
    states.clear(message.from_user.id)
    cmd_profile(message)


# --------------------------------------------------------------------------- #
#  Кнопки меню (вызываются из handlers/messages.py)
# --------------------------------------------------------------------------- #
def cmd_rules(message: types.Message) -> None:
    send(message.chat.id, render.rules_text(db.get_user(message.from_user.id)))


def cmd_shower(message: types.Message) -> None:
    images = shower.pictures()
    if not images:
        send(message.chat.id, shower.MISSING_TEXT)
        return
    sent = [send_photo(message.chat.id, image, shower.CAPTION if i == 0 else "") for i, image in enumerate(images)]
    if not any(sent):
        send(message.chat.id, shower.FAILED_TEXT)


def cmd_register(message: types.Message) -> None:
    uid = message.from_user.id
    user = db.get_user(uid)
    if services.is_registered(user):
        cmd_profile(message)
        return
    start_registration(message.chat.id, uid)


def _profile_keyboard(user: dict, pending_change: dict | None) -> types.InlineKeyboardMarkup:
    rows = []
    if not pending_change:
        rows.append(btn("✏️ Изменить фамилию / комнату", "pr:edit"))
    if (user["role"] != "starosta" and not user["starosta_pending"]
            and sched.floor_is_bookable(user["floor"])):
        rows.append(btn("⭐ Я староста этажа — подать заявку", "pr:st"))
    if services.can_link(user, "tg"):
        rows.append(btn("🔗 Привязать ВКонтакте", "pr:link"))
    return inline(*rows)


def cmd_profile(message: types.Message) -> None:
    uid = message.from_user.id
    user = db.get_user(uid)
    if not services.is_registered(user):
        if services.is_admin(user):
            send(message.chat.id, render.profile_text(user or {}, False, True)
                 + "\n\nЧтобы записываться на стирку, нажмите «📝 Регистрация».")
        else:
            start_registration(message.chat.id, uid)
        return
    room_banned = db.get_room_ban(user["room"]) is not None
    pending = db.user_pending_change(user["id"])
    text = render.profile_text(user, room_banned, services.is_admin(user))
    if pending:
        text += "\n\n📝 <b>Заявка на изменение ждёт старосту</b>\n" + change_label(pending)
    send(message.chat.id, text, _profile_keyboard(user, pending))


# --------------------------------------------------------------------------- #
#  Шаги регистрации / заявки на изменение
# --------------------------------------------------------------------------- #
@states.handler("reg_surname")
def step_surname(message: types.Message, state: states.State) -> None:
    if state.data.get("mode") != "change" and services.LINK_CODE_RE.fullmatch((message.text or "").strip()):
        link_by_code(message)
        return
    surname = services.normalize_surname(message.text)
    if surname is None:
        send(message.chat.id, "Фамилия — только буквы (можно через дефис), от 2 до 40 символов. Попробуйте ещё раз:")
        return
    states.update(message.from_user.id, "reg_room", surname=surname)
    send(message.chat.id,
         "🚪 Введите <b>номер комнаты</b> — 3 цифры, например <code>312</code>\n"
         "(первая цифра — этаж, две следующие — номер комнаты на этаже; если есть буква — допишите её: <code>323а</code>):")


@states.handler("reg_room")
def step_room(message: types.Message, state: states.State) -> None:
    uid = message.from_user.id
    try:
        room, floor, wing = sched.parse_room(message.text)
    except sched.RoomError as exc:
        send(message.chat.id, f"❗ {exc}\nВведите номер комнаты ещё раз:")
        return

    existing = db.get_user(uid)
    if state.data.get("mode") == "change" and services.is_registered(existing):
        states.clear(uid)
        try:
            req = services.request_change(existing, state.data["surname"], room, floor, wing)  # type: ignore[arg-type]
        except services.ServiceError as exc:
            show_menu(message.chat.id, uid, str(exc))
            return
        notify.change_request(req)
        show_menu(message.chat.id, uid, render.change_sent_text(req))
        return

    try:
        result = services.save_registration(existing, uid, message.from_user.username,
                                            state.data["surname"], room, floor, wing)
    except services.ServiceError as exc:
        states.clear(uid)
        show_menu(message.chat.id, uid, str(exc))
        return
    states.clear(uid)
    user = result.user
    place = render.place_text(room, floor, wing)
    if not (existing and existing.get("room")):
        notify.new_user(user, place)
    send(message.chat.id, render.registered_text(user, place), main_menu(user, uid))


def link_by_code(message: types.Message) -> None:
    uid = message.from_user.id
    try:
        user = services.link_account(message.text, "tg", uid)
    except services.ServiceError as exc:
        send(message.chat.id, f"{exc}\n\nИли введите фамилию, чтобы зарегистрироваться заново:")
        return
    states.clear(uid)
    notify.send_to_user({"vk_id": user.get("vk_id")}, render.link_notice("tg"))
    send(message.chat.id, f"✅ Аккаунт Telegram привязан. {esc(user['surname'])}, к.{user['room']}.",
         main_menu(user, uid))


@bot.callback_query_handler(func=lambda c: c.data in ("pr:st", "pr:edit", "pr:link"))
@safe
def profile_callbacks(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    user = db.get_user(uid)
    if not services.is_registered(user):
        answer_result(call, ("Сначала пройдите регистрацию: /start", True))
        return

    if call.data == "pr:edit":
        answer_result(call, None)
        start_registration(call.message.chat.id, uid, mode="change")
        return

    if call.data == "pr:link":
        try:
            code = services.issue_link_code(user, "tg")  # type: ignore[arg-type]
        except services.ServiceError as exc:
            answer_result(call, (str(exc), True))
            return
        send(call.message.chat.id, render.link_code_text(code, "vk"))
        answer_result(call, None)
        return

    try:
        services.request_starosta(user)
    except services.ServiceError as exc:
        answer_result(call, (str(exc), True))
        return
    notify.starosta_request(db.get_user(uid) or user)
    edit(call, "📨 Заявка на роль старосты отправлена администратору. Я напишу, когда её рассмотрят.")
    answer_result(call, "Заявка отправлена")


# --------------------------------------------------------------------------- #
#  Написать администратору
# --------------------------------------------------------------------------- #
SUPPORT_PROMPT = ("✉️ Напишите сообщение администратору одним сообщением — я перешлю его, "
                  "а ответ придёт сюда.\n\n/cancel — отменить")


def start_support(chat_id: int, telegram_id: int) -> None:
    if not services.admins_configured():
        send(chat_id, "Администратор пока не настроен.")
        return
    states.set_state(telegram_id, "support_msg")
    send(chat_id, SUPPORT_PROMPT)


def cmd_support(message: types.Message) -> None:
    start_support(message.chat.id, message.from_user.id)


@states.handler("support_msg")
def step_support(message: types.Message, state: states.State) -> None:
    uid = message.from_user.id
    states.clear(uid)
    text = (message.text or "").strip()[:3000]
    user = db.get_user(uid)
    if user is None:
        show_menu(message.chat.id, uid, "Сначала пройдите регистрацию: /start")
        return
    delivered = notify.support_message(user, text)
    services.actions.info("SUPPORT_IN от %s: %r", services.who(user), text[:200])
    show_menu(message.chat.id, uid,
              "✅ Сообщение отправлено администратору. Ответ придёт сюда." if delivered
              else "Не удалось доставить сообщение администратору, попробуйте позже.")


@bot.callback_query_handler(func=lambda c: c.data == "sup:new")
@safe
def support_again(call: types.CallbackQuery) -> None:
    answer_result(call, None)
    start_support(call.message.chat.id, call.from_user.id)
