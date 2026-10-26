import asyncio

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

from database.connection import Database
from database.models import BusinessConnectionModel, BusinessMessageModel
from database.repository import BusinessRepository
from handlers.user_commands import _get_welcome_text
from services.formatter import BusinessReportFormatter
from services.media import deliver_deleted_media, safe_telegram_call


class DummyBot:
    """Honest test spy recording delivered messages and media without MagicMock."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.sent_photos: list[dict] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})

    async def send_photo(self, chat_id: int, photo: str, **kwargs):
        self.sent_photos.append({"chat_id": chat_id, "photo": photo, **kwargs})


class MockRedis:
    """In-memory key-value dictionary simulating Redis persistence."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, val: str, **kwargs):
        self.store[key] = val

    async def delete(self, key: str):
        self.store.pop(key, None)


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
