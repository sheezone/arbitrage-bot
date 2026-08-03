CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    bankroll REAL NOT NULL DEFAULT 100.0,
    min_profit_pct REAL NOT NULL DEFAULT 1.0,
    watched_games TEXT NOT NULL DEFAULT 'cs2,dota2,lol,tennis',
    is_active INTEGER NOT NULL DEFAULT 1,
    menu_message_id INTEGER
);

CREATE TABLE IF NOT EXISTS seen_opportunities (
    fixture_id TEXT NOT NULL,
    bookmakers_hash TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (fixture_id, bookmakers_hash)
);
