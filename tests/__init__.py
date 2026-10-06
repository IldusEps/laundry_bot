"""Тесты обоих ботов без сети и без MySQL.

Запуск из корня проекта:   venv\\Scripts\\python -m unittest discover -s tests -t . -v

Настройки задаются здесь, до импорта laundry.config: тесты не читают ваш .env (переменные окружения
важнее файла), не подключаются к рабочей базе и не пишут в папку logs/.

Параметры «-s tests -t .» обязательны: без них unittest сначала обходит пакет laundry, бот успевает
прочитать настоящий .env, и тесты пошли бы с рабочими настройками — в этом случае запуск останавливается.
"""
import os
import sys
import tempfile

if "laundry.config" in sys.modules:
    raise RuntimeError(
        "Настройки бота уже загружены из .env — тесты с ними не запускаются. "
        "Команда запуска: venv\\Scripts\\python -m unittest discover -s tests -t . -v")

TEST_ENV = {
    "BOT_TOKEN": "1:telegram-test-token",
    "ADMIN_IDS": "1000",
    "VK_TOKEN": "vk-test-token",
    "VK_GROUP_ID": "77",
    "VK_ADMIN_IDS": "9000",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "1",                 # заведомо нерабочий порт: тесты ходят в SQLite через подмену db._connect
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_NAME": "test",
    "TIMEZONE": "Europe/Samara",
    "WEEK_OPEN_TIME": "15:00",
    "FLOOR4_WEEK_OPEN_TIME": "15:00",
    "FLOOR5_WEEK_OPEN_TIME": "15:00",
    "MAX_FLOOR": "5",
    "MAX_ROOM_ON_FLOOR": "36",
    "FLOOR_MAX_ROOMS": "3:35",
    "LETTER_ROOMS": "323а",
    "PROXY_URL": "",
    "LOG_DIR": tempfile.mkdtemp(prefix="laundry_test_logs_"),
    "LOG_LEVEL": "DEBUG",
}
os.environ.update(TEST_ENV)
