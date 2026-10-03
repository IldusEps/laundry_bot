"""Картинка с расписанием недели (Pillow).

Цвета: зелёный — свободно, красный — занято, серый — записаться нельзя (прошло или закрыто).
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import schedule as sched
from .services import Grid

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

GREEN = (76, 175, 80)
RED = (229, 57, 53)
GRAY = (189, 189, 189)
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


def _cell_style(state: str, booking: dict | None) -> tuple[tuple[int, int, int], str, tuple[int, int, int]]:
    """(цвет заливки, подпись, цвет подписи)"""
    if state == "mine":
        return RED, "ВЫ", WHITE
    if state == "busy":
        return RED, f"к.{booking['room']}" if booking else "занято", WHITE
    if state == "free":
        return GREEN, "свободно", WHITE
    if state == "closed":
        return GRAY, "закрыто", MUTED
    return GRAY, "", MUTED  # past


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
            fill, label, color = _cell_style(cell.state, cell.booking)
            x0 = left + i * CELL_W
            box = ((x0 + 2) * s, (y + 2) * s, (x0 + CELL_W - 2) * s, (y + CELL_H - 2) * s)
            draw.rounded_rectangle(box, radius=6 * s, fill=fill,
                                   outline=DARK_RED if cell.state == "mine" else None,
                                   width=3 * s if cell.state == "mine" else 0)
            if label:
                _text_center(draw, box, label, _font(12, bold=cell.state == "mine"), color)
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
