import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.winline import parse_snapshot


def _s(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def _menu(tips):
    out = struct.pack("<I", 0)  # no sports
    out += struct.pack("<I", len(tips))
    for tip_id, src in tips:
        out += struct.pack("<I", tip_id) + _s("1") + struct.pack("<III", 0, 2, src) + _s("") + _s("") * 30
    return out


def _country(cid, name):
    return bytes([1]) + struct.pack("<II", cid, 0) + _s(name) + struct.pack("<IBHHB", 0, 0, 0, 0, 0)


def _champ(cid, sport, country, name):
    return bytes([2]) + struct.pack("<III", cid, sport, country) + _s(name) + struct.pack("<iIIBII", 0, 0, 0, 0, 0, 0)


def _event(eid, date, a, b):
    return (bytes([3]) + struct.pack("<III", eid, 0, 0) + bytes(6) + struct.pack("<H", 2) + b"\x01\x00"
            + struct.pack("<IBB", date, 0, 0) + _s(a) + _s(b))


def _line(lid, tip, values, margin=10000, favorite=None, koef=None):
    out = bytes([4]) + struct.pack("<IHHH", lid, tip, 0, margin)
    if favorite is not None:
        out += bytes([favorite])
    if koef is not None:
        out += struct.pack("<H", int(round(koef * 100)))
    return out + b"".join(struct.pack("<H", int(round(v * 100))) for v in values)


TIPS = [(1049, 4), (1073, 3), (491, 1), (1366, 1), (490, 2)]
MSK_1800 = 1_800_000_000 + 3 * 3600


def test_football_total_handicap_orientation_and_margin():
    body = (_champ(10, 1, 0, "РПЛ") + _event(1, MSK_1800, "Спартак", "ЦСКА")
            + _line(1, 1049, [1.9, 1.9], koef=2.5)
            + _line(2, 1073, [5.1, 1.12], favorite=1, koef=1.5)
            + _line(3, 1073, [1.07, 6.85], favorite=2, koef=1.5)
            + _line(4, 1073, [1.65, 2.06], favorite=0, koef=0)
            + _line(5, 490, [2.4, 3.0, 3.05])
            + _line(6, 1049, [2.0, 1.8], margin=9500, koef=3.5))
    quotes = parse_snapshot(_menu(TIPS), bytes(16) + body, {"football"})
    got = [(q.outcome_name, q.odds, q.market) for q in quotes]
    assert got == [
        ("Тотал больше 2.5", 1.9, "total_2.5"), ("Тотал меньше 2.5", 1.9, "total_2.5"),
        ("H1:-1.5", 5.1, "hcp"), ("H2:1.5", 1.12, "hcp"),
        ("H1:1.5", 1.07, "hcp"), ("H2:-1.5", 6.85, "hcp"),
        ("Тотал больше 3.5", 1.95, "total_3.5"), ("Тотал меньше 3.5", 1.76, "total_3.5"),
    ]
    assert quotes[0].start_time_utc.startswith("2027-01-15T08:00")


def test_esports_game_from_country_and_unwanted_games_skipped():
    body = (_country(7, "Counter-Strike") + _country(8, "Mobile Legends")
            + _champ(20, 205, 7, "ESL") + _event(1, MSK_1800, "Spirit", "Navi") + _line(1, 1366, [1.5, 2.6])
            + _champ(21, 205, 8, "MPL") + _event(2, MSK_1800, "X", "Y") + _line(2, 1366, [1.8, 2.0]))
    quotes = parse_snapshot(_menu(TIPS), bytes(16) + body, {"cs2", "dota2"})
    assert [(q.game, q.outcome_name, q.odds) for q in quotes] == [("cs2", "Spirit", 1.5), ("cs2", "Navi", 2.6)]
