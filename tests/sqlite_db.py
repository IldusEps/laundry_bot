"""SQLite вместо MySQL для тестов: подменяет laundry.db._connect.

Запросы бота написаны для PyMySQL — адаптер переводит их на ходу: %s -> ?, убирает FOR UPDATE,
превращает ошибку уникального индекса в pymysql.IntegrityError. Каждая транзакция начинается с
BEGIN IMMEDIATE, поэтому параллельные записи идут по очереди, как под блокировками строк в MySQL.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, time
from pathlib import Path

import pymysql

SCHEMA_FILE = Path(__file__).with_name("sqlite_schema.sql")

sqlite3.register_converter("DATETIME", lambda raw: datetime.fromisoformat(raw.decode()))

_FOR_UPDATE = re.compile(r"\s+FOR UPDATE\b", re.IGNORECASE)


def _translate(sql: str) -> str:
    return _FOR_UPDATE.sub("", sql).replace("%s", "?")


def _adapt(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return value


class Cursor:
    def __init__(self, conn: sqlite3.Connection):
        self._cur = conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cur.close()

    def execute(self, sql: str, params=()):
        try:
            self._cur.execute(_translate(sql), tuple(_adapt(p) for p in params))
        except sqlite3.IntegrityError as exc:
            raise pymysql.IntegrityError(1062, str(exc)) from exc
        return self._cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self) -> int:
        return self._cur.lastrowid


class Connection:
    """Интерфейс соединения PyMySQL, которым пользуется laundry.db.transaction()."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None,
                                     detect_types=sqlite3.PARSE_DECLTYPES)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("BEGIN IMMEDIATE")

    def cursor(self) -> Cursor:
        return Cursor(self._conn)

    def commit(self) -> None:
        self._conn.execute("COMMIT")

    def rollback(self) -> None:
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    def close(self) -> None:
        self._conn.close()


def create_database(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def connector(path: str):
    """Функция для подстановки в laundry.db._connect."""
    return lambda: Connection(path)
