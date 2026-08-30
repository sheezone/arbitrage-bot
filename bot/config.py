from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from bot.providers.surebet import DEFAULT_TEST_TOKEN as SUREBET_DEFAULT_TEST_TOKEN

load_dotenv()


@dataclass
class Config:
    bot_token: str
    odds_api_key: str
    odds_api_base_url: str
    the_odds_api_key: str
    surebet_api_token: str
    yookassa_provider_token: str
    cryptobot_api_token: str
    webapp_url: str
    webapp_port: int
    admin_chat_ids: frozenset[int]
    showcase_chat_id: int | None
    showcase_interval_seconds: int
    poll_interval_seconds: int
    default_min_profit_pct: float
    db_path: str
    enable_melbet: bool = False
    games: list[str] = field(
        default_factory=lambda: [
            "cs2", "dota2", "lol", "valorant", "tennis", "basketball", "football", "hockey",
            "boxing", "mma", "volleyball",
        ]
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
        surebet_api_token=os.environ.get("SUREBET_API_TOKEN", SUREBET_DEFAULT_TEST_TOKEN),
        yookassa_provider_token=os.environ.get("YOOKASSA_PROVIDER_TOKEN", ""),
        cryptobot_api_token=os.environ.get("CRYPTOBOT_API_TOKEN", ""),
        webapp_url=os.environ.get("WEBAPP_URL", "").rstrip("/"),
        webapp_port=int(os.environ.get("WEBAPP_PORT", "8000")),
        admin_chat_ids=frozenset(
            int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip()
        ),
        showcase_chat_id=(
            int(os.environ["SHOWCASE_CHAT_ID"]) if os.environ.get("SHOWCASE_CHAT_ID", "").strip() else None
        ),
        showcase_interval_seconds=int(os.environ.get("SHOWCASE_INTERVAL_SECONDS", "600")),
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "150")),
        default_min_profit_pct=float(os.environ.get("DEFAULT_MIN_PROFIT_PCT", "1.0")),
        db_path=os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"),
        enable_melbet=os.environ.get("ENABLE_MELBET", "").strip().lower() in ("1", "true", "yes"),
    )
