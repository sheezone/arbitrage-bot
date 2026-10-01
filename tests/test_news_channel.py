import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.news_channel import build_caption, parse_rss, parse_telegram_preview

RSS = """<?xml version="1.0"?><rss><channel><title>X</title>
<item><title>Клопп: «Критикуйте меня»</title><link>https://ex.com/1</link>
<description>&lt;p&gt;Тренер &lt;b&gt;сборной&lt;/b&gt; высказался&lt;/p&gt;</description>
<pubDate>Tue, 29 Sep 2026 12:00:00 +0300</pubDate><enclosure url="https://ex.com/1.jpg" type="image/jpeg"/></item>
<item><title>Без ссылки</title><pubDate>Tue, 29 Sep 2026 12:00:00 +0300</pubDate></item>
</channel></rss>"""

TG = """<div class="tgme_widget_message" data-post="goal/5">
<a class="tgme_widget_message_photo_wrap" style="width:1px;background-image:url('https://cdn/x.jpg')"></a>
<div class="tgme_widget_message_text">Италия разнесла Турцию<br>Подробности матча</div>
<time datetime="2026-09-29T10:00:00+00:00"></time></div>
<div class="tgme_widget_message" data-post="goal/6"><div class="tgme_widget_message_text">без фото</div>
<time datetime="2026-09-29T10:00:00+00:00"></time></div>"""


def test_parse_rss_strips_html_and_skips_items_without_link():
    items = parse_rss("Src", RSS)
    assert [(i.title, i.url, i.summary, i.image) for i in items] == [
        ("Клопп: «Критикуйте меня»", "https://ex.com/1", "Тренер сборной высказался", "https://ex.com/1.jpg")
    ]


def test_parse_telegram_preview_keeps_only_posts_with_photos():
    items = parse_telegram_preview("goal", TG)
    assert [(i.url, i.title, i.image) for i in items] == [("https://t.me/goal/5", "Италия разнесла Турцию", "https://cdn/x.jpg")]


def test_caption_escapes_and_fits_limit():
    post = {"emoji": "💥", "headline": "A < B", "paragraphs": ["x" * 900, "y" * 400], "question": "Верим?", "skip": False}
    cap = build_caption(post, "vilki365", "Lineyka111_bot")
    assert cap.startswith("💥 <b>A &lt; B</b>")
    assert "y" * 400 not in cap and "t.me/vilki365" in cap
    import re, html
    assert len(html.unescape(re.sub(r"<[^>]+>", "", cap))) <= 1024


def test_window_crosses_midnight_and_news_day_starts_at_8():
    from datetime import datetime

    from bot.core.news_channel import MSK, in_news_window, news_day_start

    def at(h, m=0):
        return datetime(2026, 10, 1, h, m, tzinfo=MSK)

    assert [in_news_window(at(h)) for h in (7, 8, 13, 23, 0)] == [False, True, True, True, True]
    assert not in_news_window(at(1)) and not in_news_window(at(1, 30))
    assert news_day_start(at(0, 30)) == datetime(2026, 9, 30, 8, tzinfo=MSK)
    assert news_day_start(at(9)) == datetime(2026, 10, 1, 8, tzinfo=MSK)


def test_branded_telegram_items_borrow_clean_rss_photo_or_are_dropped():
    from datetime import datetime, timezone

    from bot.core.news_channel import NewsItem, with_clean_photos

    now = datetime.now(timezone.utc)
    rss = NewsItem("Чемпионат", "u1", "Роналду завершил карьеру в сборной Португалии", "", now, "clean.jpg")
    tg_same = NewsItem("t.me/sportsru", "u2", "Роналду ушёл из сборной Португалии", "", now, "branded.jpg")
    tg_other = NewsItem("t.me/sportsru", "u3", "Холанд заинтересовал ПСЖ", "", now, "branded2.jpg")
    out = with_clean_photos([rss, tg_same, tg_other])
    assert [(i.url, i.image) for i in out] == [("u1", "clean.jpg"), ("u2", "clean.jpg")]
