"""Конфигурация бота. Все настройки читаются из файла .env в корне проекта."""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана обязательная переменная {name} — заполните файл .env")
    return value


def _hhmm(name: str, default: str) -> time:
    raw = os.getenv(name, default).strip()
    try:
        hours, minutes = raw.split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise RuntimeError(f"{name} должно быть в формате ЧЧ:ММ, сейчас: {raw!r}") from exc


def _ids(name: str, what: str) -> frozenset[int]:
    raw = os.getenv(name, "").replace(" ", "")
    try:
        return frozenset(int(x) for x in raw.split(",") if x)
    except ValueError as exc:
        raise RuntimeError(f"{name} — это {what} через запятую, например 123456789,987654321") from exc


def _int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} должно быть числом, сейчас: {raw!r}") from exc


# --- Telegram (BOT_TOKEN проверяет main.py; пусто — уведомления в Telegram не отправляются)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = _ids("ADMIN_IDS", "telegram_id")

# --- ВКонтакте (VK_TOKEN проверяет vk_main.py; пусто — уведомления в VK не отправляются)
VK_TOKEN = os.getenv("VK_TOKEN", "").strip()
VK_GROUP_ID = abs(_int("VK_GROUP_ID"))
VK_ADMIN_IDS = _ids("VK_ADMIN_IDS", "id пользователей VK")
VK_API_VERSION = "5.199"

# --- MySQL --------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = _required("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = _required("DB_NAME")

# --- Время --------------------------------------------------------------------
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Samara"))

# --- Логирование --------------------------------------------------------------
LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
if not LOG_DIR.is_absolute():
    LOG_DIR = BASE_DIR / LOG_DIR
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_MAX_MB = int(os.getenv("LOG_MAX_MB", "10"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "10"))

# --- Общежитие ----------------------------------------------------------------
MAX_FLOOR = int(os.getenv("MAX_FLOOR", "5"))
MAX_ROOM_ON_FLOOR = int(os.getenv("MAX_ROOM_ON_FLOOR", "36"))


def _floor_max_rooms() -> dict[int, int]:
    """FLOOR_MAX_ROOMS=3:35,4:30 — свой максимальный номер комнаты для отдельных этажей."""
    raw = os.getenv("FLOOR_MAX_ROOMS", "3:35").replace(" ", "")
    try:
        return {int(f): int(n) for f, n in (pair.split(":") for pair in raw.split(",") if pair)}
    except ValueError as exc:
        raise RuntimeError("FLOOR_MAX_ROOMS — пары этаж:номер через запятую, например 3:35") from exc


# На 3 этаже вместо 336 есть 323а, поэтому номера там до 335
FLOOR_MAX_ROOMS = _floor_max_rooms()
# Комнаты с буквой, которые реально существуют (через запятую)
LETTER_ROOMS = frozenset(r.strip().lower() for r in os.getenv("LETTER_ROOMS", "323а").split(",") if r.strip())

# --- Прокси для Telegram (например socks5h://user:pass@host:1080); пусто — напрямую
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# Когда открывается запись на новую неделю
WEEK_OPEN_TIME = _hhmm("WEEK_OPEN_TIME", "15:00")                # понедельник, обычные этажи
FLOOR5_WEEK_OPEN_TIME = _hhmm("FLOOR5_WEEK_OPEN_TIME", "15:00")  # воскресенье, 5 этаж
