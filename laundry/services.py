"""Бизнес-логика бота. Не зависит от telebot: возвращает данные, а уведомления
рассылают обработчики."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time

from . import config, db
from . import schedule as sched
from .logger import ACTIONS_LOGGER

log = logging.getLogger(__name__)
actions = logging.getLogger(ACTIONS_LOGGER)


class ServiceError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


# --------------------------------------------------------------------------- #
#  Роли и доступ
# --------------------------------------------------------------------------- #
def is_admin(telegram_id: int) -> bool:
    return telegram_id in config.ADMIN_IDS


def is_registered(user: dict | None) -> bool:
    return bool(user and user.get("room"))


def who(user: dict | None) -> str:
    if not user:
        return "?"
    return f"{user.get('surname') or '—'} (к.{user.get('room') or '—'}, tg={user.get('telegram_id')})"


def can_manage(user: dict | None, floor: int) -> bool:
    """Староста управляет только своим этажом, администратор — любым."""
    if not user or not sched.floor_is_bookable(floor):
        return False
    if is_admin(user["telegram_id"]):
        return True
    return user["role"] == "starosta" and user["floor"] == floor


def _require_manager(user: dict, floor: int) -> None:
    if not can_manage(user, floor):
        raise ServiceError("⛔ Нет прав на управление этим этажом.")


def booking_block_reason(user: dict | None) -> str | None:
    if not is_registered(user):
        return "Сначала пройдите регистрацию — нажмите /start"
    assert user is not None
    if not sched.floor_is_bookable(user["floor"]):
        return "Для вашего этажа запись на стирку не проводится."
    if user["is_banned"]:
        return "🚫 Староста исключил вас из записи на стирку. Обратитесь к старосте этажа."
    if db.get_room_ban(user["room"]):
        return f"🚫 Комната {user['room']} исключена из записи на стирку. Обратитесь к старосте этажа."
    return None


# --------------------------------------------------------------------------- #
#  Регистрация и профиль
# --------------------------------------------------------------------------- #
_SURNAME_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:[- ][A-Za-zА-Яа-яЁё]+)*")


def normalize_surname(text: str) -> str | None:
    raw = " ".join((text or "").split())
    if not (2 <= len(raw) <= 40) or not _SURNAME_RE.fullmatch(raw):
        return None
    return "-".join(" ".join(w.capitalize() for w in part.split(" ")) for part in raw.split("-"))


@dataclass
class RegistrationResult:
    user: dict
    cancelled: list[dict]
    role_reset: bool
    room_changed: bool


def save_registration(existing: dict | None, telegram_id: int, username: str | None, surname: str,
                      room: int, floor: int, wing: int | None, at: datetime | None = None) -> RegistrationResult:
    at = at or sched.now()
    old_room = existing.get("room") if existing else None
    room_changed = old_room is not None and old_room != room
    if room_changed and existing and (existing["is_banned"] or db.get_room_ban(old_room)):
        raise ServiceError("🚫 Вы исключены из записи, поэтому сменить комнату нельзя. Обратитесь к старосте этажа.")

    user = db.save_registration(telegram_id, username, surname, room, floor, wing, at)

    cancelled: list[dict] = []
    role_reset = False
    if room_changed:
        cancelled = db.cancel_future_bookings_of_user(user["id"], user["id"], at)
        if existing and existing["floor"] != floor and (existing["role"] == "starosta" or existing["starosta_pending"]):
            db.set_role(user["id"], "resident", at)
            role_reset = existing["role"] == "starosta"
        user = db.get_user(telegram_id) or user

    if existing and existing.get("room"):
        actions.info("PROFILE %s -> %s, к.%s", who(existing), surname, room)
    else:
        actions.info("REGISTER %s этаж=%s крыло=%s", who(user), floor, wing)
    return RegistrationResult(user, cancelled, role_reset, room_changed)


def request_starosta(user: dict, at: datetime | None = None) -> None:
    at = at or sched.now()
    if not is_registered(user) or not sched.floor_is_bookable(user["floor"]):
        raise ServiceError("Старосту можно назначить только на этаже, где идёт запись.")
    if user["role"] == "starosta":
        raise ServiceError("Вы уже староста этажа.")
    if user["starosta_pending"]:
        raise ServiceError("Заявка уже отправлена, ждите решения администратора.")
    if not config.ADMIN_IDS:
        raise ServiceError("Администратор не настроен — заявку некому рассмотреть.")
    db.set_starosta_pending(user["id"], True, at)
    actions.info("STAROSTA_REQUEST %s этаж=%s", who(user), user["floor"])


def decide_starosta(admin: dict, user_id: int, approve: bool, at: datetime | None = None) -> dict:
    at = at or sched.now()
    if not is_admin(admin["telegram_id"]):
        raise ServiceError("⛔ Только для администратора.")
    target = db.get_user_by_id(user_id)
    if not target or not target["starosta_pending"]:
        raise ServiceError("Заявка уже обработана.")
    if approve:
        db.set_role(user_id, "starosta", at)
    else:
        db.set_starosta_pending(user_id, False, at)
    actions.info("STAROSTA_%s %s админом %s", "APPROVED" if approve else "REJECTED", who(target), who(admin))
    return db.get_user_by_id(user_id) or target


def remove_starosta(admin: dict, user_id: int, at: datetime | None = None) -> dict:
    at = at or sched.now()
    if not is_admin(admin["telegram_id"]):
        raise ServiceError("⛔ Только для администратора.")
    target = db.get_user_by_id(user_id)
    if not target or target["role"] != "starosta":
        raise ServiceError("Этот пользователь уже не староста.")
    db.set_role(user_id, "resident", at)
    actions.info("STAROSTA_REMOVED %s админом %s", who(target), who(admin))
    return target


# --------------------------------------------------------------------------- #
#  Расписание
# --------------------------------------------------------------------------- #
@dataclass
class Cell:
    state: str                    # free | busy | mine | closed | past
    booking: dict | None = None
    closure: dict | None = None


@dataclass
class Grid:
    floor: int
    wing: int | None
    rules: sched.FloorRules
    week: sched.Week
    at: datetime
    cells: dict[tuple[date, time], Cell]

    def cell(self, day: date, slot: sched.Slot) -> Cell:
        return self.cells[(day, slot.start)]

    def free_count(self, day: date) -> int:
        return sum(1 for s in self.rules.slots if self.cell(day, s).state == "free")

    def day_is_over(self, day: date) -> bool:
        return all(sched.slot_dt(day, s.start) <= self.at for s in self.rules.slots)

    def has_future(self) -> bool:
        return any(not self.day_is_over(d) for d in self.week.days)

    def bookings_on(self, day: date) -> list[tuple[sched.Slot, dict]]:
        return [(s, c.booking) for s in self.rules.slots if (c := self.cell(day, s)).booking]


def build_grid(floor: int, wing: int | None = None, viewer_id: int | None = None,
               at: datetime | None = None) -> Grid:
    at = at or sched.now()
    rules = sched.rules_for(floor)
    week = sched.current_week(floor, wing, at)
    bookings: list[dict] = []
    closures: list[dict] = []
    if week.days:
        bookings = db.bookings_between(floor, week.days[0], week.days[-1])
        closures = db.closures_between(floor, week.days[0], week.days[-1])
    by_slot = {(b["slot_date"], b["slot_start"]): b for b in bookings}

    cells: dict[tuple[date, time], Cell] = {}
    for day in week.days:
        for slot in rules.slots:
            booking = by_slot.get((day, slot.start))
            closure = next((c for c in closures if sched.closure_covers(c, day, slot)), None)
            if booking:
                state = "mine" if viewer_id is not None and booking["user_id"] == viewer_id else "busy"
            elif sched.slot_dt(day, slot.start) <= at:
                state = "past"
            elif closure:
                state = "closed"
            else:
                state = "free"
            cells[(day, slot.start)] = Cell(state, booking, closure)
    return Grid(floor, wing, rules, week, at, cells)


def day_limit_message(user: dict, day: date) -> str | None:
    """Если человек (или комната на 5 этаже) уже исчерпал лимит на этот день — текст причины."""
    rules = sched.rules_for(user["floor"])
    if rules.room_daily_limit is not None and db.count_room_bookings_on(user["room"], day) >= rules.room_daily_limit:
        return "Ваша комната уже записана на этот день — на 5 этаже комната стирается не чаще 1 раза в день."
    if db.count_user_bookings_on(user["id"], day) >= rules.user_daily_limit:
        return f"Вы уже записаны {rules.user_daily_limit} раз(а) на этот день — это максимум."
    return None


# --------------------------------------------------------------------------- #
#  Запись и отмена
# --------------------------------------------------------------------------- #
_REJECT_TEXT = {
    "taken": "Это время уже кто-то занял. Выберите другое.",
    "user_limit": "Вы уже записаны максимальное число раз на этот день.",
    "room_limit": "Ваша комната уже записана на этот день (на 5 этаже — не чаще 1 раза в день).",
    "closed": "Староста закрыл запись на это время.",
    "banned": "🚫 Вы исключены из записи на стирку. Обратитесь к старосте этажа.",
    "room_banned": "🚫 Ваша комната исключена из записи на стирку. Обратитесь к старосте этажа.",
    "stale": "Данные профиля изменились — откройте расписание заново.",
}


def book(user: dict, day: date, start: time, at: datetime | None = None) -> dict:
    at = at or sched.now()
    reason = booking_block_reason(user)
    if reason:
        raise ServiceError(reason)
    floor = user["floor"]
    rules = sched.rules_for(floor)
    week = sched.current_week(floor, user["wing"], at)
    if day not in week.days:
        raise ServiceError("Запись на этот день сейчас недоступна. Обновите расписание.")
    slot = rules.find_slot(start)
    if slot is None:
        raise ServiceError("Такого времени нет в расписании.")
    if sched.slot_dt(day, slot.start) <= at:
        raise ServiceError("Это время уже прошло.")
    try:
        booking_id = db.create_booking(
            user_id=user["id"], floor=floor, room=user["room"], slot_date=day,
            slot_start=slot.start, slot_end=slot.end,
            user_limit=rules.user_daily_limit, room_limit=rules.room_daily_limit, at=at,
        )
    except db.BookingRejected as exc:
        log.info("Запись отклонена (%s): %s %s %s", exc.code, who(user), day, slot.label)
        raise ServiceError(_REJECT_TEXT.get(exc.code, "Не удалось записаться.")) from exc
    actions.info("BOOK id=%s %s этаж=%s %s %s", booking_id, who(user), floor, day, slot.label)
    return db.get_booking(booking_id)  # type: ignore[return-value]


def upcoming_bookings(user: dict, at: datetime | None = None) -> list[dict]:
    return db.user_upcoming_bookings(user["id"], at or sched.now())


def _ensure_future(booking: dict, at: datetime) -> None:
    if sched.slot_dt(booking["slot_date"], booking["slot_start"]) <= at:
        raise ServiceError("Эта стирка уже началась или прошла — отменить нельзя.")


def cancel_own(user: dict, booking_id: int, at: datetime | None = None) -> dict:
    at = at or sched.now()
    booking = db.get_booking(booking_id)
    if not booking or booking["user_id"] != user["id"] or booking["status"] != "active":
        raise ServiceError("Запись не найдена или уже отменена.")
    _ensure_future(booking, at)
    if not db.cancel_booking(booking_id, user["id"], at):
        raise ServiceError("Запись уже отменена.")
    actions.info("CANCEL_OWN id=%s %s %s %s", booking_id, who(user), booking["slot_date"], booking["slot_start"])
    return booking


def cancel_by_manager(manager: dict, floor: int, booking_id: int, at: datetime | None = None) -> dict:
    at = at or sched.now()
    _require_manager(manager, floor)
    booking = db.get_booking(booking_id)
    if not booking or booking["floor"] != floor or booking["status"] != "active":
        raise ServiceError("Запись не найдена или уже отменена.")
    _ensure_future(booking, at)
    if not db.cancel_booking(booking_id, manager["id"], at):
        raise ServiceError("Запись уже отменена.")
    actions.info("CANCEL_BY_MANAGER id=%s запись к.%s %s %s, отменил %s",
                 booking_id, booking["room"], booking["slot_date"], booking["slot_start"], who(manager))
    return booking


# --------------------------------------------------------------------------- #
#  Закрытие записи
# --------------------------------------------------------------------------- #
def count_affected(floor: int, date_from: date, date_to: date, time_from: time, time_to: time,
                   at: datetime | None = None) -> int:
    return db.count_bookings_in_period(floor, date_from, date_to, time_from, time_to, at or sched.now())


def close_period(manager: dict, floor: int, date_from: date, date_to: date, time_from: time, time_to: time,
                 reason: str | None, at: datetime | None = None) -> tuple[int, list[dict]]:
    at = at or sched.now()
    _require_manager(manager, floor)
    today = at.date()
    if date_to < today:
        raise ServiceError("Нельзя закрыть запись на прошедшие даты.")
    if time_from >= time_to:
        raise ServiceError("Время начала должно быть раньше времени конца.")
    date_from = max(date_from, today)
    closure_id, cancelled = db.close_period(floor, date_from, date_to, time_from, time_to, reason, manager["id"], at)
    actions.info("CLOSE id=%s этаж=%s %s..%s %s-%s причина=%r, отменено записей: %d, закрыл %s",
                 closure_id, floor, date_from, date_to, time_from, time_to, reason, len(cancelled), who(manager))
    return closure_id, cancelled


def reopen(manager: dict, floor: int, closure_id: int) -> dict:
    _require_manager(manager, floor)
    closure = db.get_closure(closure_id)
    if not closure or closure["floor"] != floor:
        raise ServiceError("Закрытие не найдено — возможно, его уже удалили.")
    db.delete_closure(closure_id)
    actions.info("REOPEN id=%s этаж=%s %s..%s, открыл %s",
                 closure_id, floor, closure["date_from"], closure["date_to"], who(manager))
    return closure


# --------------------------------------------------------------------------- #
#  Исключение комнат и жильцов
# --------------------------------------------------------------------------- #
def ban_room(manager: dict, floor: int, room: int, at: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Возвращает (жильцы комнаты, отменённые записи)."""
    at = at or sched.now()
    _require_manager(manager, floor)
    if room // 100 != floor:
        raise ServiceError(f"Комната {room} не на {floor} этаже.")
    if manager.get("room") == room:
        raise ServiceError("Нельзя исключить свою комнату.")
    if not db.add_room_ban(room, floor, manager["id"], at):
        raise ServiceError(f"Комната {room} уже исключена.")
    cancelled = db.cancel_future_bookings_of_room(room, manager["id"], at)
    actions.info("BAN_ROOM к.%s этаж=%s, отменено записей: %d, исключил %s", room, floor, len(cancelled), who(manager))
    return db.users_in_room(room), cancelled


