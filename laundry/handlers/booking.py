"""Запись на стирку: неделя → день → время, «Мои записи» и отмена."""
from __future__ import annotations

import logging
from datetime import date

from telebot import types

from .. import db, image, render, services, states
from .. import schedule as sched
from ..loader import bot
from ..utils import answer_result, btn, edit, grid_kb, inline, safe, send, send_photo

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Экраны
# --------------------------------------------------------------------------- #
def week_view(user: dict) -> tuple[str, types.InlineKeyboardMarkup, bytes | None]:
    """Текст/подпись, кнопки дней и картинка расписания (None, если неделя закончилась)."""
    grid = services.build_grid(user["floor"], user["wing"], viewer_id=user["id"])
    refresh = btn("🔄 Обновить", "bk:w")
    if not grid.has_future():
        return render.week_closed_text(user["floor"], user["wing"], grid), inline(refresh), None
    buttons = []
    for day in grid.week.days:
        if grid.day_is_over(day):
            label = f"{sched.fmt_day_short(day)} · прошёл"
        else:
            free = grid.free_count(day)
            label = f"{sched.fmt_day_short(day)} · {free} своб." if free else f"{sched.fmt_day_short(day)} · мест нет"
        buttons.append(btn(label, f"bk:d:{sched.date_code(day)}"))
    picture = image.week_image(grid, render.week_title(grid))
    return render.week_text(grid), grid_kb(buttons, 2, [refresh]), picture


def show_week(call: types.CallbackQuery, user: dict) -> None:
    text, kb, picture = week_view(user)
    edit(call, text, kb, image=picture)


def day_view(user: dict, day: date) -> tuple[str, types.InlineKeyboardMarkup] | None:
    grid = services.build_grid(user["floor"], user["wing"], viewer_id=user["id"])
    if day not in grid.week.days:
        return None
    text = render.day_text(grid, day)
    free = [s for s in grid.rules.slots if grid.cell(day, s).state == "free"]
    buttons: list[types.InlineKeyboardButton] = []
    if not free:
        text += "\n\nСвободного времени на этот день нет."
    else:
        limit = services.day_limit_message(user, day)
        if limit:
            text += f"\n\n⚠️ {limit}"
        else:
            text += "\n\n👇 Выберите время:"
            code = sched.date_code(day)
            buttons = [btn(s.label, f"bk:s:{code}:{s.code}") for s in free]
    return text, grid_kb(buttons, 2, [btn("⬅️ К неделе", "bk:w")])


def my_view(user: dict) -> tuple[str, types.InlineKeyboardMarkup | None]:
    items = services.upcoming_bookings(user)
    if not items:
        return "📋 У вас нет предстоящих записей.\n\nЗаписаться — кнопка «📅 Записаться».", None
    lines = ["📋 <b>Ваши записи</b>", ""]
    buttons = []
    for i, b in enumerate(items, 1):
        lines.append(f"{i}. {render.booking_label_full(b)} (этаж {b['floor']})")
        buttons.append(btn(f"❌ Отменить: {render.booking_label(b)}", f"my:c:{b['id']}"))
    lines.append("\nОтменить запись можно до начала стирки.")
    return "\n".join(lines), grid_kb(buttons, 1)


# --------------------------------------------------------------------------- #
#  Кнопки меню
# --------------------------------------------------------------------------- #
def _user_or_reason(telegram_id: int) -> tuple[dict | None, str | None]:
    user = db.get_user(telegram_id)
    return user, services.booking_block_reason(user)


def cmd_book(message: types.Message) -> None:
    user, reason = _user_or_reason(message.from_user.id)
    if reason:
        send(message.chat.id, reason)
        return
    text, kb, picture = week_view(user)  # type: ignore[arg-type]
    if picture:
        send_photo(message.chat.id, picture, text, kb)
    else:
        send(message.chat.id, text, kb)


def cmd_my(message: types.Message) -> None:
    user = db.get_user(message.from_user.id)
    if not services.is_registered(user):
        send(message.chat.id, "Сначала пройдите регистрацию — нажмите /start")
        return
    text, kb = my_view(user)  # type: ignore[arg-type]
    send(message.chat.id, text, kb)


