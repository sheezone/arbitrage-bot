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