def unban_room(manager: dict, floor: int, room: int) -> list[dict]:
    _require_manager(manager, floor)
    if room // 100 != floor:
        raise ServiceError(f"Комната {room} не на {floor} этаже.")
    if not db.remove_room_ban(room):
        raise ServiceError(f"Комната {room} не была исключена.")
    actions.info("UNBAN_ROOM к.%s этаж=%s, вернул %s", room, floor, who(manager))
    return db.users_in_room(room)


def ban_user(manager: dict, floor: int, user_id: int, at: datetime | None = None) -> tuple[dict, list[dict]]:
    at = at or sched.now()
    _require_manager(manager, floor)
    target = db.get_user_by_id(user_id)
    if not target or target["floor"] != floor:
        raise ServiceError("Жилец не найден на этом этаже.")
    if target["id"] == manager["id"]:
        raise ServiceError("Нельзя исключить самого себя.")
    if target["is_banned"]:
        raise ServiceError("Этот жилец уже исключён.")
    db.set_user_banned(user_id, True, manager["id"], at)
    cancelled = db.cancel_future_bookings_of_user(user_id, manager["id"], at)
    actions.info("BAN_USER %s, отменено записей: %d, исключил %s", who(target), len(cancelled), who(manager))
    return target, cancelled


def unban_user(manager: dict, floor: int, user_id: int, at: datetime | None = None) -> dict:
    at = at or sched.now()
    _require_manager(manager, floor)
    target = db.get_user_by_id(user_id)
    if not target or target["floor"] != floor:
        raise ServiceError("Жилец не найден на этом этаже.")
    if not target["is_banned"]:
        raise ServiceError("Этот жилец не исключён.")
    db.set_user_banned(user_id, False, None, at)
    actions.info("UNBAN_USER %s, вернул %s", who(target), who(manager))
    return target


