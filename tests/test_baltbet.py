import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.baltbet import parse_widget_html


def _row_html(event_id: str, href: str, title: str, cells: str, is_live: str = "false") -> str:
    return f"""
    <div class="events-table__body-row m-widget " data-id="{event_id}" data-title="{title}"
         data-islive="{is_live}" data-sport="1">
        <div class="events-table__left">
            <a href="{href}" class="events-table__title-wrapper m-hot">{title}</a>
        </div>
        <div class="events-table__center m-super-market">
            {cells}
        </div>
    </div>
    """


def _cell(label: str, value: str) -> str:
    return f'<div class="events-table__markets-cell m-market-0 m-edge" data-title="{label}" data-coefvalue="{value}"></div>'


def test_parse_tennis_event():
    html = _row_html(
        "1", "/tennis/world-tennis-men/tianjin/lu-hanlei-cho-seung-woo-id-1", "Лу Ханьлей — Чо Сын У",
        _cell("Исход (1)", "1.80") + _cell("Исход (2)", "2.05"),
    )
    quotes = parse_widget_html(html, "tennis", ["tennis"])
    assert len(quotes) == 2
    assert quotes[0].game == "tennis"
    assert quotes[0].team_a == "Лу Ханьлей"
    assert quotes[0].team_b == "Чо Сын У"
    assert quotes[0].outcome_name == "Лу Ханьлей"
    assert quotes[0].odds == 1.80
    assert quotes[1].outcome_name == "Чо Сын У"
    assert quotes[1].odds == 2.05
    assert quotes[0].bookmaker == "baltbet"


def test_parse_cybersport_detects_game_from_url_segment():
    html = (
        _row_html("2", "/cybersport/dota-2/europe/1w-essence/a-b-id-2", "Team A — Team B",
                  _cell("Исход (1)", "1.50") + _cell("Исход (2)", "2.60"))
        + _row_html("3", "/cybersport/counter-strike/europe/some-league/c-d-id-3", "Team C — Team D",
                    _cell("Исход (1)", "1.90") + _cell("Исход (2)", "1.90"))
        + _row_html("4", "/cybersport/valorant/china/champions-tour/e-f-id-4", "Team E — Team F",
                    _cell("Исход (1)", "1.20") + _cell("Исход (2)", "4.50"))
    )
    quotes = parse_widget_html(html, "cybersport", ["cs2", "dota2", "valorant"])
    games = {q.game for q in quotes}
    assert games == {"dota2", "cs2", "valorant"}
    assert len(quotes) == 6


def test_parse_respects_wanted_games_filter():
    html = _row_html("5", "/cybersport/dota-2/europe/1w-essence/a-b-id-5", "Team A — Team B",
                      _cell("Исход (1)", "1.50") + _cell("Исход (2)", "2.60"))
    quotes = parse_widget_html(html, "cybersport", ["cs2"])  # dota2 not requested
    assert quotes == []


def test_parse_skips_live_events():
    html = _row_html("6", "/tennis/foo/bar-id-6", "A — B",
                      _cell("Исход (1)", "1.50") + _cell("Исход (2)", "2.60"), is_live="true")
    quotes = parse_widget_html(html, "tennis", ["tennis"])
    assert quotes == []


def test_parse_skips_three_way_market():
    html = _row_html("7", "/cybersport/dota-2/europe/foo/a-b-id-7", "A — B",
                      _cell("Исход (1)", "2.0") + _cell("Исход (X)", "3.5") + _cell("Исход (2)", "2.1"))
    quotes = parse_widget_html(html, "cybersport", ["dota2"])
    assert quotes == []
