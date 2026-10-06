"""VK: панель старосты — отмена чужих записей, закрытия, исключения, заявки на изменение, чужой этаж."""
from laundry import db
from laundry.keyboards import BTN_BOOK, BTN_PROFILE, BTN_STAROSTA

from .base import TG_ADMIN, VK_ADMIN, BotTestCase, day, hm


class StarostaCase(BotTestCase):
    def setUp(self):
        super().setUp()
        self.starosta = self.vk_user(9001).register("Старостин", "301")
        self.make_starosta(self.starosta.row)
        self.resident = self.vk_user(9002).register("Иванов", "312")
        self.other_floor = self.vk_user(9003).register("Петров", "412")

    def open_panel(self):
        self.starosta.say("меню")
        self.assertIn(BTN_STAROSTA, self.starosta.menu_labels())
        self.starosta.tap(BTN_STAROSTA)
        self.assertIn("Управление этажом 3", self.starosta.text)
        return self.starosta

    def book(self, user, code: str):
        user.tap(BTN_BOOK)
        self.assertEqual("Записано ✅", user.press_command(f"bk:c:{code}"))


class PanelTest(StarostaCase):
    def test_panel_fits_vk_limits_and_lists_sections(self):
        s = self.open_panel()
        self.assertEqual(["📅 Расписание и отмена записей", "📝 Заявки на изменение данных (0)", "🔒 Закрыть запись",
                          "🔓 Закрытые периоды", "🚫 Исключить комнату", "🚫 Исключить жильца", "✅ Исключённые",
                          "👥 Жильцы этажа"], s.labels())
        self.assertIn("Староста: Старостин (к.301)", s.text)

    def test_resident_has_no_panel(self):
        self.assertNotIn(BTN_STAROSTA, self.resident.say("меню").menu_labels())
        self.resident.say(BTN_STAROSTA)   # набрал подпись вручную
        self.assertEqual("Панель доступна только старостам этажей.", self.resident.text)
        self.assertEqual("⛔ Нет прав на управление этим этажом.", self.resident.press_command("m:3"))
        self.assertEqual("⛔ Нет прав на управление этим этажом.", self.resident.press_command("m:3:bry:301"))

    def test_foreign_floor_is_denied(self):
        self.book(self.other_floor, "20261007:1000")
        booking = self.active_bookings(4, day(10, 7))[0]
        s = self.open_panel()
        for command in ("m:4", "m:4:w", f"m:4:xy:{booking['id']}", "m:4:bry:412", "m:4:cl", "m:4:users"):
            self.assertEqual("⛔ Нет прав на управление этим этажом.", s.press_command(command), command)
        # и через «свой» этаж до чужой записи не добраться
        self.assertEqual("Запись не найдена или уже отменена.", s.press_command(f"m:3:xy:{booking['id']}"))
        self.assertEqual("Комната 412 не на 3 этаже.", s.press_command("m:3:bry:412"))
        self.assertEqual(1, len(self.active_bookings(4, day(10, 7))))
        self.assertIsNone(db.get_room_ban("412"))

    def test_users_list_mentions_vk_profiles(self):
        s = self.open_panel()
        s.press("👥 Жильцы этажа")
        self.assertIn("Жильцы этажа 3 — 2", s.text)
        self.assertIn("к.301 — [id9001|Старостин] ⭐", s.text)
        self.assertIn("к.312 — [id9002|Иванов]", s.text)
        self.assertNotIn("Петров", s.text)


class CancelBookingTest(StarostaCase):
    def test_cancel_foreign_booking_notifies_resident(self):
        self.book(self.resident, "20261007:1000")
        s = self.open_panel()
        s.press("📅 Расписание")
        self.assertTrue(s.last["attachment"].startswith("photo"))
        self.assertIn("Ср 07.10 · записей: 1", s.labels())
        s.press("Ср 07.10")
        self.assertIn("🔴 10:00–10:45 — к.312 Иванов", s.text)
        self.assertEqual(["❌ 10:00 к.312 Иванов", "⬅️ К неделе"], s.labels())
        s.press("❌ 10:00")
        self.assertIn("Жилец получит уведомление", s.text)
        before = len(self.vk.dialog(9002))
        self.assertEqual("Запись отменена", s.press("✅ Да, отменить"))

        self.assertEqual([], self.active_bookings(3, day(10, 7)))
        self.assertEqual(before + 1, len(self.vk.dialog(9002)))
        self.assertEqual("❗ Ваша запись на стирку Среда, 07.10.2026, 10:00–10:45 отменена старостой этажа.",
                         self.resident.text)
        self.assertIn("Отменять нечего", s.text)
        self.assertEqual("Запись уже отменена", s.press_command("m:3:x:1"))

    def test_own_booking_cancelled_without_self_notification(self):
        self.book(self.starosta, "20261007:1000")
        s = self.open_panel()
        count = len(self.vk.dialog(9001))
        booking = self.active_bookings(3, day(10, 7))[0]
        self.assertEqual("Запись отменена", s.press_command(f"m:3:xy:{booking['id']}"))
        self.assertEqual(count, len(self.vk.dialog(9001)), "самому себе уведомление не шлём")