# --------------------------------------------------------------------------- #
#  Смена фамилии / комнаты — только через старосту
# --------------------------------------------------------------------------- #
def request_change(user: dict, surname: str, room: int, floor: int, wing: int | None,
                   at: datetime | None = None) -> dict:
    at = at or sched.now()
    if surname == user["surname"] and room == user["room"]:
        raise ServiceError("Данные не изменились — заявка не нужна.")
    if room != user["room"] and (user["is_banned"] or db.get_room_ban(user["room"])):
        raise ServiceError("🚫 Вы исключены из записи, поэтому сменить комнату нельзя. Обратитесь к старосте этажа.")
    request_id = db.create_change_request(user, surname, room, floor, wing, at)
    actions.info("CHANGE_REQUEST id=%s %s -> %s, к.%s", request_id, who(user), surname, room)
    return db.get_change_request(request_id)  # type: ignore[return-value]


def change_approvers(request: dict) -> list[int]:
    """telegram_id тех, кто должен рассмотреть заявку: старосты старого и нового этажа
    (кроме самого заявителя), а если их нет — администраторы."""
    ids: list[int] = []
    for floor in {request["old_floor"], request["new_floor"]}:
        if floor is None:
            continue
        for s in db.starostas(floor):
            if s["id"] != request["user_id"] and s["telegram_id"] not in ids:
                ids.append(s["telegram_id"])
    return ids or sorted(config.ADMIN_IDS)


