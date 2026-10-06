"""Оба бота на одной базе: записи, отмены, заявки и сообщения видны и доходят между VK и Telegram."""
from unittest import mock

from laundry import config, db, image, render, services
from laundry.keyboards import BTN_BOOK, BTN_PROFILE, BTN_STAROSTA, BTN_SUPPORT

from .base import TG_ADMIN, VK_ADMIN, BotTestCase, day, hm


class CrossPlatformTest(BotTestCase):
    def setUp(self):
        super().setUp()
        self.vk_resident = self.vk_user(9002).register("Иванов", "312")
        self.tg_resident = self.tg_user(2002, "sidorov").register("Сидоров", "315")

    # ------------------------------------------------------------------ #
    #  Общее расписание
    # ------------------------------------------------------------------ #
    def test_vk_booking_is_visible_in_telegram_picture(self):
        tg_row = self.tg_resident.row
        empty = image.week_image(services.build_grid(3, None, viewer_id=tg_row["id"]), "Стирка")

        self.vk_resident.tap(BTN_BOOK)
        self.assertEqual("Записано ✅", self.vk_resident.press_command("bk:c:20261007:1000"))

        grid = services.build_grid(3, None, viewer_id=tg_row["id"])
        cell = grid.cells[(day(10, 7), hm(10, 0))]
        self.assertEqual(("busy", "312"), (cell.state, cell.booking["room"]))
        self.assertNotEqual(empty, image.week_image(grid, "Стирка"), "картинка расписания изменилась")

        # тот же путь глазами жильца в Telegram: /book -> картинка -> день
        self.tg_resident.say(BTN_BOOK)
        self.assertTrue(self.tg_resident.last["photo"])
        self.assertIn("Ср 07.10 · 9 своб.", self.tg_resident.labels())
        self.tg_resident.press("Ср 07.10")
        self.assertIn("🔴 10:00–10:45 — к.312 Иванов", self.tg_resident.text)
        self.assertNotIn("10:00–10:45", self.tg_resident.labels())
        self.assertEqual("Это время уже кто-то занял. Выберите другое.",
                         self.tg_resident.press_data("bk:c:20261007:1000"))

    def test_telegram_booking_is_visible_in_vk(self):
        self.tg_resident.say(BTN_BOOK)
        self.tg_resident.press("Чт 08.10")
        self.tg_resident.press("11:45–12:30")
        self.assertEqual("Записано ✅", self.tg_resident.press("✅ Записаться"))

        self.vk_resident.tap(BTN_BOOK)
        self.assertIn("Чт 08.10 · 9 своб.", self.vk_resident.labels())
        self.vk_resident.press("Чт 08.10")
        self.assertIn("🔴 11:45–12:30 — к.315 Сидоров", self.vk_resident.text)
        self.assertEqual("Это время уже кто-то занял. Выберите другое.",
                         self.vk_resident.press_command("bk:c:20261008:1145"))

    # ------------------------------------------------------------------ #
    #  Староста в одном мессенджере, жилец в другом
    # ------------------------------------------------------------------ #
    def test_telegram_starosta_cancels_vk_resident_booking(self):
        starosta = self.tg_user(2001, "starosta").register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.vk_resident.tap(BTN_BOOK)
        self.vk_resident.press_command("bk:c:20261007:1000")

        starosta.say("/start")
        self.assertIn(BTN_STAROSTA, starosta.menu_labels())
        starosta.say(BTN_STAROSTA)
        starosta.press("📅 Расписание")
        starosta.press("Ср 07.10")
        self.assertIn("❌ 10:00 к.312 Иванов", starosta.labels())
        starosta.press("❌ 10:00")
        before = len(self.vk.dialog(9002))
        self.assertEqual("Запись отменена", starosta.press("✅ Да, отменить"))

        self.assertEqual([], self.active_bookings(3, day(10, 7)))
        self.assertEqual(before + 1, len(self.vk.dialog(9002)))
        self.assertEqual("❗ Ваша запись на стирку Среда, 07.10.2026, 10:00–10:45 отменена старостой этажа.",
                         self.vk_resident.text)

    def test_vk_starosta_cancels_telegram_resident_booking(self):
        starosta = self.vk_user(9001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.tg_resident.say(BTN_BOOK)
        self.tg_resident.press_data("bk:c:20261007:1000")
        booking = self.active_bookings(3, day(10, 7))[0]

        starosta.say("меню").tap(BTN_STAROSTA)
        self.assertEqual("Запись отменена", starosta.press_command(f"m:3:xy:{booking['id']}"))
        self.assertEqual("❗ Ваша запись на стирку <b>Среда, 07.10.2026, 10:00–10:45</b> отменена старостой этажа.",
                         self.tg_resident.text)

    def test_closure_from_telegram_notifies_both_platforms(self):
        starosta = self.tg_user(2001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.vk_resident.tap(BTN_BOOK)
        self.vk_resident.press_command("bk:c:20261008:1000")
        self.tg_resident.say(BTN_BOOK)
        self.tg_resident.press_data("bk:c:20261008:1145")

        starosta.say("/start").say(BTN_STAROSTA)
        starosta.press("🔒 Закрыть запись")
        starosta.press("📆 Весь день")
        starosta.press("Чт 08.10")
        starosta.say("Санобработка")
        self.assertEqual("Готово", starosta.press("✅ Закрыть запись"))

        self.assertIn("отменена: запись на это время закрыта старостой этажа.\nПричина: Санобработка",
                      self.vk_resident.text)
        self.assertIn("Причина: Санобработка", self.tg_resident.text)
        self.assertEqual("Староста закрыл запись на это время.",
                         self.vk_resident.press_command("bk:c:20261008:1330"))

    def test_ban_from_vk_applies_in_telegram(self):
        starosta = self.vk_user(9001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        starosta.say("меню").tap(BTN_STAROSTA)
        starosta.press("🚫 Исключить комнату")
        starosta.say("315")
        self.assertEqual("Комната исключена", starosta.press("🚫 Исключить"))
        self.assertIn("🚫 Комната 315 исключена старостой этажа", self.tg_resident.text)
        self.tg_resident.say(BTN_BOOK)
        self.assertIn("🚫 Комната 315 исключена из записи на стирку", self.tg_resident.text)

    # ------------------------------------------------------------------ #
    #  Заявки
    # ------------------------------------------------------------------ #
    def test_vk_change_request_goes_to_telegram_starosta(self):
        starosta = self.tg_user(2001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.vk_resident.tap(BTN_PROFILE)
        self.vk_resident.press("✏️ Изменить")
        self.vk_resident.say("Иванова").say("314")
        self.assertIn("📨 Заявка отправлена старосте этажа", self.vk_resident.text)

        request = starosta.last
        self.assertIn("📝 <b>Заявка на изменение данных</b>", request["text"])
        self.assertIn("Стало: Иванова, к.314 (этаж 3)", request["text"])
        self.assertIn('<a href="https://vk.com/id9002">профиль ВКонтакте</a>', request["text"])
        self.assertEqual(["✅ Принять", "❌ Отклонить"], starosta.labels())
        self.assertEqual("chg:ok:1", self.tg.buttons(request)[0].callback_data)

        self.assertEqual("Заявка принята ✅", starosta.press("✅ Принять"))
        row = self.vk_resident.row
        self.assertEqual(("Иванова", "314", None), (row["surname"], row["room"], row["telegram_id"]))
        self.assertIn("✅ Изменение данных подтверждено старостой этажа.", self.vk_resident.text)
        self.assertTrue(self.vk_resident.menu_labels())

    def test_telegram_change_request_goes_to_vk_starosta(self):
        starosta = self.vk_user(9001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.tg_resident.say(BTN_PROFILE)
        self.tg_resident.press("✏️ Изменить")
        self.tg_resident.say("Сидоров").say("320")

        self.assertIn("Было: Сидоров, к.315 (этаж 3)\nСтало: Сидоров, к.320 (этаж 3)", starosta.text)
        self.assertIn("t.me/sidorov", starosta.text)
        self.assertEqual("Заявка отклонена ❌", starosta.press("❌ Отклонить"))
        self.assertIn("❌ Заявка на изменение данных отклонена старостой этажа.", self.tg_resident.text)
        self.assertEqual("315", self.tg_resident.row["room"])

    def test_starosta_request_from_vk_approved_in_telegram(self):
        admin = self.tg_user(TG_ADMIN)
        self.vk_resident.tap(BTN_PROFILE)
        self.assertEqual("Заявка отправлена", self.vk_resident.press("⭐ Я староста"))
        self.assertIn("📨 <b>Заявка на старосту</b>", admin.text)
        self.assertIn('vk: <a href="https://vk.com/id9002">id9002</a>', admin.text)
        self.assertEqual("Заявка одобрена ✅", admin.press("✅ Одобрить"))
        self.assertEqual("starosta", self.vk_resident.row["role"])
        self.assertIn("вы староста 3 этажа", self.vk_resident.text)
        self.assertIn(BTN_STAROSTA, self.vk_resident.menu_labels())

    # ------------------------------------------------------------------ #
    #  Сообщения администратору
    # ------------------------------------------------------------------ #
    def test_support_between_platforms(self):
        tg_admin, vk_admin = self.tg_user(TG_ADMIN), self.vk_user(VK_ADMIN)
        self.vk_resident.tap(BTN_SUPPORT).say("Сломалась дверца")
        for text in (tg_admin.text, vk_admin.text):
            self.assertIn("Сломалась дверца", text)
        self.assertEqual(f"adm:rp:{self.vk_resident.row['id']}", self.tg.buttons(tg_admin.last)[0].callback_data)

        tg_admin.press("↩️ Ответить")
        tg_admin.say("Мастер придёт в среду")
        self.assertEqual("✅ Ответ отправлен.", tg_admin.text)
        self.assertEqual("✉️ Ответ администратора\n\nМастер придёт в среду", self.vk_resident.text)

        # обратное направление: жилец из Telegram, отвечает админ из VK
        self.tg_resident.say(BTN_SUPPORT).say("Когда откроют запись?")
        self.assertIn("Когда откроют запись?", vk_admin.text)
        self.assertIn("t.me/sidorov", vk_admin.text)
        vk_admin.press("↩️ Ответить")
        vk_admin.say("В понедельник в 15:00")
        self.assertEqual("✉️ <b>Ответ администратора</b>\n\nВ понедельник в 15:00", self.tg_resident.text)

    # ------------------------------------------------------------------ #
    #  Уведомление о новом жильце и недоступные получатели
    # ------------------------------------------------------------------ #
    def test_new_user_notice_is_silent_in_telegram_and_absent_in_vk(self):
        tg_starosta = self.tg_user(2001).register("Старостин", "301")
        vk_starosta = self.vk_user(9001).register("Вторая", "302")
        for s in (tg_starosta, vk_starosta):
            self.make_starosta(s.row)
        vk_admin_messages = len(self.vk.dialog(VK_ADMIN))
        vk_starosta_messages = len(self.vk.dialog(9001))

        self.vk_user(9005).register("Новиков", "320")

        for uid in (TG_ADMIN, 2001):
            notice = self.tg.last(uid)
            self.assertIn("🆕 <b>Новый пользователь</b>", notice["text"])
            self.assertIn('<a href="https://vk.com/id9005">Новиков</a>, комната 320, этаж 3', notice["text"])
            self.assertTrue(notice["silent"], "уведомление о новом жильце — без звука")
        # в VK тихих сообщений нет — туда это уведомление не отправляется
        self.assertEqual(vk_admin_messages, len(self.vk.dialog(VK_ADMIN)))
        self.assertEqual(vk_starosta_messages, len(self.vk.dialog(9001)))

    def test_notification_to_user_who_blocked_the_bot_does_not_break_flow(self):
        starosta = self.vk_user(9001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        for user, code in ((self.vk_resident, "20261007:1000"),):
            user.tap(BTN_BOOK)
            user.press_command(f"bk:c:{code}")
        self.tg_resident.say(BTN_BOOK)
        self.tg_resident.press_data("bk:c:20261007:1145")
        self.vk.blocked.add(9002)        # ошибка 901
        self.tg.blocked.add(2002)        # заблокировал бота в Telegram

        starosta.say("меню").tap(BTN_STAROSTA)
        with self.assertLogs("laundry", level="WARNING") as logs:
            for booking in self.active_bookings(3, day(10, 7)):
                self.assertEqual("Запись отменена", starosta.press_command(f"m:3:xy:{booking['id']}"))
        self.assertEqual([], self.active_bookings(3, day(10, 7)))
        text = "\n".join(logs.output)
        self.assertIn("ошибка 901", text)
        self.assertIn("bot was blocked by the user", text)
        self.assertNotIn(config.VK_TOKEN, text)
        self.assertNotIn(config.BOT_TOKEN, text)

    def test_without_vk_token_telegram_bot_works_and_skips_vk(self):
        starosta = self.tg_user(2001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.vk_resident.tap(BTN_BOOK)
        self.vk_resident.press_command("bk:c:20261007:1000")
        booking = self.active_bookings(3, day(10, 7))[0]
        calls = len(self.vk.calls)
        with mock.patch.object(config, "VK_TOKEN", ""):
            starosta.say("/start").say(BTN_STAROSTA)
            self.assertEqual("Запись отменена", starosta.press_data(f"m:3:xy:{booking['id']}"))
            starosta.say(BTN_PROFILE)
            self.assertNotIn("🔗 Привязать ВКонтакте", starosta.labels())
        self.assertEqual(calls, len(self.vk.calls), "без VK_TOKEN к VK API не обращаемся")
        self.assertEqual([], self.active_bookings(3, day(10, 7)))

    def test_without_bot_token_vk_bot_works_and_skips_telegram(self):
        starosta = self.vk_user(9001).register("Старостин", "301")
        self.make_starosta(starosta.row)
        self.tg_resident.say(BTN_BOOK)
        self.tg_resident.press_data("bk:c:20261007:1000")
        booking = self.active_bookings(3, day(10, 7))[0]
        sent = len(self.tg.sent)
        with mock.patch.object(config, "BOT_TOKEN", ""):
            starosta.say("меню").tap(BTN_STAROSTA)
            self.assertEqual("Запись отменена", starosta.press_command(f"m:3:xy:{booking['id']}"))
        self.assertEqual(sent, len(self.tg.sent))

    def test_same_texts_on_both_platforms(self):
        """Текст для VK получается из того же HTML, что уходит в Telegram."""
        booking = {"slot_date": day(10, 7), "slot_start": hm(10, 0), "slot_end": hm(10, 45)}
        html = render.cancelled_by_manager_text(booking, {"telegram_id": TG_ADMIN})
        self.assertEqual("❗ Ваша запись на стирку <b>Среда, 07.10.2026, 10:00–10:45</b> отменена администратором.", html)
        self.assertEqual("❗ Ваша запись на стирку Среда, 07.10.2026, 10:00–10:45 отменена администратором.",
                         render.plain(html))
        self.assertEqual(db.admins()[0]["telegram_id"], TG_ADMIN)
