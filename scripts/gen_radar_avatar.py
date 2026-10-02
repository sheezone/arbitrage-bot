"""Матч-Радар avatar (channel / bot photo): purple radar with a sweep and blips.
Run: python scripts/gen_radar_avatar.py -> bot/assets/radar_avatar.png (1024x1024)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

N = 1024
C = N / 2
R = N * 0.46
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "radar_avatar.png"
VIOLET = (178, 107, 255)
PINK = (255, 124, 232)


def main():
    img = Image.new("RGB", (N, N), (12, 4, 26))
    # radial background
    bg = Image.new("RGB", (N, N))
    d = ImageDraw.Draw(bg)
    for i in range(200, 0, -1):
        r = R * i / 200
        t = i / 200
        col = (int(60 - 40 * t), int(22 - 16 * t), int(112 - 80 * t))
        d.ellipse([C - r, C - r, C + r, C + r], fill=col)
    img = bg
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).ellipse([C - R, C - R, C + R, C + R], fill=255)

    grid = Image.new("RGB", (N, N))
    g = ImageDraw.Draw(grid)
    step = N / 11
    for k in range(12):
        g.line([(k * step, 0), (k * step, N)], fill=(110, 60, 190), width=3)
        g.line([(0, k * step), (N, k * step)], fill=(110, 60, 190), width=3)
    for f in (0.25, 0.5, 0.75):
        g.ellipse([C - R * f, C - R * f, C + R * f, C + R * f], outline=VIOLET, width=8)
    img = ImageChops.add(img, Image.composite(grid, Image.new("RGB", (N, N)), mask))

    # sweep: fading wedge behind the beam
    sweep = Image.new("RGB", (N, N))
    s = ImageDraw.Draw(sweep)
    beam = -40  # degrees (PIL: 0 = 3 o'clock, clockwise)
    for k in range(60):
        a0 = beam - 70 + k * (70 / 60)
        v = (k / 60) ** 2
        s.pieslice([C - R, C - R, C + R, C + R], a0, a0 + 70 / 60 + 0.5, fill=(int(200 * v), int(140 * v), int(255 * v)))
    img = ImageChops.add(img, Image.composite(sweep, Image.new("RGB", (N, N)), mask))
    bx, by = C + R * math.cos(math.radians(beam)), C + R * math.sin(math.radians(beam))
    glow = Image.new("RGB", (N, N))
    gd = ImageDraw.Draw(glow)
    gd.line([(C, C), (bx, by)], fill=(240, 220, 255), width=14)
    for x, y, r in ((0.72, 0.30, 26), (0.66, 0.70, 22), (0.28, 0.66, 26), (0.33, 0.27, 20), (0.58, 0.43, 18)):
        gd.ellipse([x * N - r, y * N - r, x * N + r, y * N + r], fill=PINK)
    img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(18)))
    img = ImageChops.add(img, glow)

    ring = Image.new("RGB", (N, N))
    ImageDraw.Draw(ring).ellipse([C - R, C - R, C + R, C + R], outline=VIOLET, width=22)
    img = ImageChops.add(img, ring.filter(ImageFilter.GaussianBlur(10)))
    img = ImageChops.add(img, ring)
    ImageDraw.Draw(img).ellipse([C - 22, C - 22, C + 22, C + 22], fill=(240, 220, 255))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()
