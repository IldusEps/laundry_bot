"""Панель старосты (и администратора для любого этажа).

Все кнопки имеют вид m:<этаж>:<действие>[:аргументы]; права проверяются при каждом нажатии:
староста — только свой этаж, администратор — любой.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Callable

from telebot import types

from .. import db, image, render, services, states
from .. import schedule as sched
from ..loader import bot
from ..render import esc
from ..utils import answer_result, btn, edit, grid_kb, inline, notify, safe, send, send_long

log = logging.getLogger(__name__)

STALE = ("Сессия устарела — начните заново.", True)


def _cb(floor: int, action: str, *args: object) -> str:
    return ":".join(["m", str(floor), action, *map(str, args)])


def _back(floor: int, action: str = "", text: str = "⬅️ Назад") -> types.InlineKeyboardButton:
    return btn(text, _cb(floor, action) if action else f"m:{floor}")


def _by_role(manager: dict) -> str:
    return "администратором" if services.is_admin(manager["telegram_id"]) else "старостой этажа"


# --------------------------------------------------------------------------- #
#  Главное меню панели
# --------------------------------------------------------------------------- #
def menu_view(manager: dict, floor: int) -> tuple[str, types.InlineKeyboardMarkup]:
    starostas = db.starostas(floor)
    names = ", ".join(f"{esc(u['surname'])} (к.{u['room']})" for u in starostas) or "не назначен"
    text = (f"⭐ <b>Управление этажом {floor}</b>\n"
            f"Староста: {names}\n\n"
            "Здесь можно отменять записи, закрывать запись на дни и периоды, "
            "исключать комнаты и жильцов, которые не скинулись на стирку.")
    rows: list = [
        btn("📅 Расписание и отмена записей", _cb(floor, "w")),
        [btn("🔒 Закрыть запись", _cb(floor, "cl")), btn("🔓 Закрытые периоды", _cb(floor, "cls"))],
        [btn("🚫 Исключить комнату", _cb(floor, "br")), btn("🚫 Исключить жильца", _cb(floor, "bu"))],
        btn("✅ Исключённые — вернуть", _cb(floor, "bans")),
        btn("👥 Жильцы этажа", _cb(floor, "users")),
    ]
    changes = len(db.pending_change_requests(floor))
    rows.insert(1, btn(f"📝 Заявки на изменение данных ({changes})", _cb(floor, "chq")))
    if services.is_admin(manager["telegram_id"]):
        rows.append(btn("⬅️ К выбору этажа", "adm:floors"))
    return text, inline(*rows)


def cmd_panel(message: types.Message) -> None:
    user = db.get_user(message.from_user.id)
    if not user or user["role"] != "starosta" or not services.can_manage(user, user["floor"]):
        send(message.chat.id, "Панель доступна только старостам этажей.")
        return
    send(message.chat.id, *menu_view(user, user["floor"]))


# --------------------------------------------------------------------------- #
#  Диспетчер кнопок
# --------------------------------------------------------------------------- #
Action = Callable[[types.CallbackQuery, dict, int, list[str]], object]
ACTIONS: dict[str, Action] = {}


def action(name: str):
    def decorator(func: Action) -> Action:
        ACTIONS[name] = func
        return func
    return decorator


@bot.callback_query_handler(func=lambda c: c.data == "m" or c.data.startswith("m:"))
@safe
def manage_callbacks(call: types.CallbackQuery) -> None:
    parts = call.data.split(":")
    try:
        floor = int(parts[1])
    except (IndexError, ValueError):
        answer_result(call, None)
        return
    name = parts[2] if len(parts) > 2 else ""
    manager = db.get_user(call.from_user.id)
    if not services.can_manage(manager, floor):
        answer_result(call, ("⛔ Нет прав на управление этим этажом.", True))
        return
    handler = ACTIONS.get(name)
    if handler is None:
        answer_result(call, None)
        return
    try:
        result = handler(call, manager, floor, parts[3:])  # type: ignore[arg-type]
    except services.ServiceError as exc:
        result = (str(exc), True)
    answer_result(call, result)


@action("")
def a_menu(call, manager, floor, args):
    states.clear(call.from_user.id)
    edit(call, *menu_view(manager, floor))


# --------------------------------------------------------------------------- #
#  Расписание и отмена чужих записей
# --------------------------------------------------------------------------- #
@action("w")
def a_week(call, manager, floor, args):
    grid = services.build_grid(floor, None, viewer_id=manager["id"])
    if not grid.week.days:
        edit(call, "Нет дней для отображения.", inline(_back(floor)))
        return None
    buttons = []
    for day in grid.week.days:
        n = len(grid.bookings_on(day))
        buttons.append(btn(f"{sched.fmt_day_short(day)} · записей: {n}", _cb(floor, "d", sched.date_code(day))))
    edit(call, render.week_text(grid, manager=True),
         grid_kb(buttons, 2, [btn("🔄 Обновить", _cb(floor, "w")), _back(floor)]),
         image=image.week_image(grid, render.week_title(grid)))
    return None


def _day_view(manager: dict, floor: int, day: date) -> tuple[str, types.InlineKeyboardMarkup] | None:
    grid = services.build_grid(floor, None, viewer_id=manager["id"])
    if day not in grid.week.days:
        return None
    text = render.day_text(grid, day)
    buttons = []
    for slot, b in grid.bookings_on(day):
        if sched.slot_dt(day, slot.start) > grid.at:
            buttons.append(btn(f"❌ {slot.start:%H:%M} к.{b['room']} {b['surname'] or ''}",
                               _cb(floor, "x", b["id"])))
    text += "\n\n👇 Нажмите на запись, чтобы отменить её." if buttons else "\n\nОтменять нечего."
    return text, grid_kb(buttons, 1, [_back(floor, "w", "⬅️ К неделе")])


@action("d")
def a_day(call, manager, floor, args):
    view = _day_view(manager, floor, sched.parse_date_code(args[0]))
    if view is None:
        return a_week(call, manager, floor, [])
    edit(call, *view)
    return None


@action("x")
def a_cancel_confirm(call, manager, floor, args):
    booking = db.get_booking(int(args[0]))
    if not booking or booking["floor"] != floor or booking["status"] != "active":
        return "Запись уже отменена", True
    edit(call,
         f"Отменить запись?\n\n<b>{render.booking_label_full(booking)}</b>\n"
         f"к.{booking['room']} {esc(booking['surname'])}\n\nЖилец получит уведомление.",
         inline([btn("✅ Да, отменить", _cb(floor, "xy", booking["id"])),
                 btn("⬅️ Назад", _cb(floor, "d", sched.date_code(booking["slot_date"])))]))
    return None


@action("xy")
def a_cancel_do(call, manager, floor, args):
    booking = services.cancel_by_manager(manager, floor, int(args[0]))
    if booking["user_id"] != manager["id"]:
        notify(booking["telegram_id"],
               f"❗ Ваша запись на стирку <b>{render.booking_label_full(booking)}</b> отменена {_by_role(manager)}.")
    view = _day_view(manager, floor, booking["slot_date"])
    if view:
        edit(call, "✅ Запись отменена.\n\n" + view[0], view[1])
    return "Запись отменена"


# --------------------------------------------------------------------------- #
#  Закрытие записи на дни / период / часть дня
# --------------------------------------------------------------------------- #
def _open_days(floor: int) -> list[date]:
    today = sched.now().date()
    return [d for d in sched.current_week(floor).days if d >= today]


@action("cl")
def a_close_menu(call, manager, floor, args):
    states.clear(call.from_user.id)
    edit(call,
         f"🔒 <b>Закрыть запись · этаж {floor}</b>\n\n"
         "Записи, попавшие в закрытый период, будут отменены, а жильцы получат уведомление.",
         inline(btn("📆 Весь день или несколько дней", _cb(floor, "cld")),
                btn("⏰ Часть дня (с … по …)", _cb(floor, "clp")),
                _back(floor)))
    return None


@action("cld")
def a_close_days(call, manager, floor, args):
    states.set_state(call.from_user.id, "close_days", floor=floor)
    buttons = [btn(sched.fmt_day_short(d), _cb(floor, "cd", sched.date_code(d))) for d in _open_days(floor)]
    edit(call,
         "📆 <b>Закрыть запись на весь день</b>\n\n"
         "Выберите день текущей недели или отправьте сообщением дату или период:\n"
         "<code>14.10</code> — один день\n"
         "<code>14.10-20.10</code> — с 14 по 20 октября\n\n"
         "/cancel — отмена",
         grid_kb(buttons, 3, [_back(floor, "cl")]))
    return None


@action("cd")
def a_close_day_chosen(call, manager, floor, args):
    day = sched.parse_date_code(args[0])
    _ask_reason(call.from_user.id, floor, day, day, sched.FULL_DAY_FROM, sched.FULL_DAY_TO, call=call)
    return None


@states.handler("close_days")
def st_close_days(message: types.Message, state: states.State) -> None:
    floor = state.data["floor"]
    try:
        date_from, date_to = sched.parse_period_input(message.text, sched.now().date())
    except sched.DateInputError as exc:
        send(message.chat.id, f"❗ {exc}\nПопробуйте ещё раз или /cancel")
        return
    if date_to < sched.now().date():
        send(message.chat.id, "❗ Эти даты уже прошли. Введите другие или /cancel")
        return
    _ask_reason(message.from_user.id, floor, date_from, date_to, sched.FULL_DAY_FROM, sched.FULL_DAY_TO,
                chat_id=message.chat.id)


@action("clp")
def a_close_part(call, manager, floor, args):
    states.set_state(call.from_user.id, "close_part_date", floor=floor)
    buttons = [btn(sched.fmt_day_short(d), _cb(floor, "pd", sched.date_code(d))) for d in _open_days(floor)]
    edit(call,
         "⏰ <b>Закрыть часть дня</b>\n\n"
         "Выберите день текущей недели или отправьте дату сообщением (например <code>14.10</code>).\n\n"
         "/cancel — отмена",
         grid_kb(buttons, 3, [_back(floor, "cl")]))
    return None


def _start_slots_view(floor: int, day: date) -> tuple[str, types.InlineKeyboardMarkup]:
    rules = sched.rules_for(floor)
    now = sched.now()
    buttons = [btn(f"с {s.start:%H:%M}", _cb(floor, "ps", i))
               for i, s in enumerate(rules.slots) if sched.slot_dt(day, s.end) > now]
    text = f"⏰ <b>{sched.fmt_day_full(day)}</b>\n\nС какой стирки закрыть запись?"
    if not buttons:
        text = f"На {sched.fmt_day_short(day)} все стирки уже прошли."
    return text, grid_kb(buttons, 3, [_back(floor, "cl")])


@action("pd")
def a_part_date(call, manager, floor, args):
    day = sched.parse_date_code(args[0])
    states.set_state(call.from_user.id, "close_part_start", floor=floor, day=day)
    edit(call, *_start_slots_view(floor, day))
    return None


@states.handler("close_part_date")
def st_close_part_date(message: types.Message, state: states.State) -> None:
    floor = state.data["floor"]
    try:
        day = sched.parse_date_input(message.text, sched.now().date())
    except sched.DateInputError as exc:
        send(message.chat.id, f"❗ {exc}\nПопробуйте ещё раз или /cancel")
        return
    if day < sched.now().date():
        send(message.chat.id, "❗ Эта дата уже прошла. Введите другую или /cancel")
        return
    states.set_state(message.from_user.id, "close_part_start", floor=floor, day=day)
    send(message.chat.id, *_start_slots_view(floor, day))


@action("ps")
def a_part_start(call, manager, floor, args):
    state = states.get(call.from_user.id)
    if not state or state.name != "close_part_start" or state.data.get("floor") != floor:
        return STALE
    start_idx = int(args[0])
    rules = sched.rules_for(floor)
    states.update(call.from_user.id, "close_part_end", start_idx=start_idx)
    day = state.data["day"]
    buttons = [btn(f"по {s.end:%H:%M}", _cb(floor, "pe", i))
               for i, s in enumerate(rules.slots) if i >= start_idx]
    edit(call,
         f"⏰ <b>{sched.fmt_day_full(day)}</b>\nС {rules.slots[start_idx].start:%H:%M} — по какое время закрыть?",
         grid_kb(buttons, 3, [_back(floor, "cl")]))
    return None


@action("pe")
def a_part_end(call, manager, floor, args):
    state = states.get(call.from_user.id)
    if not state or state.name != "close_part_end" or state.data.get("floor") != floor:
        return STALE
    rules = sched.rules_for(floor)
    start = rules.slots[state.data["start_idx"]]
    end = rules.slots[int(args[0])]
    day = state.data["day"]
    _ask_reason(call.from_user.id, floor, day, day, start.start, end.end, call=call)
    return None


def _ask_reason(telegram_id: int, floor: int, date_from: date, date_to: date, time_from: time, time_to: time,
                call: types.CallbackQuery | None = None, chat_id: int | None = None) -> None:
    states.set_state(telegram_id, "close_reason", floor=floor, date_from=date_from, date_to=date_to,
                     time_from=time_from, time_to=time_to, reason=None)
    text = (f"🔒 {render.closure_label({'date_from': date_from, 'date_to': date_to, 'time_from': time_from, 'time_to': time_to})}\n\n"
            "✍️ Напишите причину (её увидят жильцы) или нажмите «Без причины».")
    kb = inline([btn("Без причины", _cb(floor, "nr")), _back(floor, "cl", "✖️ Отмена")])
    if call is not None:
        edit(call, text, kb)
    else:
        send(chat_id, text, kb)  # type: ignore[arg-type]


def _confirm_view(floor: int, data: dict) -> tuple[str, types.InlineKeyboardMarkup]:
    affected = services.count_affected(floor, data["date_from"], data["date_to"], data["time_from"], data["time_to"])
    text = (f"🔒 <b>Закрыть запись · этаж {floor}</b>\n\n"
            f"Когда: {render.closure_label(data)}\n"
            f"Причина: {esc(data['reason']) if data.get('reason') else '—'}\n")
    text += (f"\n⚠️ Будет отменено записей: {affected}. Жильцы получат уведомление." if affected
             else "\nЗаписей на это время нет.")
    return text, inline([btn("✅ Закрыть запись", _cb(floor, "cly")), _back(floor, "cl", "✖️ Отмена")])


@action("nr")
def a_no_reason(call, manager, floor, args):
    state = states.get(call.from_user.id)
    if not state or state.name != "close_reason" or state.data.get("floor") != floor:
        return STALE
    states.update(call.from_user.id, "close_confirm", reason=None)
    edit(call, *_confirm_view(floor, state.data))
    return None


@states.handler("close_reason")
def st_close_reason(message: types.Message, state: states.State) -> None:
    reason = " ".join(message.text.split())[:200]
    states.update(message.from_user.id, "close_confirm", reason=reason or None)
    send(message.chat.id, *_confirm_view(state.data["floor"], state.data))


@action("cly")
def a_close_do(call, manager, floor, args):
    state = states.get(call.from_user.id)
    if not state or state.name != "close_confirm" or state.data.get("floor") != floor:
        return STALE
    d = state.data
    _, cancelled = services.close_period(manager, floor, d["date_from"], d["date_to"], d["time_from"], d["time_to"],
                                         d.get("reason"))
    states.clear(call.from_user.id)
    reason = f"\nПричина: {esc(d['reason'])}" if d.get("reason") else ""
    for b in cancelled:
        notify(b["telegram_id"],
               f"❗ Ваша запись на стирку <b>{render.booking_label_full(b)}</b> отменена: "
               f"запись на это время закрыта {_by_role(manager)}.{reason}")
    edit(call,
         f"🔒 Запись закрыта: {render.closure_label(d)}.{reason}\n"
         f"Отменено записей: {len(cancelled)}.",
         inline(btn("🔓 Закрытые периоды", _cb(floor, "cls")), _back(floor, "", "⬅️ В панель")))
    return "Готово"


@action("cls")
def a_closures(call, manager, floor, args):
    closures = db.active_closures(floor, sched.now().date())
    if not closures:
        edit(call, f"🔓 На этаже {floor} нет закрытых периодов.", inline(_back(floor)))
        return None
    lines = [f"🔒 <b>Закрытые периоды · этаж {floor}</b>", ""]
    buttons = []
    for i, c in enumerate(closures, 1):
        reason = f" — {esc(c['reason'])}" if c.get("reason") else ""
        lines.append(f"{i}. {render.closure_label(c)}{reason}")
        buttons.append(btn(f"🔓 Открыть: {render.closure_label(c)}", _cb(floor, "uo", c["id"])))
    lines.append("\nПосле открытия запись снова станет доступна (отменённые записи не восстанавливаются).")
    edit(call, "\n".join(lines), grid_kb(buttons, 1, [_back(floor)]))
    return None


@action("uo")
def a_reopen(call, manager, floor, args):
    services.reopen(manager, floor, int(args[0]))
    a_closures(call, manager, floor, [])
    return "Запись снова открыта"


# --------------------------------------------------------------------------- #
#  Исключение и возврат комнат / жильцов
# --------------------------------------------------------------------------- #
@action("br")
def a_ban_room_prompt(call, manager, floor, args):
    states.set_state(call.from_user.id, "ban_room", floor=floor)
    edit(call,
         f"🚫 <b>Исключить комнату · этаж {floor}</b>\n\n"
         "Отправьте номер комнаты, которая не скинулась на стирку. Все её жильцы не смогут записываться, "
         "а будущие записи комнаты будут отменены.\n\n/cancel — отмена",
         inline(_back(floor)))
    return None


@states.handler("ban_room")
def st_ban_room(message: types.Message, state: states.State) -> None:
    floor = state.data["floor"]
    try:
        room = sched.room_on_floor(message.text, floor)
    except sched.RoomError as exc:
        send(message.chat.id, f"❗ {exc}\nВведите номер комнаты ещё раз или /cancel")
        return
    states.clear(message.from_user.id)
    if db.get_room_ban(room):
        send(message.chat.id, f"Комната {room} уже исключена.", inline(_back(floor, "bans", "✅ Исключённые")))
        return
    residents = db.users_in_room(room)
    names = ", ".join(esc(u["surname"] or "—") for u in residents) or "в боте пока никто не зарегистрирован"
    send(message.chat.id,
         f"Исключить комнату <b>{room}</b> из записи на стирку?\nЖильцы: {names}",
         inline([btn("🚫 Исключить", _cb(floor, "bry", room)), _back(floor, "", "✖️ Отмена")]))


@action("bry")
def a_ban_room_do(call, manager, floor, args):
    room = int(args[0])
    residents, cancelled = services.ban_room(manager, floor, room)
    for u in residents:
        notify(u["telegram_id"],
               f"🚫 Комната {room} исключена {_by_role(manager)} из записи на стирку "
               "(например, если не скинулись за стирку). Ваши будущие записи отменены. "
               "Обратитесь к старосте этажа.")
    edit(call, f"🚫 Комната {room} исключена. Отменено записей: {len(cancelled)}.",
         inline(btn("✅ Исключённые", _cb(floor, "bans")), _back(floor, "", "⬅️ В панель")))
    return "Комната исключена"


@action("bu")
def a_ban_user_prompt(call, manager, floor, args):
    states.set_state(call.from_user.id, "ban_user_room", floor=floor)
    edit(call,
         f"🚫 <b>Исключить жильца · этаж {floor}</b>\n\n"
         "Отправьте номер комнаты, в которой живёт жилец, — я покажу список.\n\n/cancel — отмена",
         inline(_back(floor)))
    return None


@states.handler("ban_user_room")
def st_ban_user_room(message: types.Message, state: states.State) -> None:
    floor = state.data["floor"]
    try:
        room = sched.room_on_floor(message.text, floor)
    except sched.RoomError as exc:
        send(message.chat.id, f"❗ {exc}\nВведите номер комнаты ещё раз или /cancel")
        return
    states.clear(message.from_user.id)
    candidates = [u for u in db.users_in_room(room) if not u["is_banned"] and u["telegram_id"] != message.from_user.id]
    if not candidates:
        send(message.chat.id, f"В комнате {room} нет жильцов, которых можно исключить.",
             inline(_back(floor, "", "⬅️ В панель")))
        return
    buttons = [btn(f"{u['surname'] or '—'} (к.{u['room']})", _cb(floor, "buc", u["id"])) for u in candidates]
    send(message.chat.id, f"Кого из комнаты {room} исключить?", grid_kb(buttons, 1, [_back(floor, "", "✖️ Отмена")]))


@action("buc")
def a_ban_user_confirm(call, manager, floor, args):
    target = db.get_user_by_id(int(args[0]))
    if not target or target["floor"] != floor:
        return "Жилец не найден", True
    edit(call,
         f"Исключить <b>{esc(target['surname'])}</b> (к.{target['room']}) из записи на стирку?\n"
         "Будущие записи жильца будут отменены.",
         inline([btn("🚫 Исключить", _cb(floor, "buy", target["id"])), _back(floor, "", "✖️ Отмена")]))
    return None


@action("buy")
def a_ban_user_do(call, manager, floor, args):
    target, cancelled = services.ban_user(manager, floor, int(args[0]))
    notify(target["telegram_id"],
           f"🚫 Вы исключены {_by_role(manager)} из записи на стирку (например, если не скинулись за стирку). "
           "Ваши будущие записи отменены. Обратитесь к старосте этажа.")
    edit(call, f"🚫 {esc(target['surname'])} (к.{target['room']}) исключён(а). Отменено записей: {len(cancelled)}.",
         inline(btn("✅ Исключённые", _cb(floor, "bans")), _back(floor, "", "⬅️ В панель")))
    return "Жилец исключён"


@action("bans")
def a_bans(call, manager, floor, args):
    rooms = db.room_bans(floor)
    users = db.banned_users(floor)
    if not rooms and not users:
        edit(call, f"✅ На этаже {floor} никто не исключён.", inline(_back(floor)))
        return None
    lines = [f"🚫 <b>Исключённые · этаж {floor}</b>", ""]
    buttons = []
    if rooms:
        lines.append("Комнаты: " + ", ".join(str(r["room"]) for r in rooms))
        buttons += [btn(f"✅ Вернуть комнату {r['room']}", _cb(floor, "urr", r["room"])) for r in rooms]
    if users:
        lines.append("Жильцы: " + ", ".join(f"{esc(u['surname'])} (к.{u['room']})" for u in users))
        buttons += [btn(f"✅ Вернуть {u['surname']} (к.{u['room']})", _cb(floor, "uru", u["id"])) for u in users]
    edit(call, "\n".join(lines), grid_kb(buttons, 1, [_back(floor)]))
    return None


@action("urr")
def a_unban_room(call, manager, floor, args):
    room = int(args[0])
    residents = services.unban_room(manager, floor, room)
    for u in residents:
        if not u["is_banned"]:
            notify(u["telegram_id"], f"✅ Комнате {room} снова доступна запись на стирку.")
    a_bans(call, manager, floor, [])
    return f"Комната {room} возвращена"


@action("uru")
def a_unban_user(call, manager, floor, args):
    target = services.unban_user(manager, floor, int(args[0]))
    notify(target["telegram_id"], "✅ Вам снова доступна запись на стирку.")
    a_bans(call, manager, floor, [])
    return "Жилец возвращён"


@action("users")
def a_users(call, manager, floor, args):
    users = db.users_on_floor(floor)
    banned_rooms = {r["room"] for r in db.room_bans(floor)}
    lines = [f"👥 <b>Жильцы этажа {floor}</b> — {len(users)}", ""]
    for u in users:
        line = render.user_line(u)
        if u["room"] in banned_rooms:
            line += " (комната исключена)"
        lines.append(line)
    if not users:
        lines.append("Пока никто не зарегистрирован.")
    lines.append("\n⭐ — староста, 🚫 — исключён")
    text = "\n".join(lines)
    if len(text) <= 4000:
        edit(call, text, inline(_back(floor)))
    else:
        send_long(call.message.chat.id, text, inline(_back(floor)))
    return None


# --------------------------------------------------------------------------- #
#  Заявки жильцов на смену фамилии / комнаты
# --------------------------------------------------------------------------- #
@action("chq")
def a_change_requests(call, manager, floor, args):
    from .common import change_label  # noqa: PLC0415  (избегаем циклического импорта)
    items = [r for r in db.pending_change_requests(floor) if services.can_decide_change(manager, r)]
    if not items:
        edit(call, f"📝 Заявок на изменение данных на этаже {floor} нет.", inline(_back(floor)))
        return None
    lines = [f"📝 <b>Заявки на изменение данных · этаж {floor}</b>", ""]
    rows: list = []
    for i, r in enumerate(items, 1):
        lines.append(f"{i}. " + change_label(r).replace("\n", " → "))
        rows.append([btn(f"✅ {i}. Принять", f"chg:ok:{r['id']}"), btn(f"❌ {i}. Отклонить", f"chg:no:{r['id']}")])
    rows.append(_back(floor))
    edit(call, "\n".join(lines), inline(*rows))
    return None


@bot.callback_query_handler(func=lambda c: c.data.startswith("chg:"))
@safe
def change_callbacks(call: types.CallbackQuery) -> None:
    from .common import change_label  # noqa: PLC0415
    from ..keyboards import main_menu  # noqa: PLC0415
    _, verdict, request_id = call.data.split(":")
    manager = db.get_user(call.from_user.id)
    try:
        request, result = services.decide_change(manager, int(request_id), verdict == "ok")  # type: ignore[arg-type]
    except services.ServiceError as exc:
        answer_result(call, (str(exc), True))
        return
    by = _by_role(manager)  # type: ignore[arg-type]
    if result is None:
        notify(request["telegram_id"], "❌ Заявка на изменение данных отклонена " + by + ".\n" + change_label(request))
        status = "отклонена ❌"
    else:
        text = "✅ Изменение данных подтверждено " + by + ".\n" + change_label(request)
        if result.cancelled:
            text += f"\nКомната изменилась — ваши будущие записи ({len(result.cancelled)}) отменены."
        if result.role_reset:
            text += "\nВы переехали на другой этаж, поэтому роль старосты снята."
        notify(request["telegram_id"], text, main_menu(result.user, request["telegram_id"]))
        status = "принята ✅"
    back = (btn("⬅️ В панель", f"m:{manager['floor']}") if manager and manager["role"] == "starosta"
            and services.can_manage(manager, manager["floor"]) else btn("🛠 Админ-панель", "adm"))
    edit(call, f"📝 Заявка {status}\n" + change_label(request), inline(back))
    answer_result(call, f"Заявка {status}")
