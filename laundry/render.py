"""Тексты сообщений: таблица недели, расписание дня, профиль, правила.

Тексты пишутся в HTML Telegram (<b>, <code>, <a>). Для VK их переводит в обычный текст plain()."""
from __future__ import annotations

import re
from datetime import date
from html import escape, unescape

from . import config, services
from . import schedule as sched
from .services import Grid

LEGEND = "🟢 свободно   🔴 занято   ⚪ нельзя записаться"


def esc(text: object) -> str:
    return escape(str(text)) if text is not None else ""


# --------------------------------------------------------------------------- #
#  HTML -> обычный текст для VK
# --------------------------------------------------------------------------- #
_LINK_RE = re.compile(r'<a\s+href="([^"]*)"\s*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_VK_PROFILE_RE = re.compile(r"https://vk\.com/id(\d+)")
_TG_PROFILE_RE = re.compile(r"https://t\.me/(\w+)")


def _plain_link(m: re.Match) -> str:
    url, text = unescape(m.group(1)), m.group(2)
    if vk := _VK_PROFILE_RE.fullmatch(url):
        return f"[id{vk.group(1)}|{text}]"  # упоминание VK
    if tg := _TG_PROFILE_RE.fullmatch(url):
        return f"t.me/{tg.group(1)}"
    return url if text == url else f"{text} ({url})"


def plain(text: str) -> str:
    """HTML-текст бота -> обычный текст VK: теги убираются, сущности раскодируются,
    ссылка на профиль VK становится упоминанием [id123|Фамилия]."""
    text = _LINK_RE.sub(_plain_link, text)
    return unescape(_TAG_RE.sub("", text))


# --------------------------------------------------------------------------- #
#  Люди
# --------------------------------------------------------------------------- #
def name(user: dict) -> str:
    """Фамилия; у пользователя VK — ссылка на профиль (в VK превращается в [id123|Фамилия])."""
    surname = esc(user.get("surname") or "—")
    if user.get("vk_id"):
        return f'<a href="https://vk.com/id{user["vk_id"]}">{surname}</a>'
    return surname


def tg_username(user: dict) -> str:
    """@username в Telegram (ссылкой) или пустая строка."""
    if not user.get("username"):
        return ""
    return f'<a href="https://t.me/{esc(user["username"])}">@{esc(user["username"])}</a>'


def contact(user: dict) -> str:
    """Как связаться: '@username' (Telegram) и/или ссылка на профиль VK."""
    parts = [tg_username(user)] if user.get("username") else []
    if user.get("vk_id"):
        parts.append(f'<a href="https://vk.com/id{user["vk_id"]}">профиль ВКонтакте</a>')
    return " · ".join(parts)


def accounts(user: dict) -> str:
    """Строка с id аккаунтов для администратора: 'telegram_id: 123 · vk: 456'."""
    parts = []
    if user.get("telegram_id"):
        parts.append(f"telegram_id: <code>{user['telegram_id']}</code>")
    if user.get("vk_id"):
        parts.append(f'vk: <a href="https://vk.com/id{user["vk_id"]}">id{user["vk_id"]}</a>')
    return " · ".join(parts)


def by_role(manager: dict) -> str:
    return "администратором" if services.is_admin(manager) else "старостой этажа"


def change_label(req: dict) -> str:
    def place(surname, room, floor):
        return f"{esc(surname or '—')}, к.{room or '—'}" + (f" (этаж {floor})" if floor else "")
    return (f"Было: {place(req['old_surname'], req['old_room'], req['old_floor'])}\n"
            f"Стало: {place(req['new_surname'], req['new_room'], req['new_floor'])}")


# Уведомления жильцам о действиях старосты (одинаковые для Telegram и VK)
def cancelled_by_manager_text(booking: dict, manager: dict) -> str:
    return f"❗ Ваша запись на стирку <b>{booking_label_full(booking)}</b> отменена {by_role(manager)}."


def cancelled_by_closure_text(booking: dict, manager: dict, reason: str | None) -> str:
    why = f"\nПричина: {esc(reason)}" if reason else ""
    return (f"❗ Ваша запись на стирку <b>{booking_label_full(booking)}</b> отменена: "
            f"запись на это время закрыта {by_role(manager)}.{why}")


def room_banned_text(room: str, manager: dict) -> str:
    return (f"🚫 Комната {room} исключена {by_role(manager)} из записи на стирку "
            "(например, если не скинулись за стирку). Ваши будущие записи отменены. "
            "Обратитесь к старосте этажа.")


def user_banned_text(manager: dict) -> str:
    return (f"🚫 Вы исключены {by_role(manager)} из записи на стирку (например, если не скинулись за стирку). "
            "Ваши будущие записи отменены. Обратитесь к старосте этажа.")


def room_unbanned_text(room: str) -> str:
    return f"✅ Комнате {room} снова доступна запись на стирку."


USER_UNBANNED_TEXT = "✅ Вам снова доступна запись на стирку."


def change_decided_text(request: dict, manager: dict, result: services.RegistrationResult | None) -> str:
    """Уведомление автору заявки на изменение данных."""
    if result is None:
        return f"❌ Заявка на изменение данных отклонена {by_role(manager)}.\n" + change_label(request)
    text = f"✅ Изменение данных подтверждено {by_role(manager)}.\n" + change_label(request)
    if result.cancelled:
        text += f"\nКомната изменилась — ваши будущие записи ({len(result.cancelled)}) отменены."
    if result.role_reset:
        text += "\nВы переехали на другой этаж, поэтому роль старосты снята."
    return text


def place_text(room: str, floor: int, wing: int | None) -> str:
    return f"комната {room}, этаж {floor}" + (f", {wing} крыло" if wing else "")


def registered_text(user: dict, place: str) -> str:
    return (f"✅ Готово! <b>{esc(user['surname'])}</b>, {place}.\n\n"
            "Записаться на стирку — кнопка «📅 Записаться».\n"
            "Изменить фамилию или комнату, подать заявку на старосту — в «👤 Профиль».")


def new_user_text(user: dict, place: str) -> str:
    text = f"🆕 <b>Новый пользователь</b>\n{name(user)}, {place}"
    if user.get("username"):
        text += f"\n{tg_username(user)}"
    return text


def change_request_text(req: dict) -> str:
    text = "📝 <b>Заявка на изменение данных</b>\n\n" + change_label(req)
    if contact(req):
        text += f"\n{contact(req)}"
    return text


def change_sent_text(req: dict) -> str:
    return ("📨 Заявка отправлена старосте этажа:\n" + change_label(req)
            + "\n\nДанные изменятся после подтверждения — я сообщу.")


def starosta_request_text(user: dict, current: list[dict]) -> str:
    """Заявка на старосту для администратора; current — нынешние старосты этажа."""
    text = (f"📨 <b>Заявка на старосту</b>\n\n"
            f"{name(user)}, к.{user['room']} (этаж {user['floor']})"
            + (f", {tg_username(user)}" if user.get("username") else "")
            + f"\n{accounts(user)}")
    others = [u for u in current if u["id"] != user["id"]]
    if others:
        text += "\n\nСейчас старосты этого этажа: " + ", ".join(
            f"{esc(u['surname'])} (к.{u['room']})" for u in others)
    return text


def support_text(user: dict, text: str) -> str:
    """Сообщение жильца администраторам (text — как ввёл человек)."""
    sender = (f"{name(user)}, к.{user['room'] or '—'}"
              + (f" (этаж {user['floor']})" if user.get("floor") else "")
              + (f", {tg_username(user)}" if user.get("username") else ""))
    return f"✉️ <b>Сообщение от пользователя</b>\n{sender}\n\n{esc(text)}"


def link_code_text(code: str, target: str) -> str:
    where = "бота нашего сообщества ВКонтакте" if target == "vk" else "нашего Telegram-бота"
    return (f"🔗 Код привязки: <code>{code}</code>\n\n"
            f"Откройте {where} и отправьте ему этот код (действует {services.LINK_TTL_MINUTES} минут). "
            "После этого записи, роль и ограничения станут общими для обоих мессенджеров.")


def link_notice(platform: str) -> str:
    """Для второго мессенджера: к профилю привязали аккаунт platform."""
    return (f"🔗 К вашему профилю привязан аккаунт {services.PLATFORM_NAMES[platform]}. "
            "Записи, роль и ограничения теперь общие. Если это были не вы — напишите администратору.")


STAROSTA_REMOVED_TEXT = "ℹ️ Администратор снял с вас роль старосты этажа."


def starosta_decided_text(user: dict, approved: bool) -> str:
    if approved:
        return (f"⭐ Администратор подтвердил: вы староста {user['floor']} этажа.\n"
                "В меню появилась кнопка «⭐ Панель старосты».")
    return "Заявка на роль старосты отклонена администратором."


def admin_reply_text(text: str) -> str:
    """Ответ администратора жильцу (text — как ввёл администратор)."""
    return f"✉️ <b>Ответ администратора</b>\n\n{esc(text)}"


# Кнопки в уведомлениях: [(подпись, команда)] — клавиатуру под мессенджер получателя строит laundry.notify
ADMIN_REPLY_BUTTONS = [[("✉️ Ответить администратору", "sup:new")]]


def change_buttons(req: dict) -> list[list[tuple[str, str]]]:
    return [[("✅ Принять", f"chg:ok:{req['id']}"), ("❌ Отклонить", f"chg:no:{req['id']}")]]


def starosta_request_buttons(user: dict) -> list[list[tuple[str, str]]]:
    return [[("✅ Одобрить", f"adm:ok:{user['id']}"), ("❌ Отклонить", f"adm:no:{user['id']}")]]


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
    username = f" {tg_username(u)}" if u.get("username") else ""
    return f"к.{u['room']} — {name(u)}{username}{marks}"


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
        if user.get("telegram_id") and user.get("vk_id"):
            lines.append("🔗 Аккаунты Telegram и ВКонтакте связаны")
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
