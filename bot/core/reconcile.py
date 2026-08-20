"""Groups SourceQuotes from independent sources (OddsPapi, Fonbet, PARI, ...) into
per-match buckets so bot/core/arbitrage.py can compare bookmakers that don't share a
common fixture id.

Two problems make exact-name matching insufficient on its own:
1. Tennis: Fonbet/PARI spell player names in Cyrillic ("Джокович Н."), OddsPapi in Latin
   ("Djokovic N."). A straight transliteration table doesn't reliably land on the
   established English spelling (e.g. literal transliteration of "Джокович" is closer to
   "dzhokovich" than "djokovic"), so transliteration alone still needs fuzzy comparison
   on top of it.
2. Any source may abbreviate a team/player name differently from another.

So matching is: bucket first by (game, rounded start time) -- cheap and reliable, since
independent sources agree on kickoff time -- then, within a bucket, cluster quotes whose
transliterated+normalized team-name pair is "close enough" (SequenceMatcher ratio) to an
existing cluster, unioning across sources. This is still not fuzzy matching against every
possible name variant (no external name-alias database), so a sufficiently different
abbreviation can still fail to merge -- a known limitation, not a bug.

A match cluster can carry more than one independent 2-way market at once (e.g. football:
only the Total-goals market, see bot/providers/_line_platform.py) -- SourceQuote.market
tags which one. split_by_market() splits a cluster into per-market groups BEFORE arbitrage
math runs, so a "Total 2.5" quote from one book is never compared against a "Total 3.5"
quote from another (that would be comparing two different bets, not two prices on the same
bet, and would produce a nonsense "arbitrage"). to_arbitrage_input() then branches: for the
"winner" market it keeps the team-name remap described above; for any other market it
groups by outcome_name literally, since providers already emit a shared canonical label
for the same outcome (e.g. every source says "Тотал больше 2.5", not team names).
"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

from bot.core.arbitrage import OutcomeOdds
from bot.providers.models import SourceQuote

TIME_BUCKET_MINUTES = 30
NAME_MATCH_THRESHOLD = 0.72

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def _transliterate(text: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in text.lower())


def normalize_team(name: str) -> str:
    translit = _transliterate(name)
    normalized = re.sub(r"[^\w\s]", " ", translit, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _pair_similarity(pair1: tuple[str, str], pair2: tuple[str, str]) -> float:
    """Best of "same order" vs "swapped order" match between two (team_a, team_b) pairs."""
    a1, b1 = pair1
    a2, b2 = pair2
    straight = (_similarity(a1, a2) + _similarity(b1, b2)) / 2
    crossed = (_similarity(a1, b2) + _similarity(b1, a2)) / 2
    return max(straight, crossed)


def _time_bucket(start_time_utc: str) -> int:
    if not start_time_utc:
        return 0
    try:
        dt = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return 0
    epoch_minutes = int(dt.timestamp() // 60)
    return epoch_minutes // TIME_BUCKET_MINUTES


def group_quotes(quotes: list[SourceQuote]) -> list[list[SourceQuote]]:
    buckets: dict[tuple[str, int], list[tuple[tuple[str, str], list[SourceQuote]]]] = {}

    for q in quotes:
        bucket_key = (q.game, _time_bucket(q.start_time_utc))
        pair = (normalize_team(q.team_a), normalize_team(q.team_b))
        clusters = buckets.setdefault(bucket_key, [])

        for cluster_pair, cluster_quotes in clusters:
            if _pair_similarity(pair, cluster_pair) >= NAME_MATCH_THRESHOLD:
                cluster_quotes.append(q)
                break
        else:
            clusters.append((pair, [q]))

    return [cluster_quotes for clusters in buckets.values() for _, cluster_quotes in clusters]


def split_by_market(group: list[SourceQuote]) -> dict[str, list[SourceQuote]]:
    """Split one match cluster into per-market sub-groups so arbitrage math never mixes
    quotes from two different bets on the same match (e.g. Total 2.5 vs Total 3.5)."""
    by_market: dict[str, list[SourceQuote]] = {}
    for q in group:
        by_market.setdefault(q.market, []).append(q)
    return by_market


def to_arbitrage_input(group: list[SourceQuote]) -> tuple[str, str, dict[str, list[OutcomeOdds]]]:
    """`group` must already be single-market (see split_by_market). Pick the first quote's
    team names as canonical display labels. For the "winner" market, remap every quote's
    outcome_name onto whichever canonical label it's most similar to; for any other market
    (e.g. a football Total), group directly by the provider-supplied outcome_name, since
    providers already emit a shared canonical label per outcome for those markets."""
    canonical_a, canonical_b = group[0].team_a, group[0].team_b

    if group[0].market != "winner":
        odds_by_outcome: dict[str, list[OutcomeOdds]] = {}
        for q in group:
            odds_by_outcome.setdefault(q.outcome_name, []).append(OutcomeOdds(q.outcome_name, q.bookmaker, q.odds))
        return canonical_a, canonical_b, odds_by_outcome

    norm_a, norm_b = normalize_team(canonical_a), normalize_team(canonical_b)

    odds_by_outcome = {canonical_a: [], canonical_b: []}
    for q in group:
        norm_outcome = normalize_team(q.outcome_name)
        sim_a, sim_b = _similarity(norm_outcome, norm_a), _similarity(norm_outcome, norm_b)
        if max(sim_a, sim_b) < NAME_MATCH_THRESHOLD:
            continue  # outcome name doesn't resemble either team closely enough -- skip rather than guess
        label = canonical_a if sim_a >= sim_b else canonical_b
        odds_by_outcome[label].append(OutcomeOdds(label, q.bookmaker, q.odds))

    return canonical_a, canonical_b, odds_by_outcome
