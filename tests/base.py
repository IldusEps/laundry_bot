"""Общая основа сценарных тестов: SQLite вместо MySQL, имитации VK и Telegram, управляемое время."""
from __future__ import annotations

import itertools
import json
import logging
import shutil
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock

from laundry import config, db, notify, services, states
from laundry import schedule as sched
from laundry.vk import dispatcher
from laundry.vk.api import VkClient

from . import TEST_ENV, mysql_db, sqlite_db
from .fake_tg import FakeTelegram
from .fake_vk import FakeVk

GROUP_ID = 77
TG_ADMIN = 1000      # из ADMIN_IDS (tests/__init__.py)
VK_ADMIN = 9000      # из VK_ADMIN_IDS

# Вторник 6 октября 2026, 09:00. Обычные этажи: открыта неделя вт 06.10 – вс 11.10 (запись открылась в пн 15:00).
# 5 этаж: открыта неделя пн 05.10 – сб 10.10 (открылась в вс 04.10 в 15:00).
TUESDAY = datetime(2026, 10, 6, 9, 0)


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=config.TIMEZONE)


class _ErrorCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


class BotTestCase(unittest.TestCase):
    expect_errors = False   # тест, который нарочно ломает обработчик, ставит True

    def setUp(self) -> None:
        # страховка: сценарии идут только с тестовыми настройками (см. tests/__init__.py)
        assert (config.BOT_TOKEN, config.VK_TOKEN, config.DB_NAME) == (
            TEST_ENV["BOT_TOKEN"], TEST_ENV["VK_TOKEN"], TEST_ENV["DB_NAME"]), "загружены не тестовые настройки"
        self._tmp = tempfile.mkdtemp(prefix="laundry_test_db_")
        self._patch(mock.patch.object(config, "LOG_DIR", Path(self._tmp) / "logs"))
        config.LOG_DIR.mkdir()
        self._mysql_db: str | None = None
        if mysql_db.enabled():
            # настоящий MySQL/MariaDB: своя пустая база на каждый тест, таблицы создаёт сам бот (init_db)
            self._mysql_db = mysql_db.create_database()
            self._patch(mysql_db.use(self._mysql_db))
            db.init_db()
        else:
            path = str(Path(self._tmp) / "test.sqlite3")
            sqlite_db.create_database(path)
            self._patch(mock.patch.object(db, "_connect", sqlite_db.connector(path)))
            # Сеть в тестах запрещена: любое реальное подключение — ошибка теста
            self._patch(mock.patch("socket.socket.connect", side_effect=AssertionError("Тест обратился к сети")))

        self._now = TUESDAY.replace(tzinfo=config.TIMEZONE)
        self._patch(mock.patch.object(sched, "now", lambda: self._now))

        states._states.clear()
        services._link_failures.clear()
        self.vk = FakeVk(GROUP_ID)
        self.client = VkClient(self.vk, self.vk)
        notify.set_vk_client(self.client)
        self.tg = FakeTelegram()
        self.tg.install()

        self._errors = _ErrorCollector()
        logging.getLogger().addHandler(self._errors)
        self._events = itertools.count(1)
        db.sync_admins(config.ADMIN_IDS, config.VK_ADMIN_IDS, sched.now())

    def tearDown(self) -> None:
        logging.getLogger().removeHandler(self._errors)
        self.tg.uninstall()
        notify.set_vk_client(None)
        for patcher in reversed(self._patchers):
            patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._mysql_db:
            mysql_db.drop_database(self._mysql_db)
        self.assertEqual([], self.vk.violations, "Бот нарушил ограничения VK API")
        if not self.expect_errors:
            self.assertEqual([], self._errors.records, "В логе есть ошибки обработчиков")

    def _patch(self, patcher) -> None:
        if not hasattr(self, "_patchers"):
            self._patchers = []
        patcher.start()
        self._patchers.append(patcher)

    # ------------------------------------------------------------------ #
    def set_now(self, moment: datetime) -> None:
        """Подменяет «сейчас» (laundry.schedule.now)."""
        self._now = moment if moment.tzinfo else moment.replace(tzinfo=config.TIMEZONE)

    def vk_user(self, vk_id: int) -> "VkUser":
        return VkUser(self, vk_id)

    def tg_user(self, tg_id: int, username: str | None = None) -> "TgUser":
        return TgUser(self, tg_id, username)

    def make_starosta(self, user: dict) -> dict:
        db.set_role(user["id"], "starosta", sched.now())
        return db.get_user_by_id(user["id"])  # type: ignore[return-value]

    def active_bookings(self, floor: int, day: date) -> list[dict]:
        return db.bookings_between(floor, day, day)


