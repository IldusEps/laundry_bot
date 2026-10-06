"""4 этаж: стирка пн–пт (сб и вс недоступны), запись на новую неделю открывается в воскресенье в 15:00."""
import io

from PIL import Image

from laundry import image, render, services
from laundry import schedule as sched
from laundry.keyboards import BTN_BOOK, BTN_RULES, BTN_STAROSTA

from .base import BotTestCase, at, day

UNAVAILABLE = "Запись на этот день сейчас недоступна. Обновите расписание."


class Floor4Test(BotTestCase):
    """Вторник 06.10.2026, 09:00 — на 4 этаже открыта неделя пн 05.10 – пт 09.10."""

    def setUp(self):
        super().setUp()
        self.user = self.vk_user(9001).register("Иванов", "412")

    def test_week_is_monday_to_friday(self):
        self.user.tap(BTN_BOOK)
        self.assertIn("Расписание стирки · этаж 4", self.user.text)
        self.assertIn("Пн 05.10 – Пт 09.10", self.user.text)
        self.assertIn("откроется в воскресенье, 11.10 в 15:00", self.user.text)
        self.assertEqual(["Пн 05.10 · прошёл", "Вт 06.10 · 8 своб.", "Ср 07.10 · 10 своб.", "Чт 08.10 · 10 своб.",
                          "Пт 09.10 · 10 своб.", "🔄 Обновить"], self.user.labels())

    def test_saturday_and_sunday_are_unavailable(self):
        self.user.tap(BTN_BOOK)
        for code in ("20261010", "20261011"):           # суббота и воскресенье
            self.assertEqual(UNAVAILABLE, self.user.press_command(f"bk:c:{code}:1000"), code)
            self.assertEqual("Этот день уже недоступен — показываю актуальную неделю",
                             self.user.press_command(f"bk:d:{code}"), code)
        self.assertEqual("Записано ✅", self.user.press_command("bk:c:20261009:1000"))   # пятница — можно
        self.assertEqual([], self.active_bookings(4, day(10, 10)) + self.active_bookings(4, day(10, 11)))

    def test_same_slots_and_limit_as_regular_floors(self):
        self.user.tap(BTN_BOOK)
        self.user.press("Ср 07.10")
        self.assertIn("🟢 06:30–07:15 — свободно", self.user.text)
        self.assertIn("🟢 22:15–23:00 — свободно", self.user.text)
        for code in ("1000", "1145"):
            self.assertEqual("Записано ✅", self.user.press_command(f"bk:c:20261007:{code}"))
        self.assertEqual("Вы уже записаны максимальное число раз на этот день.",
                         self.user.press_command("bk:c:20261007:1330"))

    def test_new_week_opens_on_sunday_1500_and_monday_is_available(self):
        self.set_now(at(2026, 10, 10, 12, 0))           # суббота: стирок нет, неделя закончилась
        self.user.tap(BTN_BOOK)
        self.assertIn("Стирки этой недели закончились", self.user.text)
        self.assertIn("Запись на неделю Пн 12.10 – Пт 16.10 откроется в воскресенье, 11.10 в 15:00", self.user.text)
        self.assertEqual(["🔄 Обновить"], self.user.labels())

        self.set_now(at(2026, 10, 11, 14, 59))          # воскресенье, за минуту до открытия
        self.assertEqual(UNAVAILABLE, self.user.press_command("bk:c:20261012:0630"))
        self.assertEqual(UNAVAILABLE, self.user.press_command("bk:c:20261011:1700"), "сегодня, в воскресенье, не стирают")

        self.set_now(at(2026, 10, 11, 15, 0))           # новое расписание появилось
        self.user.press("🔄 Обновить")
        self.assertTrue(self.user.last["attachment"].startswith("photo"))
        self.assertIn("Пн 12.10 – Пт 16.10", self.user.text)
        self.assertEqual(["Пн 12.10 · 10 своб.", "Вт 13.10 · 10 своб.", "Ср 14.10 · 10 своб.", "Чт 15.10 · 10 своб.",
                          "Пт 16.10 · 10 своб.", "🔄 Обновить"], self.user.labels())
        self.assertEqual("Записано ✅", self.user.press_command("bk:c:20261012:0630"))    # понедельник
        self.assertEqual(UNAVAILABLE, self.user.press_command("bk:c:20261017:1000"))     # суббота новой недели

    def test_can_book_on_monday_itself(self):
        self.set_now(at(2026, 10, 12, 9, 0))            # понедельник утром
        self.user.tap(BTN_BOOK)
        self.assertEqual("Пн 12.10 · 8 своб.", self.user.labels()[0])
        self.user.press("Пн 12.10")
        self.user.press("10:00–10:45")
        self.assertEqual("Записано ✅", self.user.press("✅ Записаться"))
        self.assertIn("Понедельник, 12.10.2026, 10:00–10:45", self.user.text)
        # этажи 2–3 в это же время живут по-старому: их запись откроется только в 15:00
        regular = self.vk_user(9002).register("Петров", "312")
        regular.tap(BTN_BOOK)
        self.assertIn("Стирки этой недели закончились", regular.text)
        self.assertIn("откроется в понедельник, 12.10 в 15:00", regular.text)

    def test_same_rules_in_telegram(self):
        tg = self.tg_user(2001).register("Сидоров", "415")
        tg.say(BTN_BOOK)
        self.assertTrue(tg.last["photo"])
        self.assertEqual(["Пн 05.10 · прошёл", "Вт 06.10 · 8 своб.", "Ср 07.10 · 10 своб.", "Чт 08.10 · 10 своб.",
                          "Пт 09.10 · 10 своб.", "🔄 Обновить"], tg.labels())
        self.assertEqual(UNAVAILABLE, tg.press_data("bk:c:20261010:1000"))
        self.assertEqual(UNAVAILABLE, tg.press_data("bk:c:20261011:1000"))
        self.assertEqual("Записано ✅", tg.press_data("bk:c:20261008:1000"))
        self.user.tap(BTN_BOOK)                          # запись из Telegram видна в VK
        self.assertIn("Чт 08.10 · 9 своб.", self.user.labels())

    def test_picture_has_five_days(self):
        grid = services.build_grid(4, None)
        self.assertEqual([day(10, 5), day(10, 6), day(10, 7), day(10, 8), day(10, 9)], grid.week.days)
        picture = Image.open(io.BytesIO(image.week_image(grid, render.week_title(grid))))
        self.assertEqual((image.PAD * 2 + image.TIME_W + image.CELL_W * 5) * image.SCALE, picture.width)

    def test_rules_text(self):
        self.user.tap(BTN_RULES)
        for line in ("🧺 4 этаж", "Стирка с понедельника по пятницу, суббота и воскресенье — выходные.",
                     "Не больше 2 записей в день на человека.",
                     "Запись на новую неделю открывается в воскресенье в 15:00."):
            self.assertIn(line, self.user.text)
        self.assertNotIn("Этажи 2–3", self.user.text)
        everyone = render.plain(render.rules_text(None))    # незарегистрированному — правила всех этажей
        for title in ("🧺 Этажи 2–3\n", "🧺 4 этаж\n", "🧺 5 этаж\n"):
            self.assertIn(title, everyone)
        self.assertLess(everyone.index("Этажи 2–3"), everyone.index("4 этаж"))

    def test_starosta_closes_only_working_days(self):
        self.make_starosta(self.user.row)
        self.user.say("меню").tap(BTN_STAROSTA)
        self.user.press("📅 Расписание")
        self.assertEqual(["Пн", "Вт", "Ср", "Чт", "Пт"], [label[:2] for label in self.user.labels()[:5]])
        self.user.press("⬅️ Назад")
        self.user.press("🔒 Закрыть запись")
        self.user.press("📆 Весь день")
        self.assertEqual(["Вт 06.10", "Ср 07.10", "Чт 08.10", "Пт 09.10", "⬅️ Назад"], self.user.labels())

    def test_other_floors_keep_their_rules(self):
        self.assertIs(sched.FLOOR4_RULES, sched.rules_for(4))
        self.assertIs(sched.FLOOR5_RULES, sched.rules_for(5))
        for floor in (2, 3):
            self.assertIs(sched.REGULAR_RULES, sched.rules_for(floor))
            week = sched.current_week(floor, None, self._now)
            self.assertEqual([1, 2, 3, 4, 5, 6], [d.weekday() for d in week.days], floor)   # вт–вс
        self.assertEqual([0, 1, 2, 3, 4, 5], [d.weekday() for d in sched.current_week(5, None, self._now).days])
        self.assertEqual([0, 1, 2, 3, 4], [d.weekday() for d in sched.current_week(4, None, self._now).days])
