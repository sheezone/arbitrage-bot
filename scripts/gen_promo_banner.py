"""One-off generator for a promotional ad banner (not a bot UI banner -- see
gen_banners.py for those). Run manually to (re)produce bot/assets/promo_banner.png,
used for advertising the bot in outside channels/groups."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "bot" / "assets"

BG_TOP = (10, 12, 20)
BG_BOTTOM = (18, 22, 36)
WHITE = (240, 243, 248)
GRAY = (150, 158, 172)
MINT = (70, 211, 155)
GOLD = (240, 190, 90)

W, H = 1200, 1220
FONT_BLACK = "C:/Windows/Fonts/arialbd.ttf"
FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"


def _vgradient(draw, top, bottom):
    for y in range(H):
        t = y / H
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _centered_text(draw, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)
    return w


def _chevron(draw, cx, cy, size, color, width=8):
    draw.line([(cx - size, cy + size * 0.6), (cx, cy - size * 0.6)], fill=color, width=width, joint="curve")
    draw.line([(cx, cy - size * 0.6), (cx + size, cy + size * 0.6)], fill=color, width=width, joint="curve")


def make_promo_banner(filename: str) -> None:
    img = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(img)
    _vgradient(draw, BG_TOP, BG_BOTTOM)

    _chevron(draw, W // 2, 130, 30, MINT)

    eyebrow_font = ImageFont.truetype(FONT_REGULAR, 32)
    _centered_text(draw, 190, "ПРОФЕССИОНАЛЬНЫЙ TELEGRAM-БОТ", eyebrow_font, GRAY)

    title_font = ImageFont.truetype(FONT_BLACK, 108)
    _centered_text(draw, 250, "АРБИТРАЖНЫЙ", title_font, WHITE)
    _centered_text(draw, 370, "БОТ", title_font, MINT)

    underline_w = 200
    draw.line([((W - underline_w) / 2, 505), ((W + underline_w) / 2, 505)], fill=MINT, width=5)

    slogan_font = ImageFont.truetype(FONT_BLACK, 58)
    _centered_text(draw, 560, "ЛОВИТ ВИЛКИ", slogan_font, WHITE)
    _centered_text(draw, 630, "НА КОЭФФИЦИЕНТАХ", slogan_font, WHITE)

    sub_font = ImageFont.truetype(FONT_REGULAR, 36)
    _centered_text(draw, 730, "Сравнивает 10+ букмекеров в реальном времени", sub_font, GRAY)
    _centered_text(draw, 780, "и присылает гарантированный доход", sub_font, GRAY)

    # Feature pills, two rows of three so nothing runs off the canvas edge
    pill_font = ImageFont.truetype(FONT_REGULAR, 30)
    rows = [["Футбол", "Хоккей", "Киберспорт"], ["Теннис", "Бокс", "Волейбол"]]
    gap = 20
    row_y = 850
    for row in rows:
        widths = []
        for f in row:
            bbox = draw.textbbox((0, 0), f, font=pill_font)
            widths.append(bbox[2] - bbox[0] + 56)
        total_w = sum(widths) + gap * (len(row) - 1)
        x = (W - total_w) / 2
        for f, w in zip(row, widths):
            draw.rounded_rectangle([x, row_y, x + w, row_y + 56], radius=28, outline=MINT, width=2)
            text_w = draw.textbbox((0, 0), f, font=pill_font)[2]
            draw.text((x + (w - text_w) / 2, row_y + 12), f, font=pill_font, fill=WHITE)
            x += w + gap
        row_y += 76

    cta_font = ImageFont.truetype(FONT_BLACK, 44)
    cta_w = _centered_text(draw, 1030, "3 ДНЯ БЕСПЛАТНО", cta_font, GOLD)
    draw.line([((W - cta_w) / 2 - 20, 1095), ((W + cta_w) / 2 + 20, 1095)], fill=GOLD, width=3)

    handle_font = ImageFont.truetype(FONT_REGULAR, 34)
    _centered_text(draw, 1140, "@Lineyka111_bot", handle_font, GRAY)

    img.save(ASSETS / filename)
    print(f"wrote {ASSETS / filename}")


if __name__ == "__main__":
    make_promo_banner("promo_banner.png")
