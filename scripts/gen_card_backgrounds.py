"""Procedural backgrounds for the Mini App home cards (bot/webapp/static/img/card-*.jpg).

Night stadium (floodlights, crowd bokeh, pitch in perspective) + one motif per card:
ai (glowing neural net), express (gold trophies and coins), picks (hologram chart over
the pitch), vilki (electric fork of diverging lines). Pure Pillow, deterministic seeds,
so re-running gives the same files. Run: python scripts/gen_card_backgrounds.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 1200, 430
OUT = Path(__file__).resolve().parent.parent / "bot" / "webapp" / "static" / "img"
LIME = (198, 244, 50)
TEAL = (47, 230, 200)


def glow_layer(draw_fn, blur: int) -> Image.Image:
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def add(base: Image.Image, layer: Image.Image, k: float = 1.0) -> Image.Image:
    if k != 1.0:
        layer = Image.eval(layer, lambda v: int(v * k))
    return ImageChops.add(base, layer)


def stadium(seed: int, tint: tuple[int, int, int], pitch_tint=(18, 70, 34)) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # night sky -> stands
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(6 + tint[0] * 0.10 * (1 - t)), int(8 + tint[1] * 0.10 * (1 - t)), int(12 + tint[2] * 0.12 * (1 - t))))
    # crowd bokeh in the stands (upper band)
    crowd = Image.new("RGB", (W, H))
    cd = ImageDraw.Draw(crowd)
    for _ in range(2600):
        x, y = rnd.uniform(0, W), rnd.uniform(H * 0.12, H * 0.55)
        r = rnd.uniform(1.0, 3.2)
        c = rnd.choice([(255, 255, 255), (255, 210, 120), tint, (120, 160, 255), (255, 120, 120)])
        a = rnd.uniform(0.15, 0.6)
        cd.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(v * a) for v in c))
    img = add(img, crowd.filter(ImageFilter.GaussianBlur(1.6)))
    # pitch in perspective with mowing stripes and lines
    pitch = Image.new("RGB", (W, H))
    pd = ImageDraw.Draw(pitch)
    top, bot = H * 0.55, H
    for i in range(14):
        y0 = top + (bot - top) * (i / 14) ** 1.2
        y1 = top + (bot - top) * ((i + 1) / 14) ** 1.2
        g = pitch_tint if i % 2 else tuple(int(v * 1.25) for v in pitch_tint)
        pd.rectangle([0, y0, W, y1], fill=g)
    pd.line([(W * 0.15, H), (W * 0.42, top)], fill=(90, 120, 90), width=2)
    pd.line([(W * 0.85, H), (W * 0.58, top)], fill=(90, 120, 90), width=2)
    pd.line([(0, top + 2), (W, top + 2)], fill=(80, 110, 80), width=2)
    pd.ellipse([W * 0.42, H * 0.78, W * 0.58, H * 0.98], outline=(80, 110, 80), width=2)
    img = Image.composite(pitch, img, Image.new("L", (W, H), 0).point(lambda _: 0))  # keep sky
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rectangle([0, top, W, H], fill=255)
    img.paste(pitch, (0, 0), mask.filter(ImageFilter.GaussianBlur(6)))
    # floodlights
    def lights(dd):
        for x in (W * 0.08, W * 0.3, W * 0.7, W * 0.92):
            dd.ellipse([x - 70, -40, x + 70, 70], fill=(255, 255, 240))
    img = add(img, glow_layer(lights, 40), 0.55)
    img = add(img, glow_layer(lambda dd: dd.ellipse([W * 0.2, -H * 0.3, W * 0.8, H * 0.45], fill=tint), 120), 0.35)
    return img


def vignette(img: Image.Image) -> Image.Image:
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([-W * 0.25, -H * 0.6, W * 1.25, H * 1.6], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(90))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    out = Image.composite(img, dark, mask)
    # darken the bottom-left where the chips sit, so their text stays readable
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line([(0, y), (W, y)], fill=int(150 * max(0, (y / H) - 0.45) / 0.55))
    return Image.composite(dark, out, grad)


def card_ai() -> Image.Image:
    img = stadium(1, TEAL)
    rnd = random.Random(11)
    cx, cy = W * 0.78, H * 0.42
    nodes = [(cx + rnd.gauss(0, 150), cy + rnd.gauss(0, 85)) for _ in range(46)]
    def net(dd):
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                if math.dist(a, b) < 120:
                    dd.line([a, b], fill=TEAL, width=2)
        for x, y in nodes:
            dd.ellipse([x - 5, y - 5, x + 5, y + 5], fill=LIME)
    img = add(img, glow_layer(net, 10), 0.9)
    img = add(img, glow_layer(net, 0), 0.8)
    img = add(img, glow_layer(lambda dd: dd.ellipse([cx - 120, cy - 110, cx + 120, cy + 110], fill=(255, 90, 160)), 70), 0.45)
    return vignette(img)


def trophy(dd: ImageDraw.ImageDraw, x: float, y: float, s: float, col):
    dd.polygon([(x - 50 * s, y - 70 * s), (x + 50 * s, y - 70 * s), (x + 30 * s, y), (x - 30 * s, y)], fill=col)
    dd.ellipse([x - 70 * s, y - 70 * s, x - 40 * s, y - 30 * s], outline=col, width=int(8 * s))
    dd.ellipse([x + 40 * s, y - 70 * s, x + 70 * s, y - 30 * s], outline=col, width=int(8 * s))
    dd.rectangle([x - 8 * s, y, x + 8 * s, y + 30 * s], fill=col)
    dd.rectangle([x - 34 * s, y + 30 * s, x + 34 * s, y + 46 * s], fill=col)


def card_express() -> Image.Image:
    img = stadium(2, (244, 192, 74), pitch_tint=(16, 52, 40))
    gold, light = (214, 160, 40), (255, 222, 120)
    def cups(dd):
        trophy(dd, W * 0.68, H * 0.52, 1.25, gold)
        trophy(dd, W * 0.82, H * 0.46, 1.6, gold)
        trophy(dd, W * 0.94, H * 0.55, 1.15, gold)
    img = add(img, glow_layer(cups, 30), 0.6)
    layer = Image.new("RGB", (W, H))
    cups(ImageDraw.Draw(layer))
    img = Image.composite(layer, img, layer.convert("L").point(lambda v: 255 if v > 10 else 0))
    hl = Image.new("RGB", (W, H))
    hd = ImageDraw.Draw(hl)
    for x, y, s in ((W * 0.68, H * 0.52, 1.25), (W * 0.82, H * 0.46, 1.6), (W * 0.94, H * 0.55, 1.15)):
        hd.polygon([(x - 40 * s, y - 66 * s), (x - 20 * s, y - 66 * s), (x - 12 * s, y - 6 * s), (x - 24 * s, y - 6 * s)], fill=light)
    img = add(img, hl.filter(ImageFilter.GaussianBlur(3)), 0.5)
    rnd = random.Random(22)
    coins = Image.new("RGB", (W, H))
    cd = ImageDraw.Draw(coins)
    for _ in range(40):
        x, y = rnd.uniform(W * 0.55, W), rnd.uniform(H * 0.72, H * 0.98)
        r = rnd.uniform(14, 26)
        cd.ellipse([x - r, y - r * 0.45, x + r, y + r * 0.45], fill=gold, outline=light, width=2)
    img = Image.composite(coins, img, coins.convert("L").point(lambda v: 255 if v > 10 else 0))
    return vignette(img)


def card_picks() -> Image.Image:
    img = stadium(3, (60, 110, 255), pitch_tint=(14, 40, 60))
    def holo(dd):
        x0, y0, x1, y1 = W * 0.55, H * 0.18, W * 0.96, H * 0.66
        for i in range(9):
            x = x0 + (x1 - x0) * i / 8
            dd.line([(x, y0), (x, y1)], fill=(60, 140, 255), width=1)
        for i in range(6):
            y = y0 + (y1 - y0) * i / 5
            dd.line([(x0, y), (x1, y)], fill=(60, 140, 255), width=1)
        pts = [(x0 + (x1 - x0) * i / 9, y1 - (y1 - y0) * v) for i, v in enumerate([0.15, 0.3, 0.22, 0.45, 0.38, 0.6, 0.55, 0.75, 0.7, 0.92])]
        dd.line(pts, fill=LIME, width=6)
        for x, y in pts:
            dd.ellipse([x - 7, y - 7, x + 7, y + 7], fill=TEAL)
    img = add(img, glow_layer(holo, 12), 0.9)
    img = add(img, glow_layer(holo, 0), 0.8)
    return vignette(img)


def card_vilki() -> Image.Image:
    img = stadium(4, LIME, pitch_tint=(26, 40, 12))
    def fork(dd):
        sx, sy = W * 0.6, H * 0.55
        dd.line([(sx - 120, sy), (sx, sy)], fill=LIME, width=8)
        for ex, ey in ((W * 0.95, H * 0.18), (W * 0.97, H * 0.85)):
            pts, x, y = [(sx, sy)], sx, sy
            rnd = random.Random(int(ex + ey))
            for i in range(1, 9):
                t = i / 8
                x = sx + (ex - sx) * t
                y = sy + (ey - sy) * t + rnd.uniform(-14, 14) * (1 - t)
                pts.append((x, y))
            dd.line(pts, fill=TEAL, width=7)
            dd.ellipse([ex - 18, ey - 18, ex + 18, ey + 18], fill=LIME)
        dd.ellipse([sx - 16, sy - 16, sx + 16, sy + 16], fill=(255, 255, 255))
    img = add(img, glow_layer(fork, 16), 1.0)
    img = add(img, glow_layer(fork, 0), 0.9)
    return vignette(img)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("ai", card_ai), ("express", card_express), ("picks", card_picks), ("vilki", card_vilki)):
        fn().save(OUT / f"card-{name}.jpg", quality=82, optimize=True, progressive=True)
        print("saved", name)


if __name__ == "__main__":
    main()