class VkUser:
    """Человек в VK: пишет боту, нажимает кнопки меню и inline-кнопки — как в приложении."""

    def __init__(self, case: BotTestCase, vk_id: int):
        self.case, self.id, self.vk = case, vk_id, case.vk

    # --- действия -------------------------------------------------------- #
    def say(self, text: str, payload: dict | None = None) -> "VkUser":
        message = {"id": next(self.case._events), "date": 0, "from_id": self.id, "peer_id": self.id,
                   "text": text, "attachments": []}
        if payload is not None:
            message["payload"] = json.dumps(payload)
        dispatcher.handle_event(self.case.client, {"type": "message_new", "group_id": GROUP_ID,
                                                   "object": {"message": message, "client_info": {}}})
        return self

    def start(self) -> "VkUser":
        """Кнопка «Начать» при первом входе."""
        return self.say("Начать", {"command": "start"})

    def tap(self, label: str) -> "VkUser":
        """Кнопка главного меню (обычная клавиатура): приходит сообщением с payload."""
        for button in self.menu():
            if button["label"] == label:
                return self.say(label, button["payload"])
        raise AssertionError(f"В меню VK нет кнопки «{label}». Есть: {[b['label'] for b in self.menu()]}")

    def press(self, label: str) -> str | None:
        """Inline-кнопка, подпись которой начинается с label (в последнем сообщении, где такая есть).
        Возвращает текст подсказки (snackbar)."""
        for message in reversed(self.vk.dialog(self.id)):
            keyboard = message.get("keyboard") or {}
            if not keyboard.get("inline"):
                continue
            for button in self.vk.buttons(message):
                if button["label"].startswith(label):
                    return self.press_payload(button["payload"], message["cmid"])
        raise AssertionError(f"Нет inline-кнопки «{label}» у пользователя VK {self.id}. Есть: {self.labels()}")

    def press_command(self, command: str, cmid: int | None = None, page: int = 0) -> str | None:
        """Нажатие кнопки с данной командой — в том числе «устаревшей», которой уже нет на экране."""
        payload = {"c": command, **({"p": page} if page else {})}
        return self.press_payload(payload, cmid if cmid is not None else self.last["cmid"])

    def press_payload(self, payload: dict, cmid: int) -> str | None:
        event_id = f"event{next(self.case._events)}"
        dispatcher.handle_event(self.case.client, {
            "type": "message_event", "group_id": GROUP_ID,
            "object": {"user_id": self.id, "peer_id": self.id, "event_id": event_id,
                       "payload": payload, "conversation_message_id": cmid}})
        answers = [a for a in self.vk.answers if a["event_id"] == event_id]
        self.case.assertEqual(1, len(answers), f"На нажатие {payload} бот должен ответить ровно один раз")
        return answers[0]["text"]

    def register(self, surname: str, room: str) -> "VkUser":
        self.start().say(surname).say(room)
        self.case.assertIsNotNone(self.row, f"Регистрация VK {self.id} не удалась: {self.text}")
        self.case.assertEqual(room.lower(), (self.row or {}).get("room"), self.text)
        return self

    # --- что видит человек ------------------------------------------------ #
    @property
    def row(self) -> dict | None:
        return db.get_user_by_vk(self.id)

    @property
    def last(self) -> dict:
        return self.vk.last(self.id)

    @property
    def text(self) -> str:
        return self.last["text"]

    def texts(self) -> list[str]:
        return self.vk.texts(self.id)

    def labels(self) -> list[str]:
        """Подписи inline-кнопок последнего сообщения."""
        keyboard = self.last.get("keyboard") or {}
        return self.vk.labels(self.last) if keyboard.get("inline") else []

    def menu(self) -> list[dict]:
        """Кнопки главного меню — последняя присланная обычная клавиатура."""
        for message in reversed(self.vk.dialog(self.id)):
            keyboard = message.get("keyboard")
            if keyboard is not None and not keyboard.get("inline"):
                return self.vk.buttons(message)
        return []

    def menu_labels(self) -> list[str]:
        return [b["label"] for b in self.menu()]


class TgUser:
    """Человек в Telegram."""

    def __init__(self, case: BotTestCase, tg_id: int, username: str | None = None):
        self.case, self.id, self.username, self.tg = case, tg_id, username, case.tg

    def say(self, text: str) -> "TgUser":
        self.tg.say(self.id, text, self.username)
        return self

    def press(self, label: str) -> str | None:
        return self.tg.press(self.id, label, self.username)

    def press_data(self, data: str) -> str | None:
        return self.tg.press_data(self.id, data, username=self.username)

    def register(self, surname: str, room: str) -> "TgUser":
        self.say("/start").say(surname).say(room)
        self.case.assertEqual(room.lower(), (self.row or {}).get("room"), self.text)
        return self

    @property
    def row(self) -> dict | None:
        return db.get_user(self.id)

    @property
    def last(self) -> dict:
        return self.tg.last(self.id)

    @property
    def text(self) -> str:
        return self.last["text"]

    def texts(self) -> list[str]:
        return self.tg.texts(self.id)

    def labels(self) -> list[str]:
        return self.tg.labels(self.last)

    def menu_labels(self) -> list[str]:
        return self.tg.menu_labels(self.id)


def day(month: int, dom: int) -> date:
    return date(2026, month, dom)


def hm(hour: int, minute: int) -> time:
    return time(hour, minute)
