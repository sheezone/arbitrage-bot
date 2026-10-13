"""Punchier 15s vertical promo (owner 2026-10-13: "что-то более эффектное и продажное").
Fast cuts, zoom punches, screen shake, white flash transitions, "ЗАШЛО" stamps,
a rising winnings counter, the 299 -> 149 price strike and a pulsing CTA.
Run: python scripts/gen_ad_video2.py -> bot/assets/ad_video2.mp4 (1080x1920, 30 fps)
"""
from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H, FPS, SECONDS = 1080, 1920, 30, 15
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "ad_video2.mp4"
VIOLET, LINE, PINK = (178, 107, 255), (120, 70, 210), (255, 120, 235)
WHITE, LILAC, GOLD, GREEN, RED = (250, 246, 255), (205, 185, 240), (255, 205, 90), (70, 230, 130), (255, 80, 100)
BOLD, SEMI, REG = "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeui.ttf"
_fonts: dict = {}


def F(path, size):
    key = (path, int(size))
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(path, max(8, int(size)))
    return _fonts[key]


def clamp(t):
    return max(0.0, min(1.0, t))


def ease(t):
    return 1 - (1 - clamp(t)) ** 3


def back(t):  # overshoot "pop"
    t = clamp(t)
    c = 1.9
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def background() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(20 + 12 * t), int(6 + 4 * t), int(42 + 18 * t)))
    halo = Image.new("RGB", (W, H))
    hd = ImageDraw.Draw(halo)
    hd.ellipse([-200, 300, W + 200, 1500], fill=(70, 20, 130))
    hd.ellipse([200, 1300, W + 300, 2300], fill=(90, 20, 110))
    img = ImageChops.add(img, halo.filter(ImageFilter.GaussianBlur(220)))
    grid = Image.new("RGB", (W, H))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 90):
        gd.line([(x, 0), (x, H)], fill=(38, 18, 70), width=2)
    for y in range(0, H, 90):
        gd.line([(0, y), (W, y)], fill=(38, 18, 70), width=2)
    return ImageChops.add(img, grid)


def rays(t) -> Image.Image:
    lay = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(lay)
    cx, cy = W / 2, 900
    for k in range(18):
        a = math.radians(k * 20 + t * 40)
        a2 = a + math.radians(7)
        d.polygon([(cx, cy), (cx + 2000 * math.cos(a), cy + 2000 * math.sin(a)),
                   (cx + 2000 * math.cos(a2), cy + 2000 * math.sin(a2))], fill=(55, 22, 95))
    return lay


