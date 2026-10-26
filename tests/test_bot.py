import asyncio
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from config.settings import Settings
from database.connection import Database
from database.models import (
    BusinessConnectionModel,
    BusinessMessageModel,
)
from database.repository import BusinessRepository
from keyboards.inline import get_back_keyboard, get_main_keyboard
from services.formatter import BusinessReportFormatter


def test_settings_initialization():
    """Verify settings initialization and defaults."""
    settings = Settings(
        bot_token="123456:TEST_TOKEN",
        db_path="custom.db",
        retention_days=30,
        allowed_user_ids=[965180470],
    )
    assert settings.bot_token == "123456:TEST_TOKEN"
    assert settings.db_path == "custom.db"
    assert settings.retention_days == 30
    assert settings.is_user_allowed(965180470) is True
    assert settings.is_user_allowed(111222) is False


def test_settings_allowed_user_ids_parsing():
    """Verify string parsing of comma-separated allowed user IDs."""
    settings = Settings(
        bot_token="123456:TEST_TOKEN",
        allowed_user_ids="965180470, 12345678",  # type: ignore
    )
    assert settings.is_user_allowed(965180470) is True
    assert settings.is_user_allowed(12345678) is True
    assert settings.is_user_allowed(99999999) is False


def test_database_and_repository():
    """Test business connection, message saving, edit recording and deletion in SQLite."""

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        repo = BusinessRepository(conn)

        try:
            # 1. Connection
            conn_model = BusinessConnectionModel(
                connection_id="conn_123",
                user_id=999888,
                user_chat_id=999888,
                can_reply=True,
                is_enabled=True,
                connected_at=1700000000,
            )
            await repo.save_connection(conn_model)

            fetched_conn = await repo.get_connection_by_id("conn_123")
            assert fetched_conn is not None
            assert fetched_conn.user_id == 999888
            assert fetched_conn.is_enabled is True

            user_conn = await repo.get_connection_by_user(999888)
            assert user_conn is not None
            assert user_conn.connection_id == "conn_123"

            # 2. Message saving
            msg = BusinessMessageModel(
                connection_id="conn_123",
                chat_id=111222,
                chat_title="Тестовый диалог",
                chat_username="test_dialog",
                message_id=101,
                sender_id=555666,
                sender_name="Иван Петров",
                sender_username="ivan_p",
                date=1700000100,
                text="Секретный текст",
                media_type="photo",
                media_file_id="AgACAgIAAxkBAAI...",
                is_deleted=False,
                deleted_at=None,
            )
            await repo.save_message(msg)

            retrieved_msg = await repo.get_message("conn_123", 111222, 101)
            assert retrieved_msg is not None
            assert retrieved_msg.text == "Секретный текст"
            assert retrieved_msg.media_type == "photo"

            # 3. Edit recording
            await repo.record_edit(
                connection_id="conn_123",
                chat_id=111222,
                message_id=101,
                old_text="Секретный текст",
                new_text="Измененный текст",
                edited_at=1700000150,
            )

            # 4. Message deletion
            deleted_msg = await repo.mark_deleted("conn_123", 111222, 101, 1700000200)
            assert deleted_msg is not None
            assert deleted_msg.is_deleted is True
            assert deleted_msg.deleted_at == 1700000200

            # 5. Stats
            stats = await repo.get_stats(999888)
            assert stats["total"] == 1
            assert stats["deleted"] == 1
            assert stats["edited"] == 1

            # 6. Cleanup
            deleted_count = await repo.cleanup_old(0)
            assert deleted_count >= 1
        finally:
            await db.close()

    asyncio.run(_run())


def test_formatter_deleted_report():
    """Verify deleted message report formatting with HTML markup."""
    msg = BusinessMessageModel(
        connection_id="conn_123",
        chat_id=111222,
        chat_title="Диалог с Марией",
        chat_username="maria_t",
        message_id=202,
        sender_id=777888,
        sender_name="Мария <Admin>",
        sender_username="maria_t",
        date=1700000000,
        text="Привет, это важное сообщение & тест",
        media_type="video_note",
        media_file_id="video_note_file_id_123",
        is_deleted=True,
        deleted_at=1700000045,
    )
    report = BusinessReportFormatter.format_deleted_report(msg, has_media=True)

    assert "УДАЛЁННОЕ СООБЩЕНИЕ" in report
    assert "Мария &lt;Admin&gt;" in report
    assert "45 сек." in report
    assert "Видеосообщение (кружок)" in report
    assert "восстановлено ниже" in report
    assert "&amp; тест" in report
    assert "<blockquote expandable>" in report


