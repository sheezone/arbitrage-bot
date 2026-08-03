import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.marathon import parse_category_page


def _event_html(event_id: str, path: str, team_a: str, team_b: str, price_a: str, price_b: str) -> str:
    return f"""
    <div class="bg coupon-row" data-event-eventid="{event_id}" data-event-path="{path}">
      <table class="coupon-row-item"><tbody><tr>
        <td class="hidden" data-mutable-id="eventJsonInfo"
            data-json='{{"teamNames": ["{team_a}", "{team_b}"]}}'></td>
        <td data-market-type="RESULT_2WAY" data-sel='{{"epr": "{price_a}"}}'></td>
        <td data-market-type="RESULT_2WAY" data-sel='{{"epr": "{price_b}"}}'></td>
      </tr></tbody></table>
    </div>
    """


def test_parse_tennis_event():
    html = _event_html("1", "Tennis/ATP/Some+Tournament", "Иванов, Пётр", "Петров, Иван", "1.80", "2.05")
    quotes = parse_category_page(html, "tennis", ["tennis"])
    assert len(quotes) == 2
    assert quotes[0].game == "tennis"
    assert quotes[0].team_a == "Иванов, Пётр"
    assert quotes[0].outcome_name == "Иванов, Пётр"
    assert quotes[0].odds == 1.80
    assert quotes[1].outcome_name == "Петров, Иван"
    assert quotes[1].odds == 2.05
    assert quotes[0].bookmaker == "marathon"


def test_parse_esports_event_detects_game_from_path():
    html = (
        _event_html("2", "e-Sports/Dota+2/Some+League", "Team A", "Team B", "1.50", "2.60")
        + _event_html("3", "e-Sports/Counter-Strike+2/Some+League", "Team C", "Team D", "1.90", "1.90")
        + _event_html("4", "e-Sports/LoL/Some+League", "Team E", "Team F", "1.20", "4.50")
    )
    quotes = parse_category_page(html, "esports", ["cs2", "dota2", "lol"])
    games = {q.game for q in quotes}
    assert games == {"dota2", "cs2", "lol"}
    assert len(quotes) == 6


def test_parse_esports_respects_wanted_games_filter():
    html = _event_html("5", "e-Sports/Dota+2/Some+League", "Team A", "Team B", "1.50", "2.60")
    quotes = parse_category_page(html, "esports", ["cs2"])  # dota2 not requested
    assert quotes == []


def test_parse_basketball_event():
    html = _event_html("7", "Basketball/Clubs.+International/EuroLeague", "Team A", "Team B", "1.65", "2.20")
    quotes = parse_category_page(html, "basketball", ["basketball"])
    assert len(quotes) == 2
    assert quotes[0].game == "basketball"
    assert quotes[0].outcome_name == "Team A"
    assert quotes[0].odds == 1.65
    assert quotes[1].outcome_name == "Team B"
    assert quotes[1].odds == 2.20


def test_parse_skips_event_missing_price_cells():
    html = """
    <div class="bg coupon-row" data-event-eventid="6" data-event-path="Tennis/Foo">
      <table><tbody><tr>
        <td class="hidden" data-mutable-id="eventJsonInfo"
            data-json='{"teamNames": ["A", "B"]}'></td>
      </tr></tbody></table>
    </div>
    """
    quotes = parse_category_page(html, "tennis", ["tennis"])
    assert quotes == []
