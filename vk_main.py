"""Точка входа VK-бота: python vk_main.py  (Telegram-бот — отдельный процесс: python main.py)"""
from __future__ import annotations

import logging

from laundry.logger import VK_PREFIX, setup_logging

setup_logging(prefix=VK_PREFIX)  # свои файлы логов: vk_bot.log, vk_errors.log, vk_actions.log

from laundry import config  # noqa: E402

if not config.VK_TOKEN or not config.VK_GROUP_ID:
    raise SystemExit("Не заданы VK_TOKEN и VK_GROUP_ID — заполните файл .env (см. README, раздел про VK)")

from laundry import db, notify  # noqa: E402
from laundry import schedule as sched  # noqa: E402
from laundry.vk import bot  # noqa: E402
from laundry.vk.api import VkClient  # noqa: E402

log = logging.getLogger("vk_main")


def main() -> None:
    log.info("Запуск VK-бота. Часовой пояс: %s, этажей: %s, сообщество: %s, админов VK: %d",
             config.TIMEZONE, config.MAX_FLOOR, config.VK_GROUP_ID, len(config.VK_ADMIN_IDS))
    db.init_db()
    db.sync_admins(config.ADMIN_IDS, config.VK_ADMIN_IDS, sched.now())
    if not config.VK_ADMIN_IDS:
        log.warning("VK_ADMIN_IDS пуст — в VK нет администратора")
    if not config.BOT_TOKEN:
        log.info("BOT_TOKEN не задан — уведомления в Telegram отправляться не будут")
    client = VkClient.from_token(config.VK_TOKEN)
    notify.set_vk_client(client)  # уведомления VK-жильцам идут через тот же клиент (общий лимит запросов)
    group = client.call("groups.getById", group_id=config.VK_GROUP_ID)
    info = (group.get("groups") if isinstance(group, dict) else group) or [{}]
    log.info("Сообщество «%s» (id %s), начинаю Long Poll", info[0].get("name", "?"), config.VK_GROUP_ID)
    try:
        bot.run(client, config.VK_GROUP_ID)
    finally:
        log.info("VK-бот остановлен")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Остановлено пользователем (Ctrl+C)")
    except Exception:
        log.critical("VK-бот упал при запуске", exc_info=True)
        raise
