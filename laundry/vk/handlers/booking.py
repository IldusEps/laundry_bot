"""Запись на стирку: неделя (картинка) → день → время → подтверждение, «Мои записи» и отмена.

Команды те же, что в Telegram-боте: bk:w | bk:d:<дата> | bk:s:<дата>:<время> | bk:c:<дата>:<время>,
my:l | my:c:<id> | my:y:<id>.
"""
from __future__ import annotations

import logging
from datetime import date

from ... import db, image, render, services
from ... import schedule as sched
from .. import keyboards as kb
from ..context import Ctx
from ..router import command, menu

log = logging.getLogger(__name__)

TO_WEEK = ("⬅️ К неделе", "bk:w")


# --------------------------------------------------------------------------- #
#  Экраны
# --------------------------------------------------------------------------- #
def week_view(user: dict, page: int = 0) -> tuple[str, str, bytes | None]:
    """Текст, кнопки дней и картинка расписания (None, если неделя закончилась)."""
    grid = services.build_grid(user["floor"], user["wing"], viewer_id=user["id"])
    refresh = ("🔄 Обновить", "bk:w")
    if not grid.has_future():
        return render.week_closed_text(user["floor"], user["wing"], grid), kb.inline([[refresh]]), None
    buttons = []
    for day in grid.week.days:
        if grid.day_is_over(day):
            label = f"{sched.fmt_day_short(day)} · прошёл"
        else:
            free = grid.free_count(day)
            label = f"{sched.fmt_day_short(day)} · {free} своб." if free else f"{sched.fmt_day_short(day)} · мест нет"
        buttons.append((label, f"bk:d:{sched.date_code(day)}"))
    picture = image.week_image(grid, render.week_title(grid))
    return render.week_text(grid), kb.inline_paged(buttons, 2, [[refresh]], nav="bk:w", page=page), picture


def day_view(user: dict, day: date, page: int = 0) -> tuple[str, str] | None:
    grid = services.build_grid(user["floor"], user["wing"], viewer_id=user["id"])
    if day not in grid.week.days:
        return None
    text = render.day_text(grid, day)
    free = [s for s in grid.rules.slots if grid.cell(day, s).state == "free"]
    buttons: list[tuple[str, str]] = []
    if not free:
        text += "\n\nСвободного времени на этот день нет."
    else:
        limit = services.day_limit_message(user, day)
        if limit:
            text += f"\n\n⚠️ {limit}"
        else:
            text += "\n\n👇 Выберите время:"
            code = sched.date_code(day)
            buttons = [(s.label, f"bk:s:{code}:{s.code}") for s in free]
    return text, kb.inline_paged(buttons, 2, [[TO_WEEK]], nav=f"bk:d:{sched.date_code(day)}", page=page)


def my_view(user: dict, page: int = 0) -> tuple[str, str | None]:
    items = services.upcoming_bookings(user)
    if not items:
        return "📋 У вас нет предстоящих записей.\n\nЗаписаться — кнопка «📅 Записаться».", None
    lines = ["📋 <b>Ваши записи</b>", ""]
    buttons = []
    for i, b in enumerate(items, 1):
        lines.append(f"{i}. {render.booking_label_full(b)} (этаж {b['floor']})")
        buttons.append((f"❌ Отменить: {render.booking_label(b)}", f"my:c:{b['id']}"))
    lines.append("\nОтменить запись можно до начала стирки.")
    return "\n".join(lines), kb.inline_paged(buttons, 1, nav="my:l", page=page)


# --------------------------------------------------------------------------- #
#  Кнопки меню
# --------------------------------------------------------------------------- #
def _user_or_reason(ctx: Ctx) -> tuple[dict | None, str | None]:
    user = ctx.user()
    return user, services.booking_block_reason(user)


