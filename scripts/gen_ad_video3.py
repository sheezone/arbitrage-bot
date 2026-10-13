"""Premium, calm 15s vertical promo (owner 2026-10-13: v2 was "немножечко по-детски").
Fintech-style: near-black background, one soft violet glow, thin light typography,
slow fades and slides, data visuals instead of stamps/explosions.
Run: python scripts/gen_ad_video3.py -> bot/assets/ad_video3.mp4 (1080x1920, 30 fps)
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H, FPS, SECONDS = 1080, 1920, 30, 15
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "ad_video3.mp4"
WHITE, GREY, DIM, VIOLET, GREEN = (245, 243, 250), (150, 145, 165), (80, 76, 95), (170, 120, 255), (90, 210, 150)
LIGHT, SEMIL, SEMI, BOLD = ("C:/Windows/Fonts/segoeuil.ttf", "C:/Windows/Fonts/segoeuisl.ttf",
                            "C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeuib.ttf")
_f: dict = {}


def F(p, s):
    if (p, s) not in _f:
        _f[(p, s)] = ImageFont.truetype(p, s)
    return _f[(p, s)]


def cl(t):
    return max(0.0, min(1.0, t))


def ease(t):  # smooth in-out
    t = cl(t)
    return t * t * (3 - 2 * t)


def fade(t, start, end, dur=0.6):
    return ease((t - start) / dur) * (1 - ease((t - (end - dur)) / dur))


def mix(c, a, bg=(8, 6, 14)):
    return tuple(int(bg[i] + (c[i] - bg[i]) * a) for i in range(3))


def background(t) -> Image.Image:
    img = Image.new("RGB", (W, H), (8, 6, 14))
    g = Image.new("RGB", (W, H))
    cx = W / 2 + 160 * math.sin(t * 0.35)
    cy = 820 + 120 * math.cos(t * 0.27)
    ImageDraw.Draw(g).ellipse([cx - 520, cy - 520, cx + 520, cy + 520], fill=(48, 22, 90))
    return ImageChops.add(img, g.filter(ImageFilter.GaussianBlur(260)))


def tc(d, y, text, font, color, a, dy=0):
    if a <= 0:
        return
    b = d.textbbox((0, 0), text, font=font)
    d.text(((W - (b[2] - b[0])) / 2 - b[0], y + dy * (1 - a)), text, font=font, fill=mix(color, a))


def tl(d, x, y, text, font, color, a):
    if a > 0:
        d.text((x, y), text, font=font, fill=mix(color, a))


def frame(i, glow_cache) -> Image.Image:
    t = i / FPS
    key = round(t * 6) / 6  # glow drifts slowly; recompute ~6x/s
    if key not in glow_cache:
        glow_cache[key] = background(key)
    img = glow_cache[key].copy()
    d = ImageDraw.Draw(img)

    # 1. Thesis (0–3.4)
    a1 = fade(t, 0, 3.4)
    tc(d, 760, "Ставки — это не удача.", F(LIGHT, 74), WHITE, ease(t / 0.8) * a1, dy=30)
    tc(d, 870, "Это данные.", F(SEMI, 96), VIOLET, ease((t - 0.9) / 0.8) * a1, dy=30)
    if a1 > 0:
        lw = 260 * ease((t - 1.6) / 0.8)
        d.line([(W / 2 - lw, 1030), (W / 2 + lw, 1030)], fill=mix(DIM, a1), width=2)

    # 2. Analysis panel (3.2–7.2)
    a2 = fade(t, 3.2, 7.2)
    if a2 > 0:
        lt = t - 3.2
        tl(d, 110, 420, "АНАЛИЗ МАТЧА", F(SEMI, 34), GREY, a2)
        tl(d, 110, 470, "Реал Мадрид — Барселона", F(SEMIL, 62), WHITE, a2)
        rows = (("Победа 1", 52), ("Ничья", 23), ("Победа 2", 25))
        for k, (lab, pct) in enumerate(rows):
            y = 640 + k * 130
            p = ease((lt - 0.5 - k * 0.2) / 1.0)
            tl(d, 110, y, lab, F(SEMIL, 40), GREY, a2)
            tl(d, W - 210, y, f"{int(pct * p)}%", F(SEMI, 40), WHITE, a2)
            d.line([(110, y + 70), (W - 110, y + 70)], fill=mix((40, 36, 55), a2), width=6)
            d.line([(110, y + 70), (110 + (W - 220) * pct / 100 * p, y + 70)], fill=mix(VIOLET if k == 0 else (120, 110, 150), a2), width=6)
        for k, txt in enumerate(("Форма последних матчей", "Личные встречи", "Составы и травмы", "Движение линии")):
            p = ease((lt - 1.4 - k * 0.25) / 0.6)
            tl(d, 110, 1080 + k * 78, "—  " + txt, F(LIGHT, 44), WHITE, a2 * p)
        p = ease((lt - 2.5) / 0.6)
        if p > 0:
            d.rounded_rectangle([110, 1440, W - 110, 1580], radius=18, outline=mix(VIOLET, a2 * p), width=2)
            tl(d, 150, 1478, "Вывод ИИ:  победа Реала  ·  1.85", F(SEMIL, 48), WHITE, a2 * p)

    # 3. Real track record (7.0–11.0)
    a3 = fade(t, 7.0, 11.0)
    if a3 > 0:
        lt = t - 7.0
        tl(d, 110, 420, "ПОСЛЕДНИЕ ПРОГНОЗЫ", F(SEMI, 34), GREY, a3)
        picks = (("Брага — Спортинг", "П2", "2.30"), ("Фейеноорд — АЗ", "П1", "1.75"),
                 ("Зенит — Автодор", "ТМ 164.5", "1.70"), ("Falcons — Spirit", "П2", "1.65"),
                 ("Баскония — Ховентут", "П1", "1.53"))
        for k, (m, pk, od) in enumerate(picks):
            p = ease((lt - 0.3 - k * 0.22) / 0.6) * a3
            y = 520 + k * 150
            d.line([(110, y + 120), (W - 110, y + 120)], fill=mix((40, 36, 55), p), width=2)
            tl(d, 110, y + 20, m, F(SEMIL, 50), WHITE, p)
            tl(d, 110, y + 76, pk, F(LIGHT, 36), GREY, p)
            tl(d, W - 330, y + 30, od, F(SEMIL, 50), WHITE, p)
            if p > 0:
                cx, cy = W - 140, y + 60
                d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=mix(GREEN, p))
        n = int(83 * ease((lt - 1.4) / 1.4))
        tc(d, 1330, f"{n}%", F(LIGHT, 220), WHITE, a3 * ease((lt - 1.4) / 0.6))
        tc(d, 1600, "прогнозов ИИ заходят", F(SEMIL, 46), GREY, a3 * ease((lt - 1.8) / 0.6))

    # 4. Offer (10.8–15)
    a4 = ease((t - 10.8) / 0.8)
    if a4 > 0:
        lt = t - 10.8
        tc(d, 640, "МАТЧ РАДАР", F(SEMI, 110), WHITE, a4, dy=30)
        tc(d, 790, "ИИ-аналитика спорта в Telegram", F(LIGHT, 48), GREY, a4)
        p = ease((lt - 0.7) / 0.8)
        tc(d, 1010, "Первая неделя", F(LIGHT, 56), WHITE, p, dy=20)
        tc(d, 1090, "149 ₽", F(SEMI, 170), VIOLET, p, dy=20)
        old = F(LIGHT, 52)
        if p > 0:
            b = d.textbbox((0, 0), "299 ₽", font=old)
            x = (W - (b[2] - b[0])) / 2
            d.text((x, 1310), "299 ₽", font=old, fill=mix(DIM, p))
            d.line([(x - 6, 1345), (x + (b[2] - b[0]) + 6, 1345)], fill=mix(DIM, p), width=3)
        q = ease((lt - 1.6) / 0.8)
        if q > 0:
            bw, bh, by = 700, 130, 1500
            bx = (W - bw) / 2
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=65, fill=mix((245, 243, 250), q))
            f = F(SEMI, 50)
            b = d.textbbox((0, 0), "Открыть в Telegram", font=f)
            d.text((bx + (bw - (b[2] - b[0])) / 2 - b[0], by + (bh - (b[3] - b[1])) / 2 - b[1]), "Открыть в Telegram",
                   font=f, fill=mix((20, 12, 34), q, bg=mix((245, 243, 250), q)))
    return img


def main():
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18", "-movflags", "+faststart", str(OUT)],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    cache: dict = {}
    for i in range(FPS * SECONDS):
        proc.stdin.write(frame(i, cache).tobytes())
    proc.stdin.close()
    proc.wait()
    print("saved", OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
