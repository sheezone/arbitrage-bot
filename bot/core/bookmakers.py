"""Which bookmakers are legal to surface for a Russian audience.

Only operators admitted to the single RF register (ЕРАИ) and working through the
unified accounting centre (ЦУПИС) may legally take bets from RF residents. Showing
odds from anyone else -- and especially deep-linking to them -- reads as promoting
illegal gambling (КоАП 14.1.1 / 14.3). When `licensed_bookmakers_only` is on (the
default, see bot/config.py), every quote from a bookmaker not in LICENSED_RF_BOOKMAKERS
is dropped before arbitrage evaluation, so foreign books an aggregator returns
(bet365, 1xbet.com, 22bet, Pinnacle, ...) never reach a user. Melbet IS in the register
(see below) -- verified 2026-09-11, an earlier version of this list wrongly excluded it
by confusing it with the same-named but unrelated/blocked international melbet.com.

Keys are the lowercase `SourceQuote.bookmaker` values this codebase's providers set.
Keep this list in step with the register itself -- it changes as licences are granted
or revoked.
"""
from __future__ import annotations

from bot.providers.models import SourceQuote

# ЕРАИ-registered RF bookmakers, by the key our providers tag quotes with.
# "pari" and "ligastavok" are the same legal entity / brand family; both kept because
# quotes can carry either name.
LICENSED_RF_BOOKMAKERS: frozenset[str] = frozenset({
    "fonbet",
    "pari",
    "ligastavok",
    "winline",
    "betboom",
    "marathon",
    "olimpbet",
    "leon",
    "baltbet",
    "zenit",
    "betcity",
    "tennisi",
    "astrabet",
    "1xstavka",  # the RF-licensed entity, distinct from the blocked 1xbet.com
    # ФНС license since 2012 (ООО «МЕЛОФОН»), ЦУПИС + ЕРАИ member (confirmed
    # 2026-09-11) -- the licensed brand runs on sport.melbet.ru/mel.bet, a different
    # legal entity from the blocked international melbet.com.
    # bot/providers/melbet.py already scrapes sport.melbet.ru, i.e. the licensed one.
    "melbet",
})


def is_licensed(bookmaker: str) -> bool:
    return bookmaker.strip().lower() in LICENSED_RF_BOOKMAKERS


def filter_licensed(quotes: list[SourceQuote]) -> list[SourceQuote]:
    """Drop every quote from a bookmaker not in the RF register."""
    return [q for q in quotes if is_licensed(q.bookmaker)]
