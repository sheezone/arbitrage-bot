"""Country flag for a team name shown in the Mini App -- purely decorative, mirrors the
same curated club/national-team scope already used by news.POPULAR_TEAMS and
football_stats.TEAM_NAME_EN (deliberately not exhaustive: an unrecognized team -- which
covers most esports team names, e.g. "Natus Vincere", "G2 Esports" -- just shows no flag
rather than guessing at a nationality)."""
from __future__ import annotations

# Lowercase Cyrillic substring -> flag emoji, same substring-matching convention as
# news._is_popular. England has no single ISO country flag (a club plays for one of the
# UK's four nations) -- uses the England flag tag sequence, same as e.g. Telegram/iOS
# render it, not the generic Union Jack.
_ENGLAND_FLAG = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"

TEAM_FLAGS: dict[str, str] = {
    "спартак": "🇷🇺",
    "цска": "🇷🇺",
    "зенит": "🇷🇺",
    "динамо": "🇷🇺",
    "локомотив": "🇷🇺",
    "краснодар": "🇷🇺",
    "ростов": "🇷🇺",
    "рубин": "🇷🇺",
    "реал мадрид": "🇪🇸",
    "реал": "🇪🇸",
    "барселона": "🇪🇸",
    "атлетико": "🇪🇸",
    "манчестер юнайтед": _ENGLAND_FLAG,
    "манчестер сити": _ENGLAND_FLAG,
    "ливерпуль": _ENGLAND_FLAG,
    "челси": _ENGLAND_FLAG,
    "арсенал": _ENGLAND_FLAG,
    "тоттенхэм": _ENGLAND_FLAG,
    "бавария": "🇩🇪",
    "боруссия дортмунд": "🇩🇪",
    "псж": "🇫🇷",
    "ювентус": "🇮🇹",
    "милан": "🇮🇹",
    "интер": "🇮🇹",
    "наполи": "🇮🇹",
    "рома": "🇮🇹",
    "аякс": "🇳🇱",
    "порту": "🇵🇹",
    "бенфика": "🇵🇹",
    "россия": "🇷🇺",
    "бразилия": "🇧🇷",
    "аргентина": "🇦🇷",
    "франция": "🇫🇷",
    "германия": "🇩🇪",
    "испания": "🇪🇸",
    "англия": _ENGLAND_FLAG,
    "португалия": "🇵🇹",
    "италия": "🇮🇹",
}


def get_team_flag(team_name: str) -> str | None:
    """None (no flag shown) for anything not in the curated list above -- "skip rather
    than guess" convention used throughout this webapp package."""
    lowered = team_name.lower()
    for key, flag in TEAM_FLAGS.items():
        if key in lowered:
            return flag
    return None
