"""Работа с MySQL (PyMySQL). Каждая операция — отдельное соединение и транзакция,
поэтому модуль безопасно вызывать из нескольких потоков (и из двух процессов: Telegram- и VK-бота)."""
from __future__ import annotations

import logging
import re
import secrets
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from . import config

log = logging.getLogger(__name__)

_TIME_FIELDS = ("slot_start", "slot_end", "time_from", "time_to")
_DATE_FIELDS = ("slot_date", "date_from", "date_to")

# Платформа -> колонка users с id пользователя в этом мессенджере
PLATFORM_COLUMNS = {"tg": "telegram_id", "vk": "vk_id"}


class BookingRejected(Exception):
    """Запись отклонена при проверке в транзакции. code: taken | user_limit | room_limit |
    closed | banned | room_banned | stale"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class LinkRejected(Exception):
    """Привязка второго мессенджера отклонена. code: invalid | already | occupied | room_mismatch | has_bookings"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- #
#  Соединение
# --------------------------------------------------------------------------- #
def _connect():
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
        init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
    )


@contextmanager
def transaction() -> Iterator[Any]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _to_time(value: Any) -> Any:
    """MySQL отдаёт TIME как timedelta — приводим к datetime.time."""
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds()) % 86400
        return time(seconds // 3600, seconds % 3600 // 60, seconds % 60)
    if isinstance(value, str):
        return time.fromisoformat(value)
    return value


def _to_date(value: Any) -> Any:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        return value.date()
    return value


def _fix(row: dict | None) -> dict | None:
    if row is None:
        return None
    for key in _TIME_FIELDS:
        if key in row and row[key] is not None:
            row[key] = _to_time(row[key])
    for key in _DATE_FIELDS:
        if key in row and row[key] is not None:
            row[key] = _to_date(row[key])
    return row


def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    with transaction() as cur:
        cur.execute(sql, params)
        return _fix(cur.fetchone())


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    with transaction() as cur:
        cur.execute(sql, params)
        return [_fix(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _naive(at: datetime) -> datetime:
    return at.replace(tzinfo=None, microsecond=0)


def _future_clause(alias: str = "b") -> str:
    return f"({alias}.slot_date > %s OR ({alias}.slot_date = %s AND {alias}.slot_start > %s))"


def _future_params(at: datetime) -> tuple:
    return at.date(), at.date(), at.time().replace(microsecond=0, tzinfo=None)


# --------------------------------------------------------------------------- #
#  Инициализация
# --------------------------------------------------------------------------- #
def init_db() -> None:
    sql = (config.BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    sql = re.sub(r"^\s*--.*$", "", sql, flags=re.MULTILINE)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with transaction() as cur:
        for statement in statements:
            cur.execute(statement)
        # Миграция со старой схемы, где номер комнаты был числом (SMALLINT) — теперь строка ('323а')
        for table, column, null in _ROOM_COLUMNS:
            row = _column_info(cur, table, column)
            if row and str(row["t"]).lower() != "varchar":
                cur.execute(f"ALTER TABLE {table} MODIFY {column} VARCHAR(5) {null}")
                log.info("Миграция: %s.%s -> VARCHAR(5)", table, column)
        _migrate_users_for_vk(cur)
    log.info("Схема БД проверена (%d таблиц)", len(statements))


_ROOM_COLUMNS = (
    ("users", "room", "NULL"),
    ("room_bans", "room", "NOT NULL"),
    ("bookings", "room", "NOT NULL"),
    ("change_requests", "old_room", "NULL"),
    ("change_requests", "new_room", "NOT NULL"),
)


def _column_info(cur: Any, table: str, column: str) -> dict | None:
    cur.execute(
        "SELECT DATA_TYPE AS t, IS_NULLABLE AS nullable FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s", (table, column))
    return cur.fetchone()


def _migrate_users_for_vk(cur: Any) -> None:
    """Схема до VK-бота: нет users.vk_id, telegram_id обязателен. Данные не меняются."""
    if _column_info(cur, "users", "vk_id") is None:
        cur.execute("ALTER TABLE users ADD COLUMN vk_id BIGINT NULL AFTER telegram_id")
        log.info("Миграция: добавлена колонка users.vk_id")
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND INDEX_NAME = 'uq_users_vk'")
    if not cur.fetchone()["n"]:
        cur.execute("ALTER TABLE users ADD UNIQUE KEY uq_users_vk (vk_id)")
        log.info("Миграция: уникальный индекс users.vk_id")
    row = _column_info(cur, "users", "telegram_id")
    if row and str(row["nullable"]).upper() == "NO":
        cur.execute("ALTER TABLE users MODIFY telegram_id BIGINT NULL")
        log.info("Миграция: users.telegram_id теперь может быть пустым")


def sync_admins(admin_ids: frozenset[int], vk_admin_ids: frozenset[int], at: datetime) -> None:
    """Создаёт аккаунты администраторов по telegram_id и vk_id из .env и снимает флаг с остальных.
    Вызывают оба бота при запуске — результат одинаковый."""
    now = _naive(at)
    with transaction() as cur:
        cur.execute("UPDATE users SET is_admin = 0 WHERE is_admin = 1")
        for platform, ids in (("tg", admin_ids), ("vk", vk_admin_ids)):
            column = PLATFORM_COLUMNS[platform]
            for ext_id in ids:
                cur.execute(f"SELECT id FROM users WHERE {column} = %s", (ext_id,))
                if not cur.fetchone():
                    try:
                        cur.execute(f"INSERT INTO users ({column}, is_admin, created_at, updated_at) "
                                    "VALUES (%s, 1, %s, %s)", (ext_id, now, now))
                        log.info("Создан аккаунт администратора %s=%s", column, ext_id)
                        continue
                    except pymysql.IntegrityError:  # второй бот создал его в ту же секунду
                        pass
                cur.execute(f"UPDATE users SET is_admin = 1 WHERE {column} = %s", (ext_id,))


# --------------------------------------------------------------------------- #
#  Пользователи
# --------------------------------------------------------------------------- #
def get_user(telegram_id: int) -> dict | None:
    return fetch_one("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))


def get_user_by_vk(vk_id: int) -> dict | None:
    return fetch_one("SELECT * FROM users WHERE vk_id = %s", (vk_id,))


def get_account(platform: str, ext_id: int) -> dict | None:
    return fetch_one(f"SELECT * FROM users WHERE {PLATFORM_COLUMNS[platform]} = %s", (ext_id,))


def get_user_by_id(user_id: int) -> dict | None:
    return fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))


def admins() -> list[dict]:
    """Аккаунты администраторов (флаг ставит sync_admins по ADMIN_IDS и VK_ADMIN_IDS)."""
    return fetch_all("SELECT * FROM users WHERE is_admin = 1 ORDER BY id")


def save_registration(user_id: int | None, platform: str | None, ext_id: int | None, username: str | None,
                      surname: str, room: str, floor: int, wing: int | None, at: datetime) -> dict:
    """Сохраняет фамилию и комнату. user_id — обновить этого пользователя, иначе найти или создать
    по (platform, ext_id). username (Telegram) меняется, только если platform == 'tg'."""
    now = _naive(at)
    created = False
    with transaction() as cur:
        if user_id is None:
            column = PLATFORM_COLUMNS[platform or ""]
            cur.execute(f"SELECT id FROM users WHERE {column} = %s FOR UPDATE", (ext_id,))
            row = cur.fetchone()
            if row is not None:
                user_id = int(row["id"])
            else:
                cur.execute(
                    f"INSERT INTO users ({column}, username, surname, room, floor, wing, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (ext_id, username if platform == "tg" else None, surname, room, floor, wing, now, now),
                )
                user_id = int(cur.lastrowid)
                created = True
        if not created:
            sets = "surname = %s, room = %s, floor = %s, wing = %s, updated_at = %s"
            params: list = [surname, room, floor, wing, now]
            if platform == "tg":
                sets, params = "username = %s, " + sets, [username, *params]
            cur.execute(f"UPDATE users SET {sets} WHERE id = %s", (*params, user_id))
    return get_user_by_id(user_id)  # type: ignore[return-value]


def set_starosta_pending(user_id: int, pending: bool, at: datetime) -> None:
    execute("UPDATE users SET starosta_pending = %s, updated_at = %s WHERE id = %s",
            (1 if pending else 0, _naive(at), user_id))


def set_role(user_id: int, role: str, at: datetime) -> None:
    execute("UPDATE users SET role = %s, starosta_pending = 0, updated_at = %s WHERE id = %s",
            (role, _naive(at), user_id))


def pending_starosta_requests() -> list[dict]:
    return fetch_all("SELECT * FROM users WHERE starosta_pending = 1 ORDER BY floor, room")


def starostas(floor: int | None = None) -> list[dict]:
    if floor is None:
        return fetch_all("SELECT * FROM users WHERE role = 'starosta' ORDER BY floor, room")
    return fetch_all("SELECT * FROM users WHERE role = 'starosta' AND floor = %s ORDER BY room", (floor,))


def users_on_floor(floor: int) -> list[dict]:
    return fetch_all("SELECT * FROM users WHERE floor = %s ORDER BY room, surname", (floor,))


def users_in_room(room: str) -> list[dict]:
    return fetch_all("SELECT * FROM users WHERE room = %s ORDER BY surname", (room,))


def set_user_banned(user_id: int, banned: bool, by_user_id: int | None, at: datetime) -> None:
    if banned:
        execute("UPDATE users SET is_banned = 1, banned_by = %s, banned_at = %s, updated_at = %s WHERE id = %s",
                (by_user_id, _naive(at), _naive(at), user_id))
    else:
        execute("UPDATE users SET is_banned = 0, banned_by = NULL, banned_at = NULL, updated_at = %s WHERE id = %s",
                (_naive(at), user_id))


def banned_users(floor: int) -> list[dict]:
    return fetch_all("SELECT * FROM users WHERE floor = %s AND is_banned = 1 ORDER BY room, surname", (floor,))


# --------------------------------------------------------------------------- #
#  Баны комнат
# --------------------------------------------------------------------------- #
def get_room_ban(room: str) -> dict | None:
    return fetch_one("SELECT * FROM room_bans WHERE room = %s", (room,))


def add_room_ban(room: str, floor: int, by_user_id: int, at: datetime) -> bool:
    with transaction() as cur:
        cur.execute("SELECT room FROM room_bans WHERE room = %s FOR UPDATE", (room,))
        if cur.fetchone():
            return False
        cur.execute("INSERT INTO room_bans (room, floor, banned_by, created_at) VALUES (%s, %s, %s, %s)",
                    (room, floor, by_user_id, _naive(at)))
        return True


def remove_room_ban(room: str) -> bool:
    return execute("DELETE FROM room_bans WHERE room = %s", (room,)) > 0


def room_bans(floor: int) -> list[dict]:
    return fetch_all("SELECT * FROM room_bans WHERE floor = %s ORDER BY room", (floor,))


# --------------------------------------------------------------------------- #
#  Записи
# --------------------------------------------------------------------------- #
_BOOKING_SELECT = (
    "SELECT b.*, u.surname, u.telegram_id, u.vk_id FROM bookings b JOIN users u ON u.id = b.user_id "
)


def get_booking(booking_id: int) -> dict | None:
    return fetch_one(_BOOKING_SELECT + "WHERE b.id = %s", (booking_id,))


def bookings_between(floor: int, date_from: date, date_to: date) -> list[dict]:
    return fetch_all(
        _BOOKING_SELECT + "WHERE b.floor = %s AND b.slot_date BETWEEN %s AND %s AND b.status = 'active' "
        "ORDER BY b.slot_date, b.slot_start",
        (floor, date_from, date_to),
    )


def user_upcoming_bookings(user_id: int, at: datetime) -> list[dict]:
    return fetch_all(
        _BOOKING_SELECT + "WHERE b.user_id = %s AND b.status = 'active' AND " + _future_clause() +
        " ORDER BY b.slot_date, b.slot_start",
        (user_id, *_future_params(at)),
    )


def count_user_bookings_on(user_id: int, day: date) -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM bookings WHERE user_id = %s AND slot_date = %s AND status = 'active'",
                    (user_id, day))
    return int(row["n"]) if row else 0


def count_room_bookings_on(room: str, day: date) -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM bookings WHERE room = %s AND slot_date = %s AND status = 'active'",
                    (room, day))
    return int(row["n"]) if row else 0


def create_booking(*, user_id: int, floor: int, room: str, slot_date: date, slot_start: time, slot_end: time,
                   user_limit: int, room_limit: int | None, at: datetime) -> int:
    """Создаёт запись, проверяя все ограничения внутри одной транзакции."""
    with transaction() as cur:
        # Блокируем строки жильцов комнаты: параллельные записи одной комнаты/человека идут по очереди
        cur.execute("SELECT id, is_banned FROM users WHERE room = %s FOR UPDATE", (room,))
        me = next((r for r in cur.fetchall() if r["id"] == user_id), None)
        if me is None:
            raise BookingRejected("stale")
        if me["is_banned"]:
            raise BookingRejected("banned")

        cur.execute("SELECT room FROM room_bans WHERE room = %s", (room,))
        if cur.fetchone():
            raise BookingRejected("room_banned")

        cur.execute(
            "SELECT COUNT(*) AS n FROM closures WHERE floor = %s AND date_from <= %s AND date_to >= %s "
            "AND time_from < %s AND time_to > %s",
            (floor, slot_date, slot_date, slot_end, slot_start),
        )
        if cur.fetchone()["n"]:
            raise BookingRejected("closed")

        cur.execute("SELECT COUNT(*) AS n FROM bookings WHERE user_id = %s AND slot_date = %s AND status = 'active'",
                    (user_id, slot_date))
        if cur.fetchone()["n"] >= user_limit:
            raise BookingRejected("user_limit")

        if room_limit is not None:
            cur.execute("SELECT COUNT(*) AS n FROM bookings WHERE room = %s AND slot_date = %s AND status = 'active'",
                        (room, slot_date))
            if cur.fetchone()["n"] >= room_limit:
                raise BookingRejected("room_limit")

        try:
            cur.execute(
                "INSERT INTO bookings (user_id, floor, room, slot_date, slot_start, slot_end, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)",
                (user_id, floor, room, slot_date, slot_start, slot_end, _naive(at)),
            )
        except pymysql.IntegrityError as exc:  # сработал уникальный индекс — время уже заняли
            raise BookingRejected("taken") from exc
        return int(cur.lastrowid)


def cancel_booking(booking_id: int, by_user_id: int, at: datetime) -> bool:
    return execute(
        "UPDATE bookings SET status = 'cancelled', cancelled_at = %s, cancelled_by = %s "
        "WHERE id = %s AND status = 'active'",
        (_naive(at), by_user_id, booking_id),
    ) > 0


def _cancel_where(where: str, params: tuple, by_user_id: int, at: datetime) -> list[dict]:
    """Отменяет все будущие активные записи по условию и возвращает их (для уведомлений)."""
    with transaction() as cur:
        cur.execute(
            _BOOKING_SELECT + "WHERE b.status = 'active' AND " + where + " AND " + _future_clause() +
            " ORDER BY b.slot_date, b.slot_start FOR UPDATE",
            (*params, *_future_params(at)),
        )
        rows = [_fix(r) for r in cur.fetchall()]
        for row in rows:
            cur.execute(
                "UPDATE bookings SET status = 'cancelled', cancelled_at = %s, cancelled_by = %s WHERE id = %s",
                (_naive(at), by_user_id, row["id"]),
            )
        return rows  # type: ignore[return-value]


def cancel_future_bookings_of_user(user_id: int, by_user_id: int, at: datetime) -> list[dict]:
    return _cancel_where("b.user_id = %s", (user_id,), by_user_id, at)


def cancel_future_bookings_of_room(room: str, by_user_id: int, at: datetime) -> list[dict]:
    return _cancel_where("b.room = %s", (room,), by_user_id, at)


def _period_where() -> str:
    return "b.floor = %s AND b.slot_date BETWEEN %s AND %s AND b.slot_start < %s AND b.slot_end > %s"


def count_bookings_in_period(floor: int, date_from: date, date_to: date, time_from: time, time_to: time,
                             at: datetime) -> int:
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM bookings b WHERE b.status = 'active' AND " + _period_where() +
        " AND " + _future_clause(),
        (floor, date_from, date_to, time_to, time_from, *_future_params(at)),
    )
    return int(row["n"]) if row else 0


