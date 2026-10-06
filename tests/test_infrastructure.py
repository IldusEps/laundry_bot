"""Схема БД, логи двух процессов, точки входа и необязательные токены."""
import contextlib
import io
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from laundry import config, logger, services

from . import sqlite_db

ROOT = Path(__file__).resolve().parent.parent


def mysql_columns() -> dict[str, list[str]]:
    """Колонки таблиц из schema.sql (без индексов и ограничений)."""
    sql = re.sub(r"^\s*--.*$", "", (ROOT / "schema.sql").read_text(encoding="utf-8"), flags=re.MULTILINE)
    tables: dict[str, list[str]] = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\) ENGINE", sql, flags=re.DOTALL):
        columns = []
        for line in body.splitlines():
            word = line.strip().split(" ")[0]
            if word and word not in ("PRIMARY", "UNIQUE", "KEY", "CONSTRAINT"):
                columns.append(word)
        tables[name] = columns
    return tables


class SchemaTest(unittest.TestCase):
    def test_sqlite_test_schema_matches_schema_sql(self):
        tmp = tempfile.mkdtemp()
        try:
            path = str(Path(tmp) / "schema.sqlite3")
            sqlite_db.create_database(path)
            conn = sqlite3.connect(path)
            try:
                sqlite_tables = {
                    name: [row[1] for row in conn.execute(f"PRAGMA table_xinfo({name})")]
                    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                                "AND name NOT LIKE 'sqlite_%'")}
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        expected = mysql_columns()
        self.assertEqual({"users", "room_bans", "bookings", "closures", "change_requests", "link_codes"},
                         set(expected))
        self.assertEqual(expected, sqlite_tables, "tests/sqlite_schema.sql отстал от schema.sql")

    def test_schema_sql_has_vk_columns(self):
        sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
        self.assertRegex(sql, r"telegram_id\s+BIGINT\s+NULL")
        self.assertRegex(sql, r"vk_id\s+BIGINT\s+NULL")
        self.assertIn("UNIQUE KEY uq_users_vk (vk_id)", sql)
        self.assertIn("UNIQUE KEY uq_users_telegram (telegram_id)", sql)


class LoggingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="laundry_test_logdir_"))
        root, actions = logging.getLogger(), logging.getLogger(logger.ACTIONS_LOGGER)
        self.saved = (root.handlers[:], root.level, actions.handlers[:], actions.level,
                      sys.excepthook, threading.excepthook)
        root.handlers[:], actions.handlers[:] = [], []   # чужие обработчики setup_logging закрыть не должен
        self.patcher = mock.patch.object(config, "LOG_DIR", self.tmp)
        self.patcher.start()

    def tearDown(self):
        root, actions = logging.getLogger(), logging.getLogger(logger.ACTIONS_LOGGER)
        for target in (root, actions):
            for handler in target.handlers[:]:
                target.removeHandler(handler)
                handler.close()
        root.handlers[:], actions.handlers[:] = self.saved[0], self.saved[2]
        root.setLevel(self.saved[1])
        actions.setLevel(self.saved[3])
        sys.excepthook, threading.excepthook = self.saved[4], self.saved[5]
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def setup(self, prefix: str = "") -> None:
        with contextlib.redirect_stdout(io.StringIO()):   # консольный вывод бота в отчёте тестов не нужен
            logger.setup_logging(prefix=prefix)

    def write_sample(self) -> dict[str, str]:
        logging.getLogger("laundry.test").info("обычная строка")
        logging.getLogger("laundry.test").error("строка ошибки")
        services.actions.info("BOOK id=1 тест")
        for handler in logging.getLogger().handlers + logging.getLogger(logger.ACTIONS_LOGGER).handlers:
            handler.flush()
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(self.tmp.iterdir())}

    def test_vk_process_has_its_own_files(self):
        self.setup(logger.VK_PREFIX)
        files = self.write_sample()
        self.assertEqual(["vk_actions.log", "vk_bot.log", "vk_errors.log"], list(files))
        self.assertIn("BOOK id=1 тест", files["vk_actions.log"])
        self.assertNotIn("обычная строка", files["vk_actions.log"])
        self.assertIn("строка ошибки", files["vk_errors.log"])
        self.assertNotIn("обычная строка", files["vk_errors.log"])
        for line in ("обычная строка", "строка ошибки", "BOOK id=1 тест"):
            self.assertIn(line, files["vk_bot.log"])

    def test_telegram_process_files_are_unchanged(self):
        self.setup()
        files = self.write_sample()
        self.assertEqual(["actions.log", "bot.log", "errors.log"], list(files))
        self.assertIn("BOOK id=1 тест", files["actions.log"])
        self.assertIn("строка ошибки", files["errors.log"])

    def test_repeated_setup_does_not_duplicate_lines(self):
        self.setup(logger.VK_PREFIX)
        self.setup(logger.VK_PREFIX)
        files = self.write_sample()
        self.assertEqual(1, files["vk_actions.log"].count("BOOK id=1 тест"))
        self.assertEqual(1, files["vk_bot.log"].count("BOOK id=1 тест"))


