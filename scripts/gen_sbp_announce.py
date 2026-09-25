"""One-off generator for the "оплата по СБП" announcement banner (same visual style as
gen_promo_banner.py). Run manually: python scripts/gen_sbp_announce.py
-> bot/assets/sbp_announce.png. The СБП mark comes from bot/assets/sbp_logo.png."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "bot" / "assets"

BG_TOP = (10, 12, 20)
BG_BOTTOM = (18, 22, 36)
WHITE = (240, 243, 248)
GRAY = (150, 158, 172)
MINT = (70, 211, 155)
LOGO_BG = (29, 19, 70)  # sbp_logo.png's own background

W, H = 1200, 1200
FONT_BLACK = "C:/Windows/Fonts/arialbd.ttf"
FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"


def _vgradient(draw):
    for y in range(H):
        t = y / H
        c = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)


def _centered(draw, y, text, font, fill):
    w = draw.textbbox((0, 0), text, font=font)[2]
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def _chevron(draw, cx, cy, size, color, width=8):
    draw.line([(cx - size, cy + size * 0.6), (cx, cy - size * 0.6)], fill=color, width=width, joint="curve")
    draw.line([(cx, cy - size * 0.6), (cx + size, cy + size * 0.6)], fill=color, width=width, joint="curve")


def make(filename: str = "sbp_announce.png") -> Path:
    img = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(img)
    _vgradient(draw)

    _chevron(draw, W // 2, 110, 28, MINT)
    _centered(draw, 165, "НОВОЕ В БОТЕ", ImageFont.truetype(FONT_REGULAR, 34), GRAY)

    title = ImageFont.truetype(FONT_BLACK, 112)
    _centered(draw, 220, "ОПЛАТА", title, WHITE)
    _centered(draw, 340, "ПО СБП", title, MINT)
    draw.line([((W - 200) / 2, 480), ((W + 200) / 2, 480)], fill=MINT, width=5)

    # СБП mark on a soft rounded tile with a faint mint glow.
    tile = 340
    tx, ty = (W - tile) // 2, 530
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i, a in enumerate((18, 12, 7)):
        pad = 14 + i * 12
        gd.rounded_rectangle([tx - pad, ty - pad, tx + tile + pad, ty + tile + pad], radius=70 + pad, fill=(*MINT, a))
    img.paste(glow, (0, 0), glow)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([tx, ty, tx + tile, ty + tile], radius=64, fill=LOGO_BG)
    logo = Image.open(ASSETS / "sbp_logo.png").convert("RGB").resize((tile - 40, tile - 40), Image.LANCZOS)
    img.paste(logo, (tx + 20, ty + 20))

    body = ImageFont.truetype(FONT_REGULAR, 40)
    _centered(draw, 925, "Оплата подписки в приложении вашего банка", body, WHITE)
    _centered(draw, 980, "без ввода данных карты", body, GRAY)

    _centered(draw, 1085, "@Lineyka111_bot", ImageFont.truetype(FONT_BLACK, 44), MINT)

    out = ASSETS / filename
    img.save(out, optimize=True)
    return out


if __name__ == "__main__":
    print(make())
