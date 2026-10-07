"""VK-бот не падает: ошибки API, сбой редактирования, исключение в обработчике, обрыв Long Poll, длинные тексты."""
import threading
from unittest import mock

import requests

from laundry import db
from laundry.keyboards import BTN_BOOK, BTN_STAROSTA
from laundry.vk import bot as vk_bot
from laundry.vk import dispatcher, router

from .base import GROUP_ID, BotTestCase


class ApiFailureTest(BotTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.vk_user(9001).register("Иванов", "312")

    def test_send_to_user_without_permission_is_only_a_warning(self):
        self.vk.blocked.add(9001)
        with self.assertLogs("laundry.vk.api", level="WARNING") as logs:
            self.assertIsNone(self.client.send(9001, "привет"))
            self.user.say("меню")                      # обработчик тоже не падает
        self.assertIn("пользователь не разрешил сообществу писать ему (ошибка 901)", logs.output[0])

    def test_network_error_on_send_is_only_a_warning(self):
        with mock.patch.object(self.vk, "method", side_effect=requests.ConnectionError("нет сети")):
            with self.assertLogs("laundry.vk.api", level="WARNING"):
                self.assertIsNone(self.client.send(9001, "привет"))
                self.assertFalse(self.client.edit(9001, 1, "текст"))
                self.client.answer_event("e1", 9001, 9001, "ок")

    def test_failed_edit_falls_back_to_new_message(self):
        self.user.tap(BTN_BOOK)
        count = len(self.vk.dialog(9001))
        self.vk.fail_edits = True                      # сообщение слишком старое (ошибка 909)
        self.assertIsNone(self.user.press("Ср 07.10"))
        self.assertEqual(count + 1, len(self.vk.dialog(9001)), "день показан новым сообщением")
        self.assertIn("Среда, 07.10.2026", self.user.text)
        self.assertIn("⬅️ К неделе", self.user.labels())
        self.user.press("⬅️ К неделе")                 # и картинка недели — тоже новым сообщением
        self.assertTrue(self.user.last["attachment"].startswith("photo"))
        self.assertEqual(count + 2, len(self.vk.dialog(9001)))

    def test_failed_photo_upload_still_shows_schedule(self):
        with mock.patch.object(self.vk, "photo_messages", side_effect=requests.ConnectionError("upload")):
            with self.assertLogs("laundry.vk.api", level="WARNING"):
                self.user.tap(BTN_BOOK)
        self.assertIsNone(self.user.last["attachment"])
        self.assertIn("Расписание стирки", self.user.text)
        self.assertIn("Ср 07.10 · 10 своб.", self.user.labels())

    def test_uploader_sends_photo_field_and_retries(self):
        """Сервер загрузки сначала отвечает не JSON, потом пустым photo, потом принимает файл."""
        from laundry.vk import api

        def reply(status, text):
            response = mock.Mock(status_code=status, text=text)
            response.json.side_effect = lambda: __import__("json").loads(text)
            return response

        http = mock.Mock()
        http.post.side_effect = [reply(502, "<html>Bad Gateway</html>"),
                                 reply(200, '{"server": 1, "photo": "[]", "hash": "h"}'),
                                 reply(200, '{"server": 1, "photo": "[{\\"p\\": 1}]", "hash": "h"}')]
        session = mock.Mock()
        session.method.side_effect = lambda name, values: (
            {"upload_url": "https://upload"} if name.endswith("UploadServer")
            else [{"owner_id": -77, "id": 5, "access_key": "k"}])
        client = api.VkClient(session, api.Uploader(session, http))
        with mock.patch.object(api, "UPLOAD_PAUSE", 0), mock.patch.object(api, "UPLOAD_TRIES", 3):
            self.assertEqual("photo-77_5_k", client.upload_photo(9001, b"png"))
            self.assertEqual("photo-77_5_k", client.upload_photo(9002, b"png"), "та же картинка — без загрузки")
        self.assertEqual(3, http.post.call_count)
        self.assertEqual(["photo"], list(http.post.call_args.kwargs["files"]))
        self.assertEqual(("photos.saveMessagesPhoto", {"server": 1, "photo": '[{"p": 1}]', "hash": "h"}),
                         session.method.call_args.args)
        # все попытки неудачны — в логе видно, что ответил сервер; бот не падает
        http.post.side_effect = lambda *args, **kwargs: reply(403, "<html>Forbidden</html>")
        with mock.patch.object(api, "UPLOAD_PAUSE", 0), self.assertLogs("laundry.vk.api", level="WARNING") as logs:
            self.assertIsNone(client.upload_photo(9001, b"other"))
        self.assertIn("HTTP 403", logs.output[0])
        self.assertIn("Forbidden", logs.output[0])

    def test_png_rejected_then_jpeg_accepted(self):
        """Сервер загрузки вернул пустое photo на PNG — та же картинка уходит в JPEG."""
        import io

        from PIL import Image

        from laundry.vk import api

        png = io.BytesIO()
        Image.new("RGB", (40, 30), (255, 255, 255)).save(png, format="PNG")
        names = []

        def post(url, files, timeout):
            name, _ = files["photo"]
            names.append(name)
            text = '{"server": 1, "photo": "", "hash": "h"}' if name.endswith(".png") else                 '{"server": 1, "photo": "[1]", "hash": "h"}'
            response = mock.Mock(status_code=200, text=text)
            response.json.return_value = __import__("json").loads(text)
            return response

        session = mock.Mock()
        session.method.side_effect = lambda name, values: (
            {"upload_url": "https://upload"} if name.endswith("UploadServer") else [{"owner_id": -77, "id": 6}])
        client = api.VkClient(session, api.Uploader(session, mock.Mock(post=post)))
        with mock.patch.object(api, "UPLOAD_PAUSE", 0), self.assertLogs("laundry.vk.api", level="INFO") as logs:
            self.assertEqual("photo-77_6", client.upload_photo(9001, png.getvalue()))
        self.assertEqual(["schedule.png", "schedule.png", "schedule.jpg"], names)
        self.assertIn("в JPEG — загружена", logs.output[-1])

    def test_loading_message_is_replaced_by_schedule(self):
        """«Записаться»: сначала «Загружаю расписание…», затем это же сообщение становится расписанием."""
        before = len(self.vk.dialog(9001))
        self.user.tap(BTN_BOOK)
        self.assertEqual("⏳ Загружаю расписание…", self.vk.sent[-1]["text"])
        self.assertIsNone(self.vk.sent[-1]["attachment"])
        self.assertEqual(before + 1, len(self.vk.dialog(9001)), "заглушка заменена, второго сообщения нет")
        self.assertIn("Расписание стирки", self.user.text)
        self.assertTrue(self.user.last["attachment"].startswith("photo"))
        self.assertIn("Ср 07.10 · 10 своб.", self.user.labels())
        # та же картинка уже загружена — расписание приходит сразу, без заглушки
        self.user.tap(BTN_BOOK)
        self.assertIn("Расписание стирки", self.vk.sent[-1]["text"])
        self.assertEqual(before + 2, len(self.vk.dialog(9001)))
        # заглушку не удалось заменить — расписание приходит новым сообщением
        self.client._photos.clear()
        self.vk.fail_edits = True
        self.user.tap(BTN_BOOK)
        self.assertEqual(before + 4, len(self.vk.dialog(9001)))
        self.assertTrue(self.user.last["attachment"].startswith("photo"))

    def test_unknown_and_broken_payloads(self):
        self.user.say("меню")
        cmid = self.user.last["cmid"]
        for payload in ({"c": "нет:такой"}, {"c": ""}, {}, {"c": "bk"}, {"c": "my"}, {"c": "m"}, {"c": "m:abc"},
                        {"c": "m:3:неизвестно"}, {"c": "pr"}, {"c": "adm:неизвестно"}):
            self.user.press_payload(payload, cmid)     # главное — на каждое нажатие есть ответ и нет ошибок
        self.user.say("текст", payload={"c": "нет:такой"})
        self.user.say("текст", payload={"button": "чужой формат"})
        self.assertIn("Не понял", self.user.text)

    def test_message_allow_and_deny_are_logged(self):
        with self.assertLogs("laundry.vk.dispatcher", level="INFO") as logs:
            for kind in ("message_allow", "message_deny", "group_join"):
                dispatcher.handle_event(self.client, {"type": kind, "object": {"user_id": 9001}})
        self.assertEqual(["message_allow: vk_id=9001", "message_deny: vk_id=9001"],
                         [line.split(":", 2)[2] for line in logs.output if "message_" in line])

    def test_long_hint_is_sent_as_message(self):
        long_text = "Очень длинное объяснение, почему действие нельзя выполнить. " * 3
        with mock.patch.dict(router.COMMANDS, {"long": lambda ctx, parts: (long_text, True)}):
            hint = self.user.press_command("long", cmid=1)
        self.assertEqual(90, len(hint))
        self.assertTrue(hint.endswith("…"))
        self.assertEqual(long_text.strip(), self.user.text.strip())

    def test_long_list_is_split_into_messages(self):
        starosta = self.vk_user(9000 + 500).register("Старостин", "301")
        self.make_starosta(starosta.row)
        now = self._now.replace(tzinfo=None)
        with db.transaction() as cur:
            for i in range(400):
                cur.execute("INSERT INTO users (vk_id, surname, room, floor, created_at, updated_at) "
                            "VALUES (%s, %s, %s, 3, %s, %s)",
                            (50000 + i, "Фамилия" + "я" * 20, f"3{i % 35 + 1:02d}", now, now))
        starosta.say("меню").tap(BTN_STAROSTA)
        before = len(self.vk.dialog(starosta.id))
        starosta.press("👥 Жильцы этажа")
        parts = self.vk.dialog(starosta.id)[before:]
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(m["text"]) <= 4096 for m in parts))
        self.assertEqual(402, sum(m["text"].count("к.3") for m in parts))
        self.assertEqual([None] * (len(parts) - 1) + [["⬅️ Назад"]],
                         [self.vk.labels(m) if m["keyboard"] else None for m in parts], "кнопка — у последней части")


