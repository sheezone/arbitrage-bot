"""«Прогнозы ИИ, которые зашли» -- the bot's own verified wins as advertising (owner
2026-10-24). Only real AI picks from ai_analyses that actually came in, with the real
score; a friend's coupons are NOT used (none of them matched a bot forecast).

- recent_wins(): data for the Mini App (first-launch screen + home carousel);
- wins_card_png(): a 1080x1350 picture for the bot's /start message (cached per hour).
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timedelta, timezone

from bot.db.repository import Repository

SPORT_TAG = {"football": "⚽", "hockey": "🏒", "basketball": "🏀", "tennis": "🎾", "esports": "🎮"}
LOOKBACK_DAYS = 14


def recent_wins(repo: Repository, n: int = 6, min_odds: float = 1.4) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = repo._conn.execute(
        "SELECT match_id, sport, league, team_a, team_b, option_label, odds, score, start_utc FROM ai_analyses "
        "WHERE popular = 1 AND result = 'win' AND odds >= ? AND start_utc >= ? ORDER BY odds DESC LIMIT ?",
        (min_odds, since, n)).fetchall()
    msk = timezone(timedelta(hours=3))
    return [{"sport": r["sport"], "league": (r["league"] or "").split(".")[0], "team_a": r["team_a"],
             "team_b": r["team_b"], "label": r["option_label"], "odds": r["odds"], "score": r["score"],
             "date": datetime.fromisoformat(r["start_utc"]).astimezone(msk).strftime("%d.%m")} for r in rows]


_card_cache: dict[str, object] = {"at": 0.0, "png": None}
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def wins_card_png(repo: Repository) -> bytes | None:
    """Picture for the bot's start message: 4 real wins as coupon rows + the hit rate."""
    if time.time() - float(_card_cache["at"]) < 3600 and _card_cache["png"]:
        return _card_cache["png"]  # type: ignore[return-value]
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        return None
    wins = recent_wins(repo, 4)
    if len(wins) < 2:
        return None
    wins_n, total = repo.ai_hit_rate()
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), (11, 6, 20))
    glow = Image.new("RGB", (W, H))
    ImageDraw.Draw(glow).ellipse([W - 620, -360, W + 260, 420], fill=(96, 40, 170))
    ImageDraw.Draw(glow).ellipse([-300, H - 420, 420, H + 260], fill=(120, 30, 120))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 0.55)
    d = ImageDraw.Draw(img)
    F = lambda p, s: ImageFont.truetype(p, s)  # noqa: E731
    d.text((70, 70), "МАТЧ РАДАР", font=F(FONT_BOLD, 40), fill=(178, 107, 255))
    d.text((70, 130), "Прогнозы ИИ,", font=F(FONT_BOLD, 76), fill=(255, 255, 255))
    d.text((70, 215), "которые зашли", font=F(FONT_BOLD, 76), fill=(255, 92, 225))
    y = 350
    for w in wins:
        d.rounded_rectangle([60, y, W - 60, y + 205], radius=28, fill=(30, 16, 54), outline=(60, 44, 90), width=2)
        d.rounded_rectangle([60, y + 24, 68, y + 181], radius=4, fill=(90, 210, 150))
        d.text((100, y + 22), f"{w['league']} · {w['date']}", font=F(FONT, 26), fill=(168, 152, 200))
        d.text((W - 100, y + 22), "✓ ЗАШЛО", font=F(FONT_BOLD, 26), fill=(90, 210, 150), anchor="ra")
        teams = f"{w['team_a']} — {w['team_b']}"
        size = 40 if len(teams) <= 30 else 32
        d.text((100, y + 62), teams, font=F(FONT_BOLD, size), fill=(255, 255, 255))
        d.text((100, y + 118), f"Прогноз ИИ: {w['label']} @ {w['odds']:.2f}", font=F(FONT, 30), fill=(230, 219, 247))
        d.rounded_rectangle([100, y + 158, 230, y + 194], radius=10, fill=(30, 70, 55))
        d.text((165, y + 176), w["score"] or "", font=F(FONT_BOLD, 26), fill=(90, 210, 150), anchor="mm")
        d.text((W - 100, y + 176), f"1 000 ₽ → {round(1000 * w['odds']):,} ₽".replace(",", " "),
               font=F(FONT_BOLD, 30), fill=(90, 210, 150), anchor="rm")
        y += 225
    if total:
        d.rounded_rectangle([60, y + 10, W - 60, y + 110], radius=26, fill=(70, 32, 120))
        d.text((W // 2, y + 60), f"Всего ИИ угадал {wins_n} из {total} — {round(wins_n / total * 100)}%",
               font=F(FONT_BOLD, 38), fill=(255, 255, 255), anchor="mm")
    d.text((W // 2, H - 50), "Реальные прогнозы ИИ из бота. Результат не гарантирован. 18+",
           font=F(FONT, 24), fill=(143, 127, 176), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    _card_cache.update(at=time.time(), png=buf.getvalue())
    return buf.getvalue()
