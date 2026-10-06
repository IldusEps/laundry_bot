"""Клавиатуры VK с соблюдением лимитов API.

Лимиты (dev.vk.com, «Клавиатура ботов»): в ряду до 5 кнопок; обычная клавиатура — до 10 рядов и 40 кнопок,
inline — до 6 рядов и 10 кнопок; подпись — до 40 символов; payload — до 255 символов.

Кнопка задаётся парой (подпись, команда). Команда — та же строка, что callback_data в Telegram-боте
(bk:w, m:3:x:15 …); в payload она лежит как {"c": "bk:w"}, номер страницы списка — {"c": …, "p": 2}.
Длинные списки inline_paged() делит на страницы с кнопками ◀ / ▶.
"""
from __future__ import annotations

import json
import math
from typing import Sequence

MAX_ROW = 5
MAX_INLINE_ROWS = 6
MAX_INLINE_BUTTONS = 10
MAX_MENU_ROWS = 10
MAX_MENU_BUTTONS = 40
MAX_LABEL = 40
MAX_PAYLOAD = 255

PREV, NEXT = "◀", "▶"

Button = tuple[str, str]           # (подпись, команда)
Rows = Sequence[Sequence[Button]]

# Пустая обычная клавиатура: прячет меню (например, на время регистрации)
EMPTY_MENU = json.dumps({"buttons": [], "one_time": True})


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def shorten(text: str, limit: int = MAX_LABEL) -> str:
    """Обрезает подпись до limit символов (считаем в UTF-16 — эмодзи занимают 2) и ставит «…»."""
    if utf16_len(text) <= limit:
        return text
    out = ""
    for ch in text:
        if utf16_len(out + ch) > limit - 1:
            break
        out += ch
    return out.rstrip() + "…"


def payload(command: str, page: int | None = None) -> str:
    data: dict = {"c": command}
    if page:
        data["p"] = page
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_PAYLOAD:  # считаем в байтах — так строже
        raise ValueError(f"payload длиннее {MAX_PAYLOAD} байт: {raw!r}")
    return raw


def _color(label: str) -> str:
    if label.startswith("✅"):
        return "positive"
    if label.startswith(("❌", "🚫", "🗑")):
        return "negative"
    return "secondary"


def _button(label: str, command: str, kind: str, page: int | None = None) -> dict:
    return {"action": {"type": kind, "label": shorten(label), "payload": payload(command, page)},
            "color": _color(label)}


def _check(rows: list[list[dict]], max_rows: int, max_buttons: int) -> None:
    total = sum(len(r) for r in rows)
    if len(rows) > max_rows or total > max_buttons or any(len(r) > MAX_ROW for r in rows):
        raise ValueError(f"Клавиатура не помещается в лимиты VK: рядов {len(rows)}, кнопок {total}")


def inline(rows: Rows) -> str:
    """Inline-клавиатура из callback-кнопок (нажатие приходит событием message_event)."""
    built = [[_button(lbl, cmd, "callback") for lbl, cmd in row] for row in rows if row]
    _check(built, MAX_INLINE_ROWS, MAX_INLINE_BUTTONS)
    return json.dumps({"inline": True, "buttons": built}, ensure_ascii=False)


def inline_paged(items: Sequence[Button], per_row: int = 1, footer: Rows = (), nav: str = "",
                 page: int = 0) -> str:
    """Список кнопок items (по per_row в ряду) + неизменные ряды footer. Если всё не влезает в inline-клавиатуру,
    показывается страница page и ряд ◀ / ▶ с командой nav (обычно команда этого же экрана)."""
    footer = [list(r) for r in footer if r]
    free_buttons = MAX_INLINE_BUTTONS - sum(len(r) for r in footer)
    free_rows = MAX_INLINE_ROWS - len(footer)
    per_row = max(1, min(per_row, MAX_ROW))

    def chunk(seq: Sequence[Button]) -> list[list[Button]]:
        return [list(seq[i:i + per_row]) for i in range(0, len(seq), per_row)]

    if len(items) <= free_buttons and math.ceil(len(items) / per_row) <= free_rows:
        return inline(chunk(items) + footer)

    size = min(free_buttons - 2, (free_rows - 1) * per_row)  # место под ряд ◀ / ▶
    size -= size % per_row  # только целые ряды: пары «Принять / Отклонить» не разрываются
    if size < 1 or not nav:
        raise ValueError("Для постраничного списка нужна команда nav и место под кнопки")
    pages = math.ceil(len(items) / size)
    page = min(max(page, 0), pages - 1)
    rows: list[list[dict]] = [[_button(lbl, cmd, "callback") for lbl, cmd in row]
                              for row in chunk(items[page * size:(page + 1) * size])]
    nav_row = []
    if page > 0:
        nav_row.append(_button(f"{PREV} {page}/{pages}", nav, "callback", page - 1))
    if page < pages - 1:
        nav_row.append(_button(f"{page + 2}/{pages} {NEXT}", nav, "callback", page + 1))
    rows.append(nav_row)
    rows += [[_button(lbl, cmd, "callback") for lbl, cmd in row] for row in footer]
    _check(rows, MAX_INLINE_ROWS, MAX_INLINE_BUTTONS)
    return json.dumps({"inline": True, "buttons": rows}, ensure_ascii=False)


def menu(rows: Rows) -> str:
    """Обычная клавиатура под полем ввода (главное меню). Нажатие приходит сообщением с payload."""
    built = [[_button(lbl, cmd, "text") for lbl, cmd in row] for row in rows if row]
    _check(built, MAX_MENU_ROWS, MAX_MENU_BUTTONS)
    return json.dumps({"one_time": False, "buttons": built}, ensure_ascii=False)


def parse_payload(raw: object) -> tuple[str | None, int]:
    """payload события -> (команда, страница). Понимает {"c": …, "p": …} и {"command": "start"} (кнопка «Начать»)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, 0
    if not isinstance(raw, dict):
        return None, 0
    command = raw.get("c") or raw.get("command")
    try:
        page = int(raw.get("p") or 0)
    except (TypeError, ValueError):
        page = 0
    return (str(command) if command else None), max(page, 0)