class ClosureTest(StarostaCase):
    def test_close_full_day_cancels_and_notifies(self):
        self.book(self.resident, "20261008:1000")
        self.book(self.resident, "20261009:1000")
        s = self.open_panel()
        s.press("🔒 Закрыть запись")
        s.press("📆 Весь день")
        self.assertEqual(["Вт 06.10", "Ср 07.10", "Чт 08.10", "Пт 09.10", "Сб 10.10", "Вс 11.10", "⬅️ Назад"],
                         s.labels())
        s.press("Чт 08.10")
        self.assertIn("Чт 08.10, весь день", s.text)
        s.say("Ремонт  машинки")
        self.assertIn("Причина: Ремонт машинки", s.text)
        self.assertIn("Будет отменено записей: 1", s.text)
        self.assertEqual("Готово", s.press("✅ Закрыть запись"))
        self.assertIn("Отменено записей: 1.", s.text)

        self.assertEqual([], self.active_bookings(3, day(10, 8)))
        self.assertEqual(1, len(self.active_bookings(3, day(10, 9))))
        self.assertEqual("❗ Ваша запись на стирку Четверг, 08.10.2026, 10:00–10:45 отменена: запись на это время "
                         "закрыта старостой этажа.\nПричина: Ремонт машинки", self.resident.text)

        # жилец видит закрытие и записаться не может
        self.resident.tap(BTN_BOOK)
        self.assertIn("Чт 08.10 · мест нет", self.resident.labels())
        self.resident.press("Чт 08.10")
        self.assertIn("⚪ 10:00–10:45 — закрыто (Ремонт машинки)", self.resident.text)
        self.assertEqual("Староста закрыл запись на это время.", self.resident.press_command("bk:c:20261008:1145"))

        # снятие закрытия
        s.press("🔓 Закрытые периоды")
        self.assertIn("1. Чт 08.10, весь день — Ремонт машинки", s.text)
        self.assertEqual("Запись снова открыта", s.press("🔓 1."))
        self.assertIn("нет закрытых периодов", s.text)
        self.assertEqual("Записано ✅", self.resident.press_command("bk:c:20261008:1145"))

    def test_close_period_typed_as_dates(self):
        self.book(self.resident, "20261008:1000")
        self.book(self.resident, "20261009:1845")
        self.book(self.resident, "20261010:1000")
        s = self.open_panel()
        s.press("🔒 Закрыть запись")
        s.press("📆 Весь день")
        s.say("32.10")
        self.assertIn("Введите дату (14.10) или период (14.10-20.10)", s.text)
        s.say("01.10-02.10")
        self.assertIn("Эти даты уже прошли", s.text)
        s.say("08.10-09.10")
        self.assertIn("Чт 08.10 – Пт 09.10, весь день", s.text)
        s.press("Без причины")
        self.assertIn("Причина: —", s.text)
        self.assertIn("Будет отменено записей: 2", s.text)
        before = len(self.vk.dialog(9002))
        self.assertEqual("Готово", s.press("✅ Закрыть запись"))
        self.assertEqual(before + 2, len(self.vk.dialog(9002)), "по уведомлению на каждую отменённую запись")
        self.assertEqual(1, len(db.bookings_between(3, day(10, 6), day(10, 11))))

    def test_close_part_of_day(self):
        self.book(self.resident, "20261008:1000")
        self.book(self.resident, "20261008:1845")
        s = self.open_panel()
        s.press("🔒 Закрыть запись")
        s.press("⏰ Часть дня")
        s.press("Чт 08.10")
        self.assertIn("С какой стирки закрыть запись?", s.text)
        self.assertEqual(["с 06:30", "с 08:15", "с 10:00", "с 11:45", "с 13:30", "с 15:15", "2/2 ▶", "⬅️ Назад"],
                         s.labels())
        s.press("2/2 ▶")
        s.press("с 17:00")
        self.assertEqual(["по 17:45", "по 19:30", "по 21:15", "по 23:00", "⬅️ Назад"], s.labels())
        s.press("по 21:15")
        self.assertIn("Чт 08.10, 17:00–21:15", s.text)
        s.press("Без причины")
        self.assertIn("Будет отменено записей: 1", s.text)
        self.assertEqual("Готово", s.press("✅ Закрыть запись"))

        left = self.active_bookings(3, day(10, 8))
        self.assertEqual([hm(10, 0)], [b["slot_start"] for b in left])
        self.resident.tap(BTN_BOOK)
        self.resident.press("Чт 08.10")
        for line in ("⚪ 17:00–17:45 — закрыто", "⚪ 20:30–21:15 — закрыто", "🟢 22:15–23:00 — свободно",
                     "🟢 15:15–16:00 — свободно"):
            self.assertIn(line, self.resident.text)

    def test_stale_closure_buttons(self):
        s = self.open_panel()
        for command in ("m:3:ps:0", "m:3:pe:1", "m:3:nr", "m:3:cly"):
            self.assertEqual("Сессия устарела — начните заново.", s.press_command(command), command)
        self.assertEqual([], db.active_closures(3, day(10, 6)))

    def test_cancel_word_leaves_dialog(self):
        s = self.open_panel()
        s.press("🔒 Закрыть запись")
        s.press("📆 Весь день")
        s.say("отмена")
        self.assertEqual("Действие отменено.", s.text)
        s.say("08.10")      # дату больше не ждём
        self.assertIn("Не понял", s.text)


