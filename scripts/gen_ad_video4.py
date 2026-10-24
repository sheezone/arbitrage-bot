"""20s selling promo for buying ads in other Telegram channels (owner 2026-10-24).
Vertical 1080x1920, 30 fps, with a procedurally generated soundtrack (beat + hits +
"ding" on every win + riser before the CTA). All numbers are the bot's REAL results.

Scenes: hook -> the AI analyses a match -> real wins that came in (10 000 ₽ ->) ->
hit-rate counter -> CTA.
Run on the server (needs numpy, pillow, imageio-ffmpeg):
  /root/videnv/bin/python gen_ad_video4.py  -> ad_video4.mp4
"""
from __future__ import annotations

import math
import subprocess
import wave

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS, DUR = 1080, 1920, 30, 20.0
N = int(FPS * DUR)
FONT = "Montserrat.ttf"
WHITE, GREY, VIOLET, PINK, GREEN, RED = (255, 255, 255), (170, 160, 195), (178, 107, 255), (255, 92, 225), (90, 210, 150), (255, 80, 100)
BOT = "@Lineyka111_bot"

# real wins from the bot's own statistics (popular matches, settled, verified)
WINS = [("НХЛ", "Лос-Анджелес — Бостон", "П2 Бостон", 2.75, "1:4"),
        ("Лига Европы", "Лион — Кристал Пэлас", "П1 Лион", 2.65, "3:2"),
        ("Лига чемпионов", "Ланс — Спортинг", "П2 Спортинг", 2.60, "0:2"),
        ("Лига чемпионов", "Комо — Ман Юнайтед", "П1 Комо", 2.40, "4:1")]
HIT_WINS, HIT_TOTAL = 60, 80

_fc: dict = {}


