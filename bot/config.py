from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    odds_api_key: str
    odds_api_base_url: str
    the_odds_api_key: str
    poll_interval_seconds: int
    default_min_profit_pct: float
    db_path: str
    games: list[str] = field(
        default_factory=lambda: ["cs2", "dota2", "lol", "valorant", "tennis", "basketball"]
    )


def load_config() -> Config:
    bot_token = os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    return Config(
        bot_token=bot_token,
        odds_api_key=os.environ.get("ODDS_API_KEY", ""),
        odds_api_base_url=os.environ.get("ODDS_API_BASE_URL", "https://api.oddspapi.io"),
        the_odds_api_key=os.environ.get("THE_ODDS_API_KEY", ""),
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "150")),
        default_min_profit_pct=float(os.environ.get("DEFAULT_MIN_PROFIT_PCT", "1.0")),
        db_path=os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"),
    )
