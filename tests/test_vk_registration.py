"""VK: первый вход, регистрация с ошибками ввода, главное меню, профиль, правила."""
from laundry import db
from laundry.keyboards import (BTN_ADMIN, BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_REGISTER, BTN_RULES, BTN_SHOWER, BTN_SUPPORT)

from .base import VK_ADMIN, BotTestCase


class RegistrationTest(BotTestCase):
    def test_start_button_begins_registration(self):
        user = self.vk_user(9001).start()
        self.assertIn("Привет", user.texts()[0])
        self.assertIn("фамилию", user.text)
        self.assertIsNone(user.row)

    def test_start_words(self):
        for i, word in enumerate(("начать", "Начать", "/start", "старт")):
            user = self.vk_user(9100 + i).say(word)
            self.assertIn("фамилию", user.text, word)

    def test_full_registration(self):
        user = self.vk_user(9001).start().say("иванов").say("312")
        row = user.row
        self.assertEqual(("Иванов", "312", 3, None), (row["surname"], row["room"], row["floor"], row["wing"]))
        self.assertIsNone(row["telegram_id"])
        self.assertIn("Готово! Иванов, комната 312, этаж 3", user.text)
        self.assertEqual([BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_RULES, BTN_SHOWER, BTN_SUPPORT], user.menu_labels())

    def test_surname_errors(self):
        user = self.vk_user(9001).start()
        for bad in ("123", "И", "Иванов1", "!!!"):
            user.say(bad)
            self.assertIn("только буквы", user.text, bad)
        user.say("петрова-водкина")
        self.assertIn("номер комнаты", user.text)
        user.say("204")
        self.assertEqual("Петрова-Водкина", user.row["surname"])

    def test_room_errors(self):
        user = self.vk_user(9001).start().say("Иванов")
        cases = {
            "abc": "3 цифры",
            "12": "3 цифры",
            "336": "На 3 этаже комнаты с 301 по 335 и 323а",   # на 3 этаже вместо 336 есть 323а
            "101": "Для 1 этажа запись на стирку не проводится",
            "612": "В общежитии 5 этажей",
            "300": "Такой комнаты нет",
            "537": "На 5 этаже комнаты с 501 по 536",
            "324а": "Комнаты 324а нет",
        }
        for room, expected in cases.items():
            user.say(room)
            self.assertIn(expected, user.text, room)
            self.assertIsNone(user.row, room)
        user.say("335")
        self.assertEqual("335", user.row["room"])

    def test_letter_room(self):
        self.vk_user(9001).register("Иванов", "323а")
        self.assertEqual(("323а", 3), (db.get_user_by_vk(9001)["room"], db.get_user_by_vk(9001)["floor"]))
        # заглавная и латинская буква — та же комната
        self.assertEqual("323а", self.vk_user(9002).start().say("Петров").say("323А").row["room"])
        self.assertEqual("323а", self.vk_user(9003).start().say("Сидоров").say("323a").row["room"])

    def test_floor5_wing(self):
        self.assertEqual(1, self.vk_user(9001).register("Иванов", "515").row["wing"])
        self.assertEqual(2, self.vk_user(9002).register("Петров", "505").row["wing"])
        self.assertEqual(2, self.vk_user(9003).register("Сидоров", "530").row["wing"])

    def test_menu_and_cancel_words(self):
        user = self.vk_user(9001).register("Иванов", "312")
        user.say("меню")
        self.assertEqual("Главное меню 👇", user.text)
        user.tap(BTN_PROFILE)
        self.assertIn("Фамилия: Иванов", user.text)
        self.assertIn("Комната: 312 (этаж 3)", user.text)
        user.say("какой-то текст")
        self.assertIn("Не понял", user.text)

    def test_cancel_without_registration_restarts_it(self):
        user = self.vk_user(9001).start().say("отмена")
        self.assertIn("фамилию", user.text)

    def test_rules_and_help(self):
        user = self.vk_user(9001).register("Иванов", "312")
        user.tap(BTN_RULES)
        self.assertIn("Правила записи на стирку", user.text)
        self.assertIn("Этажи 2–3", user.text)
        self.assertNotIn("4 этаж\n", user.text)
        self.assertNotIn("5 этаж\n", user.text)
        user.say("помощь")
        self.assertIn("отмена — отменить текущее действие", user.text)
        user.say("id")
        self.assertEqual("Ваш id ВКонтакте: 9001", user.text)
        self.assertEqual("Ваш id ВКонтакте: 9777", self.vk_user(9777).say("ID").text, "работает и до регистрации")

    def test_non_text_message(self):
        user = self.vk_user(9001).register("Иванов", "312")
        user.say("")   # стикер или фото без подписи
        self.assertIn("только текст", user.text)

    def test_admin_first_entry(self):
        admin = self.vk_user(VK_ADMIN).start()
        self.assertIn("администратор", admin.text)
        self.assertEqual([BTN_REGISTER, BTN_RULES, BTN_SHOWER, BTN_ADMIN], admin.menu_labels())
        admin.tap(BTN_REGISTER).say("Админов").say("210")
        # зарегистрированный админ записывается как жилец, кнопки «Написать администратору» у него нет
        self.assertEqual([BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_RULES, BTN_SHOWER, BTN_ADMIN], admin.menu_labels())

    def test_messages_in_chats_are_ignored(self):
        from laundry.vk import dispatcher
        dispatcher.handle_event(self.client, {"type": "message_new", "object": {"message": {
            "from_id": 9001, "peer_id": 2_000_000_005, "text": "начать"}}})
        self.assertEqual([], self.vk.sent)
