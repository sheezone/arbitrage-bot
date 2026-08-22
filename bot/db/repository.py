"""Plain sqlite3 access, no ORM -- the schema is small and stable enough not to need one."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@dataclass
class UserSettings:
    chat_id: int
    bankroll: float
    min_profit_pct: float
    watched_games: list[str]
    is_active: bool
    menu_message_id: int | None
    trial_started_at: str
    subscription_expires_at: str | None
    time_horizons: list[int]


class Repository:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        # schema.sql's CREATE TABLE IF NOT EXISTS doesn't add columns to an already-existing
        # table on disk, so new columns need an explicit, idempotent ALTER TABLE here.
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(users)")}
        if "menu_message_id" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN menu_message_id INTEGER")
        if "trial_started_at" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN trial_started_at TEXT")
            # Backfill existing rows so upgrading to this feature grants them a fresh
            # trial from today rather than leaving trial_started_at NULL forever.
            self._conn.execute(
                "UPDATE users SET trial_started_at = ? WHERE trial_started_at IS NULL",
                (datetime.now(timezone.utc).isoformat(),),
            )
        if "subscription_expires_at" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN subscription_expires_at TEXT")
        if "time_horizon_days" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN time_horizon_days INTEGER NOT NULL DEFAULT 3")
        if "time_horizons" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN time_horizons TEXT NOT NULL DEFAULT '3'")
            # Carry over whatever single value existing users already had under the old,
            # single-select column so upgrading to multi-select doesn't reset anyone.
            self._conn.execute("UPDATE users SET time_horizons = CAST(time_horizon_days AS TEXT)")

    def upsert_user(self, chat_id: int) -> None:
        self._conn.execute(
            "INSERT INTO users (chat_id, trial_started_at) VALUES (?, ?) ON CONFLICT(chat_id) DO NOTHING",
            (chat_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def extend_subscription(self, chat_id: int, days: int) -> None:
        user = self.get_user(chat_id)
        now = datetime.now(timezone.utc)
        current = _parse_iso(user.subscription_expires_at) if user and user.subscription_expires_at else None
        base = max(now, current) if current else now
        new_expiry = base + timedelta(days=days)
        self._conn.execute(
            "UPDATE users SET subscription_expires_at = ? WHERE chat_id = ?",
            (new_expiry.isoformat(), chat_id),
        )
        self._conn.commit()

    def record_payment(
        self, chat_id: int, plan_id: str, provider: str, amount: float, currency: str, telegram_charge_id: str
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO payments "
            "(chat_id, plan_id, provider, amount, currency, telegram_charge_id, paid_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, plan_id, provider, amount, currency, telegram_charge_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def set_bankroll(self, chat_id: int, bankroll: float) -> None:
        self._conn.execute("UPDATE users SET bankroll = ? WHERE chat_id = ?", (bankroll, chat_id))
        self._conn.commit()

    def set_min_profit_pct(self, chat_id: int, pct: float) -> None:
        self._conn.execute(
            "UPDATE users SET min_profit_pct = ? WHERE chat_id = ?", (pct, chat_id)
        )
        self._conn.commit()

    def set_watched_games(self, chat_id: int, games: list[str]) -> None:
        self._conn.execute(
            "UPDATE users SET watched_games = ? WHERE chat_id = ?",
            (",".join(games), chat_id),
        )
        self._conn.commit()

    def set_active(self, chat_id: int, is_active: bool) -> None:
        self._conn.execute(
            "UPDATE users SET is_active = ? WHERE chat_id = ?", (int(is_active), chat_id)
        )
        self._conn.commit()

    def set_time_horizons(self, chat_id: int, days: list[int]) -> None:
        self._conn.execute(
            "UPDATE users SET time_horizons = ? WHERE chat_id = ?",
            (",".join(str(d) for d in days), chat_id),
        )
        self._conn.commit()

    def set_menu_message_id(self, chat_id: int, message_id: int | None) -> None:
        self._conn.execute(
            "UPDATE users SET menu_message_id = ? WHERE chat_id = ?", (message_id, chat_id)
        )
        self._conn.commit()

    def get_user(self, chat_id: int) -> UserSettings | None:
        row = self._conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            return None
        return _row_to_user(row)

    def get_active_users(self) -> list[UserSettings]:
        rows = self._conn.execute("SELECT * FROM users WHERE is_active = 1").fetchall()
        return [_row_to_user(r) for r in rows]

    def has_seen_opportunity(self, fixture_id: str, bookmakers_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_opportunities WHERE fixture_id = ? AND bookmakers_hash = ?",
            (fixture_id, bookmakers_hash),
        ).fetchone()
        return row is not None

    def mark_opportunity_seen(self, fixture_id: str, bookmakers_hash: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_opportunities (fixture_id, bookmakers_hash, notified_at) "
            "VALUES (?, ?, ?)",
            (fixture_id, bookmakers_hash, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _row_to_user(row: sqlite3.Row) -> UserSettings:
    return UserSettings(
        chat_id=row["chat_id"],
        bankroll=row["bankroll"],
        min_profit_pct=row["min_profit_pct"],
        watched_games=row["watched_games"].split(","),
        is_active=bool(row["is_active"]),
        menu_message_id=row["menu_message_id"],
        trial_started_at=row["trial_started_at"],
        subscription_expires_at=row["subscription_expires_at"],
        time_horizons=[int(d) for d in row["time_horizons"].split(",")],
    )


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
