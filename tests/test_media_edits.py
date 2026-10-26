import asyncio

from config.settings import Settings
from database.connection import Database
from database.models import BusinessMessageModel
from database.repository import BusinessRepository
from middlewares.auth import AuthMiddleware
from services.formatter import BusinessReportFormatter
from services.media import deliver_edited_media


class DummyBot:
    """Honest test spy recording delivered messages and media without MagicMock."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.sent_photos: list[dict] = []
        self.sent_videos: list[dict] = []
        self.sent_documents: list[dict] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})

    async def send_photo(self, chat_id: int, photo: str, **kwargs):
        self.sent_photos.append({"chat_id": chat_id, "photo": photo, **kwargs})

    async def send_video(self, chat_id: int, video: str, **kwargs):
        self.sent_videos.append({"chat_id": chat_id, "video": video, **kwargs})

    async def send_document(self, chat_id: int, document: str, **kwargs):
        self.sent_documents.append({"chat_id": chat_id, "document": document, **kwargs})


def test_scenario_a_caption_added_to_video():
    """Scenario A: caption added to existing video without replacing the file."""

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        repo = BusinessRepository(conn)
        bot = DummyBot()

        try:
            old_msg = BusinessMessageModel(
                connection_id="conn_sec_a",
                chat_id=12345,
                chat_title="Тестовый чат",
                message_id=101,
                sender_id=777,
                sender_name="Алекс",
                date=1700000000,
                text=None,
                media_type="video",
                media_file_id="file_vid_orig",
                media_file_unique_id="uid_vid_orig",
            )
            await repo.save_message(old_msg)

            # 1. Check formatted report has unescaped (нет текста) and video info
            report = BusinessReportFormatter.format_edited_report(
                msg=old_msg,
                new_text="Ку ку",
                edited_at=1700000060,
                new_media_type="video",
                new_media_unique_id="uid_vid_orig",
            )
            assert "<i>(нет текста)</i>" in report
            assert "&lt;i&gt;" not in report
            assert "Ку ку" in report
            assert "🎬 Видеозапись" in report
            assert "(восстановлено ниже)" in report

            # 2. Check media delivery sends original file once (Scenario A)
            await deliver_edited_media(
                bot=bot,
                chat_id=old_msg.sender_id,
                cached=old_msg,
                new_media_type="video",
                new_media_file_id="file_vid_orig",
                new_media_file_unique_id="uid_vid_orig",
            )
            assert len(bot.sent_videos) == 1
            assert bot.sent_videos[0]["video"] == "file_vid_orig"
            assert bot.sent_videos[0]["caption"] is None
        finally:
            await db.close()

    asyncio.run(_run())


def test_scenario_b_video_replaced_no_caption():
    """Scenario B: video file replaced with different unique_id when both captions are None."""

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        repo = BusinessRepository(conn)
        bot = DummyBot()

        try:
            cached_msg = BusinessMessageModel(
                connection_id="conn_sec_b1",
                chat_id=23456,
                chat_title="ВидеоЧат",
                message_id=202,
                sender_id=888,
                sender_name="Борис",
                date=1700001000,
                text=None,
                media_type="video",
                media_file_id="vid_old_id",
                media_file_unique_id="vid_old_uid",
            )
            await repo.save_message(cached_msg)

            # Formatter card reflects replacement
            report = BusinessReportFormatter.format_edited_report(
                msg=cached_msg,
                new_text=None,
                edited_at=1700001050,
                new_media_type="video",
                new_media_unique_id="vid_new_uid",
            )
            assert "Вложение заменено" in report
            assert "🎬 Видеозапись ➔ 🎬 Видеозапись" in report

            # Dispatch both media files: old first, then new
            await deliver_edited_media(
                bot=bot,
                chat_id=cached_msg.sender_id,
                cached=cached_msg,
                new_media_type="video",
                new_media_file_id="vid_new_id",
                new_media_file_unique_id="vid_new_uid",
            )
            assert len(bot.sent_videos) == 2
            assert bot.sent_videos[0]["video"] == "vid_old_id"
            assert "Было" in bot.sent_videos[0]["caption"]
            assert bot.sent_videos[1]["video"] == "vid_new_id"
            assert "Стало" in bot.sent_videos[1]["caption"]

            # Save edit and verify state in repository
            await repo.record_edit(
                connection_id="conn_sec_b1",
                chat_id=23456,
                message_id=202,
                old_text=None,
                new_text=None,
                edited_at=1700001050,
                new_media_type="video",
                new_media_file_id="vid_new_id",
                new_media_file_unique_id="vid_new_uid",
            )
            updated = await repo.get_message("conn_sec_b1", 23456, 202)
            assert updated is not None
            assert updated.media_file_unique_id == "vid_new_uid"
        finally:
            await db.close()

    asyncio.run(_run())


def test_scenario_b_video_replaced_same_caption():
    """Scenario B: video file replaced when caption is identical before and after."""

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        repo = BusinessRepository(conn)
        bot = DummyBot()

        try:
            cached_msg = BusinessMessageModel(
                connection_id="conn_sec_b2",
                chat_id=34567,
                chat_title="ТестЧат",
                message_id=303,
                sender_id=999,
                sender_name="Виктор",
                date=1700002000,
                text="Тест",
                media_type="video",
                media_file_id="vid_file_1",
                media_file_unique_id="vid_unique_1",
            )
            await repo.save_message(cached_msg)

            # Change detector conditions
            text_changed = (cached_msg.text or "") != "Тест"
            media_type_changed = (cached_msg.media_type or None) != "video"
            media_file_changed = (
                cached_msg.media_file_unique_id or None
            ) != "vid_unique_2"
            assert not text_changed
            assert not media_type_changed
            assert media_file_changed
            assert text_changed or media_type_changed or media_file_changed

            # Report and delivery check
            report = BusinessReportFormatter.format_edited_report(
                msg=cached_msg,
                new_text="Тест",
                edited_at=1700002020,
                new_media_type="video",
                new_media_unique_id="vid_unique_2",
            )
            assert "Вложение заменено" in report

            await deliver_edited_media(
                bot=bot,
                chat_id=cached_msg.sender_id,
                cached=cached_msg,
                new_media_type="video",
                new_media_file_id="vid_file_2",
                new_media_file_unique_id="vid_unique_2",
            )
            assert len(bot.sent_videos) == 2
            assert bot.sent_videos[0]["video"] == "vid_file_1"
            assert bot.sent_videos[1]["video"] == "vid_file_2"
        finally:
            await db.close()

    asyncio.run(_run())


def test_media_type_change_video_to_photo():
    """Verifies media type change (video -> photo) formatting and delivery."""

    async def _run():
        bot = DummyBot()
        cached_msg = BusinessMessageModel(
            connection_id="conn_type_change",
            chat_id=45678,
            chat_title="МедиаЧат",
            message_id=404,
            sender_id=555,
            sender_name="Дмитрий",
            date=1700003000,
            text="Смотри",
            media_type="video",
            media_file_id="vid_file_abc",
            media_file_unique_id="vid_uid_abc",
        )

        # 1. Video -> Photo replacement report
        report = BusinessReportFormatter.format_edited_report(
            msg=cached_msg,
            new_text="Смотри фото",
            edited_at=1700003030,
            new_media_type="photo",
            new_media_unique_id="photo_uid_xyz",
        )
        assert "🎬 Видеозапись ➔ 🖼 Фотография" in report

        # 2. Both media delivered
        await deliver_edited_media(
            bot=bot,
            chat_id=cached_msg.sender_id,
            cached=cached_msg,
            new_media_type="photo",
            new_media_file_id="photo_file_xyz",
            new_media_file_unique_id="photo_uid_xyz",
        )
        assert len(bot.sent_videos) == 1
        assert bot.sent_videos[0]["video"] == "vid_file_abc"
        assert len(bot.sent_photos) == 1
        assert bot.sent_photos[0]["photo"] == "photo_file_xyz"

    asyncio.run(_run())


def test_auth_middleware_bot_and_service_messages_ignored():
    """Confirms bot service messages (is_bot=True) are silently ignored while humans are checked."""

    async def _run():
        from datetime import UTC, datetime

        from aiogram.types import Chat, Message, User

        middleware = AuthMiddleware()
        settings = Settings(
            bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            allowed_user_ids=[965180470],
        )

        handler_called = False

        async def dummy_handler(event, data):
            nonlocal handler_called
            handler_called = True
            return "ok"

        # A) Service message from bot itself (e.g. pinned message alert)
        bot_msg = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=8853113703, type="private"),
            from_user=User(id=8853113703, is_bot=True, first_name="Bot"),
        )
        res_bot = await middleware(dummy_handler, bot_msg, {"settings": settings})
        assert res_bot is None
        assert not handler_called

        # B) Unauthorized human user (is_bot=False) gets rejected
        human_answered = False

        async def fake_answer(text: str, **kwargs):
            nonlocal human_answered
            human_answered = True

        human_msg = Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(id=9999999, type="private"),
            from_user=User(id=9999999, is_bot=False, first_name="Stranger"),
        )
        object.__setattr__(human_msg, "answer", fake_answer)

        res_human = await middleware(dummy_handler, human_msg, {"settings": settings})
        assert res_human is None
        assert not handler_called
        assert human_answered

    asyncio.run(_run())
