"""Кнопка «Расписание душа» в обоих ботах: две картинки, без текста расписания."""
from unittest import mock

import requests

from laundry import shower
from laundry.keyboards import BTN_SHOWER

from .base import BotTestCase


class ShowerTest(BotTestCase):
    def test_pictures_are_in_the_project(self):
        images = shower.pictures()
        self.assertEqual(2, len(images), "пн–чт и пт–вс")
        for data in images:
            self.assertTrue(data.startswith(b"\xff\xd8"), "это JPEG")

    def test_vk_sends_both_pictures_in_one_message(self):
        user = self.vk_user(9001).register("Иванов", "312")
        before = len(self.vk.dialog(9001))
        user.tap(BTN_SHOWER)
        self.assertEqual(before + 1, len(self.vk.dialog(9001)))
        self.assertEqual("🚿 Расписание душа", user.text)
        attachments = user.last["attachment"].split(",")
        self.assertEqual(2, len(attachments))
        self.assertTrue(all(a.startswith("photo") for a in attachments))
        self.assertNotEqual(attachments[0], attachments[1])
        uploads = len(self.vk.photos)
        self.vk_user(9002).register("Петров", "313").tap(BTN_SHOWER)   # второй раз картинки не загружаются
        self.assertEqual(uploads, len(self.vk.photos))

    def test_vk_upload_failure(self):
        user = self.vk_user(9001).register("Иванов", "312")
        with mock.patch.object(self.vk, "photo_messages", side_effect=requests.ConnectionError("upload")):
            with self.assertLogs("laundry.vk.api", level="WARNING"):
                user.tap(BTN_SHOWER)
        self.assertIn("Не удалось отправить расписание душа", user.text)

    def test_telegram_sends_two_photos(self):
        user = self.tg_user(2001).register("Сидоров", "415")
        before = len(self.tg._chats[2001])
        user.say(BTN_SHOWER)
        first, second = self.tg._chats[2001][before:]
        self.assertTrue(first["photo"] and second["photo"])
        self.assertIn("Расписание душа", first["text"])
        self.assertFalse(second["text"])
