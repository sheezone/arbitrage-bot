"""Team logos for the Mini App -- unlike team_flags.py (a plain emoji, no external
asset), these are real images from external CDNs, so each sport's source is noted
explicitly below.

NBA: ESPN's own logo CDN (a.espncdn.com/i/teamlogos/nba/500/<abbr>.png) -- undocumented
(ESPN retired its public developer API in 2014) but stable and widely relied on by
hobby projects; every abbreviation below was confirmed live (2026-09-08, all 200 OK).
Same "unauthenticated internal endpoint found by inspection" risk profile this codebase
already accepts for Fonbet/PARI/Baltbet/Zenit/Leon/OlimpBet -- if it ever breaks or
changes shape, this is the one place to fix it.

Team names here match The Odds API's home_team/away_team fields exactly (see
bot/providers/the_odds_api.py -- the only source basketball_nba comes from), e.g.
"Houston Rockets", not an abbreviation or city-only name."""
from __future__ import annotations

_ESPN_NBA_ABBR: dict[str, str] = {
    "Atlanta Hawks": "atl",
    "Boston Celtics": "bos",
    "Brooklyn Nets": "bkn",
    "Charlotte Hornets": "cha",
    "Chicago Bulls": "chi",
    "Cleveland Cavaliers": "cle",
    "Dallas Mavericks": "dal",
    "Denver Nuggets": "den",
    "Detroit Pistons": "det",
    "Golden State Warriors": "gs",
    "Houston Rockets": "hou",
    "Indiana Pacers": "ind",
    "LA Clippers": "lac",
    "Los Angeles Clippers": "lac",
    "Los Angeles Lakers": "lal",
    "Memphis Grizzlies": "mem",
    "Miami Heat": "mia",
    "Milwaukee Bucks": "mil",
    "Minnesota Timberwolves": "min",
    "New Orleans Pelicans": "no",
    "New York Knicks": "ny",
    "Oklahoma City Thunder": "okc",
    "Orlando Magic": "orl",
    "Philadelphia 76ers": "phi",
    "Phoenix Suns": "phx",
    "Portland Trail Blazers": "por",
    "Sacramento Kings": "sac",
    "San Antonio Spurs": "sa",
    "Toronto Raptors": "tor",
    "Utah Jazz": "utah",
    "Washington Wizards": "wsh",
}


def get_nba_logo_url(team_name: str) -> str | None:
    abbr = _ESPN_NBA_ABBR.get(team_name)
    if abbr is None:
        return None
    return f"https://a.espncdn.com/i/teamlogos/nba/500/{abbr}.png"