def test_formatter_edited_report():
    """Verify edited message report formatting."""
    msg = BusinessMessageModel(
        connection_id="conn_123",
        chat_id=111222,
        chat_title="Личные сообщения",
        chat_username=None,
        message_id=303,
        sender_id=123,
        sender_name="Павел Дуров",
        sender_username="durov",
        date=1700000000,
        text="Старый текст",
        media_type=None,
        media_file_id=None,
    )
    report = BusinessReportFormatter.format_edited_report(
        msg, new_text="Новый отредактированный текст", edited_at=1700000120
    )

    assert "ИЗМЕНЁННОЕ СООБЩЕНИЕ" in report
    assert "Павел Дуров" in report
    assert "2 мин." in report
    assert "Старый текст" in report
    assert "Новый отредактированный текст" in report


def test_inline_keyboards():
    """Verify inline keyboards have valid buttons."""
    kb_connected = get_main_keyboard(is_connected=True, bot_username="test_bot")
    assert any(
        "Подключён" in btn.text for row in kb_connected.inline_keyboard for btn in row
    )

    kb_disconnected = get_main_keyboard(is_connected=False, bot_username="test_bot")
    has_connect_btn = any(
        "Подключить бота" in btn.text and btn.url == "tg://settings/edit"
        for row in kb_disconnected.inline_keyboard
        for btn in row
    )
    assert has_connect_btn

    kb_back = get_back_keyboard()
    assert kb_back.inline_keyboard[0][0].callback_data == "btn_menu"

    kb_back_settings = get_back_keyboard(include_settings_link=True)
    assert any(
        btn.url == "tg://settings/edit"
        for row in kb_back_settings.inline_keyboard
        for btn in row
    )


def test_models_serialization():
    """Verify to_dict and from_dict for BusinessConnectionModel and BusinessMessageModel."""
    conn = BusinessConnectionModel(
        connection_id="conn_abc",
        user_id=12345,
        user_chat_id=12345,
        can_reply=True,
        is_enabled=True,
        connected_at=1700000000,
    )
    d_conn = conn.to_dict()
    restored_conn = BusinessConnectionModel.from_dict(d_conn)
    assert restored_conn.connection_id == "conn_abc"
    assert restored_conn.user_id == 12345
    assert restored_conn.is_enabled is True

    msg = BusinessMessageModel(
        connection_id="conn_abc",
        chat_id=777,
        message_id=999,
        sender_id=12345,
        sender_name="Alice",
        date=1700000500,
        text="Test cache",
        media_type="photo",
        media_file_id="photo_123",
    )
    d_msg = msg.to_dict()
    restored_msg = BusinessMessageModel.from_dict(d_msg)
    assert restored_msg.connection_id == "conn_abc"
    assert restored_msg.text == "Test cache"
    assert restored_msg.media_type == "photo"


def test_repository_with_mock_redis():
    """Verify repository dual-write and fallback recovery using simulated Redis cache."""

    class MockRedis:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, val, **kwargs):
            self.store[key] = val

        async def delete(self, key):
            self.store.pop(key, None)

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        mock_redis = MockRedis()
        repo = BusinessRepository(conn, redis_client=mock_redis)

        try:
            # 1. Save connection -> writes to SQLite + Redis
            conn_model = BusinessConnectionModel(
                connection_id="conn_redis_1",
                user_id=8888,
                user_chat_id=8888,
                can_reply=True,
                is_enabled=True,
                connected_at=1700000000,
            )
            await repo.save_connection(conn_model)
            assert "biz:conn:conn_redis_1" in mock_redis.store
            assert "biz:user_conn:8888" in mock_redis.store

            # 2. Simulate wiping SQLite table (like Render restart)
            await conn.execute("DELETE FROM business_connections")
            await conn.commit()

            # 3. Retrieve from repo -> falls back to Redis and restores to SQLite!
            recovered_conn = await repo.get_connection_by_user(8888)
            assert recovered_conn is not None
            assert recovered_conn.connection_id == "conn_redis_1"

            # 4. Save message -> writes to SQLite + Redis
            msg = BusinessMessageModel(
                connection_id="conn_redis_1",
                chat_id=444,
                message_id=555,
                sender_id=8888,
                sender_name="RedisUser",
                date=1700001000,
                text="Persistent message",
            )
            await repo.save_message(msg)
            msg_key = "biz:msg:conn_redis_1:444:555"
            assert msg_key in mock_redis.store

            # 5. Simulate wiping messages table
            await conn.execute("DELETE FROM business_messages")
            await conn.commit()

            # 6. Retrieve -> recovers from Redis!
            recovered_msg = await repo.get_message("conn_redis_1", 444, 555)
            assert recovered_msg is not None
            assert recovered_msg.text == "Persistent message"
        finally:
            await db.close()

    asyncio.run(_run())
