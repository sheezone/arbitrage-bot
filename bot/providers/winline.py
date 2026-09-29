"""Winline (winline.ru) -- the site has no REST line at all: the whole prematch line is
pushed over its own WebSocket, `wss://wss.winline.ru/data_ng`, in a custom binary format
(reverse-engineered 2026-09-26 from winline.ru's main.*.js -- the `Qn` prematch parser,
`vn` menu parser and `Mn` margin helper). No captcha/token is involved for anonymous
line data: after connecting, the site's client sends the text commands
"lang", "AA==" (RU), "data", "WINLINE", "getdate" and the server answers with gzip'd
binary frames. Each frame starts with a little-endian uint16 "step":

  16 (GET_MENU):     sports, then the "tipLines" dictionary (market types), then TVs.
  3  (GET_PREMATCH): the full prematch snapshot (first frame), later ones are deltas.

Inside the prematch payload (read from offset 16) a stream of sub-steps follows:
  1 country, 2 championship, 3 event (belongs to the last championship),
  4 line (belongs to the last event), 31/32/33/34/42/43 = updates/deletes.

A line's odds arrive as margin-free values (V, x100) plus a per-line margin factor; the
site shows 1 + (v - 1) * margin rounded to 2 decimals (>= 10 floored) -- reproduced in
_apply_margin. Market meaning comes from the tipLine: `src` (idTipEventSrc) decides the
extra fields (handicap/total line, and for handicaps a "favorite" byte saying which team
the line is quoted for).

Markets used, same per-sport rules as the other providers:
  football (1): 1049 total, 1073 handicap (half lines), both regular time (@NP@ = RT)
  hockey (4):   1049 total, regular time
  basketball (2): 491 winner incl. OT (@NP@ = FT), 1073 handicap
  tennis (5):   491 match winner
  esports (205): 1366 match winner; the game is the championship's "country"
"""
from __future__ import annotations

import asyncio
import gzip
import struct
from datetime import datetime, timezone

import websockets

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote, tag_leagues

WS_URL = "wss://wss.winline.ru/data_ng?client=newsite&nb=true"
INIT_COMMANDS = ("lang", "AA==", "data", "WINLINE", "getdate")
HEADERS = {
    "Origin": "https://winline.ru",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
}
BOOKMAKER = "winline"
STEP_MENU, STEP_PREMATCH = 16, 3
# Winline sends Moscow wall-clock seconds (the site subtracts TZ_OFFSET_MS = 3 h).
MSK_OFFSET_S = 3 * 3600

SPORT_GAMES = {1: "football", 4: "hockey", 2: "basketball", 5: "tennis"}
ESPORTS_SPORT = 205
ESPORTS_GAMES = {"Counter-Strike": "cs2", "DOTA 2": "dota2", "League of Legends": "lol", "Valorant": "valorant"}

TIP_TOTAL, TIP_HANDICAP, TIP_WINNER, TIP_ESPORTS_WINNER = 1049, 1073, 491, 1366
TOTALS_GAMES = frozenset({"football", "hockey"})
HANDICAP_GAMES = frozenset({"football", "basketball"})
WINNER_GAMES = frozenset({"basketball", "tennis"})
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)


class WinlineProvider(OddsProvider):
    def __init__(self, url: str = WS_URL, timeout: float = 45.0):
        self._url = url
        self._timeout = timeout

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        wanted = set(games)
        if not wanted & (set(SPORT_GAMES.values()) | set(ESPORTS_GAMES.values())):
            return []
        menu, prematch = await asyncio.wait_for(self._snapshot(), self._timeout)
        return parse_snapshot(menu, prematch, wanted)

    async def _snapshot(self) -> tuple[bytes, bytes]:
        menu = prematch = None
        async with websockets.connect(
            self._url, additional_headers=HEADERS, max_size=None, ping_interval=None, open_timeout=15
        ) as ws:
            for command in INIT_COMMANDS:
                await ws.send(command)
            while menu is None or prematch is None:
                frame = await ws.recv()
                if isinstance(frame, str):
                    continue
                data = gzip.decompress(frame)
                if len(data) < 2:
                    continue
                step = struct.unpack_from("<H", data, 0)[0]
                if step == STEP_MENU and menu is None:
                    menu = data[2:]
                elif step == STEP_PREMATCH and menu is not None and prematch is None:
                    prematch = data[2:]
        return menu, prematch

    async def close(self) -> None:
        pass


class _Reader:
    def __init__(self, buf: bytes, pos: int = 0):
        self.buf, self.pos = buf, pos

    def u8(self) -> int:
        value = self.buf[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.buf, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.buf, self.pos)[0]
        self.pos += 4
        return value

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.buf, self.pos)[0]
        self.pos += 4
        return value

    def text(self) -> str:
        n = self.u16()
        raw = self.buf[self.pos:self.pos + n]
        self.pos += n
        cut = raw.find(27)
        return (raw[:cut] if cut >= 0 else raw).decode("utf-8", "replace")


def parse_menu(payload: bytes) -> dict[int, int]:
    """tipLine id -> idTipEventSrc (all the prematch parser needs to size a line)."""
    r = _Reader(payload)
    for _ in range(r.u32()):  # sports: id, sort, name, 9 label strings
        r.i32(); r.i32(); r.text()
        for _ in range(9):
            r.text()
    tips: dict[int, int] = {}
    for _ in range(r.u32()):
        tip_id = r.u32(); r.text(); r.u32(); r.u32(); src = r.u32(); r.text()
        for _ in range(30):
            r.text()
        tips[tip_id] = src
    return tips


def _apply_margin(values: list[float], margin: float) -> list[float]:
    if margin == 1:
        return values
    out = []
    for v in values:
        if v > 1:
            v = round(1 + (v - 1) * margin, 2)
            if v >= 10:
                v = float(int(v))
        out.append(v)
    return out