# --------------------------------------------------------------------------- #
#  Закрытие записи старостой
# --------------------------------------------------------------------------- #
def close_period(floor: int, date_from: date, date_to: date, time_from: time, time_to: time,
                 reason: str | None, by_user_id: int, at: datetime) -> tuple[int, list[dict]]:
    """Создаёт закрытие и в той же транзакции отменяет попавшие в него будущие записи."""
    with transaction() as cur:
        cur.execute(
            "INSERT INTO closures (floor, date_from, date_to, time_from, time_to, reason, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (floor, date_from, date_to, time_from, time_to, reason, by_user_id, _naive(at)),
        )
        closure_id = int(cur.lastrowid)
        cur.execute(
            _BOOKING_SELECT + "WHERE b.status = 'active' AND " + _period_where() + " AND " + _future_clause() +
            " ORDER BY b.slot_date, b.slot_start FOR UPDATE",
            (floor, date_from, date_to, time_to, time_from, *_future_params(at)),
        )
        cancelled = [_fix(r) for r in cur.fetchall()]
        for row in cancelled:
            cur.execute(
                "UPDATE bookings SET status = 'cancelled', cancelled_at = %s, cancelled_by = %s WHERE id = %s",
                (_naive(at), by_user_id, row["id"]),
            )
        return closure_id, cancelled  # type: ignore[return-value]


