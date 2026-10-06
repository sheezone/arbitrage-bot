"""Channel announcement banner for the Матч Радар relaunch post (owner 2026-10-06).
Layout: glowing radar on the right, clean text column on the left (no overlap), sport
"pills" row and a CTA pill at the bottom. Rendered at 2x and downscaled for smooth edges.
Run: python scripts/gen_update_banner.py -> bot/assets/update_banner.png (1280x720)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

S = 2                      # supersampling
W, H = 1280 * S, 720 * S
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "update_banner.png"
VIOLET = (178, 107, 255)
LINE = (120, 70, 210)
BLIP = (255, 140, 240)
WHITE = (250, 246, 255)
LILAC = (200, 180, 235)
BOLD = "C:/Windows/Fonts/segoeuib.ttf"
SEMI = "C:/Windows/Fonts/seguisb.ttf"
REG = "C:/Windows/Fonts/segoeui.ttf"

CX, CY = int(W * 0.78), int(H * 0.5)
R = int(H * 0.36)
DIAL = int(R * 1.1)


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


def main():
    img = layer()
    d = ImageDraw.Draw(img)
    for y in range(H):  # deep violet diagonal-ish gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(16 + 10 * t), int(7 + 4 * t), int(34 + 20 * t)))
    halo = layer()
    hd = ImageDraw.Draw(halo)
    hd.ellipse([CX - DIAL * 1.4, CY - DIAL * 1.4, CX + DIAL * 1.4, CY + DIAL * 1.4], fill=(60, 20, 110))
    hd.ellipse([-W * 0.2, H * 0.55, W * 0.35, H * 1.4], fill=(45, 15, 80))
    img = ImageChops.add(img, halo.filter(ImageFilter.GaussianBlur(120 * S)))
    img = radar(img)

    d = ImageDraw.Draw(img)
    x0 = 80 * S
    pill(d, x0, 92 * S, "НОВОЕ  ·  ОБНОВЛЕНИЕ", ImageFont.truetype(SEMI, 22 * S),
         (60, 25, 110), (178, 107, 255), (230, 205, 255))

    title = ImageFont.truetype(BOLD, 92 * S)
    d.text((x0, 150 * S), "МАТЧ РАДАР", font=title, fill=WHITE)
    # gradient second line
    grad_font = ImageFont.truetype(BOLD, 54 * S)
    text = "ИИ-аналитика спорта"
    b = d.textbbox((0, 0), text, font=grad_font)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).text((x0, 262 * S), text, font=grad_font, fill=255)
    grad = layer()
    gd = ImageDraw.Draw(grad)
    for x in range(x0, x0 + b[2] + 1):
        t = (x - x0) / max(1, b[2])
        gd.line([(x, 0), (x, H)], fill=(int(190 + 65 * t), int(120 + 20 * t), 255))
    img = Image.composite(grad, img, mask)
    d = ImageDraw.Draw(img)

    sub = ImageFont.truetype(REG, 28 * S)
    for i, line in enumerate(("Кто победит и с каким % — итог ИИ",
                              "Готовые прогнозы и экспрессы дня")):
        d.ellipse([x0, (380 + i * 46) * S + 14 * S, x0 + 10 * S, (380 + i * 46) * S + 24 * S], fill=BLIP)
        d.text((x0 + 26 * S, (380 + i * 46) * S), line, font=sub, fill=LILAC)

    chip = ImageFont.truetype(SEMI, 21 * S)
    x, y = x0, 500 * S
    for name in ("Футбол", "Хоккей", "Баскетбол", "Теннис"):
        x += pill(d, x, y, name, chip, (32, 16, 60), (95, 55, 170), WHITE) + 10 * S
    x, y = x0, 556 * S
    for name in ("Киберспорт", "Волейбол", "Наст. теннис"):
        x += pill(d, x, y, name, chip, (32, 16, 60), (95, 55, 170), WHITE) + 10 * S

    img = img.resize((W // S, H // S), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()
