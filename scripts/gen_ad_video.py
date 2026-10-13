"""15-second vertical promo video for ads (owner 2026-10-13: "короткий видос для рекламы").
1080x1920, 30 fps, H.264 -- the format Telegram/Stories/Reels all accept.
Scenes: hook -> AI analysis card -> accuracy counter -> sports -> logo + price + CTA.
Run: python scripts/gen_ad_video.py -> bot/assets/ad_video.mp4
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H, FPS, SECONDS = 1080, 1920, 30, 15
OUT = Path(__file__).resolve().parent.parent / "bot" / "assets" / "ad_video.mp4"
VIOLET, LINE, BLIP = (178, 107, 255), (120, 70, 210), (255, 140, 240)
WHITE, LILAC, GOLD = (250, 246, 255), (205, 185, 240), (255, 205, 110)
BOLD, SEMI, REG = "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeui.ttf"
F = lambda path, size: ImageFont.truetype(path, size)  # noqa: E731

CX, CY, R = W // 2, 640, 380
DIAL = int(R * 1.1)


def glow(base, lay, blur, k=1.0):
    g = lay.filter(ImageFilter.GaussianBlur(blur))
    if k != 1:
        g = Image.eval(g, lambda v: min(255, int(v * k)))
    return ImageChops.add(base, g)


def build_background() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(18 + 10 * t), int(7 + 4 * t), int(38 + 16 * t)))
    halo = Image.new("RGB", (W, H))
    ImageDraw.Draw(halo).ellipse([CX - DIAL * 1.5, CY - DIAL * 1.5, CX + DIAL * 1.5, CY + DIAL * 1.5], fill=(60, 20, 110))
    img = ImageChops.add(img, halo.filter(ImageFilter.GaussianBlur(160)))
    lines = Image.new("RGB", (W, H))
    ld = ImageDraw.Draw(lines)
    for f in (0.25, 0.5, 0.75, 1.0):
        r = R * f
        ld.ellipse([CX - r, CY - r, CX + r, CY + r], outline=LINE, width=3)
    ld.line([(CX - R, CY), (CX + R, CY)], fill=LINE, width=3)
    ld.line([(CX, CY - R), (CX, CY + R)], fill=LINE, width=3)
    ld.ellipse([CX - DIAL, CY - DIAL, CX + DIAL, CY + DIAL], outline=VIOLET, width=5)
    for k in range(120):
        a = math.radians(k * 3)
        r0, r1 = R + 10, DIAL - (16 if k % 5 else 6)
        ld.line([(CX + r0 * math.cos(a), CY + r0 * math.sin(a)), (CX + r1 * math.cos(a), CY + r1 * math.sin(a))],
                fill=VIOLET, width=3 if k % 5 == 0 else 2)
    img = glow(img, lines, 10, 1.0)
    return ImageChops.add(img, lines)


def build_sweep() -> Image.Image:
    sweep = Image.new("RGB", (W, H))
    sd = ImageDraw.Draw(sweep)
    span = 70
    for k in range(70):
        a0 = -span + k * span / 70
        v = (k / 70) ** 2
        sd.pieslice([CX - R, CY - R, CX + R, CY + R], a0, a0 + span / 70 + 0.6,
                    fill=(int(160 * v), int(80 * v), int(240 * v)))
    sd.line([(CX, CY), (CX + R, CY)], fill=(235, 190, 255), width=4)
    return sweep


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def text_c(d, y, text, font, fill, alpha=1.0, base=(20, 8, 40)):
    if alpha <= 0:
        return
    col = tuple(int(base[i] + (fill[i] - base[i]) * alpha) for i in range(3))
    b = d.textbbox((0, 0), text, font=font)
    d.text(((W - (b[2] - b[0])) / 2 - b[0], y), text, font=font, fill=col)


def pill(d, x, y, text, font, alpha=1.0):
    b = d.textbbox((0, 0), text, font=font)
    w, h = b[2] - b[0], b[3] - b[1]
    fill = tuple(int(v * alpha) for v in (40, 20, 75))
    d.rounded_rectangle([x, y, x + w + 44, y + h + 30], radius=(h + 30) // 2, fill=fill,
                        outline=tuple(int(v * alpha) for v in (150, 90, 240)), width=3)
    d.text((x + 22 - b[0], y + 13 - b[1]), text, font=font, fill=tuple(int(v * alpha) for v in WHITE))
    return w + 44


def frame(i, bg, sweep) -> Image.Image:
    t = i / FPS
    img = ImageChops.add(bg, sweep.rotate(-(t * 120) % 360, center=(CX, CY)))
    d = ImageDraw.Draw(img)
    # blips pulse
    for k, (fx, fy) in enumerate(((0.3, -0.45), (-0.35, -0.1), (-0.25, 0.42), (0.55, 0.12), (0.15, 0.3))):
        pulse = 0.5 + 0.5 * math.sin(t * 4 + k)
        r = 9 + 5 * pulse
        x, y = CX + fx * R, CY + fy * R
        d.ellipse([x - r, y - r, x + r, y + r], fill=(int(255 * (0.6 + 0.4 * pulse)), int(140 * (0.6 + 0.4 * pulse)), 240))

    ty = 1130
    if t < 3:  # hook
        a = ease(t / 0.6) * (1 - ease((t - 2.6) / 0.4))
        text_c(d, ty, "Ставишь на матчи", F(BOLD, 88), WHITE, a)
        text_c(d, ty + 110, "наугад?", F(BOLD, 110), VIOLET, a)
        text_c(d, ty + 280, "Хватит гадать 50/50", F(REG, 50), LILAC, a)
    elif t < 7:  # AI analysis card
        lt = t - 3
        a = ease(lt / 0.5) * (1 - ease((lt - 3.6) / 0.4))
        text_c(d, ty - 20, "ИИ разберёт матч", F(BOLD, 80), WHITE, a)
        text_c(d, ty + 80, "за секунды", F(BOLD, 80), VIOLET, a)
        x0, y0, w = 110, ty + 230, W - 220
        d.rounded_rectangle([x0, y0, x0 + w, y0 + 400], radius=36, fill=tuple(int(v * a) for v in (30, 16, 56)),
                            outline=tuple(int(v * a) for v in (150, 90, 240)), width=3)
        text_c(d, y0 + 30, "Реал Мадрид — Барселона", F(SEMI, 50), WHITE, a)
        for k, (name, pct, col) in enumerate((("П1", 52, VIOLET), ("Ничья", 23, LILAC), ("П2", 25, BLIP))):
            yy = y0 + 125 + k * 85
            fill_w = (w - 300) * pct / 100 * ease((lt - 0.5 - k * 0.25) / 0.8)
            d.text((x0 + 40, yy), name, font=F(SEMI, 42), fill=tuple(int(v * a) for v in WHITE))
            d.rounded_rectangle([x0 + 190, yy + 8, x0 + 190 + w - 300, yy + 42], radius=17, fill=tuple(int(v * a) for v in (50, 30, 85)))
            if fill_w > 4:
                d.rounded_rectangle([x0 + 190, yy + 8, x0 + 190 + fill_w, yy + 42], radius=17, fill=tuple(int(v * a) for v in col))
            d.text((x0 + w - 95, yy), f"{int(pct * ease((lt - 0.5 - k * 0.25) / 0.8))}%", font=F(BOLD, 42),
                   fill=tuple(int(v * a) for v in WHITE))
    elif t < 10:  # accuracy counter
        lt = t - 7
        a = ease(lt / 0.4) * (1 - ease((lt - 2.6) / 0.4))
        n = int(83 * ease(lt / 1.6))
        text_c(d, ty + 20, f"{n}%", F(BOLD, 260), GOLD, a)
        text_c(d, ty + 330, "точность прогнозов ИИ", F(SEMI, 58), WHITE, a)
        text_c(d, ty + 420, "форма · личные встречи · новости · линия", F(REG, 40), LILAC, a)
    elif t < 12.5:  # sports
        lt = t - 10
        a = 1 - ease((lt - 2.1) / 0.4)
        text_c(d, ty - 10, "9 видов спорта", F(BOLD, 90), WHITE, a * ease(lt / 0.4))
        sports = [("Футбол", "Хоккей", "Баскетбол"), ("Теннис", "Киберспорт", "Бокс"), ("MMA", "Волейбол", "Наст. теннис")]
        font = F(SEMI, 46)
        for row_i, row in enumerate(sports):
            widths = [d.textbbox((0, 0), s, font=font)[2] + 44 for s in row]
            x = (W - (sum(widths) + 24 * (len(row) - 1))) / 2
            for col_i, s in enumerate(row):
                k = row_i * 3 + col_i
                pa = a * ease((lt - 0.2 - k * 0.12) / 0.3)
                if pa > 0:
                    x += pill(d, x, ty + 150 + row_i * 115, s, font, pa) + 24
                else:
                    x += widths[col_i] + 24
    else:  # logo + price + CTA
        lt = t - 12.5
        a = ease(lt / 0.5)
        text_c(d, ty - 30, "МАТЧ РАДАР", F(BOLD, 120), WHITE, a)
        text_c(d, ty + 120, "ИИ-аналитика спорта в Telegram", F(SEMI, 50), LILAC, a)
        pa = ease((lt - 0.4) / 0.5)
        if pa > 0:
            bw, bh, by = 900, 150, ty + 250
            bx = (W - bw) / 2
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=75, fill=tuple(int(v * pa) for v in (139, 63, 240)))
            text_c(d, by + 34, "Первая неделя — 149 ₽", F(BOLD, 62), WHITE, pa, base=(0, 0, 0))
        text_c(d, ty + 470, "Жми на ссылку ↓", F(SEMI, 50), GOLD, ease((lt - 0.9) / 0.4))
    return img


def main():
    bg, sweep = build_background(), build_sweep()
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(OUT)],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    for i in range(FPS * SECONDS):
        proc.stdin.write(frame(i, bg, sweep).tobytes())
    proc.stdin.close()
    proc.wait()
    print("saved", OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