def parse_prematch(payload: bytes, tips: dict[int, int]):
    """-> (countries, championships, events, lines). Raises ValueError on an unknown
    sub-step or market, since the stream can't be resynchronised after one."""
    r = _Reader(payload, 16)
    countries: dict[int, str] = {}
    champs: dict[int, dict] = {}
    events: dict[int, dict] = {}
    lines: list[dict] = []
    cur_champ = cur_event = -1
    while r.pos < len(r.buf):
        step = r.u8()
        if step == 1:
            cid = r.u32(); r.u32(); countries[cid] = r.text(); r.u32(); r.u8(); r.u16(); r.u16(); r.u8()
        elif step == 2:
            champ = {"id": r.u32(), "sport": r.u32(), "country": r.u32(), "name": r.text()}
            r.i32(); r.u32(); r.u32(); r.u8(); r.u32(); r.u32()
            champs[champ["id"]] = champ
            cur_champ = champ["id"]
        elif step in (3, 34):
            event_id = r.u32(); r.u32(); r.u32()
            for _ in range(6):
                r.u8()
            widgets_len = r.u16()
            r.pos += widgets_len  # widget TLV block
            champ_id = r.u32() if step == 34 else cur_champ
            date = r.u32(); r.u8(); r.u8()
            events[event_id] = {"id": event_id, "champ": champ_id, "date": date, "a": r.text(), "b": r.text()}
            cur_event = event_id
        elif step in (4, 43):
            line_id = r.u32()
            event_id = r.u32() if step == 43 else cur_event
            tip = r.u16()
            r.u16()
            margin = r.u16() / 1e4
            src = tips.get(tip)
            if src is None:
                raise ValueError(f"unknown Winline market {tip}")
            favorite = koef = None
            if src in (3, 6, 61):
                favorite = r.u8()
                koef = r.u16() / 100
            elif src in (4, 7, 71):
                koef = r.u16() / 100
            values = [r.u16() / 100, r.u16() / 100]
            if src in (2, 5, 51):
                values.append(r.u16() / 100)
            elif src == 9:
                values += [r.u16() / 100, r.u16() / 100]
            lines.append({"id": line_id, "event": event_id, "tip": tip, "favorite": favorite,
                          "koef": koef, "odds": _apply_margin(values, margin)})
        elif step == 31:
            r.u32(); r.u32()
        elif step in (32, 42):
            r.u32()
        elif step == 33:
            r.u32(); r.u8(); r.u8()
        else:
            raise ValueError(f"unknown Winline prematch step {step}")
    return countries, champs, events, lines


def parse_snapshot(menu: bytes, prematch: bytes, wanted: set[str]) -> list[SourceQuote]:
    tips = parse_menu(menu)
    countries, champs, events, lines = parse_prematch(prematch, tips)
    by_event: dict[int, list[dict]] = {}
    for line in lines:
        by_event.setdefault(line["event"], []).append(line)

    quotes: list[SourceQuote] = []
    leagues: dict[tuple[str, str, str], str] = {}
    for event in events.values():
        champ = champs.get(event["champ"])
        if not champ:
            continue
        if champ["sport"] == ESPORTS_SPORT:
            game = ESPORTS_GAMES.get(countries.get(champ["country"], ""))
        else:
            game = SPORT_GAMES.get(champ["sport"])
        if game is None or game not in wanted or not event["a"] or not event["b"]:
            continue
        a, b = event["a"], event["b"]
        start = datetime.fromtimestamp(event["date"] - MSK_OFFSET_S, tz=timezone.utc).isoformat()
        country = countries.get(champ["country"], "")
        leagues[(a, b, start)] = f"{country}. {champ['name']}" if country and country not in champ["name"] else champ["name"]
        seen_totals: set[float] = set()
        seen_hcp: set[float] = set()
        for line in by_event.get(event["id"], []):
            odds = line["odds"]
            if len(odds) < 2 or odds[0] <= 1 or odds[1] <= 1:
                continue
            tip = line["tip"]
            if game in TOTALS_GAMES and tip == TIP_TOTAL:
                total = line["koef"]
                if total is None or total in seen_totals or (total * 2) % 2 != 1:
                    continue
                if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= total <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
                    continue
                seen_totals.add(total)
                market = f"total_{total}"
                quotes.append(SourceQuote(game, a, b, start, BOOKMAKER, f"Тотал больше {total}", odds[0], market))
                quotes.append(SourceQuote(game, a, b, start, BOOKMAKER, f"Тотал меньше {total}", odds[1], market))
            elif game in HANDICAP_GAMES and tip == TIP_HANDICAP:
                # favorite 1: the line is team 1's handicap given (-), 2: team 1 receives it.
                if line["favorite"] not in (1, 2) or line["koef"] is None:
                    continue
                h1 = -line["koef"] if line["favorite"] == 1 else line["koef"]
                if (abs(h1) * 2) % 2 != 1 or h1 in seen_hcp:
                    continue
                seen_hcp.add(h1)
                quotes.append(SourceQuote(game, a, b, start, BOOKMAKER, f"H1:{h1}", odds[0], "hcp"))
                quotes.append(SourceQuote(game, a, b, start, BOOKMAKER, f"H2:{-h1}", odds[1], "hcp"))
            elif (game in WINNER_GAMES and tip == TIP_WINNER) or (
                game in ESPORTS_GAMES.values() and tip == TIP_ESPORTS_WINNER
            ):
                quotes.append(SourceQuote(game, a, b, start, BOOKMAKER, a, odds[0]))
                quotes.append(SourceQuote(game, a, b, start, BOOKMAKER, b, odds[1]))
    return tag_leagues(quotes, leagues)
