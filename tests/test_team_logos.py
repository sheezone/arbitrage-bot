import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.webapp.team_logos import get_nba_logo_url


def test_known_team_returns_espn_cdn_url():
    url = get_nba_logo_url("Houston Rockets")
    assert url == "https://a.espncdn.com/i/teamlogos/nba/500/hou.png"


def test_los_angeles_clippers_matches_both_common_spellings():
    assert get_nba_logo_url("LA Clippers") == get_nba_logo_url("Los Angeles Clippers")


def test_unknown_team_returns_none():
    assert get_nba_logo_url("Not A Real Team") is None


def test_all_thirty_teams_are_mapped():
    # 30 franchises, but LA Clippers has two accepted spellings -> 31 dict entries.
    from bot.webapp.team_logos import _ESPN_NBA_ABBR

    assert len(_ESPN_NBA_ABBR) == 31
    assert len(set(_ESPN_NBA_ABBR.values())) == 30
