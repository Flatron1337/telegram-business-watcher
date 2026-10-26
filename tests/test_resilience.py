import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile

from config.settings import Settings
from database.connection import Database
from database.models import BusinessConnectionModel, BusinessMessageModel
from database.repository import BusinessRepository
from handlers.business import on_business_message
from handlers.user_commands import _get_welcome_text
from services.formatter import BusinessReportFormatter
from services.media import deliver_deleted_media, safe_telegram_call


class DummyBot:
    """Honest test spy recording delivered messages and media without MagicMock."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.sent_photos: list[dict] = []
        self.download_exception: Exception | None = None

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})

    async def send_photo(self, chat_id: int, photo: Any, **kwargs):
        self.sent_photos.append({"chat_id": chat_id, "photo": photo, **kwargs})

    async def download(self, file_id: str, destination=None):
        if self.download_exception:
            raise self.download_exception
        if destination:
            destination.write(b"fake_image_content_bytes")
            destination.seek(0)
        return destination


class MockRedis:
    """In-memory key-value dictionary simulating Redis persistence."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, val: str, **kwargs):
        self.store[key] = val

    async def delete(self, key: str):
        self.store.pop(key, None)

    async def sismember(self, key: str, member: str) -> bool:
        return member in self.sets.get(key, set())

    async def sadd(self, key: str, member: str):
        self.sets.setdefault(key, set()).add(member)


def test_deliver_deleted_media_long_report_no_caption_limit():
    """Confirms deleted media sends full HTML report via send_message and media below without caption."""

    async def _run():
        bot = DummyBot()
        long_text = "Очень длинный текст сообщения для проверки лимита подписи. " * 50
        msg = BusinessMessageModel(
            connection_id="conn_del_long",
            chat_id=111,
            chat_title="Чат с фото",
            message_id=555,
            sender_id=777,
            sender_name="Даниил",
            date=1700000000,
            text=long_text,
            media_type="photo",
            media_file_id="photo_del_1",
            media_file_unique_id="photo_del_uid_1",
        )
        report = BusinessReportFormatter.format_deleted_report(msg, has_media=True)
        assert len(report) > 1200

        await deliver_deleted_media(bot, 777, msg, report)

        # 1. Full report is delivered as a text message (never truncated by 1024-char caption limit)
        assert len(bot.sent_messages) == 1
        assert bot.sent_messages[0]["chat_id"] == 777
        assert "УДАЛЁННОЕ СООБЩЕНИЕ" in bot.sent_messages[0]["text"]
        assert len(bot.sent_messages[0]["text"]) > 1200

        # 2. Photo attachment is delivered below without caption
        assert len(bot.sent_photos) == 1
        assert bot.sent_photos[0]["chat_id"] == 777
        assert bot.sent_photos[0]["photo"] == "photo_del_1"
        assert bot.sent_photos[0]["caption"] is None

    asyncio.run(_run())


def test_safe_telegram_call_handles_flood_wait_and_network_error(monkeypatch):
    """Verifies safe_telegram_call automatically handles TelegramRetryAfter and TelegramNetworkError."""

    async def _run():
        slept_times: list[float] = []

        async def fake_sleep(secs):
            slept_times.append(secs)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        calls_count = 0

        async def flaky_api(target_id: int):
            nonlocal calls_count
            calls_count += 1
            if calls_count == 1:
                raise TelegramRetryAfter(
                    method="sendMessage", message="Flood", retry_after=0.5
                )
            if calls_count == 2:
                raise TelegramNetworkError(method="sendMessage", message="Glitch")
            return f"delivered_to_{target_id}"

        result = await safe_telegram_call(flaky_api, 999)
        assert result == "delivered_to_999"
        assert calls_count == 3
        assert len(slept_times) == 2
        assert slept_times[0] == 1.5  # retry_after (0.5) + 1

    asyncio.run(_run())


