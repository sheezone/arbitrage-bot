CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    bankroll REAL NOT NULL DEFAULT 100.0,
    min_profit_pct REAL NOT NULL DEFAULT 1.0,
    watched_games TEXT NOT NULL DEFAULT 'cs2,dota2,lol,tennis',
    is_active INTEGER NOT NULL DEFAULT 1,
    menu_message_id INTEGER,
    trial_started_at TEXT,
    subscription_expires_at TEXT,
    time_horizon_days INTEGER NOT NULL DEFAULT 3,
    time_horizons TEXT NOT NULL DEFAULT '3'
);

CREATE TABLE IF NOT EXISTS seen_opportunities (
    fixture_id TEXT NOT NULL,
    bookmakers_hash TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (fixture_id, bookmakers_hash)
);

CREATE TABLE IF NOT EXISTS payments (
    chat_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    telegram_charge_id TEXT NOT NULL,
    paid_at TEXT NOT NULL,
    PRIMARY KEY (telegram_charge_id)
);