def text_layer(text, font, fill, stroke=0, stroke_fill=(0, 0, 0)):
    b = font.getbbox(text, stroke_width=stroke)
    w, h = b[2] - b[0] + 20, b[3] - b[1] + 20
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(lay).text((10 - b[0], 10 - b[1]), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return lay


def paste_c(img, lay, cx, cy, scale=1.0, angle=0.0, alpha=1.0):
    if scale <= 0.01 or alpha <= 0.01:
        return
    if scale != 1.0:
        lay = lay.resize((max(1, int(lay.width * scale)), max(1, int(lay.height * scale))), Image.LANCZOS)
    if angle:
        lay = lay.rotate(angle, expand=True, resample=Image.BICUBIC)
    if alpha < 1:
        a = lay.split()[3].point(lambda v: int(v * alpha))
        lay.putalpha(a)
    img.paste(lay, (int(cx - lay.width / 2), int(cy - lay.height / 2)), lay)


def big(img, text, cy, size, fill, t_in, t, stroke=6, angle=0.0):
    s = back((t - t_in) / 0.35)
    paste_c(img, text_layer(text, F(BOLD, size), fill, stroke, (30, 8, 50)), W / 2, cy, s, angle)


def check_badge(img, cx, cy, r, scale, ok=True):
    if scale <= 0.01:
        return
    size = int(r * 2 * scale) + 8
    lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    rr = r * scale
    c = size / 2
    d.ellipse([c - rr, c - rr, c + rr, c + rr], fill=GREEN if ok else RED)
    w = max(3, int(rr * 0.22))
    if ok:
        d.line([(c - rr * 0.45, c + rr * 0.02), (c - rr * 0.1, c + rr * 0.38), (c + rr * 0.48, c - rr * 0.35)], fill=WHITE, width=w, joint="curve")
    else:
        d.line([(c - rr * 0.38, c - rr * 0.38), (c + rr * 0.38, c + rr * 0.38)], fill=WHITE, width=w)
        d.line([(c - rr * 0.38, c + rr * 0.38), (c + rr * 0.38, c - rr * 0.38)], fill=WHITE, width=w)
    img.paste(lay, (int(cx - size / 2), int(cy - size / 2)), lay)


def stamp(img, text, cx, cy, t_in, t, color=GREEN, angle=-12):
    p = clamp((t - t_in) / 0.25)
    if p <= 0:
        return
    s = 2.4 - 1.4 * ease(p)
    font = F(BOLD, 110)
    b = font.getbbox(text)
    w, h = b[2] - b[0] + 80, b[3] - b[1] + 60
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([6, 6, w - 6, h - 6], radius=24, outline=color, width=10)
    d.text((40 - b[0], 30 - b[1]), text, font=font, fill=color)
    paste_c(img, lay, cx, cy, s, angle, alpha=ease(p))


def ticket(img, y, title, pick, odds, t_in, t):
    p = ease((t - t_in) / 0.3)
    if p <= 0:
        return
    x = int(-W + (W + 80) * p) if p < 1 else 80
    w, h = W - 160, 170
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([0, 0, w, h], radius=30, fill=(34, 16, 62, 245), outline=(150, 90, 240), width=3)
    d.text((34, 24), title, font=F(SEMI, 44), fill=WHITE)
    d.text((34, 92), pick, font=F(REG, 40), fill=LILAC)
    ob = F(BOLD, 50).getbbox(f"@ {odds}")
    d.text((w - 150 - (ob[2] - ob[0]), 90), f"@ {odds}", font=F(BOLD, 50), fill=GOLD)
    img.paste(lay, (x, int(y)), lay)
    check_badge(img, x + w - 70, y + h / 2, 44, back((t - t_in - 0.25) / 0.3))


def frame(i, bg) -> Image.Image:
    t = i / FPS
    img = bg.copy()
    shake = 0.0

    if t < 2.0:  # HOOK: СТОП + проиграл?
        img = ImageChops.add(img, rays(t))
        big(img, "СТОП!", 760, 230, RED, 0.0, t, angle=4)
        if t > 0.7:
            big(img, "Опять слил", 1060, 110, WHITE, 0.7, t)
            big(img, "ставку?", 1190, 130, PINK, 0.95, t)
        shake = 18 * max(0, 1 - t / 0.35) + 12 * max(0, 1 - abs(t - 0.95) / 0.25)
        for k, (x, y) in enumerate(((180, 1450), (540, 1520), (900, 1450))):
            check_badge(img, x, y, 70, back((t - 1.2 - k * 0.12) / 0.3), ok=False)

    elif t < 5.0:  # AI analysis + ЗАШЛО stamp
        lt = t - 2.0
        big(img, "ИИ разбирает", 300, 100, WHITE, 0, lt)
        big(img, "матч за тебя", 420, 100, VIOLET, 0.15, lt)
        x0, y0, w = 90, 560, W - 180
        cp = ease(lt / 0.35)
        if cp > 0:
            card = Image.new("RGBA", (w, 760), (0, 0, 0, 0))
            d = ImageDraw.Draw(card)
            d.rounded_rectangle([0, 0, w, 760], radius=40, fill=(30, 14, 58, 250), outline=(160, 95, 250), width=4)
            d.text((50, 40), "Реал Мадрид — Барселона", font=F(BOLD, 54), fill=WHITE)
            for k, (lab, val) in enumerate((("Форма", "4 победы из 5"), ("Личные встречи", "3:1 за 2 сезона"),
                                             ("Травмы", "у соперника 3 игрока"), ("Линия", "рынок 52 / 23 / 25"))):
                if lt > 0.4 + k * 0.25:
                    d.text((50, 150 + k * 95), f"• {lab}:", font=F(SEMI, 42), fill=LILAC)
                    d.text((440, 150 + k * 95), val, font=F(REG, 42), fill=WHITE)
            if lt > 1.5:
                d.rounded_rectangle([40, 560, w - 40, 710], radius=30, fill=(110, 50, 210))
                d.text((80, 590), "Ставка ИИ:  П1  @ 1.85", font=F(BOLD, 60), fill=WHITE)
            card.putalpha(card.split()[3].point(lambda v: int(v * cp)))
            img.paste(card, (x0, int(y0 + 120 * (1 - cp))), card)
        stamp(img, "ЗАШЛО", W / 2, 1520, 2.1, lt)
        if 2.1 < lt < 2.4:
            shake = 16

    elif t < 8.5:  # winning tickets + counter
        lt = t - 5.0
        big(img, "Каждый день", 260, 96, WHITE, 0, lt)
        big(img, "заходят прогнозы", 380, 96, GREEN, 0.15, lt)
        # Real settled AI picks from the bot's own history (not invented wins).
        ticket(img, 560, "Брага — Спортинг 1:3", "Победа Спортинга", "2.30", 0.3, lt)
        ticket(img, 760, "Фейеноорд — АЗ 2:0", "Победа Фейеноорда", "1.75", 0.6, lt)
        ticket(img, 960, "Зенит — Автодор 91:71", "Тотал меньше 164.5", "1.70", 0.9, lt)
        ticket(img, 1160, "Falcons — Spirit 0:2", "Победа Team Spirit", "1.65", 1.2, lt)
        money = int(3400 * ease((lt - 1.5) / 1.5))
        if lt > 1.5:
            paste_c(img, text_layer(f"+{money:,} ₽".replace(",", " "), F(BOLD, 150), GOLD, 6, (40, 20, 0)),
                    W / 2, 1520, back((lt - 1.5) / 0.35))
            paste_c(img, text_layer("4 ставки по 1 000 ₽", F(SEMI, 46), LILAC), W / 2, 1650, ease((lt - 1.8) / 0.3))

    elif t < 10.5:  # 83%
        lt = t - 8.5
        img = ImageChops.add(img, rays(t))
        n = int(83 * ease(lt / 0.9))
        paste_c(img, text_layer(f"{n}%", F(BOLD, 380), GOLD, 10, (60, 25, 0)), W / 2, 850, back(lt / 0.4))
        big(img, "прогнозов ИИ заходят", 1180, 74, WHITE, 0.3, lt)
        big(img, "9 видов спорта · 24/7", 1300, 60, LILAC, 0.5, lt)
        if lt < 0.25:
            shake = 14

    else:  # OFFER + CTA
        lt = t - 10.5
        img = ImageChops.add(img, rays(t * 0.5))
        big(img, "МАТЧ РАДАР", 420, 150, WHITE, 0, lt)
        big(img, "ИИ-прогнозы в Telegram", 560, 56, LILAC, 0.15, lt)
        # 299 struck, then 149 slams in
        paste_c(img, text_layer("299 ₽", F(BOLD, 130), (150, 130, 180)), W / 2, 800, back((lt - 0.3) / 0.3))
        if lt > 0.8:
            d = ImageDraw.Draw(img)
            sp = ease((lt - 0.8) / 0.2)
            d.line([(W / 2 - 200, 810), (W / 2 - 200 + 400 * sp, 790)], fill=RED, width=14)
        if lt > 1.0:
            paste_c(img, text_layer("149 ₽", F(BOLD, 260), GOLD, 10, (60, 25, 0)), W / 2, 1020, back((lt - 1.0) / 0.3), angle=-4)
            big(img, "первая неделя", 1200, 70, WHITE, 1.1, lt)
            if 1.0 < lt < 1.25:
                shake = 18
        if lt > 1.6:
            pulse = 1 + 0.05 * math.sin((lt - 1.6) * 9)
            btn = Image.new("RGBA", (880, 170), (0, 0, 0, 0))
            bd = ImageDraw.Draw(btn)
            bd.rounded_rectangle([0, 0, 880, 170], radius=85, fill=(140, 60, 245))
            fb = F(BOLD, 66).getbbox("ЗАБРАТЬ ПРОГНОЗЫ")
            bd.text(((880 - (fb[2] - fb[0])) / 2 - fb[0], (170 - (fb[3] - fb[1])) / 2 - fb[1]), "ЗАБРАТЬ ПРОГНОЗЫ",
                    font=F(BOLD, 66), fill=WHITE)
            glowl = Image.new("RGB", (W, H))
            ImageDraw.Draw(glowl).rounded_rectangle([100, 1390, 980, 1560], radius=85, fill=(120, 40, 200))
            img = ImageChops.add(img, glowl.filter(ImageFilter.GaussianBlur(40)))
            paste_c(img, btn, W / 2, 1475, back((lt - 1.6) / 0.3) * pulse)
            big(img, "Ссылка ниже ↓", 1680, 60, GOLD, 2.0, lt)

    # white flash on every cut
    for cut in (2.0, 5.0, 8.5, 10.5):
        dt = t - cut
        if 0 <= dt < 0.18:
            img = Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.85 * (1 - dt / 0.18))
    if shake > 0:
        random.seed(i)
        img = ImageChops.offset(img, int(random.uniform(-shake, shake)), int(random.uniform(-shake, shake)))
    return img


def main():
    bg = background()
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "19", "-movflags", "+faststart", str(OUT)],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    for i in range(FPS * SECONDS):
        proc.stdin.write(frame(i, bg).tobytes())
    proc.stdin.close()
    proc.wait()
    print("saved", OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
