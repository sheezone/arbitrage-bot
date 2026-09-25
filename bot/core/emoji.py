"""Telegram Premium custom emoji used in bot message text.

Rendered with <tg-emoji emoji-id="...">fallback</tg-emoji> (parse_mode=HTML). Any bot can
send these since Bot API 6.5, but ONLY in message text/captions -- not on inline or reply
keyboard buttons, and not in callback-answer popups.

Every ID below was pulled from its pack with getStickerSet, and every fallback is the
exact single emoji Telegram lists for that sticker. A multi-glyph fallback makes Telegram
reject the whole message (ENTITY_TEXT_INVALID) -- that's what broke the search screen on
2026-08-28 -- so keep fallbacks to one emoji (test_monitor.py guards this).

Packs:
- Vector Icons (t.me/addemoji/vector_icons_by_fStikBot): monochrome, needs_repainting, so
  they take the text colour -- used for neutral UI glyphs.
- News Emoji (t.me/addemoji/NewsEmoji) and Topics (t.me/addemoji/Topics): colour.
- Icons (t.me/addemoji/IconsEmoji): brand logos; its emoji field is just 💸/💬 for all of
  them, so each was identified by rendering the set's thumbnails.
"""
from __future__ import annotations


def tg_emoji(pair: tuple[str, str]) -> str:
    emoji_id, fallback = pair
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


VI: dict[str, tuple[str, str]] = {
    # --- Vector Icons (monochrome) ---
    "diamond": ("5264892613630111886", "💎"),
    "swords": ("5453991094435997597", "⚔️"),
    "hourglass": ("5258113901106580375", "⌛"),
    "top": ("5246794802560774143", "🔝"),
    "coin": ("5237761614458933049", "🪙"),
    "exchange": ("5258296359907249075", "💱"),
    "warning": ("5220197908342648622", "❗"),
    "search": ("5258274739041883702", "🔍"),
    "cart": ("5258024802010026053", "🛒"),
    "case": ("5246942081284320100", "💼"),
    "lightning": ("5219943216781995020", "⚡"),
    "check": ("5219899949281453881", "✅"),
    "green": ("5339135753316222622", "🟢"),
    "fire": ("5222148368955877900", "🔥"),
    "lock": ("5393302369024882368", "🔒"),
    "bulb": ("5258466217273871977", "💡"),
    "wrench": ("5258023599419171861", "🔧"),
    "cross": ("5219805369806629055", "🚫"),
    "mute": ("5278619986238122736", "🔇"),
    "bell": ("5431374840332302296", "🔔"),
    "speaker": ("5388632425314140043", "🔈"),
    "refresh": ("5292226786229236118", "🔄"),
    # --- News Emoji (colour) ---
    "chart": ("5231200819986047254", "📊"),
    "up": ("5244837092042750681", "📈"),
    "down": ("5246762912428603768", "📉"),
    "gear": ("5341715473882955310", "⚙️"),
    "calendar": ("5413879192267805083", "🗓"),
    "mail": ("5253742260054409879", "✉️"),
    "link": ("5271604874419647061", "🔗"),
    "crown": ("5217822164362739968", "👑"),
    "pause": ("5359543311897998264", "⏸"),
    "down_arrow": ("5406745015365943482", "⬇️"),
    "warn_color": ("5447644880824181073", "⚠️"),
    "party": ("5461151367559141950", "🎉"),
    "star_color": ("5438496463044752972", "⭐️"),
    "play": ("5264919878082509254", "▶️"),
    "globe": ("5447410659077661506", "🌐"),
    "info": ("5334544901428229844", "ℹ️"),
    "cancel": ("5210952531676504517", "❌"),
    "megaphone": ("5424818078833715060", "📣"),
    # --- Topics (colour) ---
    "money_bag": ("5350452584119279096", "💰"),
    "check_color": ("5237699328843200968", "✅"),
    "football": ("5375159220280762629", "⚽️"),
    "basketball": ("5384327463629233871", "🏀"),
    "calc": ("5355127101970194557", "🧮"),
    "bank_building": ("5350548830041415279", "🏛"),
    # --- Icons (brand logos) ---
    "sbp": ("5294247005701292072", "💸"),
    "mir": ("5293982860917620045", "💸"),
    "visa": ("5294290857317386329", "💸"),
    "mastercard": ("5292085988611342379", "💸"),
    "yoomoney": ("5292167614464804192", "💸"),
    "usdt": ("5294015055992471554", "💸"),
    "telegram": ("5436302963117137450", "💬"),
}


def vi(name: str) -> str:
    return tg_emoji(VI[name])


# ---- Buttons ----
# Bot API now has `icon_custom_emoji_id` on inline and reply keyboard buttons: a custom
# emoji drawn before the button text. button_icon() turns a label like "💳 Подписка" into
# ("Подписка", <id>) so every button keeps its familiar emoji-prefixed label in the code
# and gets the premium icon automatically. Labels with no mapped emoji are left as-is.

_BUTTON_EXACT = {
    "💳 Банковская карта": "mir",
}

_BUTTON_EMOJI = {
    "⭐": "star_color", "⚡": "sbp", "💳": "cart", "💎": "usdt",
    "⏸": "pause", "▶": "play", "🔔": "bell", "🔕": "mute", "🔊": "speaker",
    "🤝": "crown", "🌐": "globe", "ℹ": "info", "🛠": "wrench",
    "✅": "check_color", "✉": "mail", "🔄": "refresh",
    "💰": "money_bag", "📊": "chart", "📅": "calendar", "🧮": "calc",
    "🏦": "bank_building", "⚙": "gear", "🔍": "search", "🔎": "search",
    "❌": "cancel", "📢": "megaphone", "👤": "case", "🚀": "top",
}


def button_icon(text: str) -> tuple[str, str | None]:
    key = _BUTTON_EXACT.get(text)
    head, sep, rest = text.partition(" ")
    if key is None and sep:
        key = _BUTTON_EMOJI.get(head.replace("\ufe0f", ""))
    if key is None or not sep:
        return text, None
    return rest, VI[key][0]


def icon_button(text: str, **kwargs):
    """InlineKeyboardButton with the label's leading emoji swapped for a premium icon."""
    from aiogram.types import InlineKeyboardButton

    label, icon = button_icon(text)
    return InlineKeyboardButton(text=label, icon_custom_emoji_id=icon, **kwargs)


def icon_reply_button(text: str):
    from aiogram.types import KeyboardButton

    label, icon = button_icon(text)
    return KeyboardButton(text=label, icon_custom_emoji_id=icon)


def button_texts(texts) -> set[str]:
    """Both forms a reply-keyboard tap can arrive as: the old emoji-prefixed label (from a
    keyboard cached before icons) and the new icon label (text without the emoji)."""
    out = set()
    for t in texts:
        out.add(t)
        out.add(button_icon(t)[0])
    return out
