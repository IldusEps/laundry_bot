"""Расписание душа — справочные картинки по кнопке главного меню (в душ бот не записывает).

Картинки лежат в laundry/images/shower_*.jpg и отправляются по порядку имён. Чтобы изменить расписание,
замените файлы (или добавьте shower_3.jpg и т. д.) и перезапустите ботов.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

IMAGES_DIR = Path(__file__).resolve().parent / "images"
CAPTION = "🚿 <b>Расписание душа</b>"
MISSING_TEXT = "🚿 Расписание душа пока не добавлено."
FAILED_TEXT = "⚠️ Не удалось отправить расписание душа, попробуйте ещё раз чуть позже."


@lru_cache(maxsize=1)
def pictures() -> tuple[bytes, ...]:
    return tuple(path.read_bytes() for path in sorted(IMAGES_DIR.glob("shower_*.jpg")))