class BanTest(StarostaCase):
    def test_ban_and_unban_room(self):
        roommate = self.vk_user(9004).register("Сидоров", "312")
        self.book(self.resident, "20261008:1000")
        self.book(roommate, "20261009:1000")
        s = self.open_panel()
        s.press("🚫 Исключить комнату")
        s.say("412")
        self.assertIn("Комната 412 не на 3 этаже", s.text)
        s.say("312")
        self.assertIn("Исключить комнату 312 из записи на стирку?", s.text)
        self.assertIn("Жильцы: Иванов, Сидоров", s.text)
        self.assertEqual("Комната исключена", s.press("🚫 Исключить"))
        self.assertIn("Отменено записей: 2", s.text)

        for user in (self.resident, roommate):
            self.assertIn("🚫 Комната 312 исключена старостой этажа из записи на стирку", user.text)
            user.tap(BTN_BOOK)
            self.assertIn("🚫 Комната 312 исключена из записи на стирку", user.text)
            self.assertEqual("🚫 Комната 312 исключена из записи на стирку. Обратитесь к старосте этажа.",
                             user.press_command("bk:c:20261010:1000"))
        self.assertEqual([], db.bookings_between(3, day(10, 6), day(10, 11)))

        s.press("✅ Исключённые")
        self.assertIn("Комнаты: 312", s.text)
        self.assertEqual("Комната 312 возвращена", s.press("✅ Вернуть комнату 312"))
        self.assertIn("никто не исключён", s.text)
        self.assertEqual("✅ Комнате 312 снова доступна запись на стирку.", self.resident.text)
        self.assertEqual("Записано ✅", self.resident.press_command("bk:c:20261010:1000"))

    def test_cannot_ban_own_room(self):
        s = self.open_panel()
        s.press("🚫 Исключить комнату")
        s.say("301")
        self.assertEqual("Нельзя исключить свою комнату.", s.press("🚫 Исключить"))
        self.assertIsNone(db.get_room_ban("301"))

    def test_ban_and_unban_user(self):
        roommate = self.vk_user(9004).register("Сидоров", "312")
        self.book(self.resident, "20261008:1000")
        self.book(roommate, "20261009:1000")
        s = self.open_panel()
        s.press("🚫 Исключить жильца")
        s.say("312")
        self.assertEqual("Кого из комнаты 312 исключить?", s.text)
        self.assertEqual(["Иванов (к.312)", "Сидоров (к.312)", "✖️ Отмена"], s.labels())
        s.press("Иванов")
        self.assertIn("Исключить Иванов (к.312) из записи на стирку?", s.text)
        self.assertEqual("Жилец исключён", s.press("🚫 Исключить"))

        self.assertTrue(self.resident.row["is_banned"])
        self.assertIn("🚫 Вы исключены старостой этажа из записи на стирку", self.resident.text)
        self.assertEqual([], self.active_bookings(3, day(10, 8)))
        self.assertEqual(1, len(self.active_bookings(3, day(10, 9))), "сосед по комнате не затронут")
        self.resident.tap(BTN_BOOK)
        self.assertIn("Староста исключил вас из записи", self.resident.text)
        self.resident.tap(BTN_PROFILE)
        self.assertIn("🚫 Вы исключены из записи старостой", self.resident.text)

        s.press("✅ Исключённые")
        self.assertIn("Жильцы: Иванов (к.312)", s.text)
        self.assertEqual("Жилец возвращён", s.press("✅ Вернуть Иванов"))
        self.assertEqual("✅ Вам снова доступна запись на стирку.", self.resident.text)
        self.assertFalse(self.resident.row["is_banned"])

    def test_starosta_cannot_ban_self(self):
        s = self.open_panel()
        s.press("🚫 Исключить жильца")
        s.say("301")
        self.assertIn("нет жильцов, которых можно исключить", s.text)
        self.assertEqual("Нельзя исключить самого себя.", s.press_command(f"m:3:buy:{s.row['id']}"))