def F(size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    k = (size, weight)
    if k not in _fc:
        f = ImageFont.truetype(FONT, size)
        f.set_variation_by_axes([weight])
        _fc[k] = f
    return _fc[k]


def cl(x: float) -> float:
    return max(0.0, min(1.0, x))


def eo(x: float) -> float:  # ease-out cubic
    x = cl(x)
    return 1 - (1 - x) ** 3


def eob(x: float) -> float:  # ease-out back (overshoot)
    x = cl(x)
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def mix(c, a, bg=(10, 5, 20)):
    return tuple(int(bg[i] + (c[i] - bg[i]) * a) for i in range(3))


# ---------- background (cached glows) ----------
_bg_cache: dict = {}


def background(t: float) -> Image.Image:
    key = round(t * 10) / 10
    if key in _bg_cache:
        return _bg_cache[key].copy()
    img = Image.new("RGB", (W, H), (10, 5, 20))
    g = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(g)
    x1, y1 = W * 0.75 + 140 * math.sin(key * 0.6), 420 + 120 * math.cos(key * 0.5)
    x2, y2 = W * 0.2 + 120 * math.cos(key * 0.45), 1500 + 150 * math.sin(key * 0.4)
    d.ellipse([x1 - 520, y1 - 520, x1 + 520, y1 + 520], fill=(90, 35, 170))
    d.ellipse([x2 - 480, y2 - 480, x2 + 480, y2 + 480], fill=(150, 30, 130))
    g = g.filter(ImageFilter.GaussianBlur(200))
    img = Image.blend(img, g, 0.6)
    # subtle grid
    d = ImageDraw.Draw(img)
    for gx in range(0, W, 90):
        d.line([(gx, 0), (gx, H)], fill=(22, 14, 38), width=1)
    for gy in range(int(key * 40) % 90, H, 90):
        d.line([(0, gy), (W, gy)], fill=(22, 14, 38), width=1)
    _bg_cache[key] = img
    return img.copy()


def text_c(d, y, s, size, color, a=1.0, weight=800, x=None, anchor="mm"):
    if a <= 0:
        return
    d.text((W // 2 if x is None else x, y), s, font=F(size, weight), fill=mix(color, a), anchor=anchor)


def glow_text(img, y, s, size, color, a=1.0, weight=900):
    if a <= 0:
        return
    lay = Image.new("RGB", (W, H))
    ImageDraw.Draw(lay).text((W // 2, y), s, font=F(size, weight), fill=tuple(int(c * a) for c in color), anchor="mm")
    from PIL import ImageChops  # noqa: PLC0415
    img.paste(ImageChops.add(img, lay.filter(ImageFilter.GaussianBlur(16))))
    ImageDraw.Draw(img).text((W // 2, y), s, font=F(size, weight), fill=mix(color, a), anchor="mm")


def check(d, x, y, size, color):
    """A drawn check mark (the font has no ✓ glyph)."""
    w = max(4, size // 5)
    d.line([(x - size * 0.5, y), (x - size * 0.15, y + size * 0.38), (x + size * 0.55, y - size * 0.42)],
           fill=color, width=w, joint="curve")


def card(d, x, y, w, h, a=1.0, border=(70, 50, 110)):
    d.rounded_rectangle([x, y, x + w, y + h], radius=34, fill=mix((30, 16, 56), a), outline=mix(border, a), width=3)


# ---------- scenes ----------
def scene_hook(img, t):
    d = ImageDraw.Draw(img)
    a1 = eo(t / 0.35)
    s = 1 + 0.25 * (1 - eob(t / 0.45))
    text_c(d, 760, "Ставишь", int(120 * s), WHITE, a1)
    text_c(d, 900, "наугад?", int(150 * s), PINK, eo((t - 0.25) / 0.35), weight=900)
    if t > 1.0:
        p = eo((t - 1.0) / 0.2)
        d.line([(170, 905), (170 + (W - 340) * p, 895)], fill=RED, width=16)
    if t > 1.2:
        glow_text(img, 1180, "Хватит.", 110, VIOLET, eo((t - 1.2) / 0.25))


def scene_analysis(img, t):  # t: 0..4.7
    d = ImageDraw.Draw(img)
    a = eo(t / 0.4)
    text_c(d, 300, "ИИ разбирает матч", 78, WHITE, a)
    text_c(d, 395, "за тебя — за пару минут", 64, VIOLET, eo((t - 0.2) / 0.4), weight=700)
    y0 = 520 + int(80 * (1 - eo(t / 0.5)))
    card(d, 90, y0, W - 180, 1000, a)
    text_c(d, y0 + 80, "РПЛ · сегодня", 34, GREY, a, weight=600)
    text_c(d, y0 + 150, "Ростов — Локомотив", 60, WHITE, a)
    items = ["Форма команд", "Травмы и составы", "Судья матча", "Погода и усталость", "Линия букмекера"]
    for i, it in enumerate(items):
        p = eo((t - 3.6 - i * 0.75) / 0.3)
        if p <= 0:
            continue
        yy = y0 + 260 + i * 105
        x = 150 + int(60 * (1 - p))
        d.rounded_rectangle([x, yy, W - 150, yy + 82], radius=20, fill=mix((44, 26, 80), p))
        d.text((x + 34, yy + 41), it, font=F(40, 700), fill=mix(WHITE, p), anchor="lm")
        check(d, W - 215, yy + 41, 34, mix(GREEN, p))
    p = eob((t - 7.0) / 0.45)
    if p > 0:
        yy = y0 + 810
        d.rounded_rectangle([140, yy, W - 140, yy + 150], radius=26, fill=mix((120, 50, 220), cl(p)))
        d.text((W // 2, yy + 48), "СТАВКА ИИ", font=F(32, 700), fill=mix((230, 210, 255), cl(p)), anchor="mm")
        d.text((W // 2, yy + 104), "ИТ1 Ростов меньше 1.5 @ 1.55", font=F(44, 900), fill=mix(WHITE, cl(p)), anchor="mm")


def scene_wins(img, t):  # t: 0..5.6
    d = ImageDraw.Draw(img)
    a = eo(t / 0.35)
    text_c(d, 250, "Прогнозы ИИ,", 84, WHITE, a)
    glow_text(img, 355, "которые зашли", 90, GREEN, a)
    d = ImageDraw.Draw(img)
    for i, (lg, teams, bet, odds, score) in enumerate(WINS):
        start = 0.15 + i * 1.0
        p = eob((t - start) / 0.45)
        if p <= 0:
            continue
        y = 500 + i * 330
        x = 70 + int((1 - cl(p)) * 700) * (1 if i % 2 else -1)
        al = cl(p)
        card(d, x, y, W - 140, 290, al, border=(60, 140, 100))
        d.rounded_rectangle([x, y + 30, x + 10, y + 260], radius=5, fill=mix(GREEN, al))
        d.text((x + 50, y + 50), lg, font=F(32, 600), fill=mix(GREY, al), anchor="lm")
        d.text((x + 50, y + 112), teams, font=F(48, 800), fill=mix(WHITE, al), anchor="lm")
        d.text((x + 50, y + 172), f"{bet} @ {odds:.2f}", font=F(40, 700), fill=mix((225, 210, 250), al), anchor="lm")
        d.rounded_rectangle([x + 50, y + 210, x + 190, y + 262], radius=14, fill=mix((30, 80, 60), al))
        d.text((x + 120, y + 236), score, font=F(36, 900), fill=mix(GREEN, al), anchor="mm")
        # money counter
        q = eo((t - start - 0.25) / 0.6)
        val = int(10000 + (10000 * odds - 10000) * q)
        d.text((x + W - 190, y + 236), f"10 000 → {val:,} ₽".replace(",", " "), font=F(42, 900), fill=mix(GREEN, al), anchor="rm")
        # stamp
        sp = eob((t - start - 0.35) / 0.3)
        if sp > 0:
            st = Image.new("RGBA", (330, 104), (0, 0, 0, 0))
            sd = ImageDraw.Draw(st)
            sd.rounded_rectangle([5, 5, 325, 99], radius=20, fill=(20, 60, 45, 235), outline=GREEN + (255,), width=6)
            sd.text((140, 52), "ЗАШЛО", font=F(48, 900), fill=GREEN + (255,), anchor="mm")
            check(sd, 270, 52, 30, GREEN + (255,))
            sc = (1.8 - 0.8 * cl(sp)) * 0.72
            st = st.resize((int(330 * sc), int(104 * sc))).rotate(-8, expand=True)
            img.paste(st, (x + W - 140 - st.width - 24, y - st.height // 2 + 4), st)
            d = ImageDraw.Draw(img)


def scene_rate(img, t):  # t: 0..3.0
    d = ImageDraw.Draw(img)
    p = eo(t / 1.4)
    pct = int(round(HIT_WINS / HIT_TOTAL * 100 * p))
    r = 330
    cx, cy = W // 2, 860
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(50, 32, 80), width=34)
    d.arc([cx - r, cy - r, cx + r, cy + r], -90, -90 + 360 * (pct / 100), fill=VIOLET, width=34)
    glow_text(img, cy - 10, f"{pct}%", 210, WHITE, eo(t / 0.3))
    d = ImageDraw.Draw(img)
    text_c(d, cy + 150, f"{int(HIT_WINS * p)} из {HIT_TOTAL}", 56, GREY, eo(t / 0.3), weight=700)
    text_c(d, 1340, "прогнозов ИИ зашли", 76, WHITE, eo((t - 0.4) / 0.4))
    text_c(d, 1440, "честная статистика — прямо в приложении", 40, VIOLET, eo((t - 0.7) / 0.4), weight=600)


def scene_cta(img, t):  # t: 0..4.0
    d = ImageDraw.Draw(img)
    s = eob(t / 0.5)
    glow_text(img, 560, "МАТЧ РАДАР", int(130 * (0.8 + 0.2 * cl(s))), WHITE, cl(s))
    d = ImageDraw.Draw(img)
    text_c(d, 680, "ИИ-прогнозы на спорт в Telegram", 46, GREY, eo((t - 0.3) / 0.4), weight=600)
    p = eob((t - 0.7) / 0.45)
    if p > 0:
        card(d, 120, 820, W - 240, 220, cl(p), border=PINK)
        d.text((W // 2, 890), "Первый разбор", font=F(54, 800), fill=mix(WHITE, cl(p)), anchor="mm")
        d.text((W // 2, 975), "БЕСПЛАТНО", font=F(84, 900), fill=mix(PINK, cl(p)), anchor="mm")
    q = eo((t - 1.3) / 0.4)
    if q > 0:
        pulse = 1 + 0.04 * math.sin(t * 7)
        bw, bh = int(760 * pulse), int(150 * pulse)
        bx, by = (W - bw) // 2, 1160
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2, fill=mix((140, 60, 240), q))
        d.text((W // 2, by + bh // 2), "Открыть в Telegram ▶", font=F(58, 900), fill=mix(WHITE, q), anchor="mm")
        text_c(d, 1400, BOT, 56, VIOLET, q, weight=800)
    text_c(d, 1820, "Аналитика, а не гарантия выигрыша. 18+", 30, GREY, eo((t - 1.0) / 0.5), weight=500)


# scene bounds follow the ElevenLabs voice-over pauses (2026-10-24)
T1, T2, T3, T4 = 2.1, 10.2, 15.0, 16.9
SCENES = [(0.0, T1, scene_hook), (T1, T2, scene_analysis), (T2, T3, scene_wins),
          (T3, T4, scene_rate), (T4, 20.0, scene_cta)]
HITS = [0.05, T1, T2, T3, T4]  # impacts on scene changes


def frame(i: int) -> Image.Image:
    t = i / FPS
    img = background(t)
    for a, b, fn in SCENES:
        if a <= t < b or (fn is scene_cta and t >= a):
            lt = t - a
            fn(img, lt)
            # quick fade between scenes
            fade = min(cl(lt / 0.18), cl((b - t) / 0.18)) if fn is not scene_cta else cl(lt / 0.18)
            if fade < 1:
                img = Image.blend(Image.new("RGB", (W, H), (10, 5, 20)), img, fade)
            break
    # camera punch on impacts
    for h in HITS:
        if 0 <= t - h < 0.25:
            k = 1 + 0.04 * (1 - (t - h) / 0.25)
            z = img.resize((int(W * k), int(H * k)))
            img = z.crop(((z.width - W) // 2, (z.height - H) // 2, (z.width - W) // 2 + W, (z.height - H) // 2 + H))
    return img


# ---------- soundtrack ----------
SR = 44100


def soundtrack(path: str) -> None:
    n = int(SR * DUR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    bpm = 124
    beat = 60 / bpm

    def add(sig, start):
        s = int(start * SR)
        e = min(n, s + len(sig))
        if s < n:
            out[s:e] += sig[: e - s]

    def env(length, attack=0.002, decay=0.2):
        tt = np.arange(int(length * SR)) / SR
        return np.minimum(tt / attack, 1) * np.exp(-tt / decay)

    kick_t = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * (45 + 120 * np.exp(-kick_t * 30)) * kick_t) * np.exp(-kick_t * 9) * 0.9
    rng = np.random.default_rng(7)
    hat = rng.uniform(-1, 1, int(0.05 * SR)) * env(0.05, 0.001, 0.012) * 0.18
    clap = rng.uniform(-1, 1, int(0.2 * SR)) * env(0.2, 0.001, 0.06) * 0.35
    start_beat = T1  # full beat drops after the hook
    k = 0
    while k * beat < DUR - 0.3:
        tb = k * beat
        if tb >= start_beat - 0.01 or k % 2 == 0:
            add(kick, tb)
        if tb >= start_beat:
            add(hat, tb + beat / 2)
            if k % 2 == 1:
                add(clap, tb)
        k += 1
    # bass line (sidechained feel)
    notes = [55.0, 55.0, 65.41, 49.0]
    for k in range(int(DUR / beat)):
        tb = k * beat
        if tb < start_beat:
            continue
        f = notes[(k // 4) % 4]
        ln = beat * 0.9
        tt = np.arange(int(ln * SR)) / SR
        sig = np.sign(np.sin(2 * np.pi * f * tt)) * 0.12 * (1 - np.exp(-tt * 40)) * np.exp(-tt * 2.5)
        add(sig, tb + 0.03)
    # pad chords
    for (a, b, freqs) in [(T1, T2, (220, 277.2, 329.6)), (T2, T3, (246.9, 311.1, 370)),
                          (T3, T4, (196, 246.9, 293.7)), (T4, 20.0, (220, 277.2, 329.6, 440))]:
        tt = np.arange(int((b - a) * SR)) / SR
        sig = sum(np.sin(2 * np.pi * fr * tt) + 0.3 * np.sin(2 * np.pi * fr * 2.01 * tt) for fr in freqs)
        sig *= 0.03 * np.minimum(tt / 0.3, 1) * np.minimum((b - a - tt) / 0.3, 1)
        add(sig, a)
    # impacts
    for h in HITS:
        tt = np.arange(int(0.8 * SR)) / SR
        boom = np.sin(2 * np.pi * (38 + 60 * np.exp(-tt * 12)) * tt) * np.exp(-tt * 4) * 0.8
        noise = rng.uniform(-1, 1, len(tt)) * np.exp(-tt * 14) * 0.25
        add(boom + noise, h)
    # "ding" + cash on each win stamp
    for i in range(len(WINS)):
        st = T2 + 0.15 + i * 1.0 + 0.35
        tt = np.arange(int(0.6 * SR)) / SR
        ding = (np.sin(2 * np.pi * 1318.5 * tt) + 0.6 * np.sin(2 * np.pi * 1975.5 * tt)) * np.exp(-tt * 6) * 0.22
        add(ding, st)
    # counter ticks during the hit-rate scene
    for j in range(16):
        tt = np.arange(int(0.03 * SR)) / SR
        add(np.sin(2 * np.pi * 2400 * tt) * np.exp(-tt * 120) * 0.15, T3 + j * 0.085)
    # riser into the CTA
    a, b = T4 - 1.6, T4
    tt = np.arange(int((b - a) * SR)) / SR
    riser = rng.uniform(-1, 1, len(tt)) * (tt / (b - a)) ** 2 * 0.25
    riser += np.sin(2 * np.pi * (200 + 900 * (tt / (b - a)) ** 2) * tt) * (tt / (b - a)) ** 2 * 0.12
    add(riser, a)
    out *= np.minimum(1, (DUR - t) / 0.6)  # fade-out
    out = np.tanh(out * 1.4) * 0.85
    pcm = (out * 32767).astype(np.int16)
    stereo = np.stack([pcm, pcm], axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def main() -> None:
    soundtrack("ad4_audio.wav")
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", "ad4_audio.wav", "-i", "voice.mp3",
         # ElevenLabs voice-over on top, music ducked under it
         "-filter_complex", "[1:a]volume=0.32[m];[2:a]volume=1.5,apad[v];[m][v]amix=inputs=2:duration=first:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", "ad_video4_voice.mp4"],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    for i in range(N):
        proc.stdin.write(frame(i).tobytes())
    proc.stdin.close()
    proc.wait()
    print("done")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:  # preview stills: python gen_ad_video4.py 1.5 5 10 14 18
        for s in sys.argv[1:]:
            frame(int(float(s) * FPS)).save(f"still_{s}.png")
    else:
        main()