@menu("menu:book")
def cmd_book(ctx: Ctx) -> None:
    user, reason = _user_or_reason(ctx)
    if reason:
        ctx.send(reason)
        return
    text, keyboard, picture = week_view(user)  # type: ignore[arg-type]
    ctx.send(text, keyboard, image=picture)


@menu("menu:my")
def cmd_my(ctx: Ctx) -> None:
    user = ctx.user()
    if not services.is_registered(user):
        ctx.send("Сначала пройдите регистрацию — напишите «начать».")
        return
    ctx.send(*my_view(user))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
#  Запись
# --------------------------------------------------------------------------- #
@command("bk")
def booking_action(ctx: Ctx, parts: list[str]):
    action = parts[1] if len(parts) > 1 else "w"
    user, reason = _user_or_reason(ctx)
    if reason:
        ctx.edit(reason)
        return reason, True
    assert user is not None

    if action == "w":
        text, keyboard, picture = week_view(user, ctx.page)
        ctx.edit(text, keyboard, image=picture)
        return None

    day = sched.parse_date_code(parts[2])
    if action == "d":
        view = day_view(user, day, ctx.page)
        if view is None:
            text, keyboard, picture = week_view(user)
            ctx.edit(text, keyboard, image=picture)
            return "Этот день уже недоступен — показываю актуальную неделю", True
        ctx.edit(*view)
        return None

    start = sched.parse_time_code(parts[3])
    slot = sched.rules_for(user["floor"]).find_slot(start)
    if slot is None:
        return "Такого времени нет в расписании", True

    if action == "s":
        code = f"{parts[2]}:{parts[3]}"
        ctx.edit(f"📝 Записаться на стирку?\n\n<b>{sched.fmt_day_full(day)}, {slot.label}</b>\nЭтаж {user['floor']}",
                 kb.inline([[("✅ Записаться", f"bk:c:{code}"), ("⬅️ Назад", f"bk:d:{parts[2]}")]]))
        return None

    if action == "c":
        try:
            booking = services.book(user, day, start)
        except services.ServiceError as exc:
            view = day_view(user, day)
            if view:
                ctx.edit(*view)
            else:
                text, keyboard, picture = week_view(user)
                ctx.edit(text, keyboard, image=picture)
            return str(exc), True
        ctx.edit(f"✅ Вы записаны на стирку!\n\n<b>{render.booking_label_full(booking)}</b>\nЭтаж {booking['floor']}\n\n"
                 "Отменить запись можно в «📋 Мои записи».",
                 kb.inline([[("📅 К расписанию", "bk:w")], [("📋 Мои записи", "my:l")]]))
        return "Записано ✅"
    return None


# --------------------------------------------------------------------------- #
#  «Мои записи»
# --------------------------------------------------------------------------- #
@command("my")
def my_action(ctx: Ctx, parts: list[str]):
    user = ctx.user()
    if not services.is_registered(user):
        return "Сначала пройдите регистрацию — напишите «начать».", True
    assert user is not None
    action = parts[1] if len(parts) > 1 else "l"

    if action == "l":
        ctx.edit(*my_view(user, ctx.page))
        return None

    booking_id = int(parts[2])
    if action == "c":
        booking = db.get_booking(booking_id)
        if not booking or booking["user_id"] != user["id"] or booking["status"] != "active":
            ctx.edit(*my_view(user))
            return "Запись не найдена или уже отменена", True
        ctx.edit(f"Отменить запись?\n\n<b>{render.booking_label_full(booking)}</b>",
                 kb.inline([[("✅ Да, отменить", f"my:y:{booking_id}"), ("⬅️ Назад", "my:l")]]))
        return None

    if action == "y":
        try:
            booking = services.cancel_own(user, booking_id)
        except services.ServiceError as exc:
            ctx.edit(*my_view(user))
            return str(exc), True
        text, keyboard = my_view(user)
        ctx.edit(f"🗑 Запись на {render.booking_label(booking)} отменена.\n\n" + text, keyboard)
        return "Запись отменена"
    return None
