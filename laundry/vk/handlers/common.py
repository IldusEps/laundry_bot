"""Старт, регистрация, привязка Telegram, профиль, правила, «Написать администратору»."""
from __future__ import annotations

import logging

from ... import config, db, notify, render, services, states
from ... import schedule as sched
from ...render import esc
from .. import keyboards as kb
from ..context import Ctx
from ..router import command, menu, state

log = logging.getLogger(__name__)

CANCEL_ROW = [("✖️ Отмена", "x")]
NOT_REGISTERED = "Сначала пройдите регистрацию — напишите «начать»."


def cancel_kb() -> str:
    return kb.inline([CANCEL_ROW])


def admin_of(ctx: Ctx, user: dict | None) -> bool:
    return services.is_admin(user or {"vk_id": ctx.vk_id})


# --------------------------------------------------------------------------- #
#  Старт, меню, отмена, помощь
# --------------------------------------------------------------------------- #
def start(ctx: Ctx) -> None:
    states.clear(ctx.key)
    user = ctx.user()
    if services.is_registered(user):
        ctx.menu(f"👋 С возвращением, {esc(user['surname'])}!", user)  # type: ignore[index]
        return
    if admin_of(ctx, user):
        ctx.menu("👋 Вы вошли как <b>администратор</b>.\n\n"
                 "Если вы сами живёте в общежитии и хотите записываться на стирку — нажмите «📝 Регистрация».", user)
        return
    ctx.send("👋 Привет! Это бот записи на стирку в общежитии.\n\n"
             "Для начала короткая регистрация: фамилия и номер комнаты.")
    start_registration(ctx)


def show_menu(ctx: Ctx) -> None:
    states.clear(ctx.key)
    ctx.menu()


def cancel(ctx: Ctx) -> None:
    had_state = states.clear(ctx.key) is not None
    user = ctx.user()
    if not services.is_registered(user) and not admin_of(ctx, user):
        ctx.send("Без регистрации бот не работает — давайте закончим её.")
        start_registration(ctx)
        return
    ctx.menu("Действие отменено." if had_state else "Главное меню 👇", user)


@command("x")
def cancel_button(ctx: Ctx, parts: list[str]):
    cancel(ctx)
    return "Отменено"


def show_help(ctx: Ctx) -> None:
    ctx.send("ℹ️ <b>Команды</b>\n"
             "начать — главное меню (и регистрация)\n"
             "меню — главное меню\n"
             "отмена — отменить текущее действие\n"
             "помощь — эта справка\n"
             "id — ваш id ВКонтакте\n\n"
             "Остальное — кнопками меню.\n"
             f"В общежитии {config.MAX_FLOOR} этажей, запись идёт для этажей 2–{config.MAX_FLOOR}.")


@menu("menu:rules")
def show_rules(ctx: Ctx) -> None:
    ctx.send(render.rules_text(ctx.user()))


@menu("menu:register")
def register(ctx: Ctx) -> None:
    if services.is_registered(ctx.user()):
        profile(ctx)
        return
    start_registration(ctx)


# --------------------------------------------------------------------------- #
#  Регистрация / заявка на изменение данных
# --------------------------------------------------------------------------- #
def start_registration(ctx: Ctx, mode: str = "new") -> None:
    states.set_state(ctx.key, "reg_surname", mode=mode)
    if mode == "change":
        ctx.send("✏️ <b>Заявка на изменение данных</b>\n"
                 "Изменения вступят в силу после того, как их подтвердит староста этажа.\n\n"
                 "Введите фамилию (можно прежнюю):", cancel_kb())
        return
    hint = ("\n\n🔗 Уже записываетесь через Telegram-бота? Вместо фамилии отправьте код привязки "
            "из профиля Telegram-бота — аккаунты станут общими." if services.platform_configured("tg") else "")
    ctx.send("✏️ Введите вашу <b>фамилию</b>:" + hint, kb.EMPTY_MENU)


@state("reg_surname")
def step_surname(ctx: Ctx, st: states.State) -> None:
    if st.data.get("mode") != "change" and services.LINK_CODE_RE.fullmatch(ctx.text.strip()):
        link_by_code(ctx)
        return
    surname = services.normalize_surname(ctx.text)
    if surname is None:
        ctx.send("Фамилия — только буквы (можно через дефис), от 2 до 40 символов. Попробуйте ещё раз:")
        return
    states.update(ctx.key, "reg_room", surname=surname)
    ctx.send("🚪 Введите <b>номер комнаты</b> — 3 цифры, например 312\n"
             "(первая цифра — этаж, две следующие — номер комнаты на этаже; если есть буква — допишите её: 323а):")


