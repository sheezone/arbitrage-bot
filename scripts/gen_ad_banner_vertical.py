"""Vertical/portrait version of the ad creative (owner 2026-10-07: the landscape
1280x720 banner got cropped by a Telegram-ad editor that only shows a portrait slot).
Radar centered on top, headline + stat + bullets + sport pills stacked below.
Run: python scripts/gen_ad_banner_vertical.py -> bot/assets/ad_banner_vertical.png (1080x1350)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

S = 2                      # supersampling
W, H = 1080 * S, 1560 * S
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "ad_banner_vertical.png"
VIOLET = (178, 107, 255)
LINE = (120, 70, 210)
BLIP = (255, 140, 240)
WHITE = (250, 246, 255)
LILAC = (200, 180, 235)
BOLD = "C:/Windows/Fonts/segoeuib.ttf"
SEMI = "C:/Windows/Fonts/seguisb.ttf"
REG = "C:/Windows/Fonts/segoeui.ttf"

CX, CY = int(W * 0.5), int(H * 0.235)
R = int(W * 0.34)
DIAL = int(R * 1.12)


def layer():
    return Image.new("RGB", (W, H))


def glow(base, lay, blur, k=1.0):
    g = lay.filter(ImageFilter.GaussianBlur(blur))
    if k != 1:
        g = Image.eval(g, lambda v: min(255, int(v * k)))
    return ImageChops.add(base, g)


def radar(img):
    scope = Image.new("L", (W, H), 0)
    ImageDraw.Draw(scope).ellipse([CX - R, CY - R, CX + R, CY + R], fill=255)
    grid = layer()
    gd = ImageDraw.Draw(grid)
    step = R / 5
    for k in range(-6, 7):
        gd.line([(CX + k * step, CY - R), (CX + k * step, CY + R)], fill=(42, 22, 78), width=2 * S)
        gd.line([(CX - R, CY + k * step), (CX + R, CY + k * step)], fill=(42, 22, 78), width=2 * S)
    img = ImageChops.add(img, Image.composite(grid, layer(), scope))

    lines = layer()
    ld = ImageDraw.Draw(lines)
    for f in (0.25, 0.5, 0.75, 1.0):
        r = R * f
        ld.ellipse([CX - r, CY - r, CX + r, CY + r], outline=LINE, width=2 * S)
    ld.line([(CX - R, CY), (CX + R, CY)], fill=LINE, width=2 * S)
    ld.line([(CX, CY - R), (CX, CY + R)], fill=LINE, width=2 * S)
    img = glow(img, lines, 5 * S, 0.7)
    img = ImageChops.add(img, lines)

    dial = layer()
    dd = ImageDraw.Draw(dial)
    dd.ellipse([CX - DIAL, CY - DIAL, CX + DIAL, CY + DIAL], outline=VIOLET, width=4 * S)
    for k in range(120):
        a = math.radians(k * 3)
        r0, r1 = R + 8 * S, DIAL - (14 * S if k % 5 else 6 * S)
        dd.line([(CX + r0 * math.cos(a), CY + r0 * math.sin(a)), (CX + r1 * math.cos(a), CY + r1 * math.sin(a))],
                fill=VIOLET, width=(2 if k % 5 else 3) * S)
    img = glow(img, dial, 14 * S, 1.1)
    img = ImageChops.add(img, dial)

    sweep = layer()
    sd = ImageDraw.Draw(sweep)
    beam, span = -35, 70
    for k in range(90):
        a0 = beam - span + k * span / 90
        v = (k / 90) ** 2.0
        sd.pieslice([CX - R, CY - R, CX + R, CY + R], a0, a0 + span / 90 + 0.6,
                    fill=(int(160 * v), int(80 * v), int(240 * v)))
    img = ImageChops.add(img, Image.composite(sweep, layer(), scope))
    beamline = layer()
    a = math.radians(beam)
    ImageDraw.Draw(beamline).line([(CX, CY), (CX + R * math.cos(a), CY + R * math.sin(a))], fill=(230, 180, 255), width=3 * S)
    img = glow(img, beamline, 6 * S, 1.2)
    img = ImageChops.add(img, beamline)

    blips = layer()
    bd = ImageDraw.Draw(blips)
    for fx, fy, r in ((0.30, -0.45, 9), (-0.35, -0.10, 8), (-0.25, 0.42, 8), (0.18, 0.35, 7), (0.55, 0.12, 9)):
        x, y, r = CX + fx * R, CY + fy * R, r * S
        bd.ellipse([x - r, y - r, x + r, y + r], fill=BLIP)
    img = glow(img, blips, 16 * S, 1.8)
    img = glow(img, blips, 5 * S, 1.2)
    return ImageChops.add(img, blips)


def pill(d, x, y, text, font, fill, outline, color):
    b = d.textbbox((0, 0), text, font=font)
    w, h = b[2] - b[0], b[3] - b[1]
    px, py = 18 * S, 10 * S
    d.rounded_rectangle([x, y, x + w + 2 * px, y + h + 2 * py + 4 * S], radius=(h + 2 * py) // 2,
                        fill=fill, outline=outline, width=2 * S)
    d.text((x + px - b[0], y + py - b[1] + 2 * S), text, font=font, fill=color)
    return w + 2 * px


def centered(d, cx, y, text, font, fill):
    b = d.textbbox((0, 0), text, font=font)
    w = b[2] - b[0]
    d.text((cx - w / 2 - b[0], y), text, font=font, fill=fill)
    return w


def main():
    img = layer()
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(16 + 10 * t), int(7 + 4 * t), int(34 + 20 * t)))
    halo = layer()
    hd = ImageDraw.Draw(halo)
    hd.ellipse([CX - DIAL * 1.4, CY - DIAL * 1.4, CX + DIAL * 1.4, CY + DIAL * 1.4], fill=(60, 20, 110))
    hd.ellipse([W * 0.2, H * 0.62, W * 0.95, H * 1.25], fill=(45, 15, 80))
    img = ImageChops.add(img, halo.filter(ImageFilter.GaussianBlur(130 * S)))
    img = radar(img)
    d = ImageDraw.Draw(img)

    x0 = 70 * S
    pill_y = CY + R + 46 * S
    centered(d, W / 2, pill_y, "ИИ-АНАЛИТИКА СПОРТА", ImageFont.truetype(SEMI, 22 * S), (0, 0, 0, 0))  # measure only
    pw = d.textbbox((0, 0), "ИИ-АНАЛИТИКА СПОРТА", font=ImageFont.truetype(SEMI, 22 * S))[2]
    pill(d, (W - (pw + 36 * S)) / 2, pill_y, "ИИ-АНАЛИТИКА СПОРТА", ImageFont.truetype(SEMI, 22 * S),
         (60, 25, 110), (178, 107, 255), (230, 205, 255))

    hook = ImageFont.truetype(BOLD, 58 * S)
    y = pill_y + 70 * S
    centered(d, W / 2, y, "ИИ СЧИТАЕТ ИСХОД", hook, WHITE)
    y += 68 * S
    centered(d, W / 2, y, "ЗА ТЕБЯ", hook, VIOLET)

    # hero stat, centered, gradient
    y += 110 * S
    stat_font = ImageFont.truetype(BOLD, 140 * S)
    stat = "83%"
    b = d.textbbox((0, 0), stat, font=stat_font)
    sx = (W - b[2]) / 2
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).text((sx, y), stat, font=stat_font, fill=255)
    grad = layer()
    gd = ImageDraw.Draw(grad)
    for x in range(int(sx), int(sx + b[2]) + 1):
        t = (x - sx) / max(1, b[2])
        gd.line([(x, 0), (x, H)], fill=(int(255 - 55 * t), int(150 + 10 * t), 255))
    img = Image.composite(grad, img, mask)
    d = ImageDraw.Draw(img)
    y += b[3] + 8 * S
    centered(d, W / 2, y, "точность ИИ-прогнозов", ImageFont.truetype(REG, 30 * S), LILAC)

    y += 70 * S
    sub = ImageFont.truetype(REG, 30 * S)
    bullets = ("Разбор матча по 2 командам или скриншоту",
               "Готовые прогнозы и экспрессы каждый день",
               "1 день MAX — бесплатно")
    block_w = max(d.textbbox((0, 0), line, font=sub)[2] for line in bullets) + 30 * S
    bx = (W - block_w) / 2
    for line in bullets:
        d.ellipse([bx, y + 16 * S, bx + 11 * S, y + 27 * S], fill=BLIP)
        d.text((bx + 24 * S, y), line, font=sub, fill=LILAC)
        y += 46 * S

    y += 22 * S
    chip = ImageFont.truetype(SEMI, 23 * S)
    rows = (("Футбол", "Баскетбол", "Хоккей", "Теннис"), ("Киберспорт", "Бокс", "MMA", "Волейбол", "Наст. теннис"))
    for row in rows:
        widths = [d.textbbox((0, 0), n, font=chip)[2] + 36 * S + 10 * S for n in row]
        total = sum(widths) - 10 * S
        x = (W - total) / 2
        for name in row:
            x += pill(d, x, y, name, chip, (32, 16, 60), (95, 55, 170), WHITE) + 10 * S
        y += 62 * S

    img = img.resize((W // S, H // S), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()
