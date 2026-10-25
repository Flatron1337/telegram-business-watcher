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
        bot_token="123456:TEST_TOKEN", db_path="custom.db", retention_days=30
    )
    assert settings.bot_token == "123456:TEST_TOKEN"
    assert settings.db_path == "custom.db"
    assert settings.retention_days == 30


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
        "Подключить бота" in btn.text
        for row in kb_disconnected.inline_keyboard
        for btn in row
    )
    assert has_connect_btn

    kb_back = get_back_keyboard()
    assert kb_back.inline_keyboard[0][0].callback_data == "btn_menu"