def closures_between(floor: int, date_from: date, date_to: date) -> list[dict]:
    return fetch_all(
        "SELECT * FROM closures WHERE floor = %s AND date_to >= %s AND date_from <= %s ORDER BY date_from, time_from",
        (floor, date_from, date_to),
    )


def active_closures(floor: int, today: date) -> list[dict]:
    return fetch_all("SELECT * FROM closures WHERE floor = %s AND date_to >= %s ORDER BY date_from, time_from",
                     (floor, today))


def get_closure(closure_id: int) -> dict | None:
    return fetch_one("SELECT * FROM closures WHERE id = %s", (closure_id,))


def delete_closure(closure_id: int) -> bool:
    return execute("DELETE FROM closures WHERE id = %s", (closure_id,)) > 0


# --------------------------------------------------------------------------- #
#  Заявки на смену фамилии / комнаты
# --------------------------------------------------------------------------- #
_CHANGE_SELECT = ("SELECT c.*, u.telegram_id, u.vk_id, u.username FROM change_requests c "
                  "JOIN users u ON u.id = c.user_id ")


def create_change_request(user: dict, new_surname: str, new_room: str, new_floor: int, new_wing: int | None,
                          at: datetime) -> int:
    """Создаёт заявку; прежняя незакрытая заявка этого человека отменяется."""
    with transaction() as cur:
        cur.execute("UPDATE change_requests SET status = 'cancelled', decided_at = %s "
                    "WHERE user_id = %s AND status = 'pending'", (_naive(at), user["id"]))
        cur.execute(
            "INSERT INTO change_requests (user_id, old_surname, old_room, old_floor, new_surname, new_room, "
            "new_floor, new_wing, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user["id"], user["surname"], user["room"], user["floor"], new_surname, new_room, new_floor,
             new_wing, _naive(at)),
        )
        return int(cur.lastrowid)


