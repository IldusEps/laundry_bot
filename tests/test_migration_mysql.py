"""Миграция базы в db.init_db() на настоящем MySQL / MariaDB: пустая база, старая схема, повторный запуск.

Пропускается, если не задан TEST_MYSQL_HOST (см. tests/mysql_db.py).
"""
import re
import unittest
from datetime import datetime
from pathlib import Path

import pymysql

from laundry import config, db, services

from . import mysql_db

OLD_SCHEMA = Path(__file__).with_name("old_schema.sql")
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=config.TIMEZONE)


def _statements(sql: str) -> list[str]:
    sql = re.sub(r"^\s*--.*$", "", sql, flags=re.MULTILINE)
    return [s.strip() for s in sql.split(";") if s.strip()]


@unittest.skipUnless(mysql_db.enabled(), "нужен MySQL/MariaDB: задайте TEST_MYSQL_HOST (см. tests/mysql_db.py)")
class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.name = mysql_db.create_database()
        self.patcher = mysql_db.use(self.name)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        mysql_db.drop_database(self.name)

    # ------------------------------------------------------------------ #
    def columns(self, table: str) -> dict[str, dict]:
        rows = db.fetch_all(
            "SELECT COLUMN_NAME AS name, DATA_TYPE AS type, IS_NULLABLE AS nullable FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
        return {r["name"]: r for r in rows}

    def unique_indexes(self, table: str) -> set[str]:
        rows = db.fetch_all("SELECT DISTINCT INDEX_NAME AS name FROM information_schema.STATISTICS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND NON_UNIQUE = 0", (table,))
        return {r["name"] for r in rows}

    def tables(self) -> set[str]:
        rows = db.fetch_all("SELECT TABLE_NAME AS name FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
        return {r["name"] for r in rows}

    def assert_new_schema(self):
        self.assertEqual({"users", "room_bans", "bookings", "closures", "change_requests", "link_codes"}, self.tables())
        users = self.columns("users")
        self.assertEqual(("bigint", "YES"), (users["telegram_id"]["type"], users["telegram_id"]["nullable"]))
        self.assertEqual(("bigint", "YES"), (users["vk_id"]["type"], users["vk_id"]["nullable"]))
        self.assertTrue({"uq_users_telegram", "uq_users_vk"} <= self.unique_indexes("users"))
        for table, column, _ in db._ROOM_COLUMNS:
            self.assertEqual("varchar", self.columns(table)[column]["type"], f"{table}.{column}")

    def load_old_schema(self, numeric_rooms: bool = False):
        sql = OLD_SCHEMA.read_text(encoding="utf-8")
        if numeric_rooms:   # совсем старая схема: номер комнаты — число
            sql = re.sub(r"\b(room|old_room|new_room)(\s+)VARCHAR\(5\)", r"\1\2SMALLINT UNSIGNED", sql)
        with db.transaction() as cur:
            for statement in _statements(sql):
                cur.execute(statement)

    def seed_old_data(self, room=312):
        """Данные «как у жильцов сейчас»: два жильца, староста, записи, бан, закрытие, заявка."""
        with db.transaction() as cur:
            cur.execute("INSERT INTO users (telegram_id, username, surname, room, floor, role, is_admin, created_at, "
                        "updated_at) VALUES (1000, 'admin', NULL, NULL, NULL, 'resident', 1, NOW(), NOW())")
            cur.execute("INSERT INTO users (telegram_id, username, surname, room, floor, role, created_at, updated_at) "
                        "VALUES (2001, 'boss', 'Старостин', %s, 3, 'starosta', NOW(), NOW())", (301,))
            cur.execute("INSERT INTO users (telegram_id, username, surname, room, floor, is_banned, created_at, "
                        "updated_at) VALUES (2002, 'ivanov', 'Иванов', %s, 3, 1, NOW(), NOW())", (room,))
            cur.execute("INSERT INTO bookings (user_id, floor, room, slot_date, slot_start, slot_end, status, "
                        "created_at) VALUES (3, 3, %s, '2026-10-08', '10:00:00', '10:45:00', 'active', NOW())", (room,))
            cur.execute("INSERT INTO bookings (user_id, floor, room, slot_date, slot_start, slot_end, status, "
                        "created_at, cancelled_at, cancelled_by) VALUES (3, 3, %s, '2026-10-08', '10:00:00', "
                        "'10:45:00', 'cancelled', NOW(), NOW(), 2)", (room,))
            cur.execute("INSERT INTO room_bans (room, floor, banned_by, created_at) VALUES (%s, 3, 2, NOW())", (320,))
            cur.execute("INSERT INTO closures (floor, date_from, date_to, time_from, time_to, reason, created_by, "
                        "created_at) VALUES (3, '2026-10-09', '2026-10-09', '00:00:00', '23:59:59', 'Ремонт', 2, NOW())")
            cur.execute("INSERT INTO change_requests (user_id, old_surname, old_room, old_floor, new_surname, "
                        "new_room, new_floor, created_at) VALUES (3, 'Иванов', %s, 3, 'Иванова', %s, 3, NOW())",
                        (room, 314))

    def assert_old_data_intact(self):
        users = {u["telegram_id"]: u for u in db.fetch_all("SELECT * FROM users")}
        self.assertEqual({1000, 2001, 2002}, set(users))
        self.assertEqual(("Иванов", "312", 3, "ivanov", 1, None),
                         tuple(users[2002][k] for k in ("surname", "room", "floor", "username", "is_banned", "vk_id")))
        self.assertEqual(("starosta", "301"), (users[2001]["role"], users[2001]["room"]))
        bookings = db.fetch_all("SELECT * FROM bookings ORDER BY id")
        self.assertEqual([("active", "312"), ("cancelled", "312")], [(b["status"], b["room"]) for b in bookings])
        self.assertEqual("320", db.fetch_one("SELECT * FROM room_bans")["room"])
        self.assertEqual("Ремонт", db.fetch_one("SELECT * FROM closures")["reason"])
        request = db.pending_change_requests(3)[0]
        self.assertEqual(("312", "314", 2002), (request["old_room"], request["new_room"], request["telegram_id"]))

    # ------------------------------------------------------------------ #
    def test_empty_database(self):
        db.init_db()
        self.assert_new_schema()
        db.init_db()      # повторный запуск (второй бот или перезапуск) ничего не ломает
        self.assert_new_schema()
        db.sync_admins(frozenset({1000}), frozenset({9000}), NOW)
        db.sync_admins(frozenset({1000}), frozenset({9000}), NOW)
        self.assertEqual([(1000, None), (None, 9000)], [(a["telegram_id"], a["vk_id"]) for a in db.admins()])

    def test_old_schema_with_data(self):
        self.load_old_schema()
        self.assertNotIn("vk_id", self.columns("users"))
        self.assertEqual("NO", self.columns("users")["telegram_id"]["nullable"])
        self.seed_old_data()

        db.init_db()
        self.assert_new_schema()
        self.assert_old_data_intact()
        db.init_db()      # идемпотентность
        self.assert_new_schema()
        self.assert_old_data_intact()

        # после миграции оба бота работают на этой базе
        vk_user = services.save_registration(None, 9001, None, "Петров", "315", 3, None, NOW, platform="vk").user
        self.assertEqual((None, 9001), (vk_user["telegram_id"], vk_user["vk_id"]))
        another = services.save_registration(None, 9002, None, "Сидоров", "316", 3, None, NOW, platform="vk").user
        self.assertIsNone(another["telegram_id"], "несколько пользователей без telegram_id не мешают друг другу")
        with self.assertRaises(pymysql.IntegrityError):
            db.execute("INSERT INTO users (vk_id, created_at, updated_at) VALUES (9001, NOW(), NOW())")
        with self.assertRaises(pymysql.IntegrityError):
            db.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (2002, NOW(), NOW())")
        booking = services.book(vk_user, datetime(2026, 10, 8).date(), datetime(2026, 10, 8, 11, 45).time(), NOW)
        self.assertEqual((9001, None), (booking["vk_id"], booking["telegram_id"]))
        with self.assertRaises(services.ServiceError):   # время занято записью, сделанной ещё до миграции
            services.book(vk_user, datetime(2026, 10, 8).date(), datetime(2026, 10, 8, 10, 0).time(), NOW)

    def test_very_old_schema_with_numeric_rooms(self):
        self.load_old_schema(numeric_rooms=True)
        self.assertEqual("smallint", self.columns("users")["room"]["type"])
        self.seed_old_data()
        db.init_db()
        self.assert_new_schema()
        self.assert_old_data_intact()

    def test_half_migrated_database(self):
        """Колонка vk_id уже есть, а индекса и NULL у telegram_id ещё нет (миграцию прервали)."""
        self.load_old_schema()
        self.seed_old_data()
        db.execute("ALTER TABLE users ADD COLUMN vk_id BIGINT NULL AFTER telegram_id")
        db.init_db()
        self.assert_new_schema()
        self.assert_old_data_intact()

    def test_schema_file_can_be_applied_by_hand(self):
        """README: файл schema.sql можно выполнить вручную — результат тот же, что у init_db()."""
        with db.transaction() as cur:
            for statement in _statements((config.BASE_DIR / "schema.sql").read_text(encoding="utf-8")):
                cur.execute(statement)
        self.assert_new_schema()
        db.init_db()
        self.assert_new_schema()