def can_decide_change(manager: dict | None, request: dict) -> bool:
    if not manager:
        return False
    if is_admin(manager["telegram_id"]):
        return True
    if manager["id"] == request["user_id"]:
        return False
    return any(f is not None and can_manage(manager, f) for f in (request["old_floor"], request["new_floor"]))


def decide_change(manager: dict, request_id: int, approve: bool,
                  at: datetime | None = None) -> tuple[dict, RegistrationResult | None]:
    at = at or sched.now()
    request = db.get_change_request(request_id)
    if not request or request["status"] != "pending":
        raise ServiceError("Заявка уже обработана.")
    if not can_decide_change(manager, request):
        raise ServiceError("⛔ Эту заявку может рассмотреть только староста этажа.")
    if not db.close_change_request(request_id, "approved" if approve else "rejected", manager["id"], at):
        raise ServiceError("Заявка уже обработана.")
    if not approve:
        actions.info("CHANGE_REJECTED id=%s, отклонил %s", request_id, who(manager))
        return request, None

    current = db.get_user_by_id(request["user_id"])
    try:
        result = save_registration(current, request["telegram_id"], request["username"], request["new_surname"],
                                   request["new_room"], request["new_floor"], request["new_wing"], at)
    except ServiceError:
        db.execute("UPDATE change_requests SET status = 'rejected' WHERE id = %s", (request_id,))
        raise
    actions.info("CHANGE_APPROVED id=%s, принял %s", request_id, who(manager))
    return request, result
