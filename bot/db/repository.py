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
    referred_by: int | None
    referral_balance_rub: float
    expiry_reminder_sent_for: str | None
    allowed_bookmakers: list[str]
    muted: bool


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
        if "referred_by" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "referral_balance_rub" not in columns:
            self._conn.execute("ALTER TABLE users ADD COLUMN referral_balance_rub REAL NOT NULL DEFAULT 0")
        if "expiry_reminder_sent_for" not in columns:
            # Stores the exact access-end timestamp a reminder was already sent for --
            # comparing against the *current* access end (not just a boolean) means a
            # renewal that pushes the deadline out automatically re-arms the reminder for
            # the new deadline, with no separate "reset on purchase" step needed.
            self._conn.execute("ALTER TABLE users ADD COLUMN expiry_reminder_sent_for TEXT")
        if "allowed_bookmakers" not in columns:
            # Empty string = no restriction (every bookmaker allowed) -- the common case,
            # so a fresh/upgraded user sees everything by default rather than nothing.
            self._conn.execute("ALTER TABLE users ADD COLUMN allowed_bookmakers TEXT NOT NULL DEFAULT ''")
        if "muted" not in columns:
            # Separate from is_active (which stops notifications entirely): muted still
            # sends them, just silently (disable_notification) -- so someone can keep
            # getting vilki without a ping for every one.
            self._conn.execute("ALTER TABLE users ADD COLUMN muted INTEGER NOT NULL DEFAULT 0")

    def upsert_user(self, chat_id: int, referred_by: int | None = None) -> None:
        """`referred_by` only ever takes effect for a genuinely new row -- ON CONFLICT DO
        NOTHING means an existing user's referrer can never be silently overwritten by a
        later /start with a different referral link."""
        self._conn.execute(
            "INSERT INTO users (chat_id, trial_started_at, referred_by) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id) DO NOTHING",
            (chat_id, datetime.now(timezone.utc).isoformat(), referred_by),
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

    def set_muted(self, chat_id: int, muted: bool) -> None:
        self._conn.execute("UPDATE users SET muted = ? WHERE chat_id = ?", (int(muted), chat_id))
        self._conn.commit()

    def set_time_horizons(self, chat_id: int, days: list[int]) -> None:
        self._conn.execute(
            "UPDATE users SET time_horizons = ? WHERE chat_id = ?",
            (",".join(str(d) for d in days), chat_id),
        )
        self._conn.commit()

    def credit_referral_balance(self, chat_id: int, amount_rub: float) -> None:
        self._conn.execute(
            "UPDATE users SET referral_balance_rub = referral_balance_rub + ? WHERE chat_id = ?",
            (amount_rub, chat_id),
        )
        self._conn.commit()

    def consume_referral_balance(self, chat_id: int, amount_rub: float) -> None:
        self._conn.execute(
            "UPDATE users SET referral_balance_rub = MAX(0, referral_balance_rub - ?) WHERE chat_id = ?",
            (amount_rub, chat_id),
        )
        self._conn.commit()

    def count_referrals(self, chat_id: int) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by = ?", (chat_id,)).fetchone()
        return row["c"]

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

    def get_all_users(self) -> list[UserSettings]:
        """Unlike get_active_users, includes users who paused notifications -- used for
        things that are account-level rather than a notification preference, e.g. the
        expiry reminder: a paused user's subscription still runs out either way."""
        rows = self._conn.execute("SELECT * FROM users").fetchall()
        return [_row_to_user(r) for r in rows]

    def set_expiry_reminder_sent(self, chat_id: int, access_end_iso: str) -> None:
        self._conn.execute(
            "UPDATE users SET expiry_reminder_sent_for = ? WHERE chat_id = ?", (access_end_iso, chat_id)
        )
        self._conn.commit()

    def set_allowed_bookmakers(self, chat_id: int, bookmakers: list[str]) -> None:
        self._conn.execute(
            "UPDATE users SET allowed_bookmakers = ? WHERE chat_id = ?", (",".join(bookmakers), chat_id)
        )
        self._conn.commit()

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

    def log_opportunity(self, profit_pct: float) -> None:
        """One row per genuinely new arbitrage opportunity (call this alongside
        mark_opportunity_seen, which already guarantees "once per unique opportunity"
        regardless of how many users end up notified) -- feeds the stats screen."""
        self._conn.execute(
            "INSERT INTO opportunity_log (found_at, profit_pct) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), profit_pct),
        )
        self._conn.commit()

    def get_opportunity_stats(self) -> dict:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today = self._conn.execute(
            "SELECT COUNT(*) AS c, AVG(profit_pct) AS avg_p, MAX(profit_pct) AS max_p "
            "FROM opportunity_log WHERE found_at >= ?",
            (today_start,),
        ).fetchone()
        alltime = self._conn.execute(
            "SELECT COUNT(*) AS c, AVG(profit_pct) AS avg_p FROM opportunity_log"
        ).fetchone()
        return {
            "today_count": today["c"],
            "today_avg_profit": today["avg_p"] or 0.0,
            "today_best_profit": today["max_p"] or 0.0,
            "alltime_count": alltime["c"],
            "alltime_avg_profit": alltime["avg_p"] or 0.0,
        }

    def record_support_message(self, admin_chat_id: int, admin_message_id: int, user_chat_id: int) -> None:
        """Remembers which user a message forwarded into an admin's chat came from, so a
        plain Telegram reply to it (see get_support_message_user) can be relayed back."""
        self._conn.execute(
            "INSERT OR REPLACE INTO support_messages (admin_chat_id, admin_message_id, user_chat_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (admin_chat_id, admin_message_id, user_chat_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get_support_message_user(self, admin_chat_id: int, admin_message_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT user_chat_id FROM support_messages WHERE admin_chat_id = ? AND admin_message_id = ?",
            (admin_chat_id, admin_message_id),
        ).fetchone()
        return row["user_chat_id"] if row else None

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
        referred_by=row["referred_by"],
        referral_balance_rub=row["referral_balance_rub"],
        expiry_reminder_sent_for=row["expiry_reminder_sent_for"],
        allowed_bookmakers=[b for b in row["allowed_bookmakers"].split(",") if b],
        muted=bool(row["muted"]),
    )


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
