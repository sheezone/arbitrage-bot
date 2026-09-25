"""Flag emoji for NATIONAL teams only ("Италия", "Бразилия U21", "Россия (ж)") -- never
for clubs. Used in vilka notifications, the search screen and the Mini App.

Matching is exact on the country name after stripping age/gender suffixes, so a club
that merely contains a country word ("Динамо Минск", "Реал Бразилиа") never gets a flag.
Plain Unicode regional-indicator flags, so they work everywhere (unlike custom emoji).
"""
from __future__ import annotations

import re

_TAG_ENGLAND = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
_TAG_SCOTLAND = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
_TAG_WALES = "\U0001F3F4\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F"

_COUNTRY_ISO: dict[str, str] = {
    "россия": "RU", "украина": "UA", "беларусь": "BY", "белоруссия": "BY", "казахстан": "KZ",
    "узбекистан": "UZ", "таджикистан": "TJ", "кыргызстан": "KG", "киргизия": "KG",
    "туркменистан": "TM", "азербайджан": "AZ", "армения": "AM", "грузия": "GE",
    "молдова": "MD", "молдавия": "MD", "латвия": "LV", "литва": "LT", "эстония": "EE",
    "северная ирландия": "GB", "великобритания": "GB", "ирландия": "IE", "франция": "FR",
    "германия": "DE", "испания": "ES", "португалия": "PT", "италия": "IT", "нидерланды": "NL",
    "голландия": "NL", "бельгия": "BE", "швейцария": "CH", "австрия": "AT", "польша": "PL",
    "чехия": "CZ", "словакия": "SK", "венгрия": "HU", "румыния": "RO", "болгария": "BG",
    "сербия": "RS", "хорватия": "HR", "словения": "SI", "босния и герцеговина": "BA",
    "черногория": "ME", "северная македония": "MK", "македония": "MK", "албания": "AL",
    "греция": "GR", "турция": "TR", "кипр": "CY", "мальта": "MT", "дания": "DK",
    "швеция": "SE", "норвегия": "NO", "финляндия": "FI", "исландия": "IS",
    "фарерские острова": "FO", "люксембург": "LU", "лихтенштейн": "LI", "андорра": "AD",
    "сан-марино": "SM", "гибралтар": "GI", "косово": "XK", "израиль": "IL", "сша": "US",
    "канада": "CA", "мексика": "MX", "бразилия": "BR", "аргентина": "AR", "уругвай": "UY",
    "парагвай": "PY", "чили": "CL", "колумбия": "CO", "эквадор": "EC", "перу": "PE",
    "боливия": "BO", "венесуэла": "VE", "коста-рика": "CR", "панама": "PA", "гондурас": "HN",
    "ямайка": "JM", "япония": "JP", "южная корея": "KR", "корея": "KR", "кндр": "KP",
    "китай": "CN", "австралия": "AU", "новая зеландия": "NZ", "иран": "IR",
    "саудовская аравия": "SA", "катар": "QA", "оаэ": "AE", "ирак": "IQ", "иордания": "JO",
    "египет": "EG", "марокко": "MA", "тунис": "TN", "алжир": "DZ", "нигерия": "NG",
    "гана": "GH", "сенегал": "SN", "камерун": "CM", "кот-д'ивуар": "CI", "юар": "ZA",
    "мали": "ML", "индия": "IN", "таиланд": "TH", "вьетнам": "VN", "индонезия": "ID",
    "филиппины": "PH", "малайзия": "MY", "сингапур": "SG", "кувейт": "KW", "бахрейн": "BH",
    "оман": "OM", "ливан": "LB", "сирия": "SY", "палестина": "PS", "монголия": "MN",
}
_SPECIAL = {"англия": _TAG_ENGLAND, "шотландия": _TAG_SCOTLAND, "уэльс": _TAG_WALES}

# Trailing age/gender/squad markers bookmakers append to national-team names.
_SUFFIX = re.compile(
    r"\s*(\([^)]*\)|u\s?\d{2}|до\s*\d{2}|жен(щины|ская)?|ж|мол(одежная|одёжная)?|олимп(ийская)?)\s*$",
    re.IGNORECASE,
)


def _iso_flag(iso: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso.upper())


def national_flag(team: str) -> str | None:
    name = (team or "").strip().lower().replace("ё", "е")
    prev = None
    while prev != name:
        prev, name = name, _SUFFIX.sub("", name).strip()
    if name in _SPECIAL:
        return _SPECIAL[name]
    iso = _COUNTRY_ISO.get(name)
    return _iso_flag(iso) if iso else None


def with_flag(team: str) -> str:
    """Team name with its national flag prepended, or unchanged for clubs/unknowns."""
    flag = national_flag(team)
    return f"{flag} {team}" if flag else team