class HandlerErrorTest(BotTestCase):
    expect_errors = True    # здесь обработчики падают нарочно

    def test_exception_in_button_handler(self):
        user = self.vk_user(9001).register("Иванов", "312")
        with mock.patch.dict(router.COMMANDS, {"bk": mock.Mock(side_effect=RuntimeError("сбой"))}):
            hint = user.press_command("bk:w", cmid=1)
        self.assertEqual("⚠️ Произошла ошибка, попробуйте ещё раз.", hint)
        self.assertEqual(1, len(self._errors.records))
        user.tap(BTN_BOOK)                              # бот продолжает работать
        self.assertIn("Расписание стирки", user.text)

    def test_exception_in_message_handler(self):
        user = self.vk_user(9001).start()
        with mock.patch.dict(router.STATES, {"reg_surname": mock.Mock(side_effect=RuntimeError("сбой"))}):
            user.say("Иванов")
        self.assertEqual("⚠️ Произошла ошибка, попробуйте ещё раз чуть позже.", user.text)
        user.say("Иванов").say("312")
        self.assertEqual("312", user.row["room"])

    def test_database_error_is_reported_politely(self):
        user = self.vk_user(9001).register("Иванов", "312")
        with mock.patch.object(db, "_connect", side_effect=OSError("MySQL недоступен")):
            user.say("меню")
            self.assertEqual("⚠️ Произошла ошибка, попробуйте ещё раз чуть позже.", user.text)
            self.assertEqual("⚠️ Произошла ошибка, попробуйте ещё раз.", user.press_command("bk:w", cmid=1))
        self.assertEqual(2, len(self._errors.records))


