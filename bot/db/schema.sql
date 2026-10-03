CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    bankroll REAL NOT NULL DEFAULT 100.0,
    min_profit_pct REAL NOT NULL DEFAULT 1.0,
    watched_games TEXT NOT NULL DEFAULT 'cs2,dota2,lol,tennis',
    is_active INTEGER NOT NULL DEFAULT 1,
    menu_message_id INTEGER,
    trial_started_at TEXT,
    subscription_expires_at TEXT,
    time_horizon_days INTEGER NOT NULL DEFAULT 7,
    time_horizons TEXT NOT NULL DEFAULT '1,2',
    referred_by INTEGER,
    referral_balance_rub REAL NOT NULL DEFAULT 0,
    expiry_reminder_sent_for TEXT,
    allowed_bookmakers TEXT NOT NULL DEFAULT '',
    muted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS seen_opportunities (
    fixture_id TEXT NOT NULL,
    bookmakers_hash TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (fixture_id, bookmakers_hash)
);

CREATE TABLE IF NOT EXISTS opportunity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    found_at TEXT NOT NULL,
    profit_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS support_messages (
    admin_chat_id INTEGER NOT NULL,
    admin_message_id INTEGER NOT NULL,
    user_chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (admin_chat_id, admin_message_id)
);

CREATE TABLE IF NOT EXISTS showcase_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_key TEXT,
    last_posted_at TEXT
);

CREATE TABLE IF NOT EXISTS showcase_posts (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    showcase_key TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
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

-- One row per (user, match, day) a Mini App analysis was actually used for -- the
-- 1/day quota (see bot/webapp/api.py's /api/analysis) is scoped to a single match, not
-- the whole user, so analysing one popular match doesn't lock out the other two shown
-- the same day.
CREATE TABLE IF NOT EXISTS match_analysis_uses (
    chat_id INTEGER NOT NULL,
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    used_on TEXT NOT NULL,
    PRIMARY KEY (chat_id, team_a, team_b, used_on)
);

CREATE TABLE IF NOT EXISTS news_posts (
    source_url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0
);

-- AI match analysis: one shared analysis per match (cached, reused by every user) and
-- its single pick, settled from the final score for an honest hit-rate.
CREATE TABLE IF NOT EXISTS ai_analyses (
    match_id TEXT PRIMARY KEY,
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    league TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    payload TEXT NOT NULL,
    option_id TEXT NOT NULL,
    option_kind TEXT NOT NULL,
    option_line REAL NOT NULL,
    option_label TEXT NOT NULL,
    odds REAL NOT NULL,
    confidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result TEXT,
    score TEXT,
    settled_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage (
    chat_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    match_id TEXT NOT NULL,
    PRIMARY KEY (chat_id, day, match_id)
);

-- Expresses already pushed to users ("ваш экспресс готов"), so each goes out once.
CREATE TABLE IF NOT EXISTS sent_expresses (
    express_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_logos (
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    url TEXT NOT NULL,          -- '' = looked up, nothing found
    checked_at TEXT NOT NULL,
    PRIMARY KEY (name, sport)
);

CREATE TABLE IF NOT EXISTS user_expresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    express_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    found_at TEXT NOT NULL
);
