"""Правила стирки: сетка времени, недели записи, этажи и крылья 5 этажа."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import config

WEEKDAYS_FULL = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
WEEKDAYS_ON = ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье")

FULL_DAY_FROM = time(0, 0)
FULL_DAY_TO = time(23, 59, 59)

FIRST_BOOKABLE_FLOOR = 2
SPECIAL_FLOOR = 5


# --------------------------------------------------------------------------- #
#  Сетка времени
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Slot:
    start: time
    end: time

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"

    @property
    def code(self) -> str:
        return f"{self.start:%H%M}"


def _make_slots(first: time, step_min: int, duration_min: int, count: int) -> tuple[Slot, ...]:
    base = datetime.combine(date(2000, 1, 1), first)
    return tuple(
        Slot((base + timedelta(minutes=step_min * i)).time(),
             (base + timedelta(minutes=step_min * i + duration_min)).time())
        for i in range(count)
    )


# Обычные этажи: стирка 45 минут + 1 час между стирками → 06:30–07:15, 08:15–09:00 … 22:15–23:00
REGULAR_SLOTS = _make_slots(time(6, 30), step_min=105, duration_min=45, count=10)
# 5 этаж: 06:30–07:24, 08:30–09:24 … 20:30–21:24 и последняя 22:00–22:54
FLOOR5_SLOTS = _make_slots(time(6, 30), step_min=120, duration_min=54, count=8) + (Slot(time(22, 0), time(22, 54)),)


@dataclass(frozen=True)
class FloorRules:
    slots: tuple[Slot, ...]
    anchor_weekday: int            # день открытия записи на новую неделю (0 = пн, 6 = вс)
    open_time: time                # время открытия записи
    user_daily_limit: int          # сколько раз в день может записаться один человек
    room_daily_limit: int | None   # сколько раз в день может стираться одна комната
    wing_weekdays: tuple[tuple[int, tuple[int, ...]], ...] | None = None  # крыло -> дни недели

    def find_slot(self, start: time) -> Slot | None:
        return next((s for s in self.slots if s.start == start), None)

    def weekdays_for_wing(self, wing: int | None) -> tuple[int, ...] | None:
        if self.wing_weekdays is None or wing is None:
            return None
        return dict(self.wing_weekdays).get(wing, ())

    def wing_for_weekday(self, weekday: int) -> int | None:
        for wing, days in self.wing_weekdays or ():
            if weekday in days:
                return wing
        return None


REGULAR_RULES = FloorRules(
    slots=REGULAR_SLOTS,
    anchor_weekday=0,                 # запись открывается в понедельник, стирка вт–вс
    open_time=config.WEEK_OPEN_TIME,
    user_daily_limit=2,
    room_daily_limit=None,
)

FLOOR5_RULES = FloorRules(
    slots=FLOOR5_SLOTS,
    anchor_weekday=6,                 # запись открывается в воскресенье, стирка пн–сб
    open_time=config.FLOOR5_WEEK_OPEN_TIME,
    user_daily_limit=1,               # комната — 1 раз в день, значит и человек не чаще
    room_daily_limit=1,
    wing_weekdays=((1, (1, 3, 5)),    # 1 крыло: вт, чт, сб
                   (2, (0, 2, 4))),   # 2 крыло: пн, ср, пт
)


def rules_for(floor: int) -> FloorRules:
    return FLOOR5_RULES if floor == SPECIAL_FLOOR else REGULAR_RULES


def bookable_floors() -> list[int]:
    return list(range(FIRST_BOOKABLE_FLOOR, config.MAX_FLOOR + 1))


def floor_is_bookable(floor: int | None) -> bool:
    return floor is not None and FIRST_BOOKABLE_FLOOR <= floor <= config.MAX_FLOOR


# --------------------------------------------------------------------------- #
#  Комнаты и крылья
# --------------------------------------------------------------------------- #
ROOM_RE = re.compile(r"(\d)(\d{2})\s*([а-яё]?)", re.IGNORECASE)
_LATIN_TO_CYR = str.maketrans("abvgdezikmnoprstufhc", "абвгдезикмнопрстуфхс")


def room_floor(room: str) -> int:
    return int(str(room)[0])


def room_number(room: str) -> int:
    return int(str(room)[1:3])


def wing_of(room: str) -> int | None:
    """1 крыло: 511–527, 2 крыло: 501–510 и 528–536 (буква в номере на крыло не влияет)."""
    if room_floor(room) != SPECIAL_FLOOR:
        return None
    number = room_number(room)
    if 11 <= number <= 27:
        return 1
    if 1 <= number <= 10 or 28 <= number <= 36:
        return 2
    return None


class RoomError(ValueError):
    pass


def parse_room(text: str) -> tuple[str, int, int | None]:
    """'312' -> ('312', 3, None); '323А' -> ('323а', 3, None); '515' -> ('515', 5, 1).

    Номер — 3 цифры (этаж + комната на этаже), после них может стоять одна буква: 323а.
    Комната с буквой — отдельная комната (свои баны и лимиты), этаж и крыло — как у номера без буквы.
    """
    raw = (text or "").strip().lower().translate(_LATIN_TO_CYR)
    m = ROOM_RE.fullmatch(raw)
    if not m:
        raise RoomError("Номер комнаты — это 3 цифры, например 312 (можно с буквой: 323а).")
    floor, number, letter = int(m.group(1)), int(m.group(2)), m.group(3)
    room = f"{floor}{number:02d}{letter}"
    if floor == 0 or number == 0:
        raise RoomError("Такой комнаты нет: первая цифра — этаж, две следующие — номер комнаты (01, 02, …).")
    if floor == 1:
        raise RoomError("Для 1 этажа запись на стирку не проводится.")
    if floor > config.MAX_FLOOR:
        raise RoomError(f"В общежитии {config.MAX_FLOOR} этажей — проверьте номер комнаты.")
    if letter and room not in config.LETTER_ROOMS:
        known = ", ".join(sorted(config.LETTER_ROOMS)) or "нет"
        raise RoomError(f"Комнаты {room} нет. Комнаты с буквой в общежитии: {known}.")
    if floor == SPECIAL_FLOOR:
        wing = wing_of(room)
        if wing is None:
            raise RoomError("На 5 этаже комнаты с 501 по 536.")
        return room, floor, wing
    max_room = max_room_on(floor)
    if number > max_room:
        extra = "".join(f" и {r}" for r in sorted(config.LETTER_ROOMS) if room_floor(r) == floor)
        raise RoomError(f"На {floor} этаже комнаты с {floor}01 по {floor}{max_room:02d}{extra}.")
    return room, floor, None


def max_room_on(floor: int) -> int:
    return config.FLOOR_MAX_ROOMS.get(floor, config.MAX_ROOM_ON_FLOOR)


def room_on_floor(text: str, floor: int) -> str:
    room, room_floor_, _ = parse_room(text)
    if room_floor_ != floor:
        raise RoomError(f"Комната {room} не на {floor} этаже.")
    return room


# --------------------------------------------------------------------------- #
#  Время и недели записи
# --------------------------------------------------------------------------- #
def now() -> datetime:
    return datetime.now(config.TIMEZONE)


def slot_dt(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=config.TIMEZONE)


@dataclass
class Week:
    anchor: date            # день открытия записи (пн для обычных этажей, вс для 5 этажа)
    days: list[date]        # дни стирки, доступные этому крылу/этажу
    opened_at: datetime
    next_open_at: datetime  # когда откроется запись на следующую неделю


def current_week(floor: int, wing: int | None = None, at: datetime | None = None) -> Week:
    """Неделя, на которую сейчас открыта запись.

    Обычные этажи: запись открывается в пн в 15:00 на вт–вс этой недели.
    5 этаж: запись открывается в вс в 15:00 на пн–сб следующей недели; каждому крылу — свои дни.
    """
    rules = rules_for(floor)
    at = at or now()
    today = at.date()
    anchor = today - timedelta(days=(today.weekday() - rules.anchor_weekday) % 7)
    if at < slot_dt(anchor, rules.open_time):
        anchor -= timedelta(days=7)
    days = [anchor + timedelta(days=i) for i in range(1, 7)]
    allowed = rules.weekdays_for_wing(wing)
    if allowed is not None:
        days = [d for d in days if d.weekday() in allowed]
    return Week(
        anchor=anchor,
        days=days,
        opened_at=slot_dt(anchor, rules.open_time),
        next_open_at=slot_dt(anchor + timedelta(days=7), rules.open_time),
    )


def closure_covers(closure: dict, day: date, slot: Slot) -> bool:
    return (closure["date_from"] <= day <= closure["date_to"]
            and closure["time_from"] < slot.end and closure["time_to"] > slot.start)


def is_full_day(time_from: time, time_to: time) -> bool:
    return time_from <= FULL_DAY_FROM and time_to >= time(23, 59)


# --------------------------------------------------------------------------- #
#  Форматирование
# --------------------------------------------------------------------------- #
def fmt_date(d: date) -> str:
    return d.strftime("%d.%m")


def fmt_day_short(d: date) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}"


def fmt_day_full(d: date) -> str:
    return f"{WEEKDAYS_FULL[d.weekday()]}, {d:%d.%m.%Y}"


def fmt_moment(dt: datetime) -> str:
    """'в понедельник, 06.10 в 15:00'"""
    return f"{WEEKDAYS_ON[dt.weekday()]}, {dt:%d.%m} в {dt:%H:%M}"


def date_code(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_date_code(code: str) -> date:
    return datetime.strptime(code, "%Y%m%d").date()


def parse_time_code(code: str) -> time:
    return datetime.strptime(code, "%H%M").time()


# --------------------------------------------------------------------------- #
#  Ввод дат старостой
# --------------------------------------------------------------------------- #
class DateInputError(ValueError):
    pass


_DATE_RE = r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?"


def _build_date(day: str, month: str, year: str | None, today: date) -> date:
    try:
        if year:
            y = int(year) + (2000 if len(year) == 2 else 0)
            return date(y, int(month), int(day))
        candidate = date(today.year, int(month), int(day))
        if candidate < today - timedelta(days=60):  # «05.01», введённое в декабре, — это следующий год
            candidate = date(today.year + 1, int(month), int(day))
        return candidate
    except ValueError as exc:
        raise DateInputError("Такой даты не существует.") from exc


def parse_date_input(text: str, today: date) -> date:
    m = re.fullmatch(_DATE_RE, (text or "").strip())
    if not m:
        raise DateInputError("Введите дату в формате ДД.ММ, например 14.10")
    day, month, year = m.groups()
    return _build_date(day, month, year, today)


def parse_period_input(text: str, today: date) -> tuple[date, date]:
    raw = (text or "").strip()
    m = re.fullmatch(_DATE_RE + r"\s*(?:-|–|—|по)\s*" + _DATE_RE, raw)
    if m:
        g = m.groups()
        start = _build_date(g[0], g[1], g[2], today)
        end = _build_date(g[3], g[4], g[5], today)
        if end < start and not g[5]:  # «28.12-05.01» — период через Новый год
            wrapped = _build_date(g[3], g[4], str(start.year + 1), today)
            if (wrapped - start).days <= 92:
                end = wrapped
    else:
        try:
            start = end = parse_date_input(raw, today)
        except DateInputError:
            raise DateInputError("Введите дату (14.10) или период (14.10-20.10).") from None
    if end < start:
        raise DateInputError("Конец периода раньше начала.")
    if (end - start).days > 366:
        raise DateInputError("Период не может быть длиннее года.")
    return start, end
