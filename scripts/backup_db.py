"""Daily database backup (owner 2026-10-13). Run by the arbitrage-backup systemd timer.

1. A consistent snapshot via SQLite's online backup API (safe while the bot is writing),
   gzipped into /root/backups, keeping the last KEEP_DAYS.
2. The same file sent to the first admin's Telegram DM -- an off-server copy, so a dead
   VPS doesn't take the only backup with it.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = Path("/root/backups")
KEEP_DAYS = 14

load_dotenv(ROOT / ".env")


def main() -> None:
    db = ROOT / os.environ.get("DB_PATH", "arbitrage_bot.sqlite3")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    raw = BACKUP_DIR / f"db-{stamp}.sqlite3"
    src, dst = sqlite3.connect(db), sqlite3.connect(raw)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    gz = raw.with_suffix(".sqlite3.gz")
    with open(raw, "rb") as f_in, gzip.open(gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw.unlink()

    cutoff = time.time() - KEEP_DAYS * 86400
    for old in BACKUP_DIR.glob("db-*.sqlite3.gz"):
        if old.stat().st_mtime < cutoff:
            old.unlink()

    token = os.environ.get("BOT_TOKEN", "")
    admins = [a.strip() for a in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if a.strip()]
    if token and admins:
        with open(gz, "rb") as f:
            r = httpx.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": admins[0], "caption": f"🗄 Бэкап базы {stamp}", "disable_notification": "true"},
                files={"document": (gz.name, f, "application/gzip")},
                timeout=120,
            )
        print("telegram:", r.status_code)
    print("saved", gz, gz.stat().st_size)


if __name__ == "__main__":
    main()