def get_change_request(request_id: int) -> dict | None:
    return fetch_one(_CHANGE_SELECT + "WHERE c.id = %s", (request_id,))


def pending_change_requests(floor: int | None = None) -> list[dict]:
    if floor is None:
        return fetch_all(_CHANGE_SELECT + "WHERE c.status = 'pending' ORDER BY c.created_at")
    return fetch_all(_CHANGE_SELECT + "WHERE c.status = 'pending' AND (c.old_floor = %s OR c.new_floor = %s) "
                     "ORDER BY c.created_at", (floor, floor))


def user_pending_change(user_id: int) -> dict | None:
    return fetch_one(_CHANGE_SELECT + "WHERE c.user_id = %s AND c.status = 'pending' ORDER BY c.id DESC LIMIT 1",
                     (user_id,))


def close_change_request(request_id: int, status: str, by_user_id: int | None, at: datetime) -> bool:
    """Переводит заявку из pending в status. False — её уже обработал кто-то другой."""
    return execute("UPDATE change_requests SET status = %s, decided_by = %s, decided_at = %s "
                   "WHERE id = %s AND status = 'pending'",
                   (status, by_user_id, _naive(at), request_id)) > 0


# --------------------------------------------------------------------------- #
#  Привязка второго мессенджера
# --------------------------------------------------------------------------- #
def create_link_code(user_id: int, platform: str, at: datetime, ttl_minutes: int = 15) -> str:
    """Одноразовый 6-значный код: его нужно ввести в боте платформы platform. Прежние коды человека сгорают."""
    now = _naive(at)
    for _ in range(20):
        code = f"{secrets.randbelow(10 ** 6):06d}"
        with transaction() as cur:
            cur.execute("DELETE FROM link_codes WHERE expires_at < %s OR user_id = %s", (now, user_id))
            try:
                cur.execute("INSERT INTO link_codes (code, user_id, platform, expires_at, created_at) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (code, user_id, platform, now + timedelta(minutes=ttl_minutes), now))
            except pymysql.IntegrityError:  # такой код уже выдан кому-то другому
                continue
            return code
    raise RuntimeError("Не удалось подобрать свободный код привязки")


def link_account(code: str, platform: str, ext_id: int, at: datetime) -> tuple[dict, dict | None]:
    """Привязывает аккаунт (platform, ext_id) к пользователю, выдавшему код.

    Если у этого аккаунта уже был свой пользователь в БД, он вливается в основного: та же комната и нет
    предстоящих записей — иначе LinkRejected. История записей и заявок переходит к основному, исключение
    и роль старосты сохраняются. Возвращает (основной пользователь до привязки, влитый пользователь или None)."""
    now = _naive(at)
    column = PLATFORM_COLUMNS[platform]
    with transaction() as cur:
        cur.execute("SELECT * FROM link_codes WHERE code = %s FOR UPDATE", (code,))
        link = cur.fetchone()
        if not link or link["platform"] != platform or link["expires_at"] < now:
            raise LinkRejected("invalid")
        cur.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (link["user_id"],))
        main = cur.fetchone()
        if main is None:
            raise LinkRejected("invalid")
        if main[column] is not None:
            raise LinkRejected("already" if main[column] == ext_id else "occupied")

        cur.execute(f"SELECT * FROM users WHERE {column} = %s FOR UPDATE", (ext_id,))
        other = cur.fetchone()
        if other is not None:
            if other["room"] and other["room"] != main["room"]:
                raise LinkRejected("room_mismatch")
            cur.execute("SELECT COUNT(*) AS n FROM bookings b WHERE b.user_id = %s AND b.status = 'active' AND "
                        + _future_clause(), (other["id"], *_future_params(at)))
            if cur.fetchone()["n"]:
                raise LinkRejected("has_bookings")
            _merge_user(cur, main, other, now)

        cur.execute(f"UPDATE users SET {column} = %s, updated_at = %s WHERE id = %s", (ext_id, now, main["id"]))
        cur.execute("DELETE FROM link_codes WHERE code = %s", (code,))
    return main, other