@state("reg_room")
def step_room(ctx: Ctx, st: states.State) -> None:
    try:
        room, floor, wing = sched.parse_room(ctx.text)
    except sched.RoomError as exc:
        ctx.send(f"❗ {esc(exc)}\nВведите номер комнаты ещё раз:")
        return

    existing = ctx.user()
    if st.data.get("mode") == "change" and services.is_registered(existing):
        states.clear(ctx.key)
        try:
            req = services.request_change(existing, st.data["surname"], room, floor, wing)  # type: ignore[arg-type]
        except services.ServiceError as exc:
            ctx.menu(esc(exc), existing)
            return
        notify.change_request(req)
        ctx.menu(render.change_sent_text(req), existing)
        return

    try:
        result = services.save_registration(existing, ctx.vk_id, None, st.data["surname"], room, floor, wing,
                                            platform="vk")
    except services.ServiceError as exc:
        states.clear(ctx.key)
        ctx.menu(esc(exc), existing)
        return
    states.clear(ctx.key)
    user = result.user
    place = render.place_text(room, floor, wing)
    if not (existing and existing.get("room")):
        notify.new_user(user, place)
    ctx.menu(render.registered_text(user, place), user)


def link_by_code(ctx: Ctx) -> None:
    """Код из профиля Telegram-бота: этот аккаунт VK становится частью того же пользователя."""
    try:
        user = services.link_account(ctx.text, "vk", ctx.vk_id)
    except services.ServiceError as exc:
        registering = (st := states.get(ctx.key)) is not None and st.name == "reg_surname"
        ctx.send(esc(exc) + ("\n\nИли введите фамилию, чтобы зарегистрироваться заново:" if registering else ""))
        return
    states.clear(ctx.key)
    notify.send_to_user({"telegram_id": user.get("telegram_id")}, render.link_notice("vk"))
    ctx.menu(f"✅ Аккаунт ВКонтакте привязан. {esc(user['surname'])}, к.{user['room']}.", user)


# --------------------------------------------------------------------------- #
#  Профиль
# --------------------------------------------------------------------------- #
@menu("menu:profile")
def profile(ctx: Ctx) -> None:
    user = ctx.user()
    if not services.is_registered(user):
        if admin_of(ctx, user):
            ctx.send(render.profile_text(user or {}, False, True)
                     + "\n\nЧтобы записываться на стирку, нажмите «📝 Регистрация».")
        else:
            start_registration(ctx)
        return
    assert user is not None
    pending = db.user_pending_change(user["id"])
    text = render.profile_text(user, db.get_room_ban(user["room"]) is not None, services.is_admin(user))
    if pending:
        text += "\n\n📝 <b>Заявка на изменение ждёт старосту</b>\n" + render.change_label(pending)
    rows = []
    if not pending:
        rows.append([("✏️ Изменить фамилию / комнату", "pr:edit")])
    if user["role"] != "starosta" and not user["starosta_pending"] and sched.floor_is_bookable(user["floor"]):
        rows.append([("⭐ Я староста — подать заявку", "pr:st")])
    if services.can_link(user, "vk"):
        rows.append([("🔗 Привязать Telegram", "pr:link")])
    ctx.send(text, kb.inline(rows) if rows else None)


@command("pr")
def profile_buttons(ctx: Ctx, parts: list[str]):
    user = ctx.user()
    if not services.is_registered(user):
        return NOT_REGISTERED, True
    assert user is not None
    action = parts[1] if len(parts) > 1 else ""
    if action == "edit":
        start_registration(ctx, mode="change")
        return None
    if action == "link":
        code = services.issue_link_code(user, "vk")
        ctx.send(render.link_code_text(code, "tg"))
        return None
    if action == "st":
        services.request_starosta(user)
        notify.starosta_request(ctx.user() or user)
        ctx.edit("📨 Заявка на роль старосты отправлена администратору. Я напишу, когда её рассмотрят.")
        return "Заявка отправлена"
    return None


# --------------------------------------------------------------------------- #
#  Написать администратору
# --------------------------------------------------------------------------- #
@menu("menu:support")
def start_support(ctx: Ctx) -> None:
    if not services.admins_configured():
        ctx.send("Администратор пока не настроен.")
        return
    states.set_state(ctx.key, "support_msg")
    ctx.send("✉️ Напишите сообщение администратору одним сообщением — я перешлю его, а ответ придёт сюда.",
             cancel_kb())


@command("sup")
def support_again(ctx: Ctx, parts: list[str]):
    start_support(ctx)
    return None


@state("support_msg")
def step_support(ctx: Ctx, st: states.State) -> None:
    states.clear(ctx.key)
    text = ctx.text.strip()[:3000]
    user = ctx.user()
    if user is None:
        ctx.menu(NOT_REGISTERED, user)
        return
    delivered = notify.support_message(user, text)
    services.actions.info("SUPPORT_IN от %s: %r", services.who(user), text[:200])
    ctx.menu("✅ Сообщение отправлено администратору. Ответ придёт сюда." if delivered
             else "Не удалось доставить сообщение администратору, попробуйте позже.", user)


# --------------------------------------------------------------------------- #
#  Прочий текст
# --------------------------------------------------------------------------- #
def other_text(ctx: Ctx) -> None:
    user = ctx.user()
    if services.LINK_CODE_RE.fullmatch(ctx.text.strip()):
        link_by_code(ctx)
        return
    if not services.is_registered(user) and not admin_of(ctx, user):
        start_registration(ctx)
        return
    ctx.menu("Не понял 🤔 Воспользуйтесь кнопками меню.", user)
