"""Telegram-бот после доработок под общую базу работает как раньше.

Сценарии идут через настоящий telebot: bot.process_new_updates -> фильтры -> обработчики из laundry/handlers.
"""
from laundry import config, db
from laundry.keyboards import (BTN_ADMIN, BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_REGISTER, BTN_RULES, BTN_STAROSTA,
                               BTN_SUPPORT)

from .base import TG_ADMIN, BotTestCase, day, hm


class TelegramResidentTest(BotTestCase):
    def test_registration_with_input_errors(self):
        user = self.tg_user(2001, "ivanov").say("/start")
        self.assertIn("Привет", user.texts()[0])
        self.assertIn("Введите вашу <b>фамилию</b>", user.text)
        user.say("123")
        self.assertIn("только буквы", user.text)
        user.say("иванов")
        self.assertIn("номер комнаты", user.text)
        for room, expected in (("336", "На 3 этаже комнаты с 301 по 335 и 323а"),
                               ("101", "Для 1 этажа запись на стирку не проводится"),
                               ("abc", "3 цифры")):
            user.say(room)
            self.assertIn(expected, user.text)
        user.say("323а")
        row = user.row
        self.assertEqual(("Иванов", "323а", 3, "ivanov", None),
                         (row["surname"], row["room"], row["floor"], row["username"], row["vk_id"]))
        self.assertIn("✅ Готово! <b>Иванов</b>, комната 323а, этаж 3.", user.text)
        self.assertEqual([BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_RULES, BTN_SUPPORT], user.menu_labels())

    def test_booking_flow(self):
        user = self.tg_user(2001).register("Иванов", "312")
        user.say(BTN_BOOK)
        week = user.last
        self.assertTrue(week["photo"])
        self.assertIn("🧺 <b>Расписание стирки · этаж 3</b>", week["text"])
        self.assertEqual(["Вт 06.10 · 8 своб.", "Ср 07.10 · 10 своб.", "Чт 08.10 · 10 своб.", "Пт 09.10 · 10 своб.",
                          "Сб 10.10 · 10 своб.", "Вс 11.10 · 10 своб.", "🔄 Обновить"], user.labels())
        user.press("Ср 07.10")
        self.assertIn("📅 <b>Среда, 07.10.2026</b> · этаж 3", user.text)
        self.assertEqual(11, len(user.labels()), "в Telegram все 10 времён и «К неделе» — в одной клавиатуре")
        user.press("20:30–21:15")
        self.assertEqual("Записано ✅", user.press("✅ Записаться"))
        self.assertIn("✅ Вы записаны на стирку!", user.text)
        self.assertEqual(week["message_id"], user.last["message_id"], "весь путь — в одном сообщении")
        booking, = self.active_bookings(3, day(10, 7))
        self.assertEqual((hm(20, 30), "312"), (booking["slot_start"], booking["room"]))

        user.say("/my")
        self.assertIn("1. Среда, 07.10.2026, 20:30–21:15 (этаж 3)", user.text)
        user.press("❌ Отменить")
        self.assertEqual("Запись отменена", user.press("✅ Да, отменить"))
        self.assertEqual([], self.active_bookings(3, day(10, 7)))

    def test_limits(self):
        user = self.tg_user(2001).register("Иванов", "312")
        user.say("/book")
        self.assertEqual("Записано ✅", user.press_data("bk:c:20261008:1000"))
        self.assertEqual("Записано ✅", user.press_data("bk:c:20261008:1145"))
        self.assertEqual("Вы уже записаны максимальное число раз на этот день.",
                         user.press_data("bk:c:20261008:1330"))
        f5 = self.tg_user(2002).register("Петров", "515")
        mate = self.tg_user(2003).register("Сидоров", "515")
        f5.say("/book")
        self.assertEqual(["Вт 06.10 · 7 своб.", "Чт 08.10 · 9 своб.", "Сб 10.10 · 9 своб.", "🔄 Обновить"], f5.labels())
        self.assertEqual("Записано ✅", f5.press_data("bk:c:20261008:0830"))
        mate.say("/book")
        self.assertEqual("Ваша комната уже записана на этот день (на 5 этаже — не чаще 1 раза в день).",
                         mate.press_data("bk:c:20261008:1030"))

    def test_commands_and_other_text(self):
        user = self.tg_user(2001).register("Иванов", "312")
        user.say("/help")
        self.assertIn("/cancel — отменить текущее действие", user.text)
        user.say("/rules")
        self.assertIn("Правила записи на стирку", user.text)
        user.say(BTN_SUPPORT).say("/cancel")
        self.assertEqual("Действие отменено.", user.text)
        user.say("что-то непонятное")
        self.assertIn("Не понял", user.text)
        user.say(BTN_PROFILE)
        self.assertIn("Фамилия: <b>Иванов</b>", user.text)
        self.assertEqual(["✏️ Изменить фамилию / комнату", "⭐ Я староста этажа — подать заявку",
                          "🔗 Привязать ВКонтакте"], user.labels())


