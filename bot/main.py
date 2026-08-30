from __future__ import annotations

import asyncio
import logging
import socket

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from bot.config import load_config
from bot.core.monitor import run_monitor_loop
from bot.core.state import LatestState
from bot.db.repository import Repository
from bot.handlers.commands import register_handlers
from bot.providers.baltbet import BaltbetProvider
from bot.providers.cryptobot import CryptoPayClient
from bot.providers.fonbet import FonbetProvider
from bot.providers.leon import LeonProvider
from bot.providers.marathon import MarathonProvider
from bot.providers.melbet import MelbetProvider
from bot.providers.oddspapi import OddsPapiProvider
from bot.providers.olimpbet import OlimpBetProvider
from bot.providers.pari import PariProvider
from bot.providers.surebet import SurebetFinder
from bot.providers.the_odds_api import TheOddsApiProvider
from bot.providers.zenit import ZenitProvider
from bot.webapp.api import register_api

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

    # Confirmed live on the production VPS: resolving api.telegram.org's AAAA (IPv6)
    # record stalls for 100+ seconds before falling back to IPv4 -- a plain `curl -4`
    # to the same host responds in well under a second, and disabling IPv6 at the OS
    # level (sysctl) didn't fix it (still a DNS-resolution-time issue, not a routing
    # one). Forcing the connector to IPv4 here sidesteps it at the source rather than
    # depending on the retry/timeout hardening elsewhere to paper over a 100+ second hang.
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher()
    state = LatestState()

    sources = [
        OddsPapiProvider(api_key=config.odds_api_key, base_url=config.odds_api_base_url),
        FonbetProvider(),
        PariProvider(),
        MarathonProvider(),
        BaltbetProvider(),
        TheOddsApiProvider(api_key=config.the_odds_api_key),
        ZenitProvider(),
        LeonProvider(),
        OlimpBetProvider(),
    ]
    # Opt-in, heavier than everything else here (drives a real headless Chromium) --
    # see bot/providers/melbet.py's module docstring. Off by default; ENABLE_MELBET=true
    # to turn it on once its memory footprint has been watched on the production VPS.
    if config.enable_melbet:
        sources.append(MelbetProvider())
    surebet_finder = SurebetFinder(api_token=config.surebet_api_token)
    crypto_pay_client = CryptoPayClient(api_token=config.cryptobot_api_token) if config.cryptobot_api_token else None

    me = await _get_me_with_retries(bot)

    dp.include_router(
        register_handlers(
            repo,
            state,
            yookassa_provider_token=config.yookassa_provider_token,
            admin_chat_ids=config.admin_chat_ids,
            bot_username=me.username or "",
            poll_interval_seconds=config.poll_interval_seconds,
            crypto_pay_client=crypto_pay_client,
            webapp_url=config.webapp_url,
        )
    )

    # Mini App backend -- bound to 127.0.0.1 only (never exposed on the public interface
    # directly); reaching it from the outside needs a reverse proxy/tunnel in front (see
    # bot/webapp/api.py's module docstring and the deploy notes for how this VPS does it
    # via cloudflared, since there's no domain to put a normal TLS cert on). A no-op,
    # not an error, when WEBAPP_URL is unset -- same opt-in pattern as Melbet/crypto pay.
    webapp_task: asyncio.Task | None = None
    if config.webapp_url:
        webapp_app = register_api(repo, state, config.admin_chat_ids)
        uv_config = uvicorn.Config(webapp_app, host="127.0.0.1", port=config.webapp_port, log_level="warning")
        webapp_task = asyncio.create_task(uvicorn.Server(uv_config).serve())

    monitor_task = asyncio.create_task(
        run_monitor_loop(
            sources=sources,
            repo=repo,
            bot=bot,
            games=config.games,
            poll_interval_seconds=config.poll_interval_seconds,
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

    if config.webapp_url:
        # The reply-keyboard "web_app" button (see commands.py's _main_menu_keyboard)
        # sends empty initData on at least Telegram Desktop -- confirmed live, the Mini
        # App loaded but every API call failed with "Missing Telegram init data" even
        # though the button worked and opened it. The chat menu button (next to the
        # message input) is Telegram's other, more thoroughly-supported Mini App launch
        # surface and doesn't have this problem -- set both so every client has one that
        # actually works, rather than relying on the reply-keyboard button alone.
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🚀 Мини-приложение", web_app=WebAppInfo(url=config.webapp_url))
            )
        except TelegramNetworkError:
            logger.warning("set_chat_menu_button failed on startup -- non-critical, continuing without it")

    try:
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        if webapp_task is not None:
            webapp_task.cancel()
        for source in sources:
            await source.close()
        await surebet_finder.close()
        if crypto_pay_client is not None:
            await crypto_pay_client.close()
        repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