def _merge_user(cur: Any, main: dict, other: dict, now: datetime) -> None:
    """Переносит всё, что связано с other, на main и удаляет other (внутри транзакции link_account)."""
    old, new = other["id"], main["id"]
    cur.execute("UPDATE change_requests SET status = 'cancelled', decided_at = %s "
                "WHERE user_id = %s AND status = 'pending'", (now, old))
    for table, col in (("bookings", "user_id"), ("bookings", "cancelled_by"), ("change_requests", "user_id"),
                       ("change_requests", "decided_by"), ("closures", "created_by"), ("room_bans", "banned_by"),
                       ("users", "banned_by")):
        cur.execute(f"UPDATE {table} SET {col} = %s WHERE {col} = %s", (new, old))
    sets, params = ["updated_at = %s"], [now]
    if other["is_banned"] and not main["is_banned"]:
        sets.append("is_banned = 1, banned_by = %s, banned_at = %s")
        params += [other["banned_by"] if other["banned_by"] != old else new, other["banned_at"]]
    if other["role"] == "starosta" and main["role"] != "starosta" and other["floor"] == main["floor"]:
        sets.append("role = 'starosta', starosta_pending = 0")
    elif other["starosta_pending"] and main["role"] != "starosta":
        sets.append("starosta_pending = 1")
    if other["is_admin"]:
        sets.append("is_admin = 1")
    cur.execute("DELETE FROM link_codes WHERE user_id = %s", (old,))
    cur.execute("DELETE FROM users WHERE id = %s", (old,))
    cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = %s", (*params, new))


# --------------------------------------------------------------------------- #
#  Статистика
# --------------------------------------------------------------------------- #
def stats(today: date) -> dict:
    with transaction() as cur:
        cur.execute("SELECT floor, COUNT(*) AS n FROM users WHERE room IS NOT NULL GROUP BY floor ORDER BY floor")
        by_floor = {int(r["floor"]): int(r["n"]) for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'starosta'")
        n_starostas = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE starosta_pending = 1")
        n_pending = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE is_banned = 1")
        n_banned_users = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM room_bans")
        n_banned_rooms = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM bookings WHERE status = 'active' AND slot_date >= %s", (today,))
        n_upcoming = int(cur.fetchone()["n"])
    return {
        "by_floor": by_floor,
        "users": sum(by_floor.values()),
        "starostas": n_starostas,
        "pending": n_pending,
        "banned_users": n_banned_users,
        "banned_rooms": n_banned_rooms,
        "upcoming": n_upcoming,
    }
