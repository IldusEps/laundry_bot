"""Картинка расписания: прошедшие записи серые, но комната и фамилия на них остаются. Общая для VK и Telegram."""
import io
from unittest import mock

from PIL import Image, ImageDraw

from laundry import image, render, services
from laundry.keyboards import BTN_BOOK

from .base import BotTestCase, at, day, hm


class ScheduleImageTest(BotTestCase):
    """Сначала вторник 09:00 (все записи впереди), затем среда 12:00 (часть уже прошла)."""

    def setUp(self):
        super().setUp()
        self.me = self.vk_user(9001).register("Иванов", "312")
        self.other = self.tg_user(2001).register("Петрова-Водкина", "315")
        self.me.tap(BTN_BOOK)
        self.other.say(BTN_BOOK)
        for user_press, code in ((self.me.press_command, "20261006:1000"),      # вт 10:00 — моя, прошедшая
                                 (self.other.press_data, "20261007:0815"),      # ср 08:15 — чужая, прошедшая
                                 (self.other.press_data, "20261007:1330"),      # ср 13:30 — чужая, будущая
                                 (self.me.press_command, "20261008:1145")):     # чт 11:45 — моя, будущая
            self.assertEqual("Записано ✅", user_press("bk:c:" + code))
        self.set_now(at(2026, 10, 7, 12, 0))

    def grid(self):
        return services.build_grid(3, None, viewer_id=self.me.row["id"])

    def cell_pixels(self, picture: Image.Image, grid, when, start):
        """Точки внутри ячейки (без скруглённых углов и рамки)."""
        s = image.SCALE
        column = grid.week.days.index(when)
        row = [slot.start for slot in grid.rules.slots].index(start)
        x0 = image.PAD + image.TIME_W + column * image.CELL_W
        y0 = image.PAD + image.TITLE_H + image.HEAD_H + row * image.CELL_H
        pixels = picture.load()
        return [pixels[x, y] for y in range((y0 + 7) * s, (y0 + image.CELL_H - 7) * s)
                for x in range((x0 + 8) * s, (x0 + image.CELL_W - 8) * s)]

    def test_cells_know_that_their_time_has_passed(self):
        grid = self.grid()
        cells = {name: grid.cells[key] for name, key in {
            "my_past": (day(10, 6), hm(10, 0)), "other_past": (day(10, 7), hm(8, 15)),
            "other_future": (day(10, 7), hm(13, 30)), "my_future": (day(10, 8), hm(11, 45)),
            "empty_past": (day(10, 7), hm(6, 30)), "free": (day(10, 7), hm(15, 15))}.items()}
        # запись остаётся в ячейке и после того, как время прошло
        self.assertEqual({"my_past": ("mine", True), "other_past": ("busy", True), "other_future": ("busy", False),
                          "my_future": ("mine", False), "empty_past": ("past", True), "free": ("free", False)},
                         {name: (c.state, c.past) for name, c in cells.items()})
        self.assertEqual("315", cells["other_past"].booking["room"])
        # среда 12:00: четыре стирки прошли, 13:30 занята — свободны 15:15, 17:00, 18:45, 20:30 и 22:15
        self.assertEqual(5, grid.free_count(day(10, 7)))

    def test_past_bookings_are_gray_with_room_and_surname(self):
        grid = self.grid()
        picture = Image.open(io.BytesIO(image.week_image(grid, render.week_title(grid)))).convert("RGB")

        def colors(when, start):
            pixels = self.cell_pixels(picture, grid, when, start)
            return pixels[0], sum(1 for p in pixels if p != pixels[0])

        fill, text = colors(day(10, 7), hm(8, 15))            # чужая прошедшая: серая, подпись тёмным
        self.assertEqual(image.GRAY, fill)
        self.assertGreater(text, 300, "на серой ячейке есть подпись")
        self.assertIn(image.TEXT, self.cell_pixels(picture, grid, day(10, 7), hm(8, 15)))

        fill, text = colors(day(10, 6), hm(10, 0))            # моя прошедшая: серая, «ВЫ»
        self.assertEqual(image.GRAY, fill)
        self.assertGreater(text, 100)

        fill, text = colors(day(10, 7), hm(13, 30))           # будущая: красная, подпись белым
        self.assertEqual(image.RED, fill)
        self.assertGreater(text, 300)
        self.assertIn(image.WHITE, self.cell_pixels(picture, grid, day(10, 7), hm(13, 30)))

        self.assertEqual((image.GRAY, 0), colors(day(10, 7), hm(6, 30)), "прошло без записи — серая и пустая")
        self.assertEqual(image.GREEN, colors(day(10, 7), hm(15, 15))[0])

    def test_cell_label_has_room_and_surname(self):
        grid = self.grid()
        fill, lines, color = image._cell_style(grid.cells[(day(10, 7), hm(8, 15))])
        self.assertEqual((image.GRAY, ["к.315", "Петрова-Водкина"], image.TEXT), (fill, lines, color))
        fill, lines, color = image._cell_style(grid.cells[(day(10, 7), hm(13, 30))])
        self.assertEqual((image.RED, ["к.315", "Петрова-Водкина"], image.WHITE), (fill, lines, color))
        self.assertEqual((image.GRAY, ["ВЫ"], image.TEXT), image._cell_style(grid.cells[(day(10, 6), hm(10, 0))]))
        self.assertEqual((image.RED, ["ВЫ"], image.WHITE), image._cell_style(grid.cells[(day(10, 8), hm(11, 45))]))
        self.assertEqual((image.GRAY, [], image.MUTED), image._cell_style(grid.cells[(day(10, 7), hm(6, 30))]))

    def test_long_surname_is_cut_to_cell_width(self):
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = image._font(10)
        width = (image.CELL_W - 12) * image.SCALE
        cut = image._fit(draw, "Константинопольская-Задунайская", font, width)
        self.assertTrue(cut.endswith("…") and cut.startswith("Констан"), cut)
        self.assertLessEqual(draw.textlength(cut, font=font), width)
        self.assertEqual("Ли", image._fit(draw, "Ли", font, width))

    def test_same_picture_and_day_list_in_both_bots(self):
        with mock.patch.object(image, "week_image", wraps=image.week_image) as drawn:
            self.me.tap(BTN_BOOK)              # VK
            self.other.say(BTN_BOOK)           # Telegram
        self.assertEqual(2, drawn.call_count, "оба бота рисуют картинку одной функцией")
        for call in drawn.call_args_list:
            grid = call.args[0]
            self.assertTrue(grid.cells[(day(10, 7), hm(8, 15))].past)
            self.assertFalse(grid.cells[(day(10, 7), hm(13, 30))].past)
        self.assertTrue(self.me.last["attachment"].startswith("photo"))
        self.assertTrue(self.other.last["photo"])

        self.me.press("Ср 07.10")              # список дня под картинкой — те же обозначения
        self.other.press("Ср 07.10")
        for text in (self.me.text, self.other.text):
            self.assertIn("⚪ 08:15–09:00 — к.315 Петрова-Водкина", text)
            self.assertIn("🔴 13:30–14:15 — к.315 Петрова-Водкина", text)
            self.assertIn("⚪ 06:30–07:15 — прошло", text)
        self.me.press("⬅️ К неделе")
        self.me.press("Вт 06.10")
        self.assertIn("⚪ 10:00–10:45 — к.312 Иванов (вы)", self.me.text)

    def test_starosta_sees_who_washed(self):
        starosta = self.vk_user(9005).register("Старостин", "301")
        self.make_starosta(starosta.row)
        starosta.say("меню").tap("⭐ Панель старосты")
        starosta.press("📅 Расписание")
        self.assertIn("Ср 07.10 · записей: 2", starosta.labels())
        starosta.press("Ср 07.10")
        self.assertIn("⚪ 08:15–09:00 — к.315 Петрова-Водкина", starosta.text)
        # отменить можно только будущую запись
        self.assertEqual(["❌ 13:30 к.315 Петрова-Водкина", "⬅️ К неделе"], starosta.labels())
