"""VK: администратор — заявки старост, ответы жильцам, любой этаж, старосты, статистика, логи."""
from laundry import config, db
from laundry.keyboards import BTN_ADMIN, BTN_BOOK, BTN_PROFILE, BTN_STAROSTA, BTN_SUPPORT

from .base import TG_ADMIN, VK_ADMIN, BotTestCase, day


class AdminCase(BotTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.vk_user(VK_ADMIN).start()
        self.resident = self.vk_user(9002).register("Иванов", "312")

    def open_panel(self):
        self.admin.tap(BTN_ADMIN)
        self.assertIn("Админ-панель", self.admin.text)
        return self.admin


class StarostaRequestTest(AdminCase):
    def request(self):
        self.resident.tap(BTN_PROFILE)
        self.assertIn("⭐ Я староста — подать заявку", self.resident.labels())
        self.assertEqual("Заявка отправлена", self.resident.press("⭐ Я староста"))
        self.assertIn("Заявка на роль старосты отправлена администратору", self.resident.text)
        self.assertEqual([], self.resident.labels(), "кнопки профиля из отредактированного сообщения убраны")

    def test_approve(self):
        self.request()
        self.assertTrue(self.resident.row["starosta_pending"])
        a = self.admin
        self.assertIn("📨 Заявка на старосту", a.text)
        self.assertIn("[id9002|Иванов], к.312 (этаж 3)", a.text)
        self.assertEqual(["✅ Одобрить", "❌ Отклонить"], a.labels())
        self.assertIn("Заявка на старосту", self.tg.last(TG_ADMIN)["text"], "админ в Telegram тоже получил заявку")

        self.assertEqual("Заявка одобрена ✅", a.press("✅ Одобрить"))
        self.assertIn("Заявка Иванов (к.312) одобрена ✅", a.text)
        row = self.resident.row
        self.assertEqual(("starosta", 0), (row["role"], row["starosta_pending"]))
        self.assertIn("⭐ Администратор подтвердил: вы староста 3 этажа.", self.resident.text)
        self.assertIn(BTN_STAROSTA, self.resident.menu_labels())
        self.resident.tap(BTN_STAROSTA)
        self.assertIn("Управление этажом 3", self.resident.text)
        # вторая попытка решить ту же заявку (например, другой админ из Telegram)
        self.assertEqual("Заявка уже обработана.", a.press_command(f"adm:ok:{row['id']}"))

    def test_reject(self):
        self.request()
        self.assertEqual("Заявка отклонена ❌", self.admin.press("❌ Отклонить"))
        row = self.resident.row
        self.assertEqual(("resident", 0), (row["role"], row["starosta_pending"]))
        self.assertEqual("Заявка на роль старосты отклонена администратором.", self.resident.text)

    def test_duplicate_request_and_list_in_panel(self):
        self.request()
        self.assertEqual("Заявка уже отправлена, ждите решения администратора.",
                         self.resident.press_command("pr:st"))
        a = self.open_panel()
        self.assertIn("📨 Заявки старост (1)", a.labels())
        a.press("📨 Заявки старост")
        self.assertIn("• [id9002|Иванов], к.312 (этаж 3)", a.text)
        self.assertEqual(["✅ Иванов 312", "❌ Иванов 312", "⬅️ Назад"], a.labels())
        self.assertEqual("Заявка одобрена ✅", a.press("✅ Иванов 312"))
        a.press("📨 Остальные заявки")
        self.assertEqual("📨 Новых заявок нет.", a.text)

    def test_non_admin_is_denied(self):
        self.assertNotIn(BTN_ADMIN, self.resident.say("меню").menu_labels())
        self.resident.say(BTN_ADMIN)
        self.assertEqual("Команда доступна только администратору.", self.resident.text)
        for command in ("adm", "adm:req", f"adm:ok:{self.resident.row['id']}", "adm:log", "adm:floors"):
            self.assertEqual("⛔ Только для администратора.", self.resident.press_command(command), command)
        self.assertEqual("resident", self.resident.row["role"])


class SupportTest(AdminCase):
    def test_message_to_admin_and_reply(self):
        self.resident.tap(BTN_SUPPORT)
        self.assertIn("Напишите сообщение администратору", self.resident.text)
        self.resident.say("Не работает машинка <на 3 этаже> & течёт")
        self.assertEqual("✅ Сообщение отправлено администратору. Ответ придёт сюда.", self.resident.text)

        a = self.admin
        self.assertEqual("✉️ Сообщение от пользователя\n[id9002|Иванов], к.312 (этаж 3)\n\n"
                         "Не работает машинка <на 3 этаже> & течёт", a.text)
        self.assertEqual(["↩️ Ответить"], a.labels())
        self.assertIsNone(a.press("↩️ Ответить"))
        self.assertIn("Напишите ответ для Иванов (к.312)", a.text)
        a.say("Починим завтра")
        self.assertEqual("✅ Ответ отправлен.", a.text)

        self.assertEqual("✉️ Ответ администратора\n\nПочиним завтра", self.resident.text)
        self.assertEqual(["✉️ Ответить администратору"], self.resident.labels())
        self.resident.press("✉️ Ответить администратору")
        self.assertIn("Напишите сообщение администратору", self.resident.text)
        self.resident.say("Спасибо")
        self.assertIn("Спасибо", a.text)

    def test_reply_to_user_who_blocked_messages(self):
        self.resident.tap(BTN_SUPPORT).say("Вопрос")
        self.admin.press("↩️ Ответить")
        self.vk.blocked.add(9002)       # жилец запретил сообщения сообщества (ошибка 901)
        self.admin.say("Ответ")
        self.assertIn("Не удалось доставить ответ", self.admin.text)

    def test_cancel_support(self):
        self.resident.tap(BTN_SUPPORT)
        self.assertEqual("Отменено", self.resident.press("✖️ Отмена"))
        self.assertEqual("Действие отменено.", self.resident.text)
        self.resident.say("это уже не сообщение админу")
        self.assertIn("Не понял", self.resident.text)


class AdminPanelTest(AdminCase):
    def test_panel_sections(self):
        a = self.open_panel()
        self.assertEqual(["📨 Заявки старост (0)", "📝 Заявки на изменение данных (0)", "🏢 Управление этажом",
                          "⭐ Старосты", "📊 Статистика", "📄 Логи"], a.labels())

    def test_manage_any_floor(self):
        self.resident.tap(BTN_BOOK)
        self.resident.press_command("bk:c:20261007:1000")
        a = self.open_panel()
        a.press("🏢 Управление этажом")
        self.assertEqual(["2 этаж", "3 этаж", "4 этаж", "5 этаж", "⬅️ Назад"], a.labels())
        a.press("3 этаж")
        self.assertIn("Управление этажом 3", a.text)
        self.assertIn("Староста: не назначен", a.text)
        self.assertIn("⬅️ К выбору этажа", a.labels())
        self.assertEqual(9, len(a.labels()), "панель этажа у админа: 9 кнопок в 6 рядах — впритык к лимиту VK")
        booking = self.active_bookings(3, day(10, 7))[0]
        self.assertEqual("Запись отменена", a.press_command(f"m:3:xy:{booking['id']}"))
        self.assertEqual("❗ Ваша запись на стирку Среда, 07.10.2026, 10:00–10:45 отменена администратором.",
                         self.resident.text)
        a.press_command("m:5:w")
        self.assertIn("Расписание стирки · этаж 5", a.text)
        self.assertEqual("⛔ Нет прав на управление этим этажом.", a.press_command("m:1"))

    def test_starostas_list_and_removal(self):
        a = self.open_panel()
        a.press("⭐ Старосты")
        self.assertEqual("⭐ Старосты пока не назначены.", a.text)
        self.make_starosta(self.resident.row)
        a.press_command("adm:st")
        self.assertIn("• 3 этаж — [id9002|Иванов] (к.312)", a.text)
        self.assertEqual("Роль снята", a.press("Снять: Иванов"))
        self.assertEqual("resident", self.resident.row["role"])
        self.assertEqual("ℹ️ Администратор снял с вас роль старосты этажа.", self.resident.text)
        self.assertNotIn(BTN_STAROSTA, self.resident.menu_labels())
        self.assertEqual("Этот пользователь уже не староста.", a.press_command(f"adm:rm:{self.resident.row['id']}"))

    def test_stats(self):
        self.vk_user(9003).register("Петров", "412")
        self.resident.tap(BTN_BOOK)
        self.resident.press_command("bk:c:20261007:1000")
        a = self.open_panel()
        a.press("📊 Статистика")
        for line in ("Зарегистрировано жильцов: 2", "3 этаж: 1", "4 этаж: 1", "Предстоящих записей: 1"):
            self.assertIn(line, a.text)

    def test_logs_are_sent_as_files(self):
        a = self.open_panel()
        self.assertEqual("Логи пока пустые.", a.press("📄 Логи"))
        for name in ("vk_bot.log", "vk_actions.log", "bot.log"):
            (config.LOG_DIR / name).write_text("строка лога\n", encoding="utf-8")
        (config.LOG_DIR / "vk_errors.log").write_text("", encoding="utf-8")   # пустой файл не отправляем
        try:
            self.assertEqual("Логи отправлены", a.press("📄 Логи"))
        finally:
            for name in ("vk_bot.log", "vk_actions.log", "bot.log", "vk_errors.log"):
                (config.LOG_DIR / name).unlink()
        self.assertEqual(["vk_bot.log.txt", "vk_actions.log.txt", "bot.log.txt"], [d["title"] for d in self.vk.docs])
        files = [m for m in self.vk.dialog(VK_ADMIN) if (m["attachment"] or "").startswith("doc")]
        self.assertEqual(["📄 vk_bot.log", "📄 vk_actions.log", "📄 bot.log"], [m["text"] for m in files])

    def test_change_requests_of_all_floors(self):
        self.resident.tap(BTN_PROFILE)
        self.resident.press("✏️ Изменить")
        self.resident.say("Иванов").say("420")
        a = self.open_panel()
        self.assertIn("📝 Заявки на изменение данных (1)", a.labels())
        a.press("📝 Заявки на изменение данных")
        self.assertEqual("Заявка принята ✅", a.press("✅ 1. Принять"))
        self.assertEqual(["🛠 Админ-панель"], a.labels())
        self.assertEqual("420", self.resident.row["room"])
        self.assertIn("подтверждено администратором", self.resident.text)

    def test_long_lists_are_paged(self):
        for i in range(12):   # 12 заявок на старосту — по 2 кнопки на каждую
            user = self.vk_user(9100 + i).register("Жилец" + "абвгдежзиклм"[i], f"2{10 + i}")
            db.set_starosta_pending(user.row["id"], True, self._now)
        a = self.open_panel()
        a.press("📨 Заявки старост")
        self.assertEqual(12, a.text.count("•"))
        self.assertEqual(8, len(a.labels()), "6 кнопок заявок + ▶ + Назад")
        self.assertEqual("2/4 ▶", a.labels()[-2])
        a.press("2/4 ▶")
        self.assertEqual(["◀ 1/4", "3/4 ▶", "⬅️ Назад"], a.labels()[-3:])
        a.press("3/4 ▶")
        a.press("4/4 ▶")
        self.assertEqual(["✅ Жилецл 220", "❌ Жилецл 220", "✅ Жилецм 221", "❌ Жилецм 221", "◀ 3/4", "⬅️ Назад"],
                         a.labels()[-6:])
