"""Тексты сообщений: таблица недели, расписание дня, профиль, правила."""
from __future__ import annotations

from datetime import date
from html import escape

from . import config
from . import schedule as sched
from .services import Grid

LEGEND = "🟢 свободно   🔴 занято   ⚪ нельзя записаться"


def esc(text: object) -> str:
    return escape(str(text)) if text is not None else ""


def _floor_title(floor: int, wing: int | None) -> str:
    title = f"этаж {floor}"
    if wing:
        title += f", {wing} крыло"
    return title


def week_title(grid: Grid) -> str:
    return f"Стирка · {_floor_title(grid.floor, grid.wing)}"


def week_text(grid: Grid, manager: bool = False) -> str:
    """Подпись к картинке с расписанием (до 1024 символов)."""
    days = grid.week.days
    parts = [f"🧺 <b>Расписание стирки · {_floor_title(grid.floor, grid.wing)}</b>\n"
             f"📆 {sched.fmt_day_short(days[0])} – {sched.fmt_day_short(days[-1])}",
             LEGEND,
             f"⏳ Запись на следующую неделю откроется {sched.fmt_moment(grid.week.next_open_at)}."]
    if manager and not grid.has_future():
        parts.append("Стирки этой недели закончились.")
    parts.append("👇 Выберите день, чтобы посмотреть записи и отменить нужную:" if manager
                 else "👇 Выберите день:")
    return "\n\n".join(parts)


def week_closed_text(floor: int, wing: int | None, grid: Grid) -> str:
    nxt = sched.current_week(floor, wing, at=grid.week.next_open_at)
    return (f"🧺 <b>Расписание стирки · {_floor_title(floor, wing)}</b>\n\n"
            f"Стирки этой недели закончились.\n"
            f"⏳ Запись на неделю <b>{sched.fmt_day_short(nxt.days[0])} – {sched.fmt_day_short(nxt.days[-1])}</b> "
            f"откроется {sched.fmt_moment(grid.week.next_open_at)}.")


def day_text(grid: Grid, day: date) -> str:
    title = f"📅 <b>{sched.fmt_day_full(day)}</b> · этаж {grid.floor}"
    wing = grid.rules.wing_for_weekday(day.weekday()) if grid.rules.wing_weekdays else None
    if wing:
        title += f", {wing} крыло"
    lines = [title, ""]
    for slot in grid.rules.slots:
        cell = grid.cell(day, slot)
        if cell.booking:
            b = cell.booking
            suffix = " (вы)" if cell.state == "mine" else ""
            lines.append(f"🔴 {slot.label} — к.{b['room']} {esc(b['surname'])}{suffix}")
        elif cell.state == "closed":
            reason = f" ({esc(cell.closure['reason'])})" if cell.closure and cell.closure.get("reason") else ""
            lines.append(f"⚪ {slot.label} — закрыто{reason}")
        elif cell.state == "past":
            lines.append(f"⚪ {slot.label} — прошло")
        else:
            lines.append(f"🟢 {slot.label} — свободно")
    return "\n".join(lines)


def booking_label(b: dict) -> str:
    return f"{sched.fmt_day_short(b['slot_date'])}, {b['slot_start']:%H:%M}–{b['slot_end']:%H:%M}"


def booking_label_full(b: dict) -> str:
    return f"{sched.fmt_day_full(b['slot_date'])}, {b['slot_start']:%H:%M}–{b['slot_end']:%H:%M}"


def closure_label(c: dict) -> str:
    if c["date_from"] == c["date_to"]:
        when = sched.fmt_day_short(c["date_from"])
    else:
        when = f"{sched.fmt_day_short(c['date_from'])} – {sched.fmt_day_short(c['date_to'])}"
    if sched.is_full_day(c["time_from"], c["time_to"]):
        return f"{when}, весь день"
    return f"{when}, {c['time_from']:%H:%M}–{c['time_to']:%H:%M}"


def user_line(u: dict) -> str:
    marks = ""
    if u["role"] == "starosta":
        marks += " ⭐"
    if u["is_banned"]:
        marks += " 🚫"
    username = f" @{esc(u['username'])}" if u.get("username") else ""
    return f"к.{u['room']} — {esc(u['surname'] or '—')}{username}{marks}"