@bot.message_handler(commands=["book"])
@safe
def cmd_book_command(message: types.Message) -> None:
    states.clear(message.from_user.id)
    cmd_book(message)


@bot.message_handler(commands=["my"])
@safe
def cmd_my_command(message: types.Message) -> None:
    states.clear(message.from_user.id)
    cmd_my(message)


# --------------------------------------------------------------------------- #
#  Inline-кнопки записи: bk:w | bk:d:<дата> | bk:s:<дата>:<время> | bk:c:<дата>:<время>
# --------------------------------------------------------------------------- #
@bot.callback_query_handler(func=lambda c: c.data.startswith("bk:"))
@safe
def booking_callbacks(call: types.CallbackQuery) -> None:
    answer_result(call, _booking_action(call))


def _booking_action(call: types.CallbackQuery):
    parts = call.data.split(":")
    action = parts[1]
    user, reason = _user_or_reason(call.from_user.id)
    if reason:
        edit(call, reason)
        return reason, True
    assert user is not None

    if action == "w":
        show_week(call, user)
        return None

    day = sched.parse_date_code(parts[2])

    if action == "d":
        view = day_view(user, day)
        if view is None:
            show_week(call, user)
            return "Этот день уже недоступен — показываю актуальную неделю", True
        edit(call, *view)
        return None

    start = sched.parse_time_code(parts[3])
    slot = sched.rules_for(user["floor"]).find_slot(start)
    if slot is None:
        return "Такого времени нет в расписании", True

    if action == "s":
        code = f"{parts[2]}:{parts[3]}"
        edit(call,
             f"📝 Записаться на стирку?\n\n<b>{sched.fmt_day_full(day)}, {slot.label}</b>\nЭтаж {user['floor']}",
             inline([btn("✅ Записаться", f"bk:c:{code}"), btn("⬅️ Назад", f"bk:d:{parts[2]}")]))
        return None

    if action == "c":
        try:
            booking = services.book(user, day, start)
        except services.ServiceError as exc:
            view = day_view(user, day)
            if view:
                edit(call, *view)
            else:
                show_week(call, user)
            return str(exc), True
        edit(call,
             f"✅ Вы записаны на стирку!\n\n<b>{render.booking_label_full(booking)}</b>\nЭтаж {booking['floor']}\n\n"
             "Отменить запись можно в «📋 Мои записи».",
             inline(btn("📅 К расписанию", "bk:w"), btn("📋 Мои записи", "my:l")))
        return "Записано ✅"

    return None


# --------------------------------------------------------------------------- #
#  «Мои записи»: my:l | my:c:<id> | my:y:<id>
# --------------------------------------------------------------------------- #
@bot.callback_query_handler(func=lambda c: c.data.startswith("my:"))
@safe
def my_callbacks(call: types.CallbackQuery) -> None:
    answer_result(call, _my_action(call))


def _my_action(call: types.CallbackQuery):
    parts = call.data.split(":")
    user = db.get_user(call.from_user.id)
    if not services.is_registered(user):
        return "Сначала пройдите регистрацию: /start", True
    assert user is not None

    if parts[1] == "l":
        text, kb = my_view(user)
        edit(call, text, kb)
        return None

    booking_id = int(parts[2])
    if parts[1] == "c":
        booking = db.get_booking(booking_id)
        if not booking or booking["user_id"] != user["id"] or booking["status"] != "active":
            edit(call, *my_view(user))
            return "Запись не найдена или уже отменена", True
        edit(call, f"Отменить запись?\n\n<b>{render.booking_label_full(booking)}</b>",
             inline([btn("✅ Да, отменить", f"my:y:{booking_id}"), btn("⬅️ Назад", "my:l")]))
        return None

    if parts[1] == "y":
        try:
            booking = services.cancel_own(user, booking_id)
        except services.ServiceError as exc:
            edit(call, *my_view(user))
            return str(exc), True
        text, kb = my_view(user)
        edit(call, f"🗑 Запись на {render.booking_label(booking)} отменена.\n\n" + text, kb)
        return "Запись отменена"
    return None
