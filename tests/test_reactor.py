import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from aiogram.types import CallbackQuery, Message

from config.settings import Settings
from database.connection import Database
from database.models import BusinessConnectionModel, ReactorConfig
from database.repository import BusinessRepository
from handlers.business import on_business_message
from handlers.reactor import (
    REACTOR_EMOJI_PRESETS,
    cb_reactor,
    cmd_reactor,
    cmd_reactor_target,
    on_forwarded_target,
)
from handlers.user_commands import _get_welcome_text
from keyboards.inline import get_back_keyboard, get_main_keyboard
from middlewares.auth import AuthMiddleware
from services.reactor_mtproto import ReactorMTProtoClient
from services.reactor_service import apply_auto_reaction
from tests.test_resilience import DummyBot
from tests.test_resilience import MockRedis as FakeRedis

OWNER_ID = 965180470
SECOND_ALLOWED_ID = 226583258
OUTSIDER_ID = 999999


class DummyMTProtoClient:
    """Honest test double for ReactorMTProtoClient recording MTProto reactions."""

    def __init__(self) -> None:
        self.sent_reactions: list[dict[str, Any]] = []

    async def send_reaction(
        self, chat_id: int, message_id: int, emoji: str, is_big: bool = True
    ) -> bool:
        self.sent_reactions.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "emoji": emoji,
                "is_big": is_big,
            }
        )
        return True


def _make_event_message(
    user_id: int,
    text: str,
    msg_id: int = 10,
    chat_id: int | None = None,
    bot: DummyBot | None = None,
    forward_from_id: int | None = None,
) -> Any:
    target_chat_id = chat_id or user_id
    answered: list[dict[str, Any]] = []
    edited: list[dict[str, Any]] = []
    deleted: list[int] = []

    async def _answer(txt: str, **kwargs: Any) -> Any:
        answered.append({"text": txt, **kwargs})
        if bot:
            await bot.send_message(target_chat_id, txt, **kwargs)

    async def _edit_text(txt: str, **kwargs: Any) -> Any:
        edited.append({"text": txt, **kwargs})

    async def _delete() -> bool:
        deleted.append(msg_id)
        return True

    fwd_user = SimpleNamespace(id=forward_from_id) if forward_from_id else None
    empty_fields = dict.fromkeys(
        [
            "caption",
            "business_connection_id",
            "forward_from_chat",
            "forward_origin",
            "reply_to_message",
        ],
        None,
    )
    return SimpleNamespace(
        message_id=msg_id,
        from_user=SimpleNamespace(id=user_id, is_bot=False, username=f"u_{user_id}"),
        chat=SimpleNamespace(id=target_chat_id),
        text=text,
        forward_from=fwd_user,
        answer=_answer,
        edit_text=_edit_text,
        delete=_delete,
        answered=answered,
        edited=edited,
        deleted=deleted,
        **empty_fields,
    )


def _make_callback(
    user_id: int, data: str, panel_msg: Any, bot: DummyBot | None = None
) -> Any:
    answers: list[dict[str, Any]] = []

    async def _answer(text: str | None = None, **kwargs: Any) -> None:
        answers.append({"text": text, **kwargs})
        if text and bot:
            await bot.send_message(user_id, text, **kwargs)

    return SimpleNamespace(
        id="cb_1",
        from_user=SimpleNamespace(id=user_id, is_bot=False, username=f"u_{user_id}"),
        data=data,
        message=panel_msg,
        answer=_answer,
        answers=answers,
    )


def test_reactor_zero_knowledge_stealth():
    """Verifies non-owners (both allowed users and outsiders) receive zero responses on /reactor."""

    async def _run():
        settings = Settings(
            bot_token="123456:TEST",
            allowed_user_ids=[OWNER_ID, SECOND_ALLOWED_ID],
            reactor_owner_id=OWNER_ID,
        )
        middleware = AuthMiddleware()
        bot = DummyBot()
        handler_calls: list[int] = []

        async def _spy_handler(event: Any, data: dict[str, Any]) -> str:
            handler_calls.append(event.from_user.id)
            return "handled"

        # 1. Second allowed user & outsider sending /reactor or pressing reactor:* via AuthMiddleware
        for uid in (SECOND_ALLOWED_ID, OUTSIDER_ID):
            msg = Message.model_construct(
                message_id=1,
                date=datetime.now(tz=UTC),
                chat=SimpleNamespace(id=uid, type="private"),
                from_user=SimpleNamespace(id=uid, is_bot=False, username="user"),
                text="/reactor",
            )
            res_msg = await middleware(
                _spy_handler, msg, {"settings": settings, "bot": bot}
            )
            assert res_msg is None

            cb = CallbackQuery.model_construct(
                id="cb_stealth",
                from_user=SimpleNamespace(id=uid, is_bot=False, username="user"),
                chat_instance="ci_1",
                data="reactor:toggle_enabled",
            )
            res_cb = await middleware(
                _spy_handler, cb, {"settings": settings, "bot": bot}
            )
            assert res_cb is None

        assert len(handler_calls) == 0
        assert len(bot.sent_messages) == 0

        # 2. Welcome text and public keyboards contain no trace of /reactor
        welcome = _get_welcome_text("Иван", True, "watcher_bot")
        assert "reactor" not in welcome.lower()
        kb_main = str(get_main_keyboard(True, "watcher_bot"))
        kb_back = str(get_back_keyboard(True))
        assert "reactor" not in kb_main.lower()
        assert "reactor" not in kb_back.lower()

    asyncio.run(_run())