def profile_text(user: dict, room_banned: bool, is_admin: bool) -> str:
    lines = ["👤 <b>Профиль</b>", ""]
    if user.get("room"):
        lines += [f"Фамилия: <b>{esc(user['surname'])}</b>",
                  f"Комната: <b>{user['room']}</b> (этаж {user['floor']}"
                  + (f", {user['wing']} крыло)" if user.get("wing") else ")")]
        role = "староста этажа ⭐" if user["role"] == "starosta" else "жилец"
        if user["starosta_pending"]:
            role += " (заявка на старосту на рассмотрении)"
        lines.append(f"Роль: {role}")
        if user["is_banned"]:
            lines.append("🚫 Вы исключены из записи старостой")
        if room_banned:
            lines.append("🚫 Ваша комната исключена из записи")
    else:
        lines.append("Вы ещё не зарегистрированы как жилец.")
    if is_admin:
        lines.append("🛠 Администратор бота")
    return "\n".join(lines)


def _slots_list(rules: sched.FloorRules) -> str:
    return ", ".join(s.label for s in rules.slots)


def _regular_floors() -> str:
    """[2, 3, 4] -> '2–4'; [2, 3, 4, 6, 7] -> '2–4, 6–7'"""
    floors = [f for f in sched.bookable_floors() if f != sched.SPECIAL_FLOOR]
    groups: list[list[int]] = []
    for f in floors:
        if groups and f == groups[-1][-1] + 1:
            groups[-1].append(f)
        else:
            groups.append([f])
    return ", ".join(f"{g[0]}–{g[-1]}" if len(g) > 1 else str(g[0]) for g in groups) or "—"


def rules_text(user: dict | None) -> str:
    floor = user.get("floor") if user else None
    regular = sched.REGULAR_RULES
    f5 = sched.FLOOR5_RULES
    regular_block = (
        f"🧺 <b>Этажи {_regular_floors()}</b>\n"
        "• Стирка со вторника по воскресенье, понедельник — выходной.\n"
        "• Одна стирка — 45 минут, между стирками 1 час.\n"
        f"• Время: {_slots_list(regular)}.\n"
        f"• Не больше {regular.user_daily_limit} записей в день на человека.\n"
        f"• Запись на новую неделю открывается в понедельник в {regular.open_time:%H:%M}."
    )
    floor5_block = (
        "🧺 <b>5 этаж</b>\n"
        "• Стирка с понедельника по субботу.\n"
        "• 1 крыло (511–527): вторник, четверг, суббота.\n"
        "• 2 крыло (501–510, 528–536): понедельник, среда, пятница.\n"
        f"• Время: {_slots_list(f5)}.\n"
        "• Одна комната — не больше 1 стирки в день.\n"
        f"• Запись на новую неделю открывается в воскресенье в {f5.open_time:%H:%M}."
    )
    common = ("• Отменить свою запись можно в «📋 Мои записи» до начала стирки.\n"
              "• Сменить фамилию или комнату — заявкой в «👤 Профиль», её подтверждает староста этажа.\n"
              "• Староста может отменять записи, закрывать запись на дни и исключать тех, кто не скинулся на стирку.\n"
              "• 1 этаж в записи не участвует.")
    if floor == sched.SPECIAL_FLOOR:
        blocks = [floor5_block]
    elif sched.floor_is_bookable(floor):
        blocks = [regular_block]
    else:
        blocks = [regular_block, floor5_block]
    return "📖 <b>Правила записи на стирку</b>\n\n" + "\n\n".join(blocks) + "\n\n" + common


def help_text() -> str:
    return ("ℹ️ <b>Команды</b>\n"
            "/start — главное меню\n"
            "/book — записаться на стирку\n"
            "/my — мои записи\n"
            "/profile — профиль\n"
            "/rules — правила\n"
            "/cancel — отменить текущее действие\n"
            f"\nВ общежитии {config.MAX_FLOOR} этажей, запись идёт для этажей 2–{config.MAX_FLOOR}.")
