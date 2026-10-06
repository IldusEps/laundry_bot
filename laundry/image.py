"""Картинка с расписанием недели (Pillow).

Цвета: зелёный — свободно, красный — занято, серый — записаться нельзя (прошло или закрыто).
В занятой ячейке — номер комнаты и фамилия. Когда время записи прошло, ячейка становится серой,
а подпись остаётся: видно, кто стирал. Картинка общая для Telegram- и VK-бота.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import schedule as sched
from .services import Cell, Grid

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

GREEN = (76, 175, 80)
RED = (229, 57, 53)
GRAY = (189, 189, 189)
DARK_GRAY = (117, 117, 117)
DARK_RED = (140, 20, 20)
BG = (255, 255, 255)
HEADER_BG = (245, 245, 245)
GRID_LINE = (255, 255, 255)
TEXT = (33, 33, 33)
MUTED = (110, 110, 110)
WHITE = (255, 255, 255)

SCALE = 2           # рисуем в 2 раза крупнее — картинка остаётся чёткой после сжатия Telegram
CELL_W = 96
CELL_H = 38
TIME_W = 112
PAD = 16
TITLE_H = 54
HEAD_H = 50
LEGEND_H = 46


@lru_cache(maxsize=8)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONTS_DIR / name), size * SCALE)


def _text_center(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str,
                 font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    draw.text(((x0 + x1) / 2, (y0 + y1) / 2), text, font=font, fill=fill, anchor="mm")


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Обрезает текст с «…», чтобы он поместился в ячейку по ширине."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while len(text) > 1 and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _cell_style(cell: Cell) -> tuple[tuple[int, int, int], list[str], tuple[int, int, int]]:
    """(цвет заливки, строки подписи, цвет подписи).

    Запись, время которой прошло, — серая, но подпись (комната и фамилия) остаётся видна."""
    if cell.booking:
        fill, color = (GRAY, TEXT) if cell.past else (RED, WHITE)
        if cell.state == "mine":
            return fill, ["ВЫ"], color
        return fill, [f"к.{cell.booking['room']}", str(cell.booking.get("surname") or "")], color
    if cell.state == "free":
        return GREEN, ["свободно"], WHITE
    if cell.state == "closed":
        return GRAY, ["закрыто"], MUTED
    return GRAY, [], MUTED  # прошло, записи не было


def week_image(grid: Grid, title: str) -> bytes:
    days = grid.week.days
    slots = grid.rules.slots
    show_wing = grid.wing is None and bool(grid.rules.wing_weekdays)
    head_h = HEAD_H + (18 if show_wing else 0)

    width = PAD * 2 + TIME_W + CELL_W * len(days)
    height = PAD * 2 + TITLE_H + head_h + CELL_H * len(slots) + LEGEND_H
    s = SCALE
    img = Image.new("RGB", (width * s, height * s), BG)
    draw = ImageDraw.Draw(img)

    # заголовок
    draw.text((PAD * s, (PAD + 6) * s), title, font=_font(20, bold=True), fill=TEXT)
    draw.text((PAD * s, (PAD + 32) * s),
              f"{sched.fmt_day_short(days[0])} – {sched.fmt_day_short(days[-1])}",
              font=_font(13), fill=MUTED)

    top = PAD + TITLE_H
    left = PAD + TIME_W

    # шапка: день недели, дата, (крыло)
    draw.rectangle((PAD * s, top * s, (width - PAD) * s, (top + head_h) * s), fill=HEADER_BG)
    _text_center(draw, (PAD * s, top * s, left * s, (top + head_h) * s), "Время", _font(13, True), MUTED)
    for i, day in enumerate(days):
        x0 = left + i * CELL_W
        _text_center(draw, (x0 * s, (top + 4) * s, (x0 + CELL_W) * s, (top + 24) * s),
                     sched.WEEKDAYS_SHORT[day.weekday()], _font(15, True), TEXT)
        _text_center(draw, (x0 * s, (top + 24) * s, (x0 + CELL_W) * s, (top + 44) * s),
                     day.strftime("%d.%m"), _font(12), MUTED)
        if show_wing:
            wing = grid.rules.wing_for_weekday(day.weekday())
            _text_center(draw, (x0 * s, (top + 44) * s, (x0 + CELL_W) * s, (top + 62) * s),
                         f"{wing} крыло", _font(11), MUTED)

    # ячейки
    y = top + head_h
    for slot in slots:
        _text_center(draw, (PAD * s, y * s, left * s, (y + CELL_H) * s), slot.label, _font(12), TEXT)
        for i, day in enumerate(days):
            cell = grid.cell(day, slot)
            fill, lines, color = _cell_style(cell)
            lines = [line for line in lines if line]
            mine = cell.state == "mine"
            x0 = left + i * CELL_W
            box = ((x0 + 2) * s, (y + 2) * s, (x0 + CELL_W - 2) * s, (y + CELL_H - 2) * s)
            draw.rounded_rectangle(box, radius=6 * s, fill=fill,
                                   outline=(DARK_GRAY if cell.past else DARK_RED) if mine else None,
                                   width=3 * s if mine else 0)
            if len(lines) == 1:
                _text_center(draw, box, lines[0], _font(12, bold=mine), color)
            elif lines:  # запись: номер комнаты, под ним фамилия
                middle = (box[1] + box[3]) / 2
                inner = box[2] - box[0] - 8 * s
                _text_center(draw, (box[0], box[1] + 3 * s, box[2], middle + 1 * s), lines[0], _font(11, True), color)
                _text_center(draw, (box[0], middle - 1 * s, box[2], box[3] - 3 * s),
                             _fit(draw, lines[1], _font(10), inner), _font(10), color)
        y += CELL_H

    # легенда
    ly = y + 16
    x = PAD
    for color, text in ((GREEN, "свободно"), (RED, "занято"), (GRAY, "нельзя записаться")):
        draw.rounded_rectangle((x * s, ly * s, (x + 18) * s, (ly + 18) * s), radius=4 * s, fill=color)
        draw.text(((x + 26) * s, (ly + 9) * s), text, font=_font(13), fill=TEXT, anchor="lm")
        x += 26 + int(draw.textlength(text, font=_font(13)) / s) + 24

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