def test_reactor_control_panel_and_zwj_cycling():
    """Verifies /reactor controls, default disabled state, and ZWJ emoji cycling."""

    async def _run():
        db = Database(":memory:")
        sql_conn = await db.connect()
        fake_redis = FakeRedis()
        repo = BusinessRepository(sql_conn, redis_client=fake_redis)
        bot = DummyBot()
        settings = Settings(
            bot_token="123456:TEST",
            allowed_user_ids=[OWNER_ID, SECOND_ALLOWED_ID],
            reactor_owner_id=OWNER_ID,
        )

        try:
            cfg = await repo.get_reactor_config(OWNER_ID)
            assert cfg.enabled is False
            assert cfg.emoji == "❤️\u200d🔥"
            assert "\u200d" in cfg.emoji

            legacy = ReactorConfig.from_dict({"owner_id": OWNER_ID, "emoji": "❤️🔥"})
            assert legacy.emoji == "❤️\u200d🔥"
            assert "\u200d" in legacy.emoji

            cmd_msg = _make_event_message(OWNER_ID, "/reactor", msg_id=50, bot=bot)
            await cmd_reactor(cmd_msg, repo, settings)
            assert len(cmd_msg.answered) == 1
            assert "ВЫКЛ" in cmd_msg.answered[0]["text"]

            panel_msg = _make_event_message(OWNER_ID, "panel", msg_id=51, bot=bot)
            cb_toggle = _make_callback(
                OWNER_ID, "reactor:toggle_enabled", panel_msg, bot
            )
            await cb_reactor(cb_toggle, bot, repo, settings)
            cfg = await repo.get_reactor_config(OWNER_ID)
            assert cfg.enabled is True
            assert f"biz:reactor:{OWNER_ID}" in fake_redis.store

            seen_emojis: list[str] = []
            for _ in range(len(REACTOR_EMOJI_PRESETS)):
                cb_emoji = _make_callback(
                    OWNER_ID, "reactor:cycle_emoji", panel_msg, bot
                )
                await cb_reactor(cb_emoji, bot, repo, settings)
                seen_emojis.append((await repo.get_reactor_config(OWNER_ID)).emoji)

            assert seen_emojis == ["🔥", "❤️", "⚡", "👍", "❤️\u200d🔥"]
            assert "\u200d" in seen_emojis[-1]
        finally:
            await db.close()

    asyncio.run(_run())


