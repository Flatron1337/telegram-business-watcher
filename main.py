import asyncio
import contextlib
import sys
from pathlib import Path

import aiosqlite

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from config.settings import Settings, get_settings
from database.connection import Database
from database.repository import BusinessRepository
from handlers import business_router, user_commands_router
from utils.logging import setup_logger

logger = setup_logger("business.main")

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


async def periodic_cleanup_task(
    repository: BusinessRepository, retention_days: int
) -> None:
    """Periodically purges expired message history from SQLite."""
    while True:
        try:
            await asyncio.sleep(86400)
            logger.info(
                "Starting scheduled cleanup for data older than %d days...",
                retention_days,
            )
            deleted = await repository.cleanup_old(retention_days)
            logger.info("Cleanup completed: %d old messages purged.", deleted)
        except asyncio.CancelledError:
            break
        except (aiosqlite.Error, OSError) as e:
            logger.error("Error during scheduled cleanup: %s", e)


def load_settings() -> Settings:
    """Loads configuration settings from local .env."""
    env_path = Path(__file__).parent / ".env"
    return get_settings(env_path)


def create_dispatcher(repository: BusinessRepository) -> Dispatcher:
    """Creates and configures Aiogram Dispatcher with routers and dependencies."""
    dp = Dispatcher()
    dp["repository"] = repository
    dp.include_router(user_commands_router)
    dp.include_router(business_router)
    return dp


async def start_health_server(port: int) -> web.AppRunner:
    """Starts a minimal HTTP healthcheck server for Render."""
    app = web.Application()

    async def handle_health(_: web.Request) -> web.Response:
        return web.Response(text="Watcher Bot is running", status=200)

    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Healthcheck web server listening on 0.0.0.0:%d", port)
    return runner


async def keep_alive_task(ping_url: str, interval: int = 300) -> None:
    """Periodically sends an HTTP GET request to prevent Render from sleeping."""
    health_url = f"{ping_url.rstrip('/')}/health"
    await asyncio.sleep(30)
    logger.info(
        "Anti-sleep task activated: pinging %s every %d s", health_url, interval
    )

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(health_url) as resp:
                    logger.debug("Anti-sleep ping: %s -> %d", health_url, resp.status)
            except asyncio.CancelledError:
                break
            except (aiohttp.ClientError, TimeoutError) as err:
                logger.warning("Anti-sleep ping failure: %s", err)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break


async def run_bot() -> None:
    """Main async entrypoint for the Telegram Business Watcher bot."""
    settings = load_settings()
    logger.info("Initializing Telegram Business Watcher...")

    db_path = Path(__file__).parent / settings.db_path
    database = Database(db_path)
    db_conn = await database.connect()
    repository = BusinessRepository(db_conn)

    cleanup_task = asyncio.create_task(
        periodic_cleanup_task(repository, settings.retention_days)
    )
    web_runner: web.AppRunner | None = None
    if settings.port:
        web_runner = await start_health_server(settings.port)

    target_ping_url = settings.render_external_url or (
        "https://telegram-business-watcher.onrender.com" if settings.port else None
    )
    pinger_task: asyncio.Task[None] | None = None
    if target_ping_url:
        pinger_task = asyncio.create_task(
            keep_alive_task(target_ping_url, settings.ping_interval_seconds)
        )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher(repository)

    try:
        bot_info = await bot.get_me()
        logger.info(
            "Bot started successfully: @%s (ID: %s, Name: %s)",
            bot_info.username,
            bot_info.id,
            bot_info.first_name,
        )
        logger.info("Listening for Telegram Business events and user commands...")

        await dp.start_polling(
            bot,
            repository=repository,
            allowed_updates=ALLOWED_UPDATES,
        )
    finally:
        logger.info("Shutting down bot and closing connections...")
        if pinger_task:
            pinger_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinger_task
        if web_runner:
            await web_runner.cleanup()
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        await bot.session.close()
        await database.close()
        logger.info("Shutdown complete.")


def main() -> None:
    """Console entrypoint."""
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit) as exc:
        logger.info("Bot execution terminated by user (%s).", exc or "signal")


if __name__ == "__main__":
    main()
