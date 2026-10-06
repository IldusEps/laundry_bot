"""Привязка второго мессенджера кодом: один человек — одна строка users, общие лимиты, баны и роль."""
import re
from datetime import timedelta

from laundry import db, services
from laundry.keyboards import BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_STAROSTA

from .base import BotTestCase, day


class LinkingTest(BotTestCase):
    def tg_code(self, user) -> str:
        """Жилец в Telegram берёт код для VK в профиле."""
        user.say(BTN_PROFILE)
        user.press("🔗 Привязать ВКонтакте")
        return re.search(r"<code>(\d{6})</code>", user.text).group(1)

    def vk_code(self, user) -> str:
        user.tap(BTN_PROFILE)
        user.press("🔗 Привязать Telegram")
        return re.search(r"Код привязки: (\d{6})", user.text).group(1)

    def users_count(self) -> int:
        return db.fetch_one("SELECT COUNT(*) AS n FROM users WHERE room IS NOT NULL")["n"]

    # ------------------------------------------------------------------ #
    def test_link_new_vk_account_to_telegram_profile(self):
        tg = self.tg_user(2001, "ivanov").register("Иванов", "312")
        code = self.tg_code(tg)
        self.assertIn("отправьте ему этот код (действует 15 минут)", tg.text)

        vk = self.vk_user(9001).start()
        self.assertIn("отправьте код привязки", vk.text)
        vk.say(code)
        self.assertEqual("✅ Аккаунт ВКонтакте привязан. Иванов, к.312.", vk.text)
        self.assertIn(BTN_BOOK, vk.menu_labels())

        row = vk.row
        self.assertEqual((2001, 9001, "312", "ivanov"),
                         (row["telegram_id"], row["vk_id"], row["room"], row["username"]))
        self.assertEqual(row["id"], tg.row["id"])
        self.assertEqual(1, self.users_count())
        self.assertIn("🔗 К вашему профилю привязан аккаунт ВКонтакте", tg.text)

        vk.tap(BTN_PROFILE)
        self.assertIn("🔗 Аккаунты Telegram и ВКонтакте связаны", vk.text)
        self.assertNotIn("🔗 Привязать Telegram", vk.labels())
        tg.say(BTN_PROFILE)
        self.assertNotIn("🔗 Привязать ВКонтакте", tg.labels())

    def test_link_new_telegram_account_to_vk_profile(self):
        vk = self.vk_user(9001).register("Иванов", "312")
        code = self.vk_code(vk)
        self.assertIn("Откройте нашего Telegram-бота", vk.text)
        tg = self.tg_user(2001, "ivanov").say("/start").say(code)
        self.assertEqual("✅ Аккаунт Telegram привязан. Иванов, к.312.", tg.text)
        self.assertEqual((2001, 9001), (tg.row["telegram_id"], tg.row["vk_id"]))
        self.assertIn("🔗 К вашему профилю привязан аккаунт Telegram", vk.text)
        self.assertEqual(1, self.users_count())

    def test_limit_is_shared_after_linking(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).start().say(self.tg_code(tg))
        tg.say(BTN_BOOK)
        self.assertEqual("Записано ✅", tg.press_data("bk:c:20261008:1000"))
        vk.tap(BTN_BOOK)
        self.assertEqual("Записано ✅", vk.press_command("bk:c:20261008:1145"))
        # третья запись в тот же день — отказ в обоих мессенджерах
        self.assertEqual("Вы уже записаны максимальное число раз на этот день.",
                         vk.press_command("bk:c:20261008:1330"))
        self.assertEqual("Вы уже записаны максимальное число раз на этот день.",
                         tg.press_data("bk:c:20261008:1515"))
        self.assertEqual(2, len(self.active_bookings(3, day(10, 8))))

        vk.tap(BTN_MY)   # в VK видны обе записи, в том числе сделанная в Telegram
        self.assertIn("1. Четверг, 08.10.2026, 10:00–10:45", vk.text)
        self.assertIn("2. Четверг, 08.10.2026, 11:45–12:30", vk.text)
        vk.press("❌ Отменить: Чт 08.10, 10:00")
        vk.press("✅ Да, отменить")
        tg.say(BTN_MY)
        self.assertNotIn("10:00–10:45", tg.text)

    def test_notifications_go_to_both_messengers(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).start().say(self.tg_code(tg))
        starosta = self.vk_user(9005).register("Старостин", "301")
        self.make_starosta(starosta.row)
        vk.tap(BTN_BOOK)
        vk.press_command("bk:c:20261008:1000")
        booking = self.active_bookings(3, day(10, 8))[0]
        starosta.say("меню").tap(BTN_STAROSTA)
        starosta.press_command(f"m:3:xy:{booking['id']}")
        self.assertIn("отменена старостой этажа", vk.text)
        self.assertIn("отменена старостой этажа", tg.text)

    def test_ban_applies_to_both_messengers(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).start().say(self.tg_code(tg))
        starosta = self.tg_user(2005).register("Старостин", "301")
        self.make_starosta(starosta.row)
        services.ban_user(starosta.row, 3, tg.row["id"])
        vk.tap(BTN_BOOK)
        self.assertIn("Староста исключил вас из записи", vk.text)
        tg.say(BTN_BOOK)
        self.assertIn("Староста исключил вас из записи", tg.text)

    # ------------------------------------------------------------------ #
    #  Неверные и устаревшие коды
    # ------------------------------------------------------------------ #
    def test_wrong_code(self):
        self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).start().say("000000")
        self.assertIn("Код не подошёл", vk.text)
        self.assertIn("введите фамилию", vk.text)
        vk.say("Петров").say("315")          # регистрация продолжается обычным путём
        self.assertEqual("315", vk.row["room"])
        self.assertEqual(2, self.users_count())

    def test_code_expires_and_works_once(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        code = self.tg_code(tg)
        self.set_now(self._now + timedelta(minutes=16))
        vk = self.vk_user(9001).start().say(code)
        self.assertIn("Код не подошёл", vk.text)

        code = self.tg_code(tg)
        vk.say(code)
        self.assertIn("привязан", vk.text)
        other = self.vk_user(9002).start().say(code)   # тот же код второй раз
        self.assertIn("Код не подошёл", other.text)
        self.assertIsNone(other.row)

    def test_code_guessing_is_blocked(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        code = self.tg_code(tg)
        wrong = "000000" if code != "000000" else "111111"
        attacker = self.vk_user(9666).start()
        with self.assertLogs("laundry.services", level="WARNING") as logs:
            for _ in range(services.LINK_MAX_ATTEMPTS):
                attacker.say(wrong)
                self.assertIn("Код не подошёл", attacker.text)
        self.assertIn("Подбор кода привязки? vk_id=9666", logs.output[0])
        attacker.say(code)                  # теперь не принимается даже верный код
        self.assertIn("Слишком много неверных кодов", attacker.text)
        self.assertIsNone(tg.row["vk_id"])
        self.assertIsNone(attacker.row)

        owner = self.vk_user(9001).start().say(code)   # на других людей блокировка не действует
        self.assertIn("привязан", owner.text)
        self.set_now(self._now + timedelta(minutes=16))  # через 15 минут блокировка снимается
        attacker.say(wrong)
        self.assertIn("Код не подошёл", attacker.text)

    def test_new_code_cancels_previous(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        old, new = self.tg_code(tg), self.tg_code(tg)
        vk = self.vk_user(9001).start()
        if old != new:
            vk.say(old)
            self.assertIn("Код не подошёл", vk.text)
        vk.say(new)
        self.assertIn("привязан", vk.text)

    def test_code_must_be_entered_in_the_other_messenger(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        code = self.tg_code(tg)
        stranger = self.tg_user(2002).say("/start").say(code)   # код для VK ввели в Telegram
        self.assertIn("Код не подошёл", stranger.text)
        self.assertIsNone(stranger.row)

    def test_profile_already_has_vk_account(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        code = self.tg_code(tg)
        db.execute("UPDATE users SET vk_id = 9009 WHERE telegram_id = 2001")   # пока шёл код, привязали другой VK
        vk = self.vk_user(9001).start().say(code)
        self.assertIn("К тому профилю уже привязан другой аккаунт ВКонтакте", vk.text)
        self.assertEqual("Аккаунт ВКонтакте уже привязан.", tg.press_data("pr:link"))

    # ------------------------------------------------------------------ #
    #  Оба аккаунта уже зарегистрированы — слияние
    # ------------------------------------------------------------------ #
    def test_merge_two_registered_accounts_of_same_room(self):
        tg = self.tg_user(2001, "ivanov").register("Иванов", "312")
        vk = self.vk_user(9001).register("Иванов", "312")
        self.make_starosta(vk.row)                         # роль была получена в VK
        tg.say(BTN_BOOK)
        tg.press_data("bk:c:20261008:1000")
        self.assertEqual(2, self.users_count())

        vk.say(self.tg_code(tg))                           # код можно прислать просто сообщением
        self.assertEqual("✅ Аккаунт ВКонтакте привязан. Иванов, к.312.", vk.text)
        self.assertEqual(1, self.users_count())
        row = vk.row
        self.assertEqual((2001, 9001, "starosta"), (row["telegram_id"], row["vk_id"], row["role"]))
        self.assertEqual(row["id"], self.active_bookings(3, day(10, 8))[0]["user_id"])
        self.assertIn(BTN_STAROSTA, vk.menu_labels())

    def test_merge_keeps_ban(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).register("Иванов", "312")
        starosta = self.tg_user(2005).register("Старостин", "301")
        self.make_starosta(starosta.row)
        services.ban_user(starosta.row, 3, vk.row["id"])   # исключён аккаунт VK
        vk.say(self.tg_code(tg))
        self.assertIn("привязан", vk.text)
        self.assertTrue(tg.row["is_banned"], "привязкой нельзя обойти исключение")
        tg.say(BTN_BOOK)
        self.assertIn("Староста исключил вас из записи", tg.text)

    def test_merge_refused_for_different_rooms(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).register("Иванов", "314")
        vk.say(self.tg_code(tg))
        self.assertIn("Здесь вы зарегистрированы в другой комнате", vk.text)
        self.assertEqual(2, self.users_count())
        self.assertIsNone(tg.row["vk_id"])

    def test_merge_refused_while_second_account_has_bookings(self):
        tg = self.tg_user(2001).register("Иванов", "312")
        vk = self.vk_user(9001).register("Иванов", "312")
        vk.tap(BTN_BOOK)
        vk.press_command("bk:c:20261008:1000")
        code = self.tg_code(tg)
        vk.say(code)
        self.assertIn("У этого аккаунта есть предстоящие записи", vk.text)
        self.assertEqual(2, self.users_count())
        # отменил запись — тот же код срабатывает
        vk.tap(BTN_MY)
        vk.press("❌ Отменить")
        vk.press("✅ Да, отменить")
        vk.say(code)
        self.assertIn("привязан", vk.text)
        self.assertEqual(1, self.users_count())

    def test_unregistered_cannot_issue_code(self):
        self.assertEqual("Сначала пройдите регистрацию — напишите «начать».",
                         self.vk_user(9001).start().press_command("pr:link"))
        self.assertFalse(services.can_link(None, "vk"))