def test_reactor_targets_hide_and_redis_self_healing():
    """Verifies target chat toggling, panel hide deletion, and Dual-Write Redis self-healing."""

    async def _run():
        db = Database(":memory:")
        sql_conn = await db.connect()
        fake_redis = FakeRedis()
        repo = BusinessRepository(sql_conn, redis_client=fake_redis)
        bot = DummyBot()
        settings = Settings(
            bot_token="123456:TEST",
            allowed_user_ids=[OWNER_ID, SECOND_ALLOWED_ID],
            reactor_owner_id=OWNER_ID,
        )

        try:
            cmd_msg = _make_event_message(OWNER_ID, "/reactor", msg_id=50, bot=bot)
            await cmd_reactor(cmd_msg, repo, settings)
            panel_msg = _make_event_message(OWNER_ID, "panel", msg_id=51, bot=bot)

            t_msg = _make_event_message(OWNER_ID, "/reactor_target 777888", bot=bot)
            await cmd_reactor_target(t_msg, repo, settings)
            assert 777888 in (await repo.get_reactor_config(OWNER_ID)).target_chat_ids

            fwd_msg = _make_event_message(
                OWNER_ID, "hi", bot=bot, forward_from_id=333444
            )
            await on_forwarded_target(fwd_msg, repo, settings)
            assert 333444 in (await repo.get_reactor_config(OWNER_ID)).target_chat_ids

            await cmd_reactor_target(t_msg, repo, settings)
            assert (
                777888 not in (await repo.get_reactor_config(OWNER_ID)).target_chat_ids
            )

            cb_hide = _make_callback(OWNER_ID, "reactor:hide", panel_msg, bot)
            await cb_reactor(cb_hide, bot, repo, settings)
            assert (OWNER_ID, 51) in bot.deleted_messages
            assert (OWNER_ID, 50) in bot.deleted_messages

            await sql_conn.execute("DELETE FROM reactor_settings")
            await sql_conn.commit()

            healed = await repo.get_reactor_config(OWNER_ID)
            assert healed.emoji == "❤️\u200d🔥"
            assert healed.target_chat_ids == [333444]

            cursor = await sql_conn.execute(
                "SELECT emoji FROM reactor_settings WHERE owner_id = ?",
                (OWNER_ID,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["emoji"] == "❤️\u200d🔥"
        finally:
            await db.close()

    asyncio.run(_run())


def _make_biz_msg(conn_id: str, chat_id: int, msg_id: int, sender_id: int) -> Any:
    keys = [
        "photo",
        "video",
        "video_note",
        "voice",
        "audio",
        "document",
        "animation",
        "sticker",
    ]
    empty_media = dict.fromkeys(keys, None)
    return SimpleNamespace(
        business_connection_id=conn_id,
        chat=SimpleNamespace(
            id=chat_id, title="Chat", full_name="Chat", username="chat"
        ),
        message_id=msg_id,
        from_user=SimpleNamespace(
            id=sender_id, first_name="User", last_name="", username="u"
        ),
        reply_to_message=None,
        text="Hello business",
        caption=None,
        date=datetime.fromtimestamp(1700000100, tz=UTC),
        **empty_media,
    )


def test_auto_reaction_isolation_and_incoming_filter():
    """Verifies reactions apply only to owner's connection, respect only_incoming, and support MTProto."""

    async def _run():
        db = Database(":memory:")
        sql_conn = await db.connect()
        repo = BusinessRepository(sql_conn, redis_client=FakeRedis())
        bot = DummyBot()
        settings = Settings(
            bot_token="123456:TEST",
            allowed_user_ids=[OWNER_ID, SECOND_ALLOWED_ID],
            reactor_owner_id=OWNER_ID,
        )

        try:
            for cid, uid in (
                ("conn_owner", OWNER_ID),
                ("conn_second", SECOND_ALLOWED_ID),
            ):
                await repo.save_connection(
                    BusinessConnectionModel(cid, uid, uid, True, True, 1700000000)
                )
            await repo.save_reactor_config(
                ReactorConfig(OWNER_ID, True, "❤️\u200d🔥", True, True)
            )

            # 1. Incoming message from interlocutor (555) in owner's chat -> gets ❤️‍🔥 reaction
            await on_business_message(
                _make_biz_msg("conn_owner", 555, 101, 555), bot, repo, settings
            )
            assert len(bot.reactions) == 1
            assert bot.reactions[0]["chat_id"] == 555
            assert bot.reactions[0]["message_id"] == 101
            assert bot.reactions[0]["reaction"][0].emoji == "❤️\u200d🔥"
            assert bot.reactions[0]["is_big"] is True

            # 2. Outgoing message from owner himself -> ignored when only_incoming=True
            await on_business_message(
                _make_biz_msg("conn_owner", 555, 102, OWNER_ID), bot, repo, settings
            )
            assert len(bot.reactions) == 1

            # 3. Incoming message in second allowed user's business connection -> strictly ignored!
            await on_business_message(
                _make_biz_msg("conn_second", 888, 201, 888), bot, repo, settings
            )
            assert len(bot.reactions) == 1

            # 4. MTProto bridge dispatches ❤️‍🔥 directly without Bot API set_message_reaction
            mtproto = DummyMTProtoClient()
            msg_mt = _make_biz_msg(
                "conn_owner", SECOND_ALLOWED_ID, 562718, SECOND_ALLOWED_ID
            )
            ok = await apply_auto_reaction(
                bot, msg_mt, repo, settings, OWNER_ID, mtproto_client=mtproto
            )
            assert ok is True
            assert mtproto.sent_reactions == [
                {
                    "chat_id": 226583258,
                    "message_id": 562718,
                    "emoji": "❤️\u200d🔥",
                    "is_big": True,
                }
            ]
            assert len(bot.reactions) == 1
        finally:
            await db.close()

    asyncio.run(_run())


def test_mtproto_client_retry_on_missing_peer():
    """Verifies ReactorMTProtoClient refreshes dialogs once when peer is missing in cache."""

    class FakeTelethonClient:
        def __init__(self) -> None:
            self.calls = 0
            self.dialogs_fetched: list[int] = []

        def is_connected(self) -> bool:
            return True

        async def is_user_authorized(self) -> bool:
            return True

        async def get_dialogs(self, limit: int = 100) -> list[Any]:
            self.dialogs_fetched.append(limit)
            return []

        async def __call__(self, request: Any) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Could not find the input entity")
            return True

        async def disconnect(self) -> None:
            pass

    async def _run():
        mt = ReactorMTProtoClient(12345, "hash_abc", "session_xyz")
        fake_tl = FakeTelethonClient()
        mt._client = fake_tl  # type: ignore[assignment]
        ok = await mt.send_reaction(226583258, 562718, "❤️\u200d🔥", is_big=True)
        assert ok is True
        assert fake_tl.calls == 2
        assert fake_tl.dialogs_fetched == [100, 50]
        await mt.close()

    asyncio.run(_run())
