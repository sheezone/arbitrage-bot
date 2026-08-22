from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand

from bot.config import load_config
from bot.core.monitor import run_monitor_loop
from bot.core.state import LatestState
from bot.db.repository import Repository
from bot.handlers.commands import register_handlers
from bot.providers.baltbet import BaltbetProvider
from bot.providers.fonbet import FonbetProvider
from bot.providers.marathon import MarathonProvider
from bot.providers.melbet import MelbetProvider
from bot.providers.oddspapi import OddsPapiProvider
from bot.providers.pari import PariProvider
from bot.providers.surebet import SurebetFinder
from bot.providers.the_odds_api import TheOddsApiProvider
from bot.providers.zenit import ZenitProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STARTUP_RETRY_ATTEMPTS = 5
STARTUP_RETRY_DELAY_SECONDS = 5


async def _get_me_with_retries(bot: Bot):
    """A single flaky network timeout during startup used to crash the whole process --
    confirmed live on the production VPS, systemd was restarting it every ~70 seconds on
    repeat TelegramNetworkError from this exact call. Retrying a handful of times here is
    far cheaper than a full process restart (which also drops the monitor loop and any
    in-flight browser/HTTP sessions)."""
    last_error: Exception | None = None
    for attempt in range(1, STARTUP_RETRY_ATTEMPTS + 1):
        try:
            return await bot.get_me()
        except TelegramNetworkError as e:
            last_error = e
            logger.warning("bot.get_me() failed (attempt %d/%d): %s", attempt, STARTUP_RETRY_ATTEMPTS, e)
            await asyncio.sleep(STARTUP_RETRY_DELAY_SECONDS)
    raise last_error


async def main() -> None:
    config = load_config()
    repo = Repository(config.db_path)
    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    state = LatestState()
    dp.include_router(
        register_handlers(
            repo,
            state,
            yookassa_provider_token=config.yookassa_provider_token,
            admin_chat_ids=config.admin_chat_ids,
        )
    )

    sources = [
        OddsPapiProvider(api_key=config.odds_api_key, base_url=config.odds_api_base_url),
        FonbetProvider(),
        PariProvider(),
        MarathonProvider(),
        BaltbetProvider(),
        TheOddsApiProvider(api_key=config.the_odds_api_key),
        ZenitProvider(),
    ]
    # Opt-in, heavier than everything else here (drives a real headless Chromium) --
    # see bot/providers/melbet.py's module docstring. Off by default; ENABLE_MELBET=true
    # to turn it on once its memory footprint has been watched on the production VPS.
    if config.enable_melbet:
        sources.append(MelbetProvider())
    surebet_finder = SurebetFinder(api_token=config.surebet_api_token)

    me = await _get_me_with_retries(bot)

    monitor_task = asyncio.create_task(
        run_monitor_loop(
            sources=sources,
            repo=repo,
            bot=bot,
            games=config.games,
            poll_interval_seconds=config.poll_interval_seconds,
            default_min_profit_pct=config.default_min_profit_pct,
            state=state,
            surebet_finder=surebet_finder,
            admin_chat_ids=config.admin_chat_ids,
            showcase_chat_id=config.showcase_chat_id,
            showcase_interval_seconds=config.showcase_interval_seconds,
            bot_username=me.username or "",
        )
    )

    try:
        await bot.set_my_commands([BotCommand(command="start", description="Запуск бота / показать меню")])
    except TelegramNetworkError:
        logger.warning("set_my_commands failed on startup -- non-critical, continuing without it")

    try:
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        for source in sources:
            await source.close()
        await surebet_finder.close()
        repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
