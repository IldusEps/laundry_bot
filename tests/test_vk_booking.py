"""VK: запись на стирку, лимиты, занятое время, гонка за слот, «Мои записи», 5 этаж, открытие недели."""
import threading

from laundry import db
from laundry.keyboards import BTN_BOOK, BTN_MY

from .base import BotTestCase, at, day, hm


class BookingFlowTest(BotTestCase):
    """Вторник 06.10.2026, 09:00 — открыта неделя вт 06.10 – вс 11.10."""

    def setUp(self):
        super().setUp()
        self.user = self.vk_user(9001).register("Иванов", "312")

    def test_week_is_a_picture_with_day_buttons(self):
        self.user.tap(BTN_BOOK)
        message = self.user.last
        self.assertTrue(message["attachment"].startswith("photo-77_"), message["attachment"])
        self.assertEqual(1, len(self.vk.photos))
        self.assertEqual(9001, self.vk.photos[0]["peer_id"])
        self.assertIn("Расписание стирки · этаж 3", message["text"])
        self.assertIn("Вт 06.10 – Вс 11.10", message["text"])
        self.assertIn("откроется в понедельник, 12.10 в 15:00", message["text"])
        # вторник 09:00: стирки 06:30 и 08:15 уже начались, свободно 8 из 10
        self.assertEqual(["Вт 06.10 · 8 своб.", "Ср 07.10 · 10 своб.", "Чт 08.10 · 10 своб.", "Пт 09.10 · 10 своб.",
                          "Сб 10.10 · 10 своб.", "Вс 11.10 · 10 своб.", "🔄 Обновить"], self.user.labels())

    def test_full_booking_flow_edits_one_message(self):
        self.user.tap(BTN_BOOK)
        cmid = self.user.last["cmid"]

        self.assertIsNone(self.user.press("Ср 07.10"))
        self.assertIn("Среда, 07.10.2026 · этаж 3", self.user.text)
        self.assertIn("🟢 06:30–07:15 — свободно", self.user.text)
        self.assertIsNone(self.user.last["attachment"])   # день — текстовый список без картинки

        # 10 свободных времён + «К неделе» не помещаются в inline-клавиатуру (10 кнопок) — страницы
        self.assertEqual(["06:30–07:15", "08:15–09:00", "10:00–10:45", "11:45–12:30", "13:30–14:15", "15:15–16:00",
                          "2/2 ▶", "⬅️ К неделе"], self.user.labels())
        self.user.press("2/2 ▶")
        self.assertEqual(["17:00–17:45", "18:45–19:30", "20:30–21:15", "22:15–23:00", "◀ 1/2", "⬅️ К неделе"],
                         self.user.labels())

        self.user.press("20:30–21:15")
        self.assertIn("Записаться на стирку?", self.user.text)
        self.assertIn("Среда, 07.10.2026, 20:30–21:15", self.user.text)
        self.assertEqual(["✅ Записаться", "⬅️ Назад"], self.user.labels())

        self.assertEqual("Записано ✅", self.user.press("✅ Записаться"))
        self.assertIn("Вы записаны на стирку!", self.user.text)
        self.assertEqual(cmid, self.user.last["cmid"], "весь путь — в одном сообщении")
        self.assertEqual(1, len(self.vk.dialog(9001)) - 4, "новых сообщений, кроме расписания, нет")

        booking, = self.active_bookings(3, day(10, 7))
        self.assertEqual((self.user.row["id"], "312", hm(20, 30), hm(21, 15)),
                         (booking["user_id"], booking["room"], booking["slot_start"], booking["slot_end"]))

        # назад к неделе: снова картинка в том же сообщении, занятое время учтено
        self.user.press("📅 К расписанию")
        self.assertEqual(cmid, self.user.last["cmid"])
        self.assertTrue(self.user.last["attachment"].startswith("photo"))
        self.assertIn("Ср 07.10 · 9 своб.", self.user.labels())
        self.assertEqual(2, len(self.vk.photos))

    def test_day_list_marks_own_and_past(self):
        self.user.tap(BTN_BOOK)
        self.user.press("Вт 06.10")
        self.assertIn("⚪ 06:30–07:15 — прошло", self.user.text)
        self.assertIn("⚪ 08:15–09:00 — прошло", self.user.text)
        self.assertIn("🟢 10:00–10:45 — свободно", self.user.text)
        self.user.press("10:00–10:45")
        self.user.press("✅ Записаться")
        self.user.press("📅 К расписанию")
        self.user.press("Вт 06.10")
        self.assertIn("🔴 10:00–10:45 — к.312 Иванов (вы)", self.user.text)

    def test_limit_two_per_day(self):
        self.user.tap(BTN_BOOK)
        for slot in ("10:00–10:45", "11:45–12:30"):
            self.user.press("Чт 08.10")
            self.user.press(slot)
            self.assertEqual("Записано ✅", self.user.press("✅ Записаться"))
            self.user.press("📅 К расписанию")
        self.user.press("Чт 08.10")
        self.assertIn("⚠️ Вы уже записаны 2 раз(а) на этот день — это максимум.", self.user.text)
        self.assertEqual(["⬅️ К неделе"], self.user.labels(), "времени для выбора больше не предлагают")

        # «устаревшая» кнопка подтверждения третьей записи — отказ в самой транзакции
        hint = self.user.press_command("bk:c:20261008:1330")
        self.assertEqual("Вы уже записаны максимальное число раз на этот день.", hint)
        self.assertEqual(2, len(self.active_bookings(3, day(10, 8))))
        # на другой день записаться по-прежнему можно
        self.assertEqual("Записано ✅", self.user.press_command("bk:c:20261009:1330"))

    def test_taken_slot(self):
        other = self.vk_user(9002).register("Петров", "315")
        other.tap(BTN_BOOK)
        other.press("Ср 07.10")
        other.press("10:00–10:45")          # Петров дошёл до подтверждения…
        self.user.tap(BTN_BOOK)
        self.user.press("Ср 07.10")
        self.user.press("10:00–10:45")
        self.assertEqual("Записано ✅", self.user.press("✅ Записаться"))   # …но Иванов нажал раньше

        self.assertEqual("Это время уже кто-то занял. Выберите другое.", other.press("✅ Записаться"))
        self.assertIn("🔴 10:00–10:45 — к.312 Иванов", other.text)
        self.assertNotIn("10:00–10:45", other.labels())
        self.assertEqual(1, len(self.active_bookings(3, day(10, 7))))

    def test_race_for_one_slot(self):
        users = [self.user] + [self.vk_user(9010 + i).register(f"Жилец{'абвгдежз'[i]}", f"3{10 + i}")
                               for i in range(7)]
        for u in users:
            u.tap(BTN_BOOK)
        barrier = threading.Barrier(len(users))
        hints: list[str | None] = []

        def book(u):
            barrier.wait()
            hints.append(u.press_command("bk:c:20261010:1515"))

        threads = [threading.Thread(target=book, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(1, hints.count("Записано ✅"), hints)
        self.assertEqual(len(users) - 1, hints.count("Это время уже кто-то занял. Выберите другое."), hints)
        self.assertEqual(1, len(self.active_bookings(3, day(10, 10))))

    def test_past_and_foreign_slots_rejected(self):
        self.user.tap(BTN_BOOK)
        self.assertEqual("Это время уже прошло.", self.user.press_command("bk:c:20261006:0815"))
        self.assertEqual("Запись на этот день сейчас недоступна. Обновите расписание.",
                         self.user.press_command("bk:c:20261013:1000"))
        self.assertEqual("Такого времени нет в расписании", self.user.press_command("bk:c:20261007:1234"))
        self.assertEqual("Этот день уже недоступен — показываю актуальную неделю",
                         self.user.press_command("bk:d:20261020"))
        self.assertEqual([], db.user_upcoming_bookings(self.user.row["id"], self._now))

    def test_my_bookings_and_cancel(self):
        self.user.tap(BTN_MY)
        self.assertIn("нет предстоящих записей", self.user.text)
        self.user.tap(BTN_BOOK)
        self.user.press_command("bk:c:20261007:1000")
        self.user.press_command("bk:c:20261008:1145")

        self.user.tap(BTN_MY)
        self.assertIn("1. Среда, 07.10.2026, 10:00–10:45 (этаж 3)", self.user.text)
        self.assertEqual(["❌ Отменить: Ср 07.10, 10:00–10:45", "❌ Отменить: Чт 08.10, 11:45–12:30"],
                         self.user.labels())
        self.user.press("❌ Отменить: Ср 07.10")
        self.assertIn("Отменить запись?", self.user.text)
        self.assertEqual("Запись отменена", self.user.press("✅ Да, отменить"))
        self.assertIn("🗑 Запись на Ср 07.10, 10:00–10:45 отменена.", self.user.text)
        self.assertEqual(["❌ Отменить: Чт 08.10, 11:45–12:30"], self.user.labels())
        self.assertEqual([], self.active_bookings(3, day(10, 7)))

    def test_cannot_cancel_started_or_foreign_booking(self):
        other = self.vk_user(9002).register("Петров", "315")
        other.tap(BTN_BOOK)
        other.press_command("bk:c:20261007:1000")
        foreign = self.active_bookings(3, day(10, 7))[0]["id"]
        self.user.tap(BTN_BOOK)
        self.user.press_command("bk:c:20261006:1000")
        own = self.active_bookings(3, day(10, 6))[0]["id"]

        self.user.tap(BTN_MY)
        self.assertEqual("Запись не найдена или уже отменена.", self.user.press_command(f"my:y:{foreign}"))
        self.set_now(at(2026, 10, 6, 10, 0))   # стирка началась
        self.assertEqual("Эта стирка уже началась или прошла — отменить нельзя.",
                         self.user.press_command(f"my:y:{own}"))
        self.assertEqual(2, len(db.bookings_between(3, day(10, 6), day(10, 7))))

    def test_unregistered_cannot_book(self):
        stranger = self.vk_user(9005).start()
        self.assertEqual("Сначала пройдите регистрацию — нажмите /start",
                         stranger.press_command("bk:w", cmid=1))


class Floor5Test(BotTestCase):
    """5 этаж: пн–сб, 1 крыло — вт/чт/сб, 2 крыло — пн/ср/пт, комната — 1 раз в день."""

    def setUp(self):
        super().setUp()
        self.wing1 = self.vk_user(9001).register("Иванов", "515")
        self.wing2 = self.vk_user(9002).register("Петров", "505")

    def test_wings_see_their_days(self):
        self.wing1.tap(BTN_BOOK)
        self.assertIn("этаж 5, 1 крыло", self.wing1.text)
        # вторник 09:00: из 9 стирок 5 этажа две (06:30 и 08:30) уже начались
        self.assertEqual(["Вт 06.10 · 7 своб.", "Чт 08.10 · 9 своб.", "Сб 10.10 · 9 своб.", "🔄 Обновить"],
                         self.wing1.labels())
        self.wing2.tap(BTN_BOOK)
        self.assertIn("этаж 5, 2 крыло", self.wing2.text)
        self.assertEqual(["Пн 05.10 · прошёл", "Ср 07.10 · 9 своб.", "Пт 09.10 · 9 своб.", "🔄 Обновить"],
                         self.wing2.labels())

    def test_floor5_slots(self):
        self.wing1.tap(BTN_BOOK)
        self.wing1.press("Чт 08.10")
        for line in ("🟢 06:30–07:24", "🟢 08:30–09:24", "🟢 20:30–21:24", "🟢 22:00–22:54"):
            self.assertIn(line, self.wing1.text)
        self.assertIn("Четверг, 08.10.2026 · этаж 5, 1 крыло", self.wing1.text)

    def test_other_wing_day_is_rejected(self):
        self.wing1.tap(BTN_BOOK)
        self.assertEqual("Запись на этот день сейчас недоступна. Обновите расписание.",
                         self.wing1.press_command("bk:c:20261007:0830"))   # среда — день 2 крыла
        self.assertEqual("Записано ✅", self.wing1.press_command("bk:c:20261008:0830"))

    def test_room_limit_one_per_day(self):
        roommate = self.vk_user(9003).register("Сидоров", "515")
        self.wing1.tap(BTN_BOOK)
        self.assertEqual("Записано ✅", self.wing1.press_command("bk:c:20261008:0830"))

        roommate.tap(BTN_BOOK)
        roommate.press("Чт 08.10")
        self.assertIn("Ваша комната уже записана на этот день", roommate.text)
        self.assertEqual(["⬅️ К неделе"], roommate.labels())
        self.assertEqual("Ваша комната уже записана на этот день (на 5 этаже — не чаще 1 раза в день).",
                         roommate.press_command("bk:c:20261008:1030"))
        # сам записавшийся второй раз в этот день тоже не может
        self.assertIn(self.wing1.press_command("bk:c:20261008:1230"),
                      ("Вы уже записаны максимальное число раз на этот день.",
                       "Ваша комната уже записана на этот день (на 5 этаже — не чаще 1 раза в день)."))
        # другая комната того же крыла и другой день — можно
        neighbour = self.vk_user(9004).register("Орлов", "516")
        neighbour.tap(BTN_BOOK)
        self.assertEqual("Записано ✅", neighbour.press_command("bk:c:20261008:1030"))
        self.assertEqual("Записано ✅", roommate.press_command("bk:c:20261010:1030"))
        self.assertEqual(2, len(self.active_bookings(5, day(10, 8))))


class WeekOpeningTest(BotTestCase):
    """Запись на новую неделю: обычные этажи — пн 15:00, 5 этаж — вс 15:00."""

    def test_regular_floor_opens_on_monday_1500(self):
        user = self.vk_user(9001).register("Иванов", "312")
        self.set_now(at(2026, 10, 12, 14, 59))           # понедельник, за минуту до открытия
        user.tap(BTN_BOOK)
        self.assertIn("Стирки этой недели закончились", user.text)
        self.assertIn("Запись на неделю Вт 13.10 – Вс 18.10 откроется в понедельник, 12.10 в 15:00", user.text)
        self.assertIsNone(user.last["attachment"])
        self.assertEqual(["🔄 Обновить"], user.labels())
        self.assertEqual("Запись на этот день сейчас недоступна. Обновите расписание.",
                         user.press_command("bk:c:20261013:1000"))

        self.set_now(at(2026, 10, 12, 15, 0))            # открылась
        user.press("🔄 Обновить")
        self.assertIn("Вт 13.10 – Вс 18.10", user.text)
        self.assertTrue(user.last["attachment"].startswith("photo"))
        self.assertEqual("Вт 13.10 · 10 своб.", user.labels()[0])
        self.assertEqual("Записано ✅", user.press_command("bk:c:20261013:1000"))

    def test_floor5_opens_on_sunday_1500(self):
        user = self.vk_user(9001).register("Иванов", "515")     # 1 крыло: вт, чт, сб
        self.set_now(at(2026, 10, 11, 14, 59))           # воскресенье
        user.tap(BTN_BOOK)
        self.assertIn("Стирки этой недели закончились", user.text)
        self.assertIn("откроется в воскресенье, 11.10 в 15:00", user.text)
        self.assertEqual("Запись на этот день сейчас недоступна. Обновите расписание.",
                         user.press_command("bk:c:20261013:0830"))

        self.set_now(at(2026, 10, 11, 15, 0))
        user.press("🔄 Обновить")
        self.assertEqual(["Вт 13.10 · 9 своб.", "Чт 15.10 · 9 своб.", "Сб 17.10 · 9 своб.", "🔄 Обновить"],
                         user.labels())
        self.assertEqual("Записано ✅", user.press_command("bk:c:20261013:0830"))

    def test_monday_is_day_off_on_regular_floors(self):
        user = self.vk_user(9001).register("Иванов", "412")
        user.tap(BTN_BOOK)
        self.assertFalse(any(label.startswith("Пн") for label in user.labels()))
