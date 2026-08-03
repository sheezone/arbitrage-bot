"""Plain sqlite3 access, no ORM -- the schema is small and stable enough not to need one."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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

    def upsert_user(self, chat_id: int) -> None:
        self._conn.execute(
            "INSERT INTO users (chat_id) VALUES (?) ON CONFLICT(chat_id) DO NOTHING",
            (chat_id,),
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
    )