class ChangeRequestTest(StarostaCase):
    def request_change(self, user, surname: str, room: str):
        user.tap(BTN_PROFILE)
        user.press("✏️ Изменить")
        self.assertIn("Заявка на изменение данных", user.text)
        user.say(surname).say(room)

    def test_approve(self):
        self.book(self.resident, "20261008:1000")
        self.request_change(self.resident, "Иванова", "314")
        self.assertIn("📨 Заявка отправлена старосте этажа", self.resident.text)
        self.assertEqual("312", self.resident.row["room"], "до решения старосты данные прежние")
        self.resident.tap(BTN_PROFILE)
        self.assertIn("Заявка на изменение ждёт старосту", self.resident.text)
        self.assertNotIn("✏️ Изменить фамилию / комнату", self.resident.labels())

        s = self.starosta
        self.assertIn("📝 Заявка на изменение данных", s.text)
        self.assertIn("Было: Иванов, к.312 (этаж 3)\nСтало: Иванова, к.314 (этаж 3)", s.text)
        self.assertIn("[id9002|профиль ВКонтакте]", s.text)
        self.assertEqual(["✅ Принять", "❌ Отклонить"], s.labels())
        self.assertEqual("Заявка принята ✅", s.press("✅ Принять"))
        self.assertEqual(["⬅️ В панель"], s.labels())

        row = self.resident.row
        self.assertEqual(("Иванова", "314"), (row["surname"], row["room"]))
        self.assertIn("✅ Изменение данных подтверждено старостой этажа.", self.resident.text)
        self.assertIn("Комната изменилась — ваши будущие записи (1) отменены.", self.resident.text)
        self.assertTrue(self.resident.menu_labels(), "вместе с уведомлением приходит главное меню")
        self.assertEqual([], self.active_bookings(3, day(10, 8)))
        self.assertEqual("Заявка уже обработана.", s.press_command("chg:ok:1"))

    def test_reject(self):
        self.request_change(self.resident, "Иванов", "320")
        self.assertEqual("Заявка отклонена ❌", self.starosta.press("❌ Отклонить"))
        self.assertEqual("312", self.resident.row["room"])
        self.assertIn("❌ Заявка на изменение данных отклонена старостой этажа.", self.resident.text)

    def test_unchanged_data_needs_no_request(self):
        self.request_change(self.resident, "Иванов", "312")
        self.assertEqual("Данные не изменились — заявка не нужна.", self.resident.text)
        self.assertEqual([], db.pending_change_requests())

    def test_list_in_panel_and_foreign_starosta(self):
        self.request_change(self.resident, "Иванов", "320")
        s = self.open_panel()
        self.assertIn("📝 Заявки на изменение данных (1)", s.labels())
        s.press("📝 Заявки на изменение данных")
        self.assertIn("1. Было: Иванов, к.312 (этаж 3) → Стало: Иванов, к.320 (этаж 3)", s.text)
        self.assertEqual(["✅ 1. Принять", "❌ 1. Отклонить", "⬅️ Назад"], s.labels())
        # староста другого этажа решить заявку не может
        self.make_starosta(self.other_floor.row)
        self.assertEqual("⛔ Эту заявку может рассмотреть только староста этажа.",
                         self.other_floor.press_command("chg:ok:1", cmid=1))
        self.assertEqual("Заявка принята ✅", s.press("✅ 1. Принять"))

    def test_move_to_another_floor_goes_to_both_starostas(self):
        self.make_starosta(self.other_floor.row)
        self.request_change(self.resident, "Иванов", "420")
        for starosta in (self.starosta, self.other_floor):
            self.assertIn("Стало: Иванов, к.420 (этаж 4)", starosta.text)
        self.assertEqual("Заявка принята ✅", self.other_floor.press("✅ Принять"))
        self.assertEqual(4, self.resident.row["floor"])
        self.assertEqual("Заявка уже обработана.", self.starosta.press("✅ Принять"))

    def test_starosta_own_request_goes_to_admins(self):
        self.request_change(self.starosta, "Старостин", "420")
        admin = self.vk_user(VK_ADMIN)
        self.assertIn("Было: Старостин, к.301 (этаж 3)", admin.text)
        self.assertIn("Было: Старостин, к.301 (этаж 3)", self.tg.last(TG_ADMIN)["text"])
        self.assertEqual("Заявка принята ✅", admin.press("✅ Принять"))
        row = self.starosta.row
        self.assertEqual(("420", "resident"), (row["room"], row["role"]))
        self.assertIn("Вы переехали на другой этаж, поэтому роль старосты снята.", self.starosta.text)
        self.assertNotIn(BTN_STAROSTA, self.starosta.menu_labels())