class TelegramStarostaAdminTest(BotTestCase):
    def setUp(self):
        super().setUp()
        self.starosta = self.tg_user(2001, "boss").register("Старостин", "301")
        self.make_starosta(self.starosta.row)
        self.resident = self.tg_user(2002, "ivanov").register("Иванов", "312")
        self.admin = self.tg_user(TG_ADMIN)

    def panel(self):
        self.starosta.say("/start").say(BTN_STAROSTA)
        self.assertIn("⭐ <b>Управление этажом 3</b>", self.starosta.text)
        return self.starosta

    def test_starosta_cancels_booking_and_resident_is_notified(self):
        self.resident.say(BTN_BOOK)
        self.resident.press_data("bk:c:20261007:1000")
        s = self.panel()
        s.press("📅 Расписание")
        s.press("Ср 07.10")
        s.press("❌ 10:00 к.312 Иванов")
        self.assertEqual("Запись отменена", s.press("✅ Да, отменить"))
        self.assertEqual("❗ Ваша запись на стирку <b>Среда, 07.10.2026, 10:00–10:45</b> отменена старостой этажа.",
                         self.resident.text)
        self.assertEqual("⛔ Нет прав на управление этим этажом.", s.press_data("m:4:w"))
        self.assertEqual("⛔ Нет прав на управление этим этажом.", self.resident.press_data("m:3:w"))

    def test_closure_and_ban(self):
        self.resident.say(BTN_BOOK)
        self.resident.press_data("bk:c:20261008:1000")
        s = self.panel()
        s.press("🔒 Закрыть запись")
        s.press("📆 Весь день")
        s.say("08.10")
        s.press("Без причины")
        self.assertEqual("Готово", s.press("✅ Закрыть запись"))
        self.assertIn("запись на это время закрыта старостой этажа", self.resident.text)
        self.assertEqual("Староста закрыл запись на это время.", self.resident.press_data("bk:c:20261008:1145"))

        s.say(BTN_STAROSTA)
        s.press("🚫 Исключить комнату")
        s.say("312")
        self.assertEqual("Комната исключена", s.press("🚫 Исключить"))
        self.assertIn("🚫 Комната 312 исключена старостой этажа", self.resident.text)
        s.press("✅ Исключённые")
        self.assertEqual("Комната 312 возвращена", s.press("✅ Вернуть комнату 312"))
        self.assertEqual("✅ Комнате 312 снова доступна запись на стирку.", self.resident.text)

    def test_change_request(self):
        self.resident.say(BTN_PROFILE)
        self.resident.press("✏️ Изменить")
        self.resident.say("Иванова").say("314")
        self.assertIn("📨 Заявка отправлена старосте этажа", self.resident.text)
        request = self.starosta.last
        self.assertIn("Стало: Иванова, к.314 (этаж 3)", request["text"])
        self.assertIn('<a href="https://t.me/ivanov">@ivanov</a>', request["text"])
        self.assertEqual("Заявка принята ✅", self.starosta.press("✅ Принять"))
        row = self.resident.row
        self.assertEqual(("Иванова", "314", "ivanov"), (row["surname"], row["room"], row["username"]))
        self.assertIn("✅ Изменение данных подтверждено старостой этажа.", self.resident.text)

    def test_admin_approves_starosta_from_notification(self):
        self.resident.say(BTN_PROFILE)
        self.assertEqual("Заявка отправлена", self.resident.press("⭐ Я староста"))
        notice = self.admin.last
        self.assertIn("telegram_id: <code>2002</code>", notice["text"])
        self.assertIn("Сейчас старосты этого этажа: Старостин (к.301)", notice["text"])
        self.assertEqual("Заявка одобрена ✅", self.admin.press("✅ Одобрить"))
        self.assertEqual("Заявка Иванов (к.312) одобрена ✅", self.admin.text)
        self.assertIn("⭐ Администратор подтвердил", self.resident.text)
        self.assertIn(BTN_STAROSTA, self.resident.menu_labels())
        self.assertEqual("starosta", self.resident.row["role"])

    def test_admin_panel_support_and_logs(self):
        self.admin.say("/start")
        self.assertEqual([BTN_REGISTER, BTN_RULES, BTN_ADMIN], self.admin.menu_labels())
        self.admin.say(BTN_ADMIN)
        self.assertEqual(["📨 Заявки старост (0)", "📝 Заявки на изменение данных (0)", "🏢 Управление этажом",
                          "⭐ Старосты", "📊 Статистика", "📄 Логи"], self.admin.labels())
        self.admin.press("📊 Статистика")
        self.assertIn("Зарегистрировано жильцов: 2", self.admin.text)
        self.admin.press_data("adm:st")
        self.assertIn("• 3 этаж — Старостин (к.301)", self.admin.text)
        self.admin.press_data("adm:floors")
        self.admin.press("4 этаж")
        self.assertIn("Управление этажом 4", self.admin.text)

        self.resident.say(BTN_SUPPORT).say("Вопрос <про> стирку")
        self.assertIn("Вопрос &lt;про&gt; стирку", self.admin.text)
        self.admin.press("↩️ Ответить")
        self.admin.say("Ответ")
        self.assertEqual("✉️ <b>Ответ администратора</b>\n\nОтвет", self.resident.text)

        (config.LOG_DIR / "bot.log").write_text("лог\n", encoding="utf-8")
        (config.LOG_DIR / "vk_bot.log").write_text("лог vk\n", encoding="utf-8")
        try:
            self.assertEqual("Логи отправлены", self.admin.press_data("adm:log"))
        finally:
            (config.LOG_DIR / "bot.log").unlink()
            (config.LOG_DIR / "vk_bot.log").unlink()
        self.assertEqual(["📄 bot.log", "📄 vk_bot.log"], [d["caption"] for d in self.tg.documents])

        self.assertEqual("⛔ Только для администратора.", self.resident.press_data("adm"))
        self.resident.say("/admin")
        self.assertEqual("Команда доступна только администратору.", self.resident.text)

    def test_new_user_notice(self):
        newcomer = self.tg_user(2005, "newbie").register("Новиков", "320")
        for uid in (TG_ADMIN, 2001):
            notice = self.tg.last(uid)
            self.assertEqual('🆕 <b>Новый пользователь</b>\nНовиков, комната 320, этаж 3\n'
                             '<a href="https://t.me/newbie">@newbie</a>', notice["text"])
            self.assertTrue(notice["silent"])
        self.assertNotIn("Новый пользователь", "\n".join(newcomer.texts()))
        self.assertEqual(2, len(db.admins()), "по одному администратору из ADMIN_IDS и VK_ADMIN_IDS")
