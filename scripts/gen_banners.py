"""One-off generator for the bot's dashboard-style banner images (bot/assets/*.png).
Not used at runtime -- run manually whenever a new banner variant is needed, then commit
the resulting PNG like any other static asset."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "bot" / "assets"

BG = (12, 15, 21)
WHITE = (237, 240, 245)
GRAY = (120, 128, 143)
MINT = (70, 211, 155)
RED = (224, 90, 90)

W, H = 1200, 600
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"


def _chevron(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    draw.line([(cx - size, cy + size * 0.6), (cx, cy - size * 0.6)], fill=color, width=6, joint="curve")
    draw.line([(cx, cy - size * 0.6), (cx + size, cy + size * 0.6)], fill=color, width=6, joint="curve")


def _centered_text(draw, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)
    return w


def make_banner(filename: str, title: str, subtitle: str, footer: str = "", accent=MINT) -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    _chevron(draw, W // 2, 195, 26, accent)

    title_font = ImageFont.truetype(FONT_BOLD, 64)
    title_w = _centered_text(draw, 260, title, title_font, WHITE)
    underline_w = max(80, title_w * 0.28)
    underline_x = (W - underline_w) / 2
    draw.line([(underline_x, 335), (underline_x + underline_w, 335)], fill=accent, width=4)

    subtitle_font = ImageFont.truetype(FONT_REGULAR, 30)
    _centered_text(draw, 365, subtitle, subtitle_font, GRAY)

    if footer:
        footer_font = ImageFont.truetype(FONT_REGULAR, 24)
        lines = footer.split("\n")
        start_y = 535 - (len(lines) - 1) * 17  # keep the block vertically centered around the old single-line spot
        for i, line in enumerate(lines):
            _centered_text(draw, start_y + i * 34, line, footer_font, GRAY)

    img.save(ASSETS / filename)
    print(f"wrote {ASSETS / filename}")


if __name__ == "__main__":
    make_banner(
        "banner.png",
        "АРБИТРАЖНЫЙ БОТ",
        "Вилки на коэффициентах в реальном времени",
        footer="CS2 · DOTA2 · LOL · VALORANT\nТЕННИС · БАСКЕТБОЛ · ФУТБОЛ · ХОККЕЙ\nБОКС · MMA · ВОЛЕЙБОЛ",
    )
    make_banner(
        "banner_referral.png",
        "ПАРТНЁРСКАЯ ПРОГРАММА",
        "Приглашайте друзей — получайте скидку на подписку",
    )
    make_banner(
        "banner_status_active.png",
        "СТАТУС: АКТИВЕН",
        "Бот присылает уведомления о новых вилках",
        accent=MINT,
    )
    make_banner(
        "banner_status_paused.png",
        "СТАТУС: НА ПАУЗЕ",
        "Уведомления временно отключены",
        accent=RED,
    )
    make_banner(
        "banner_search.png",
        "ПОИСК ВИЛОК",
        "Актуальные арбитражные ситуации прямо сейчас",
    )
    make_banner(
        "banner_subscription.png",
        "ПОДПИСКА",
        "Выберите тариф и получайте уведомления без ограничений",
    )
    make_banner(
        "banner_help.png",
        "ПОМОЩЬ",
        "Как это работает и что делать дальше",
    )
    make_banner(
        "banner_threshold.png",
        "ПОРОГ ПРИБЫЛИ",
        "Минимальный % прибыли для уведомления",
    )
    make_banner(
        "banner_stats.png",
        "СТАТИСТИКА",
        "Сколько вилок найдено и с какой прибылью",
    )
    make_banner(
        "banner_bookmakers.png",
        "МОИ БУКМЕКЕРЫ",
        "Выберите конторы, где у вас есть аккаунт",
    )
    make_banner(
        "banner_settings.png",
        "НАСТРОЙКИ ПОИСКА",
        "Банкролл, порог прибыли, период и букмекеры",
    )
