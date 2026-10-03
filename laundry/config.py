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


def _admin_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_IDS", "").replace(" ", "")
    try:
        return frozenset(int(x) for x in raw.split(",") if x)
    except ValueError as exc:
        raise RuntimeError("ADMIN_IDS — это telegram_id через запятую, например 123456789,987654321") from exc


# --- Telegram -----------------------------------------------------------------
BOT_TOKEN = _required("BOT_TOKEN")
ADMIN_IDS = _admin_ids()

# --- MySQL --------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = _required("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = _required("DB_NAME")
PROXY_URL = os.getenv("PROXY_URL")

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
MAX_FLOOR = int(os.getenv("MAX_FLOOR", "9"))
MAX_ROOM_ON_FLOOR = int(os.getenv("MAX_ROOM_ON_FLOOR", "36"))

# Когда открывается запись на новую неделю
WEEK_OPEN_TIME = _hhmm("WEEK_OPEN_TIME", "15:00")                # понедельник, обычные этажи
FLOOR5_WEEK_OPEN_TIME = _hhmm("FLOOR5_WEEK_OPEN_TIME", "15:00")  # воскресенье, 5 этаж
