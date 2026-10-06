"""Панель старосты (и администратора для любого этажа).

Команды те же, что в Telegram-боте: m:<этаж>:<действие>[:аргументы]; права проверяются при каждом нажатии:
староста — только свой этаж, администратор — любой. Бизнес-логика — в services, здесь только экраны.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Callable

from ... import db, image, notify, render, services, states
from ... import schedule as sched
from ...render import esc
from .. import keyboards as kb
from ..api import MAX_TEXT
from ..context import Ctx
from ..router import command, menu, state

log = logging.getLogger(__name__)

STALE = ("Сессия устарела — начните заново.", True)
Button = tuple[str, str]


def _cb(floor: int, action: str = "", *args: object) -> str:
    return ":".join(["m", str(floor), *([action] if action else []), *map(str, args)])


def _back(floor: int, action: str = "", text: str = "⬅️ Назад") -> Button:
    return text, _cb(floor, action)


# --------------------------------------------------------------------------- #
#  Главное меню панели
# --------------------------------------------------------------------------- #
def menu_view(manager: dict, floor: int) -> tuple[str, str]:
    starostas = db.starostas(floor)
    names = ", ".join(f"{esc(u['surname'])} (к.{u['room']})" for u in starostas) or "не назначен"
    text = (f"⭐ <b>Управление этажом {floor}</b>\n"
            f"Староста: {names}\n\n"
            "Здесь можно отменять записи, закрывать запись на дни и периоды, "
            "исключать комнаты и жильцов, которые не скинулись на стирку.")
    changes = len(db.pending_change_requests(floor))
    rows: list[list[Button]] = [  # 6 рядов — предел inline-клавиатуры VK
        [("📅 Расписание и отмена записей", _cb(floor, "w"))],
        [(f"📝 Заявки на изменение данных ({changes})", _cb(floor, "chq"))],
        [("🔒 Закрыть запись", _cb(floor, "cl")), ("🔓 Закрытые периоды", _cb(floor, "cls"))],
        [("🚫 Исключить комнату", _cb(floor, "br")), ("🚫 Исключить жильца", _cb(floor, "bu"))],
        [("✅ Исключённые", _cb(floor, "bans")), ("👥 Жильцы этажа", _cb(floor, "users"))],
    ]
    if services.is_admin(manager):
        rows.append([("⬅️ К выбору этажа", "adm:floors")])
    return text, kb.inline(rows)


@menu("menu:starosta")
def cmd_panel(ctx: Ctx) -> None:
    user = ctx.user()
    if not user or user["role"] != "starosta" or not services.can_manage(user, user["floor"]):
        ctx.send("Панель доступна только старостам этажей.")
        return
    ctx.send(*menu_view(user, user["floor"]))


# --------------------------------------------------------------------------- #
#  Диспетчер кнопок
# --------------------------------------------------------------------------- #
Action = Callable[[Ctx, dict, int, list[str]], object]
ACTIONS: dict[str, Action] = {}


def action(name: str):
    def decorator(func: Action) -> Action:
        ACTIONS[name] = func
        return func
    return decorator


@command("m")
def manage_buttons(ctx: Ctx, parts: list[str]):
    try:
        floor = int(parts[1])
    except (IndexError, ValueError):
        return None
    name = parts[2] if len(parts) > 2 else ""
    manager = ctx.user()
    if not services.can_manage(manager, floor):
        return "⛔ Нет прав на управление этим этажом.", True
    handler = ACTIONS.get(name)
    if handler is None:
        return None
    return handler(ctx, manager, floor, parts[3:])  # type: ignore[arg-type]


@action("")
def a_menu(ctx, manager, floor, args):
    states.clear(ctx.key)
    ctx.edit(*menu_view(manager, floor))


# --------------------------------------------------------------------------- #
#  Расписание и отмена чужих записей
# --------------------------------------------------------------------------- #
@action("w")
def a_week(ctx, manager, floor, args):
    grid = services.build_grid(floor, None, viewer_id=manager["id"])
    if not grid.week.days:
        ctx.edit("Нет дней для отображения.", kb.inline([[_back(floor)]]))
        return None
    buttons = [(f"{sched.fmt_day_short(day)} · записей: {len(grid.bookings_on(day))}",
                _cb(floor, "d", sched.date_code(day))) for day in grid.week.days]
    ctx.edit(render.week_text(grid, manager=True),
             kb.inline_paged(buttons, 2, [[("🔄 Обновить", _cb(floor, "w")), _back(floor)]],
                             nav=_cb(floor, "w"), page=ctx.page),
             image=image.week_image(grid, render.week_title(grid)))
    return None


def _day_view(manager: dict, floor: int, day: date, page: int = 0) -> tuple[str, str] | None:
    grid = services.build_grid(floor, None, viewer_id=manager["id"])
    if day not in grid.week.days:
        return None
    text = render.day_text(grid, day)
    buttons = [(f"❌ {slot.start:%H:%M} к.{b['room']} {b['surname'] or ''}", _cb(floor, "x", b["id"]))
               for slot, b in grid.bookings_on(day) if sched.slot_dt(day, slot.start) > grid.at]
    text += "\n\n👇 Нажмите на запись, чтобы отменить её." if buttons else "\n\nОтменять нечего."
    return text, kb.inline_paged(buttons, 1, [[_back(floor, "w", "⬅️ К неделе")]],
                                 nav=_cb(floor, "d", sched.date_code(day)), page=page)


@action("d")
def a_day(ctx, manager, floor, args):
    view = _day_view(manager, floor, sched.parse_date_code(args[0]), ctx.page)
    if view is None:
        return a_week(ctx, manager, floor, [])
    ctx.edit(*view)
    return None


@action("x")
def a_cancel_confirm(ctx, manager, floor, args):
    booking = db.get_booking(int(args[0]))
    if not booking or booking["floor"] != floor or booking["status"] != "active":
        return "Запись уже отменена", True
    ctx.edit(f"Отменить запись?\n\n<b>{render.booking_label_full(booking)}</b>\n"
             f"к.{booking['room']} {esc(booking['surname'])}\n\nЖилец получит уведомление.",
             kb.inline([[("✅ Да, отменить", _cb(floor, "xy", booking["id"])),
                         ("⬅️ Назад", _cb(floor, "d", sched.date_code(booking["slot_date"])))]]))
    return None


@action("xy")
def a_cancel_do(ctx, manager, floor, args):
    booking = services.cancel_by_manager(manager, floor, int(args[0]))
    if booking["user_id"] != manager["id"]:
        notify.send_to_user(booking, render.cancelled_by_manager_text(booking, manager))
    view = _day_view(manager, floor, booking["slot_date"])
    if view:
        ctx.edit("✅ Запись отменена.\n\n" + view[0], view[1])
    return "Запись отменена"


# --------------------------------------------------------------------------- #
#  Закрытие записи на дни / период / часть дня
# --------------------------------------------------------------------------- #
def _open_days(floor: int) -> list[date]:
    today = sched.now().date()
    return [d for d in sched.current_week(floor).days if d >= today]


def _day_buttons(floor: int, action_name: str) -> list[Button]:
    return [(sched.fmt_day_short(d), _cb(floor, action_name, sched.date_code(d))) for d in _open_days(floor)]


@action("cl")
def a_close_menu(ctx, manager, floor, args):
    states.clear(ctx.key)
    ctx.edit(f"🔒 <b>Закрыть запись · этаж {floor}</b>\n\n"
             "Записи, попавшие в закрытый период, будут отменены, а жильцы получат уведомление.",
             kb.inline([[("📆 Весь день или несколько дней", _cb(floor, "cld"))],
                        [("⏰ Часть дня (с … по …)", _cb(floor, "clp"))],
                        [_back(floor)]]))
    return None


@action("cld")
def a_close_days(ctx, manager, floor, args):
    states.set_state(ctx.key, "close_days", floor=floor)
    ctx.edit("📆 <b>Закрыть запись на весь день</b>\n\n"
             "Выберите день текущей недели или отправьте сообщением дату или период:\n"
             "14.10 — один день\n"
             "14.10-20.10 — с 14 по 20 октября\n\n"
             "Передумали — кнопка «Назад» или слово «отмена».",
             kb.inline_paged(_day_buttons(floor, "cd"), 3, [[_back(floor, "cl")]],
                             nav=_cb(floor, "cld"), page=ctx.page))
    return None


@action("cd")
def a_close_day_chosen(ctx, manager, floor, args):
    day = sched.parse_date_code(args[0])
    _ask_reason(ctx, floor, day, day, sched.FULL_DAY_FROM, sched.FULL_DAY_TO)
    return None


@state("close_days")
def st_close_days(ctx: Ctx, st: states.State) -> None:
    try:
        date_from, date_to = sched.parse_period_input(ctx.text, sched.now().date())
    except sched.DateInputError as exc:
        ctx.send(f"❗ {esc(exc)}\nПопробуйте ещё раз или напишите «отмена».")
        return
    if date_to < sched.now().date():
        ctx.send("❗ Эти даты уже прошли. Введите другие или напишите «отмена».")
        return
    _ask_reason(ctx, st.data["floor"], date_from, date_to, sched.FULL_DAY_FROM, sched.FULL_DAY_TO, new=True)


@action("clp")
def a_close_part(ctx, manager, floor, args):
    states.set_state(ctx.key, "close_part_date", floor=floor)
    ctx.edit("⏰ <b>Закрыть часть дня</b>\n\n"
             "Выберите день текущей недели или отправьте дату сообщением (например 14.10).\n\n"
             "Передумали — кнопка «Назад» или слово «отмена».",
             kb.inline_paged(_day_buttons(floor, "pd"), 3, [[_back(floor, "cl")]],
                             nav=_cb(floor, "clp"), page=ctx.page))
    return None


def _start_slots_view(floor: int, day: date, page: int = 0) -> tuple[str, str]:
    rules = sched.rules_for(floor)
    now = sched.now()
    buttons = [(f"с {s.start:%H:%M}", _cb(floor, "ps", i))
               for i, s in enumerate(rules.slots) if sched.slot_dt(day, s.end) > now]
    text = f"⏰ <b>{sched.fmt_day_full(day)}</b>\n\nС какой стирки закрыть запись?"
    if not buttons:
        text = f"На {sched.fmt_day_short(day)} все стирки уже прошли."
    return text, kb.inline_paged(buttons, 3, [[_back(floor, "cl")]],
                                 nav=_cb(floor, "pd", sched.date_code(day)), page=page)


@action("pd")
def a_part_date(ctx, manager, floor, args):
    day = sched.parse_date_code(args[0])
    states.set_state(ctx.key, "close_part_start", floor=floor, day=day)
    ctx.edit(*_start_slots_view(floor, day, ctx.page))
    return None


@state("close_part_date")
def st_close_part_date(ctx: Ctx, st: states.State) -> None:
    floor = st.data["floor"]
    try:
        day = sched.parse_date_input(ctx.text, sched.now().date())
    except sched.DateInputError as exc:
        ctx.send(f"❗ {esc(exc)}\nПопробуйте ещё раз или напишите «отмена».")
        return
    if day < sched.now().date():
        ctx.send("❗ Эта дата уже прошла. Введите другую или напишите «отмена».")
        return
    states.set_state(ctx.key, "close_part_start", floor=floor, day=day)
    ctx.send(*_start_slots_view(floor, day))


@action("ps")
def a_part_start(ctx, manager, floor, args):
    st = states.get(ctx.key)
    # close_part_end — человек листает страницы списка «по какое время»
    if not st or st.name not in ("close_part_start", "close_part_end") or st.data.get("floor") != floor:
        return STALE
    start_idx = int(args[0])
    rules = sched.rules_for(floor)
    states.update(ctx.key, "close_part_end", start_idx=start_idx)
    day = st.data["day"]
    buttons = [(f"по {s.end:%H:%M}", _cb(floor, "pe", i)) for i, s in enumerate(rules.slots) if i >= start_idx]
    ctx.edit(f"⏰ <b>{sched.fmt_day_full(day)}</b>\nС {rules.slots[start_idx].start:%H:%M} — по какое время закрыть?",
             kb.inline_paged(buttons, 3, [[_back(floor, "cl")]], nav=_cb(floor, "ps", start_idx), page=ctx.page))
    return None


@action("pe")
def a_part_end(ctx, manager, floor, args):
    st = states.get(ctx.key)
    if not st or st.name != "close_part_end" or st.data.get("floor") != floor:
        return STALE
    rules = sched.rules_for(floor)
    start = rules.slots[st.data["start_idx"]]
    end = rules.slots[int(args[0])]
    day = st.data["day"]
    _ask_reason(ctx, floor, day, day, start.start, end.end)
    return None


def _ask_reason(ctx: Ctx, floor: int, date_from: date, date_to: date, time_from: time, time_to: time,
                new: bool = False) -> None:
    states.set_state(ctx.key, "close_reason", floor=floor, date_from=date_from, date_to=date_to,
                     time_from=time_from, time_to=time_to, reason=None)
    label = render.closure_label({"date_from": date_from, "date_to": date_to,
                                  "time_from": time_from, "time_to": time_to})
    text = f"🔒 {label}\n\n✍️ Напишите причину (её увидят жильцы) или нажмите «Без причины»."
    keyboard = kb.inline([[("Без причины", _cb(floor, "nr")), _back(floor, "cl", "✖️ Отмена")]])
    if new:
        ctx.send(text, keyboard)
    else:
        ctx.edit(text, keyboard)


def _confirm_view(floor: int, data: dict) -> tuple[str, str]:
    affected = services.count_affected(floor, data["date_from"], data["date_to"], data["time_from"], data["time_to"])
    text = (f"🔒 <b>Закрыть запись · этаж {floor}</b>\n\n"
            f"Когда: {render.closure_label(data)}\n"
            f"Причина: {esc(data['reason']) if data.get('reason') else '—'}\n")
    text += (f"\n⚠️ Будет отменено записей: {affected}. Жильцы получат уведомление." if affected
             else "\nЗаписей на это время нет.")
    return text, kb.inline([[("✅ Закрыть запись", _cb(floor, "cly")), _back(floor, "cl", "✖️ Отмена")]])


@action("nr")
def a_no_reason(ctx, manager, floor, args):
    st = states.get(ctx.key)
    if not st or st.name != "close_reason" or st.data.get("floor") != floor:
        return STALE
    states.update(ctx.key, "close_confirm", reason=None)
    ctx.edit(*_confirm_view(floor, st.data))
    return None


@state("close_reason")
def st_close_reason(ctx: Ctx, st: states.State) -> None:
    reason = " ".join(ctx.text.split())[:200]
    states.update(ctx.key, "close_confirm", reason=reason or None)
    ctx.send(*_confirm_view(st.data["floor"], st.data))


@action("cly")
def a_close_do(ctx, manager, floor, args):
    st = states.get(ctx.key)
    if not st or st.name != "close_confirm" or st.data.get("floor") != floor:
        return STALE
    d = st.data
    _, cancelled = services.close_period(manager, floor, d["date_from"], d["date_to"], d["time_from"], d["time_to"],
                                         d.get("reason"))
    states.clear(ctx.key)
    for b in cancelled:
        notify.send_to_user(b, render.cancelled_by_closure_text(b, manager, d.get("reason")))
    reason = f"\nПричина: {esc(d['reason'])}" if d.get("reason") else ""
    ctx.edit(f"🔒 Запись закрыта: {render.closure_label(d)}.{reason}\n"
             f"Отменено записей: {len(cancelled)}.",
             kb.inline([[("🔓 Закрытые периоды", _cb(floor, "cls"))], [_back(floor, "", "⬅️ В панель")]]))
    return "Готово"


@action("cls")
def a_closures(ctx, manager, floor, args):
    closures = db.active_closures(floor, sched.now().date())
    if not closures:
        ctx.edit(f"🔓 На этаже {floor} нет закрытых периодов.", kb.inline([[_back(floor)]]))
        return None
    lines = [f"🔒 <b>Закрытые периоды · этаж {floor}</b>", ""]
    buttons = []
    for i, c in enumerate(closures, 1):
        reason = f" — {esc(c['reason'])}" if c.get("reason") else ""
        lines.append(f"{i}. {render.closure_label(c)}{reason}")
        buttons.append((f"🔓 {i}. {render.closure_label(c)}", _cb(floor, "uo", c["id"])))
    lines.append("\nНажмите на период, чтобы снова открыть запись (отменённые записи не восстанавливаются).")
    ctx.edit("\n".join(lines), kb.inline_paged(buttons, 1, [[_back(floor)]], nav=_cb(floor, "cls"), page=ctx.page))
    return None


@action("uo")
def a_reopen(ctx, manager, floor, args):
    services.reopen(manager, floor, int(args[0]))
    a_closures(ctx, manager, floor, [])
    return "Запись снова открыта"


# --------------------------------------------------------------------------- #
#  Исключение и возврат комнат / жильцов
# --------------------------------------------------------------------------- #
@action("br")
def a_ban_room_prompt(ctx, manager, floor, args):
    states.set_state(ctx.key, "ban_room", floor=floor)
    ctx.edit(f"🚫 <b>Исключить комнату · этаж {floor}</b>\n\n"
             "Отправьте номер комнаты, которая не скинулась на стирку. Все её жильцы не смогут записываться, "
             "а будущие записи комнаты будут отменены.",
             kb.inline([[_back(floor, "", "✖️ Отмена")]]))
    return None


@state("ban_room")
def st_ban_room(ctx: Ctx, st: states.State) -> None:
    floor = st.data["floor"]
    try:
        room = sched.room_on_floor(ctx.text, floor)
    except sched.RoomError as exc:
        ctx.send(f"❗ {esc(exc)}\nВведите номер комнаты ещё раз или напишите «отмена».")
        return
    states.clear(ctx.key)
    if db.get_room_ban(room):
        ctx.send(f"Комната {room} уже исключена.", kb.inline([[_back(floor, "bans", "✅ Исключённые")]]))
        return
    residents = db.users_in_room(room)
    names = ", ".join(esc(u["surname"] or "—") for u in residents) or "в боте пока никто не зарегистрирован"
    ctx.send(f"Исключить комнату <b>{room}</b> из записи на стирку?\nЖильцы: {names}",
             kb.inline([[("🚫 Исключить", _cb(floor, "bry", room)), _back(floor, "", "✖️ Отмена")]]))


@action("bry")
def a_ban_room_do(ctx, manager, floor, args):
    room = args[0]
    residents, cancelled = services.ban_room(manager, floor, room)
    for u in residents:
        notify.send_to_user(u, render.room_banned_text(room, manager))
    ctx.edit(f"🚫 Комната {room} исключена. Отменено записей: {len(cancelled)}.",
             kb.inline([[("✅ Исключённые", _cb(floor, "bans"))], [_back(floor, "", "⬅️ В панель")]]))
    return "Комната исключена"


@action("bu")
def a_ban_user_prompt(ctx, manager, floor, args):
    states.set_state(ctx.key, "ban_user_room", floor=floor)
    ctx.edit(f"🚫 <b>Исключить жильца · этаж {floor}</b>\n\n"
             "Отправьте номер комнаты, в которой живёт жилец, — я покажу список.",
             kb.inline([[_back(floor, "", "✖️ Отмена")]]))
    return None


def _ban_candidates_view(manager: dict, floor: int, room: str, page: int = 0) -> tuple[str, str]:
    candidates = [u for u in db.users_in_room(room) if not u["is_banned"] and u["id"] != manager["id"]]
    if not candidates:
        return (f"В комнате {room} нет жильцов, которых можно исключить.",
                kb.inline([[_back(floor, "", "⬅️ В панель")]]))
    buttons = [(f"{u['surname'] or '—'} (к.{u['room']})", _cb(floor, "buc", u["id"])) for u in candidates]
    return (f"Кого из комнаты {room} исключить?",
            kb.inline_paged(buttons, 1, [[_back(floor, "", "✖️ Отмена")]], nav=_cb(floor, "bul", room), page=page))


@state("ban_user_room")
def st_ban_user_room(ctx: Ctx, st: states.State) -> None:
    floor = st.data["floor"]
    try:
        room = sched.room_on_floor(ctx.text, floor)
    except sched.RoomError as exc:
        ctx.send(f"❗ {esc(exc)}\nВведите номер комнаты ещё раз или напишите «отмена».")
        return
    states.clear(ctx.key)
    manager = ctx.user()
    if not services.can_manage(manager, floor):
        ctx.send("⛔ Нет прав на управление этим этажом.")
        return
    ctx.send(*_ban_candidates_view(manager, floor, room))  # type: ignore[arg-type]


@action("bul")
def a_ban_user_list(ctx, manager, floor, args):
    """Листание списка жильцов комнаты (страницы ◀ / ▶)."""
    ctx.edit(*_ban_candidates_view(manager, floor, sched.room_on_floor(args[0], floor), ctx.page))
    return None


@action("buc")
def a_ban_user_confirm(ctx, manager, floor, args):
    target = db.get_user_by_id(int(args[0]))
    if not target or target["floor"] != floor:
        return "Жилец не найден", True
    ctx.edit(f"Исключить <b>{esc(target['surname'])}</b> (к.{target['room']}) из записи на стирку?\n"
             "Будущие записи жильца будут отменены.",
             kb.inline([[("🚫 Исключить", _cb(floor, "buy", target["id"])), _back(floor, "", "✖️ Отмена")]]))
    return None


@action("buy")
def a_ban_user_do(ctx, manager, floor, args):
    target, cancelled = services.ban_user(manager, floor, int(args[0]))
    notify.send_to_user(target, render.user_banned_text(manager))
    ctx.edit(f"🚫 {esc(target['surname'])} (к.{target['room']}) исключён(а). Отменено записей: {len(cancelled)}.",
             kb.inline([[("✅ Исключённые", _cb(floor, "bans"))], [_back(floor, "", "⬅️ В панель")]]))
    return "Жилец исключён"


@action("bans")
def a_bans(ctx, manager, floor, args):
    rooms = db.room_bans(floor)
    users = db.banned_users(floor)
    if not rooms and not users:
        ctx.edit(f"✅ На этаже {floor} никто не исключён.", kb.inline([[_back(floor)]]))
        return None
    lines = [f"🚫 <b>Исключённые · этаж {floor}</b>", ""]
    buttons = []
    if rooms:
        lines.append("Комнаты: " + ", ".join(str(r["room"]) for r in rooms))
        buttons += [(f"✅ Вернуть комнату {r['room']}", _cb(floor, "urr", r["room"])) for r in rooms]
    if users:
        lines.append("Жильцы: " + ", ".join(f"{esc(u['surname'])} (к.{u['room']})" for u in users))
        buttons += [(f"✅ Вернуть {u['surname']} (к.{u['room']})", _cb(floor, "uru", u["id"])) for u in users]
    ctx.edit("\n".join(lines), kb.inline_paged(buttons, 1, [[_back(floor)]], nav=_cb(floor, "bans"), page=ctx.page))
    return None


@action("urr")
def a_unban_room(ctx, manager, floor, args):
    room = args[0]
    residents = services.unban_room(manager, floor, room)
    for u in residents:
        if not u["is_banned"]:
            notify.send_to_user(u, render.room_unbanned_text(room))
    a_bans(ctx, manager, floor, [])
    return f"Комната {room} возвращена"


@action("uru")
def a_unban_user(ctx, manager, floor, args):
    target = services.unban_user(manager, floor, int(args[0]))
    notify.send_to_user(target, render.USER_UNBANNED_TEXT)
    a_bans(ctx, manager, floor, [])
    return "Жилец возвращён"


@action("users")
def a_users(ctx, manager, floor, args):
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
    keyboard = kb.inline([[_back(floor)]])
    if len(render.plain(text)) <= MAX_TEXT:
        ctx.edit(text, keyboard)
    else:
        ctx.send(text, keyboard)  # длинный список уйдёт несколькими сообщениями
    return None


# --------------------------------------------------------------------------- #
#  Заявки жильцов на смену фамилии / комнаты
# --------------------------------------------------------------------------- #
def change_list_view(items: list[dict], title: str, empty: str, back: Button, nav: str, page: int) -> tuple[str, str]:
    """Список заявок с кнопками «Принять / Отклонить» (общий для панели старосты и админ-панели)."""
    if not items:
        return empty, kb.inline([[back]])
    lines = [title, ""]
    buttons: list[Button] = []
    for i, r in enumerate(items, 1):
        lines.append(f"{i}. " + render.change_label(r).replace("\n", " → "))
        buttons += [(f"✅ {i}. Принять", f"chg:ok:{r['id']}"), (f"❌ {i}. Отклонить", f"chg:no:{r['id']}")]
    return "\n".join(lines), kb.inline_paged(buttons, 2, [[back]], nav=nav, page=page)


@action("chq")
def a_change_requests(ctx, manager, floor, args):
    items = [r for r in db.pending_change_requests(floor) if services.can_decide_change(manager, r)]
    ctx.edit(*change_list_view(items, f"📝 <b>Заявки на изменение данных · этаж {floor}</b>",
                               f"📝 Заявок на изменение данных на этаже {floor} нет.",
                               _back(floor), _cb(floor, "chq"), ctx.page))
    return None


@command("chg")
def change_buttons(ctx: Ctx, parts: list[str]):
    _, verdict, request_id = parts
    manager = ctx.user()
    request, result = services.decide_change(manager, int(request_id), verdict == "ok")  # type: ignore[arg-type]
    assert manager is not None
    text = render.change_decided_text(request, manager, result)
    if result is None:
        notify.send_to_user(request, text)
        status = "отклонена ❌"
    else:
        notify.send_to_user(result.user, text, menu=True)
        status = "принята ✅"
    back = (("⬅️ В панель", _cb(manager["floor"])) if manager["role"] == "starosta"
            and services.can_manage(manager, manager["floor"]) else ("🛠 Админ-панель", "adm"))
    ctx.edit(f"📝 Заявка {status}\n" + render.change_label(request), kb.inline([[back]]))
    return f"Заявка {status}"
