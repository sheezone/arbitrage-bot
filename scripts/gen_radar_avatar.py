"""Матч Радар avatar (channel / bot photo): purple radar -- tick-marked outer dial, many
thin rings, crosshair + diagonals, a soft fading sweep and glowing blips.
Run: python scripts/gen_radar_avatar.py -> bot/assets/radar_avatar.png (1024x1024)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

N = 1024
C = N / 2
R = N * 0.40          # inner scope radius
DIAL = N * 0.445      # tick ring radius
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "radar_avatar.png"
VIOLET = (178, 107, 255)
LINE = (140, 80, 230)
BLIP = (255, 140, 240)


def layer():
    return Image.new("RGB", (N, N))


def glow(base, lay, blur, k=1.0):
    g = lay.filter(ImageFilter.GaussianBlur(blur))
    if k != 1:
        g = Image.eval(g, lambda v: int(v * k))
    return ImageChops.add(base, g)


def main():
    img = layer()
    d = ImageDraw.Draw(img)
    for i in range(160, 0, -1):  # dark violet vignette
        r = N * 0.72 * i / 160
        t = i / 160
        d.ellipse([C - r, C - r, C + r, C + r], fill=(int(30 - 22 * t), int(10 - 7 * t), int(58 - 40 * t)))

    scope = Image.new("L", (N, N), 0)
    ImageDraw.Draw(scope).ellipse([C - R, C - R, C + R, C + R], fill=255)

    # faint square grid inside the scope
    grid = layer()
    gd = ImageDraw.Draw(grid)
    step = R / 6
    for k in range(-7, 8):
        gd.line([(C + k * step, 0), (C + k * step, N)], fill=(50, 26, 90), width=2)
        gd.line([(0, C + k * step), (N, C + k * step)], fill=(50, 26, 90), width=2)
    img = ImageChops.add(img, Image.composite(grid, layer(), scope))

    # rings, crosshair, diagonals
    lines = layer()
    ld = ImageDraw.Draw(lines)
    for f in (1 / 7, 2 / 7, 3 / 7, 4 / 7, 5 / 7, 6 / 7, 1.0):
        r = R * f
        ld.ellipse([C - r, C - r, C + r, C + r], outline=LINE, width=4)
    ld.line([(C - R, C), (C + R, C)], fill=LINE, width=4)
    ld.line([(C, C - R), (C, C + R)], fill=LINE, width=4)
    for a in (45, 135):
        dx, dy = R * math.cos(math.radians(a)), R * math.sin(math.radians(a))
        ld.line([(C - dx, C - dy), (C + dx, C + dy)], fill=(110, 60, 190), width=3)
    img = glow(img, lines, 6, 0.8)
    img = ImageChops.add(img, lines)

    # outer dial with tick marks
    dial = layer()
    dd = ImageDraw.Draw(dial)
    dd.ellipse([C - DIAL, C - DIAL, C + DIAL, C + DIAL], outline=VIOLET, width=10)
    dd.ellipse([C - R - 10, C - R - 10, C + R + 10, C + R + 10], outline=VIOLET, width=6)
    for k in range(180):
        a = math.radians(k * 2)
        r0 = R + 18
        r1 = DIAL - (26 if k % 5 else 14)
        dd.line([(C + r0 * math.cos(a), C + r0 * math.sin(a)), (C + r1 * math.cos(a), C + r1 * math.sin(a))],
                fill=VIOLET, width=4 if k % 5 == 0 else 2)
    img = glow(img, dial, 14, 1.0)
    img = ImageChops.add(img, dial)

    # sweep wedge, brightest at the leading edge (beam at -50 deg, sweeping clockwise)
    sweep = layer()
    sd = ImageDraw.Draw(sweep)
    beam = -50
    span = 55
    for k in range(80):
        a0 = beam - span + k * span / 80
        v = (k / 80) ** 1.8
        sd.pieslice([C - R, C - R, C + R, C + R], a0, a0 + span / 80 + 0.6,
                    fill=(int(150 * v), int(80 * v), int(235 * v)))
    img = ImageChops.add(img, Image.composite(sweep, layer(), scope))

    # soft glowing blips
    blips = layer()
    bd = ImageDraw.Draw(blips)
    for x, y, r in ((0.565, 0.265, 14), (0.31, 0.45, 13), (0.28, 0.62, 13), (0.41, 0.63, 12),
                    (0.56, 0.64, 12), (0.74, 0.55, 14)):
        bd.ellipse([x * N - r, y * N - r, x * N + r, y * N + r], fill=BLIP)
    img = glow(img, blips, 20, 1.6)
    img = glow(img, blips, 6, 1.0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()
