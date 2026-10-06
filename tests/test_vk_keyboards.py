"""Построитель клавиатур VK: лимиты API, страницы ◀ / ▶, сокращение подписей, перевод HTML в текст."""
import json
import unittest

from laundry import render
from laundry.vk import keyboards as kb
from laundry.vk.api import split_text
from laundry.vk.menu import main_menu

from .fake_vk import FakeVk


def parse(raw: str) -> list[list[dict]]:
    return json.loads(raw)["buttons"]


def labels(raw: str) -> list[str]:
    return [b["action"]["label"] for row in parse(raw) for b in row]


class InlineKeyboardTest(unittest.TestCase):
    def setUp(self):
        self.vk = FakeVk()

    def test_small_keyboard_is_not_paged(self):
        raw = kb.inline_paged([(f"день {i}", f"bk:d:{i}") for i in range(6)], 2, [[("🔄 Обновить", "bk:w")]], nav="bk:w")
        self.assertEqual([2, 2, 2, 1], [len(r) for r in parse(raw)])
        self.vk._check_keyboard(raw)

    def test_ten_slots_and_back_do_not_fit(self):
        items = [(f"{h:02d}:00", f"bk:s:20261007:{h:02d}00") for h in range(10)]
        footer = [[("⬅️ К неделе", "bk:w")]]
        first = kb.inline_paged(items, 2, footer, nav="bk:d:20261007", page=0)
        self.assertEqual(["00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "2/2 ▶", "⬅️ К неделе"], labels(first))
        second = kb.inline_paged(items, 2, footer, nav="bk:d:20261007", page=1)
        self.assertEqual(["06:00", "07:00", "08:00", "09:00", "◀ 1/2", "⬅️ К неделе"], labels(second))
        nav = parse(first)[-2][0]["action"]["payload"]
        self.assertEqual({"c": "bk:d:20261007", "p": 1}, json.loads(nav))
        for raw in (first, second):
            self.vk._check_keyboard(raw)

    def test_every_page_respects_limits_for_any_list_size(self):
        for per_row in (1, 2, 3, 4, 5):
            for footer_rows in (0, 1, 2):
                footer = [[("Назад", "x")] for _ in range(footer_rows)]
                for n in range(0, 60):
                    items = [(f"кнопка {i}", f"m:3:x:{i}") for i in range(n)]
                    seen: list[str] = []
                    page = 0
                    while True:
                        raw = kb.inline_paged(items, per_row, footer, nav="m:3:d:20261007", page=page)
                        self.vk._check_keyboard(raw)          # упадёт, если нарушены лимиты VK
                        got = labels(raw)
                        seen += [x for x in got if x.startswith("кнопка")]
                        if not any(x.endswith("▶") for x in got):
                            break
                        page += 1
                    self.assertEqual([f"кнопка {i}" for i in range(n)], seen, (per_row, footer_rows, n))

    def test_pairs_are_not_split_between_pages(self):
        items = []
        for i in range(9):
            items += [(f"✅ {i}", f"chg:ok:{i}"), (f"❌ {i}", f"chg:no:{i}")]
        for page in range(3):
            rows = parse(kb.inline_paged(items, 2, [[("Назад", "adm")]], nav="adm:chg", page=page))
            for row in rows[:-2]:
                self.assertEqual(2, len(row))
                self.assertEqual(row[0]["action"]["label"][2:], row[1]["action"]["label"][2:])

    def test_page_out_of_range_is_clamped(self):
        items = [(str(i), f"c:{i}") for i in range(30)]
        last = labels(kb.inline_paged(items, 1, nav="c", page=99))
        self.assertEqual("29", last[-2])
        self.assertTrue(last[-1].startswith("◀"))
        self.assertEqual(labels(kb.inline_paged(items, 1, nav="c", page=0)),
                         labels(kb.inline_paged(items, 1, nav="c", page=-5)))

    def test_too_many_fixed_buttons_is_an_error(self):
        with self.assertRaises(ValueError):
            kb.inline([[("a", "1")] for _ in range(7)])            # 7 рядов
        with self.assertRaises(ValueError):
            kb.inline([[("a", "1")] * 5, [("a", "1")] * 5, [("a", "1")]])   # 11 кнопок
        with self.assertRaises(ValueError):
            kb.inline([[("a", "1")] * 6])                          # 6 кнопок в ряду
        with self.assertRaises(ValueError):
            kb.inline_paged([(str(i), "c") for i in range(20)], 1)  # нужен nav

    def test_long_labels_are_shortened(self):
        label = "❌ 06:30 к.312 Константинопольская-Задунайская"
        raw = kb.inline([[(label, "m:3:x:1")]])
        short = labels(raw)[0]
        self.assertLessEqual(kb.utf16_len(short), 40)
        self.assertTrue(short.endswith("…"))
        self.assertTrue(short.startswith("❌ 06:30 к.312 Констан"))
        self.assertEqual("ровно сорок символов в подписи кнопки!!!", kb.shorten("ровно сорок символов в подписи кнопки!!!"))
        self.vk._check_keyboard(raw)

    def test_emoji_count_as_two(self):
        self.assertEqual(40, kb.utf16_len("🧺" * 20))
        self.assertLessEqual(kb.utf16_len(kb.shorten("🧺" * 30)), 40)

    def test_payload_limit(self):
        self.assertEqual('{"c":"m:3:bry:323а"}', kb.payload("m:3:bry:323а"))
        with self.assertRaises(ValueError):
            kb.payload("x" * 250)
        with self.assertRaises(ValueError):
            kb.payload("я" * 125)       # 250 байт в UTF-8 + обёртка

    def test_parse_payload(self):
        self.assertEqual(("bk:w", 0), kb.parse_payload('{"c": "bk:w"}'))
        self.assertEqual(("bk:d:20261007", 2), kb.parse_payload({"c": "bk:d:20261007", "p": 2}))
        self.assertEqual(("start", 0), kb.parse_payload('{"command":"start"}'))
        for junk in (None, "", "не json", "[1]", '"строка"', {"c": "x", "p": "abc"}, {"c": "x", "p": -4}):
            command, page = kb.parse_payload(junk)
            self.assertEqual(0, page)
            self.assertIn(command, (None, "x"))
        self.assertEqual((None, 3), kb.parse_payload({"p": 3}))

    def test_colors(self):
        row = parse(kb.inline([[("✅ Да", "y"), ("❌ Нет", "n"), ("⬅️ Назад", "b")]]))[0]
        self.assertEqual(["positive", "negative", "secondary"], [b["color"] for b in row])


class MenuKeyboardTest(unittest.TestCase):
    def test_menus_for_all_roles_fit_limits(self):
        vk = FakeVk()
        resident = {"room": "312", "role": "resident", "telegram_id": None, "vk_id": 1}
        starosta = dict(resident, role="starosta")
        admin = dict(starosta, vk_id=9000)
        for user in (None, {}, resident, starosta, admin):
            keyboard = vk._check_keyboard(main_menu(user))
            self.assertFalse(keyboard.get("inline"))
            self.assertFalse(keyboard["one_time"])
        self.assertEqual(5, len(labels(main_menu(resident))))
        self.assertEqual(6, len(labels(main_menu(starosta))))
        self.assertIn("🛠 Админ-панель", labels(main_menu(admin)))
        self.assertNotIn("✉️ Написать администратору", labels(main_menu(admin)))


class TextTest(unittest.TestCase):
    def test_html_to_plain(self):
        self.assertEqual("Жирный и код 312", render.plain("<b>Жирный</b> и <code>код 312</code>"))
        self.assertEqual("a < b & c > d \"кавычки\"", render.plain("a &lt; b &amp; c &gt; d &quot;кавычки&quot;"))
        self.assertEqual("[id42|Иванов-Петров], к.312",
                         render.plain(render.name({"surname": "Иванов-Петров", "vk_id": 42}) + ", к.312"))
        self.assertEqual("Иванов", render.name({"surname": "Иванов", "vk_id": None}))
        self.assertEqual("t.me/ivan_ov", render.plain(render.tg_username({"username": "ivan_ov"})))
        self.assertEqual("сайт (https://example.com/?a=1&b=2)",
                         render.plain('<a href="https://example.com/?a=1&amp;b=2">сайт</a>'))
        self.assertEqual("telegram_id: 5 · vk: [id7|id7]", render.plain(render.accounts({"telegram_id": 5, "vk_id": 7})))

    def test_user_text_is_not_treated_as_markup(self):
        user = {"surname": "Иванов", "room": "312", "floor": 3, "vk_id": None, "username": None}
        text = "<b>не жирный</b> & <script>"
        self.assertIn(text, render.plain(render.support_text(user, text)))
        self.assertIn(text, render.plain(render.admin_reply_text(text)))

    def test_all_screen_texts_have_no_markup_after_conversion(self):
        user = {"id": 1, "surname": "Иванов", "room": "515", "floor": 5, "wing": 1, "role": "starosta",
                "starosta_pending": 0, "is_banned": 1, "telegram_id": 5, "vk_id": 7, "username": "ivan"}
        req = {"id": 3, "old_surname": "Иванов", "old_room": "312", "old_floor": 3, "new_surname": "Иванова",
               "new_room": "412", "new_floor": 4, "vk_id": 7, "username": "ivan"}
        texts = [render.rules_text(None), render.rules_text(user), render.profile_text(user, True, True),
                 render.help_text(), render.registered_text(user, "комната 515"), render.new_user_text(user, "к.515"),
                 render.change_request_text(req), render.change_sent_text(req), render.user_line(user),
                 render.starosta_request_text(user, [dict(user, id=2)]), render.link_code_text("123456", "vk"),
                 render.link_notice("tg"), render.room_banned_text("515", user), render.user_banned_text(user)]
        for html in texts:
            plain = render.plain(html)
            self.assertNotRegex(plain, r"</?(b|code|a|i)\b")
            self.assertNotIn("&lt;", plain)

    def test_split_text(self):
        self.assertEqual(["короткий"], split_text("короткий"))
        self.assertEqual([""], split_text(""))
        text = "\n".join(f"строка {i} " + "х" * 50 for i in range(300))
        chunks = split_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(c) <= 4096 for c in chunks))
        self.assertEqual(text, "\n".join(chunks), "деление по строкам, без потерь")
        solid = "я" * 10000                      # одна очень длинная строка без переносов
        self.assertEqual([4096, 4096, 1808], [len(c) for c in split_text(solid)])
        self.assertEqual(solid, "".join(split_text(solid)))
