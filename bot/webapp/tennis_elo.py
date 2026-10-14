"""Tennis Elo ratings from Tennis Abstract (owner 2026-10-15: tennis went 0/3 even on
clear favourites -- needed real data). Jeff Sackmann's GitHub match archive is gone
(404), but his public Elo report pages are plain HTML (no bot protection, checked
2026-10-15): overall Elo plus hard / clay / grass Elo for every active ATP/WTA player.

Elo is the strongest single predictor in tennis (better than the official ranking);
the blended overall+surface rating gives a win probability the AI analysis is anchored on.
Cached for CACHE_TTL; any failure just means no Elo block in the analysis.
"""
from __future__ import annotations

import difflib
import html
import re
import time

import httpx

URLS = {"atp": "https://tennisabstract.com/reports/atp_elo_ratings.html",
        "wta": "https://tennisabstract.com/reports/wta_elo_ratings.html"}
CACHE_TTL = 12 * 3600
MIN_NAME_SCORE = 0.72
_cache: dict[str, tuple[float, list[dict]]] = {}

_ROW = re.compile(r"<tr><td[^>]*>(\d+)</td><td><a[^>]*>([^<]+)</a></td>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>([^<]*)</td>")

# Russian -> Latin, tuned towards how Fonbet spells foreign names back.
_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
       "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
       "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
       "э": "e", "ю": "yu", "я": "ya"}


def _key(s: str) -> str:
    """Rough phonetic key so 'Музетти' ~ 'Musetti', 'Чжэн' ~ 'Zheng', 'Циньвэнь' ~ 'Qinwen'."""
    s = "".join(_TR.get(c, c) for c in s.lower())
    for a, b in (("chzh", "zh"), ("ts", "q"), ("ch", "q"), ("kh", "h"), ("ks", "x"), ("sch", "sh"),
                 ("w", "v"), ("y", "i"), ("j", "i"), ("z", "s"), ("c", "k"), ("q", "k"), ("ph", "f"),
                 ("tt", "t"), ("ss", "s"), ("ll", "l"), ("nn", "n"), ("rr", "r"), ("gu", "g")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def _num(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


def _parse(page: str) -> list[dict]:
    players = []
    for m in _ROW.finditer(page):
        cells = [html.unescape(c).strip() for c in _CELL.findall(m.group(3))]
        # age, Elo, '', hRank, hElo, cRank, cElo, gRank, gElo, '', peak, peakDate, '', tourRank, ...
        if len(cells) < 14:
            continue
        name = html.unescape(m.group(2)).replace("\xa0", " ")
        players.append({"name": name, "elo_rank": int(m.group(1)), "elo": _num(cells[1]),
                        "hard": _num(cells[4]), "clay": _num(cells[6]), "grass": _num(cells[8]),
                        "rank": cells[13], "keys": [_key(t) for t in name.split()]})
    return players


async def _players(client: httpx.AsyncClient, tour: str) -> list[dict]:
    hit = _cache.get(tour)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        r = await client.get(URLS[tour], headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        players = _parse(r.text)
    except httpx.HTTPError:
        players = []
    if players:
        _cache[tour] = (time.time(), players)
    return players or (hit[1] if hit else [])


def _find(players: list[dict], ru_name: str) -> dict | None:
    """Fonbet: 'Музетти Л' (surname + initial) or 'Чжэн Циньвэнь' (full, surname first)."""
    parts = [p for p in re.split(r"[\s\-]+", ru_name.replace(".", " ")) if p]
    if not parts:
        return None
    surname, rest = _key(parts[0]), [_key(p) for p in parts[1:]]
    best, best_score = None, 0.0
    for p in players:
        s = max((difflib.SequenceMatcher(None, surname, k).ratio() for k in p["keys"]), default=0)
        if rest and rest[0]:
            others = [k for k in p["keys"]]
            if len(rest[0]) == 1:  # initial
                s += 0.05 if any(k.startswith(rest[0]) for k in others) else -0.1
            else:
                s = (s + max(difflib.SequenceMatcher(None, rest[0], k).ratio() for k in others)) / 2
        if s > best_score:
            best, best_score = p, s
    return best if best_score >= MIN_NAME_SCORE else None


def _surface(league: str) -> str | None:
    low = league.lower()
    return "clay" if "грунт" in low else "grass" if "трав" in low else "hard" if "хард" in low else None


async def match_elo(client: httpx.AsyncClient, player_a: str, player_b: str, league: str) -> str:
    """Ready-to-paste Russian text block for the analysis prompt, or "" if either
    player isn't found."""
    tour = "wta" if league.startswith("WTA") else "atp"
    players = await _players(client, tour)
    a, b = _find(players, player_a), _find(players, player_b)
    if not a or not b:
        return ""
    surf = _surface(league)

    def rating(p: dict) -> float:
        if surf and p.get(surf):  # Tennis Abstract's own blend: half overall, half surface
            return (p["elo"] + p[surf]) / 2
        return p["elo"]

    ra, rb = rating(a), rating(b)
    pa = 1 / (1 + 10 ** ((rb - ra) / 400))
    surf_ru = {"hard": "хард", "clay": "грунт", "grass": "трава"}.get(surf or "", "")

    def line(ru: str, p: dict) -> str:
        sp = f", на покрытии {p[surf]:.0f}" if surf and p.get(surf) else ""
        return f"- {ru} ({p['name']}): Elo {p['elo']:.0f} (#{p['elo_rank']}){sp}, рейтинг {tour.upper()} {p['rank']}"

    return ("Рейтинги Elo (Tennis Abstract" + (f", покрытие {surf_ru}" if surf_ru else "") + "):\n"
            f"{line(player_a, a)}\n{line(player_b, b)}\n"
            f"Вероятность по Elo: {player_a} {pa * 100:.0f}% — {player_b} {(1 - pa) * 100:.0f}%")