def test_mark_deleted_persists_in_sqlite_after_redis_recovery():
    """Verifies mark_deleted updates SQLite after recovery from Redis even if table was cleared."""

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        mock_redis = MockRedis()
        repo = BusinessRepository(conn, redis_client=mock_redis)

        try:
            # 1. Setup connection and message in SQLite and Redis
            conn_model = BusinessConnectionModel(
                connection_id="conn_resilience",
                user_id=123,
                user_chat_id=123,
                can_reply=True,
                is_enabled=True,
                connected_at=1700000000,
            )
            await repo.save_connection(conn_model)

            msg = BusinessMessageModel(
                connection_id="conn_resilience",
                chat_id=10,
                message_id=20,
                sender_id=123,
                sender_name="User",
                date=1700000000,
                text="Message to delete",
            )
            await repo.save_message(msg)
            assert msg.id is not None

            # 2. Simulate wiping SQLite table (container reboot / clean workspace)
            await conn.execute("DELETE FROM business_messages")
            await conn.commit()

            # 3. mark_deleted restores message from Redis and updates SQLite composite key
            deleted_msg = await repo.mark_deleted("conn_resilience", 10, 20, 1700000050)
            assert deleted_msg is not None
            assert deleted_msg.is_deleted is True

            # 4. Verify get_stats reports deleted message count in SQLite
            stats = await repo.get_stats(123)
            assert stats["total"] == 1
            assert stats["deleted"] == 1

            # 5. Verify direct SQLite record state
            cursor = await conn.execute(
                "SELECT is_deleted, deleted_at FROM business_messages WHERE message_id = 20"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["is_deleted"] == 1
            assert row["deleted_at"] == 1700000050
        finally:
            await db.close()

    asyncio.run(_run())


def test_welcome_text_escapes_html_and_attachment_deleted_branch():
    """Verifies welcome text escapes user name and formatter reports removed attachments."""
    # 1. HTML escaping in welcome text
    welcome = _get_welcome_text("Алекс <Admin> & Co", True, "test_bot")
    assert "&lt;Admin&gt;" in welcome
    assert "&amp;" in welcome
    assert "<Admin>" not in welcome

    # 2. Attachment deletion scenario (has_old and not has_new)
    old_msg = BusinessMessageModel(
        connection_id="conn_del_att",
        chat_id=1,
        message_id=1,
        sender_id=1,
        sender_name="User",
        date=1700000000,
        text="Video was removed",
        media_type="video",
        media_file_id="vid_1",
        media_file_unique_id="uid_1",
    )
    report = BusinessReportFormatter.format_edited_report(
        msg=old_msg,
        new_text="Video was removed",
        edited_at=1700000010,
        new_media_type=None,
        new_media_unique_id=None,
    )
    assert "(удалено, файл ниже)" in report
    assert "🎬 Видеозапись" in report


def _build_view_once_messages():
    empty_media = dict.fromkeys(
        ["video", "video_note", "voice", "audio", "document", "animation", "sticker"],
        None,
    )
    chat = SimpleNamespace(
        id=555, title="Secret Chat", full_name="Secret Chat", username=None
    )
    reply = SimpleNamespace(
        message_id=999,
        chat=chat,
        photo=[SimpleNamespace(file_id="vo_photo_id", file_unique_id="vo_photo_uid")],
        has_protected_content=True,
        from_user=SimpleNamespace(
            id=200, first_name="Agent", last_name="007", username="bond"
        ),
        date=datetime.fromtimestamp(1700000000, tz=UTC),
        text=None,
        caption="Top secret snapshot",
        **empty_media,
    )
    user = SimpleNamespace(id=100, first_name="Me", last_name="", username="me")
    msg1 = SimpleNamespace(
        business_connection_id="conn_vo",
        chat=chat,
        message_id=1001,
        from_user=user,
        reply_to_message=reply,
        text="Catch it!",
        caption=None,
        photo=None,
        date=datetime.fromtimestamp(1700000010, tz=UTC),
        **empty_media,
    )
    msg2 = SimpleNamespace(
        business_connection_id="conn_vo",
        chat=chat,
        message_id=1002,
        from_user=user,
        reply_to_message=reply,
        text="Second reply to same view-once message",
        caption=None,
        photo=None,
        date=datetime.fromtimestamp(1700000020, tz=UTC),
        **empty_media,
    )
    return reply, msg1, msg2


async def _setup_test_env(conn_id: str = "conn_vo"):
    db = Database(":memory:")
    sql_conn = await db.connect()
    repo = BusinessRepository(sql_conn, MockRedis())
    conn = BusinessConnectionModel(
        connection_id=conn_id,
        user_id=100,
        user_chat_id=100,
        can_reply=True,
        is_enabled=True,
        connected_at=1700000000,
    )
    await repo.save_connection(conn)
    settings = Settings(
        bot_token="123456:ABC-DEF",
        allowed_user_ids=[100],
        allowed_usernames=[],
    )
    return db, repo, settings


def test_view_once_media_interception_via_reply():
    """Verifies reply to view-once media downloads to RAM, sends card, and deduplicates in DB."""

    async def _run():
        db, repo, settings = await _setup_test_env("conn_vo")
        bot = DummyBot()

        try:
            _reply, msg1, msg2 = _build_view_once_messages()
            await on_business_message(msg1, bot, repo, settings)

            # 1. Report card assertions
            assert len(bot.sent_messages) == 1
            card = bot.sent_messages[0]["text"]
            assert "🔥 <b>ПЕРЕХВАЧЕНО ОДНОРАЗОВОЕ МЕДИА</b>" in card
            assert "Agent 007" in card
            assert "Top secret snapshot" in card
            assert "🖼 Фотография" in card

            # 2. Re-uploaded media assertions
            assert len(bot.sent_photos) == 1
            photo_file = bot.sent_photos[0]["photo"]
            assert isinstance(photo_file, BufferedInputFile)
            assert photo_file.data == b"fake_image_content_bytes"
            assert photo_file.filename == "view_once_555_999.jpg"

            # 3. Database persistence check
            saved = await repo.get_message("conn_vo", 555, 999)
            assert saved is not None
            assert saved.media_file_id == "vo_photo_id"
            assert saved.media_type == "photo"

            # 4. Duplicate reply deduplication check
            bot.sent_messages.clear()
            bot.sent_photos.clear()
            await on_business_message(msg2, bot, repo, settings)

            assert len(bot.sent_messages) == 0
            assert len(bot.sent_photos) == 0
        finally:
            await db.close()

    asyncio.run(_run())


def test_view_once_media_large_file_fallback_on_download_error():
    """Verifies fallback sends report card and attempts send_media_attachment by file_id on >20MB error."""

    async def _run():
        db, repo, settings = await _setup_test_env("conn_vo_large")
        bot = DummyBot()
        bot.download_exception = TelegramAPIError(
            method="downloadFile", message="Bad Request: file is too big"
        )

        try:
            _reply, msg1, _ = _build_view_once_messages()
            msg1.business_connection_id = "conn_vo_large"

            await on_business_message(msg1, bot, repo, settings)

            # 1. HTML card sent despite download failure
            assert len(bot.sent_messages) == 1
            assert (
                "🔥 <b>ПЕРЕХВАЧЕНО ОДНОРАЗОВОЕ МЕДИА</b>"
                in bot.sent_messages[0]["text"]
            )

            # 2. Fallback to direct file_id dispatch
            assert len(bot.sent_photos) == 1
            assert bot.sent_photos[0]["photo"] == "vo_photo_id"

            # 3. Message persisted in DB to avoid duplicate loops
            saved = await repo.get_message("conn_vo_large", 555, 999)
            assert saved is not None
        finally:
            await db.close()

    asyncio.run(_run())
