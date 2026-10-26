import asyncio
import contextlib
import sys
from pathlib import Path

import aiosqlite

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import re
import time

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from config.settings import Settings, get_settings
from database.connection import Database
from database.models import BusinessConnectionModel
from database.repository import BusinessRepository
from handlers import business_router, user_commands_router
from middlewares import AuthMiddleware
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


def create_dispatcher(repository: BusinessRepository, settings: Settings) -> Dispatcher:
    """Creates and configures Aiogram Dispatcher with routers and dependencies."""
    dp = Dispatcher()
    dp["repository"] = repository
    dp["settings"] = settings
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())
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


async def restore_startup_state(
    bot: Bot, repository: BusinessRepository, settings: Settings
) -> None:
    """Pre-warms local SQLite database with active business connections on startup."""
    target_ids: list[str] = []
    if settings.last_business_connection_id:
        target_ids.append(settings.last_business_connection_id)

    for uid in settings.allowed_user_ids:
        try:
            chat = await bot.get_chat(uid)
            if chat.pinned_message and chat.pinned_message.text:
                match = re.search(
                    r"BUSINESS_CONN:([A-Za-z0-9_-]+)", chat.pinned_message.text
                )
                if match:
                    target_ids.append(match.group(1))
        except (TelegramAPIError, OSError) as e:
            logger.debug("Chat inspect for user %s: %s", uid, e)

    for c_id in set(target_ids):
        try:
            tg_conn = await bot.get_business_connection(c_id)
            if (
                tg_conn
                and tg_conn.is_enabled
                and settings.is_user_allowed(tg_conn.user.id)
            ):
                conn_date = int(
                    tg_conn.date.timestamp()
                    if hasattr(tg_conn.date, "timestamp")
                    else tg_conn.date or time.time()
                )
                conn_model = BusinessConnectionModel(
                    connection_id=tg_conn.id,
                    user_id=tg_conn.user.id,
                    user_chat_id=tg_conn.user_chat_id,
                    can_reply=bool(tg_conn.can_reply),
                    is_enabled=bool(tg_conn.is_enabled),
                    connected_at=conn_date,
                )
                await repository.save_connection(conn_model)
                logger.info(
                    "Restored active business connection on startup: %s (user=%s)",
                    tg_conn.id,
                    tg_conn.user.id,
                )
        except TelegramAPIError as e:
            logger.warning("Could not restore startup connection %s: %s", c_id, e)


async def run_bot() -> None:
    """Main async entrypoint for the Telegram Business Watcher bot."""
    settings = load_settings()
    logger.info("Initializing Telegram Business Watcher...")

    db_path = Path(__file__).parent / settings.db_path
    database = Database(db_path)
    db_conn = await database.connect()

    redis_client = None
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis
            from redis.exceptions import RedisError

            redis_client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5,
            )
            await redis_client.ping()
            logger.info("Connected to Redis persistent cache.")
        except (RedisError, OSError, TimeoutError) as e:
            logger.warning("Redis persistent cache unavailable: %s", e)
            redis_client = None

    repository = BusinessRepository(db_conn, redis_client=redis_client)

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
    dp = create_dispatcher(repository, settings)

    try:
        bot_info = await bot.get_me()
        logger.info(
            "Bot started successfully: @%s (ID: %s, Name: %s)",
            bot_info.username,
            bot_info.id,
            bot_info.first_name,
        )
        await restore_startup_state(bot, repository, settings)
        logger.info("Listening for Telegram Business events and user commands...")

        await dp.start_polling(
            bot,
            repository=repository,
            settings=settings,
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
        if redis_client:
            with contextlib.suppress(OSError, TimeoutError):
                await redis_client.aclose()
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