class _FakeLongPoll:
    """Long Poll, который сначала дважды рвётся, потом отдаёт события."""
    script: list = []
    created = 0

    def __init__(self, session, group_id, wait=25):
        type(self).created += 1
        self.group_id, self.wait = group_id, wait
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self.steps = step

    def check(self):
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return [mock.Mock(raw=raw) for raw in step]


class LongPollTest(BotTestCase):
    def test_reconnects_after_network_errors_and_keeps_handling(self):
        def message(text):
            return {"type": "message_new", "group_id": GROUP_ID,
                    "object": {"message": {"from_id": 9001, "peer_id": 9001, "text": text}}}

        stop = threading.Event()

        def finish():
            stop.set()
            return []

        _FakeLongPoll.created = 0
        _FakeLongPoll.script = [
            requests.ConnectionError("нет сети при подключении"),
            [[message("начать")], requests.ReadTimeout("обрыв во время ожидания")],
            [ValueError("ответ не JSON")],
            [[message("Иванов"), {"type": "wall_post_new", "object": {}}], [], [message("312")]],
        ]
        _FakeLongPoll.script[-1].append(mock.Mock(side_effect=finish))
        original_check = _FakeLongPoll.check

        def check(self):
            if self.steps and callable(self.steps[0]):
                return self.steps.pop(0)()
            return original_check(self)

        with mock.patch.object(vk_bot, "VkBotLongPoll", _FakeLongPoll), \
                mock.patch.object(_FakeLongPoll, "check", check), \
                mock.patch.object(vk_bot, "RETRY_MIN", 0), mock.patch.object(vk_bot, "RETRY_MAX", 0), \
                mock.patch.object(vk_bot, "WORKERS", 1), \
                self.assertLogs("laundry.vk.bot", level="WARNING") as logs:
            runner = threading.Thread(target=vk_bot.run, args=(self.client, GROUP_ID, stop))
            runner.start()
            runner.join(20)
        self.assertFalse(runner.is_alive(), "цикл Long Poll должен завершиться по stop")
        self.assertEqual(4, _FakeLongPoll.created, "после каждой ошибки — новое подключение")
        self.assertEqual(3, len(logs.output))
        self.assertIn("ConnectionError", logs.output[0])
        self.assertIn("ReadTimeout", logs.output[1])
        # события до и после обрывов обработаны, регистрация прошла
        self.assertEqual("312", db.get_user_by_vk(9001)["room"])

    def test_missing_token_rights_are_explained(self):
        """Ключ без права «Управление сообществом»: groups.getLongPollServer отвечает ошибкой 15."""
        stop = threading.Event()

        def finish():
            stop.set()
            return []

        denied = self.vk._error("groups.getLongPollServer", {}, 15, "Access denied: no access to call this method")
        _FakeLongPoll.created = 0
        _FakeLongPoll.script = [denied, [mock.Mock(side_effect=finish)]]
        with mock.patch.object(vk_bot, "VkBotLongPoll", _FakeLongPoll), \
                mock.patch.object(_FakeLongPoll, "check", lambda self: self.steps.pop(0)()), \
                mock.patch.object(vk_bot, "RETRY_MAX", 0), \
                self.assertLogs("laundry.vk.bot", level="WARNING") as logs:
            runner = threading.Thread(target=vk_bot.run, args=(self.client, GROUP_ID, stop))
            runner.start()
            runner.join(20)
        self.assertFalse(runner.is_alive())
        self.assertEqual(2, _FakeLongPoll.created, "после исправления ключа бот подключится сам")
        self.assertEqual(1, len(logs.output))
        self.assertTrue(logs.output[0].startswith("ERROR"), logs.output[0])
        self.assertIn("Управление сообществом", logs.output[0])