class EntryPointTest(unittest.TestCase):
    """Каждый бот проверяет свой токен сам; без токена другого бота работает."""

    def run_python(self, code: str, **env: str) -> subprocess.CompletedProcess:
        full_env = {**os.environ, "PYTHONUTF8": "1", **env}
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full_env, capture_output=True,
                              text=True, encoding="utf-8", timeout=120)

    def test_config_loads_without_any_token(self):
        result = self.run_python("from laundry import config; print(repr(config.BOT_TOKEN), repr(config.VK_TOKEN), "
                                 "config.VK_GROUP_ID, sorted(config.VK_ADMIN_IDS))",
                                 BOT_TOKEN="", VK_TOKEN="", VK_GROUP_ID="", VK_ADMIN_IDS="")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("'' '' 0 []", result.stdout.strip())

    def test_telegram_entry_point_requires_bot_token_only(self):
        missing = self.run_python("import main", BOT_TOKEN="", VK_TOKEN="")
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("Не задан BOT_TOKEN", missing.stderr)
        self.assertNotIn("Traceback", missing.stderr)
        ok = self.run_python("import main; print('импорт main: ок')", VK_TOKEN="", VK_GROUP_ID="", VK_ADMIN_IDS="")
        self.assertEqual(0, ok.returncode, ok.stderr)
        self.assertIn("импорт main: ок", ok.stdout)

    def test_vk_entry_point_requires_vk_settings_only(self):
        for env in ({"VK_TOKEN": ""}, {"VK_GROUP_ID": ""}):
            missing = self.run_python("import vk_main", **env)
            self.assertNotEqual(0, missing.returncode, env)
            self.assertIn("Не заданы VK_TOKEN и VK_GROUP_ID", missing.stderr)
            self.assertNotIn("Traceback", missing.stderr)
        ok = self.run_python("import vk_main; print('импорт vk_main: ок')", BOT_TOKEN="", ADMIN_IDS="")
        self.assertEqual(0, ok.returncode, ok.stderr)
        self.assertIn("импорт vk_main: ок", ok.stdout)

    def test_bad_ids_give_clear_error(self):
        result = self.run_python("from laundry import config", VK_ADMIN_IDS="12,abc")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("VK_ADMIN_IDS — это id пользователей VK через запятую", result.stderr)
        result = self.run_python("from laundry import config", VK_GROUP_ID="club123")
        self.assertIn("VK_GROUP_ID должно быть числом", result.stderr)

    def test_vk_process_does_not_load_telegram_handlers(self):
        result = self.run_python(
            "import sys, vk_main; print('laundry.handlers' in sys.modules, 'laundry.loader' in sys.modules, "
            "'laundry.vk.handlers' in sys.modules)")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("False False True", result.stdout.strip().splitlines()[-1])
