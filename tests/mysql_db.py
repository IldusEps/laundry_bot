"""Тесты на настоящем MySQL / MariaDB (по желанию).

Включается переменными окружения — нужен сервер, на котором можно создавать и удалять базы:
    TEST_MYSQL_HOST      (обязательно, например 127.0.0.1)
    TEST_MYSQL_PORT      (по умолчанию 3306)
    TEST_MYSQL_USER      (по умолчанию root)
    TEST_MYSQL_PASSWORD  (по умолчанию пусто)

Каждый тест получает свою пустую базу laundry_test_<случайное имя> и удаляет её за собой.
НЕ указывайте здесь рабочий сервер бота с правами, которыми жалко рисковать: тесты создают и удаляют
только базы с префиксом laundry_test_, рабочую базу из .env они не трогают.
"""
from __future__ import annotations

import os
import uuid
from unittest import mock

import pymysql

from laundry import config

PREFIX = "laundry_test_"
HOST = os.environ.get("TEST_MYSQL_HOST", "").strip()
PORT = int(os.environ.get("TEST_MYSQL_PORT", "3306"))
USER = os.environ.get("TEST_MYSQL_USER", "root")
PASSWORD = os.environ.get("TEST_MYSQL_PASSWORD", "")


def enabled() -> bool:
    return bool(HOST)


def _server():
    return pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, charset="utf8mb4",
                           autocommit=True, connect_timeout=10)


def create_database() -> str:
    name = PREFIX + uuid.uuid4().hex[:12]
    conn = _server()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        conn.close()
    return name


def drop_database(name: str) -> None:
    if not name.startswith(PREFIX):
        raise AssertionError(f"Отказ удалять базу {name!r}: не тестовая")
    conn = _server()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()


def use(name: str):
    """Патч настроек: laundry.db ходит в тестовую базу name на тестовом сервере."""
    return mock.patch.multiple(config, DB_HOST=HOST, DB_PORT=PORT, DB_USER=USER, DB_PASSWORD=PASSWORD, DB_NAME=name)


def server_version() -> str:
    conn = _server()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            return str(cur.fetchone()[0])
    finally:
        conn.close()
