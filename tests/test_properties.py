import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from database.connection import Database
from database.models import BusinessMessageModel, ReactorConfig
from database.repository import BusinessRepository
from handlers.reactor import REACTOR_EMOJI_PRESETS, cycle_reactor_emoji
from services.formatter import BusinessReportFormatter
from services.reactor_service import should_react_to_message

MEDIA_TYPES = [
    "photo",
    "video",
    "video_note",
    "voice",
    "audio",
    "document",
    "sticker",
    "animation",
]


@given(
    sender_name=st.text(min_size=0, max_size=3000),
    chat_title=st.text(min_size=0, max_size=3000),
    old_text=st.one_of(st.none(), st.text(min_size=0, max_size=4000)),
    new_text=st.one_of(st.none(), st.text(min_size=0, max_size=4000)),
    old_media_type=st.one_of(st.none(), st.sampled_from(MEDIA_TYPES)),
    new_media_type=st.one_of(st.none(), st.sampled_from(MEDIA_TYPES)),
    old_uid=st.one_of(st.none(), st.text(min_size=0, max_size=50)),
    new_uid=st.one_of(st.none(), st.text(min_size=0, max_size=50)),
)
@settings(max_examples=50)
def test_prop_format_edited_report(
    sender_name,
    chat_title,
    old_text,
    new_text,
    old_media_type,
    new_media_type,
    old_uid,
    new_uid,
):
    """Property test for format_edited_report: limits, tag balancing, and placeholders."""
    msg = BusinessMessageModel(
        connection_id="conn_prop",
        chat_id=123,
        chat_title=chat_title,
        message_id=456,
        sender_id=789,
        sender_name=sender_name,
        date=1700000000,
        text=old_text,
        media_type=old_media_type,
        media_file_unique_id=old_uid,
    )

    report = BusinessReportFormatter.format_edited_report(
        msg=msg,
        new_text=new_text,
        edited_at=1700000100,
        new_media_type=new_media_type,
        new_media_unique_id=new_uid,
    )

    # 1. Length invariant: strictly <= 4096 chars
    assert len(report) <= 4096

    # 2. Tag balance invariants
    assert report.count("<blockquote expandable>") == report.count("</blockquote>")
    assert report.count("<b>") == report.count("</b>")
    assert report.count("<i>") == report.count("</i>")

    # 3. Empty text unescaped placeholder invariants
    if not old_text:
        assert "<i>(нет текста)</i>" in report
        assert "&lt;i&gt;(нет текста)&lt;/i&gt;" not in report
    if not new_text:
        assert "<i>(нет текста)</i>" in report
        assert "&lt;i&gt;(нет текста)&lt;/i&gt;" not in report


@given(
    sender_name=st.text(min_size=0, max_size=3000),
    chat_title=st.text(min_size=0, max_size=3000),
    text=st.one_of(st.none(), st.text(min_size=0, max_size=5000)),
    media_type=st.one_of(st.none(), st.sampled_from(MEDIA_TYPES)),
)
@settings(max_examples=50)
def test_prop_format_deleted_report(sender_name, chat_title, text, media_type):
    """Property test for format_deleted_report: limits and tag balancing."""
    msg = BusinessMessageModel(
        connection_id="conn_prop_del",
        chat_id=123,
        chat_title=chat_title,
        message_id=456,
        sender_id=789,
        sender_name=sender_name,
        date=1700000000,
        text=text,
        media_type=media_type,
        media_file_id="del_file_id" if media_type else None,
    )

    report = BusinessReportFormatter.format_deleted_report(
        msg, has_media=bool(media_type)
    )

    assert len(report) <= 4096
    assert report.count("<blockquote expandable>") == report.count("</blockquote>")
    assert report.count("<b>") == report.count("</b>")
    assert report.count("<i>") == report.count("</i>")


@given(
    date_ts=st.integers(min_value=-10000, max_value=2500000000),
    action_ts=st.integers(min_value=-10000, max_value=2500000000),
)
@settings(max_examples=50)
def test_prop_chronology_and_time_delta(date_ts, action_ts):
    """Property test: time delta calculations never yield negative signs or crash."""
    msg = BusinessMessageModel(
        connection_id="conn_chrono",
        chat_id=1,
        message_id=1,
        sender_id=1,
        sender_name="User",
        date=date_ts,
        deleted_at=action_ts,
    )

    diff_str = BusinessReportFormatter._format_duration(action_ts - date_ts)
    assert "-" not in diff_str

    del_report = BusinessReportFormatter.format_deleted_report(msg)
    assert len(del_report) <= 4096

    edit_report = BusinessReportFormatter.format_edited_report(
        msg=msg, new_text="text", edited_at=action_ts
    )
    assert len(edit_report) <= 4096


@given(
    insert_ids=st.lists(
        st.integers(min_value=1, max_value=500), min_size=0, max_size=15
    ),
    delete_ids=st.lists(
        st.integers(min_value=-50, max_value=600), min_size=0, max_size=20
    ),
)
@settings(max_examples=30)
def test_prop_batch_deletion_and_repository(insert_ids, delete_ids):
    """Property test: repository handles arbitrary deletion lists and duplicates safely."""

    async def _run():
        db = Database(":memory:")
        conn = await db.connect()
        repo = BusinessRepository(conn)

        try:
            for m_id in set(insert_ids):
                msg = BusinessMessageModel(
                    connection_id="conn_batch",
                    chat_id=99,
                    message_id=m_id,
                    sender_id=10,
                    sender_name="Tester",
                    date=1700000000,
                    text=f"Msg {m_id}",
                )
                await repo.save_message(msg)

            for d_id in delete_ids:
                res = await repo.mark_deleted("conn_batch", 99, d_id, 1700000050)
                if d_id in insert_ids:
                    assert res is not None
                    assert res.is_deleted is True
                else:
                    assert res is None
        finally:
            await db.close()

    asyncio.run(_run())


@given(
    owner_id=st.integers(min_value=1, max_value=10**12),
    enabled=st.booleans(),
    emoji=st.sampled_from(["❤️🔥", "❤️\u200d🔥", "🔥", "❤️", "⚡", "👍", "unknown"]),
    is_big=st.booleans(),
    only_incoming=st.booleans(),
    target_chat_ids=st.lists(
        st.integers(min_value=-(10**12), max_value=10**12), max_size=20
    ),
    updated_at=st.integers(min_value=1, max_value=2500000000),
)
@settings(max_examples=50)
def test_prop_reactor_config_and_emoji_cycling(
    owner_id, enabled, emoji, is_big, only_incoming, target_chat_ids, updated_at
):
    """Property test: ReactorConfig serialization, ZWJ normalization, and emoji preset cycle."""
    cfg = ReactorConfig.from_dict(
        {
            "owner_id": owner_id,
            "enabled": enabled,
            "emoji": emoji,
            "is_big": is_big,
            "only_incoming": only_incoming,
            "target_chat_ids": target_chat_ids,
            "updated_at": updated_at,
        }
    )
    assert cfg.emoji != "❤️🔥"
    if emoji in ("❤️🔥", "❤️\u200d🔥"):
        assert cfg.emoji == "❤️\u200d🔥"
        assert "\u200d" in cfg.emoji

    restored = ReactorConfig.from_dict(cfg.to_dict())
    assert restored == cfg

    next_emoji = cycle_reactor_emoji(emoji)
    assert next_emoji in REACTOR_EMOJI_PRESETS
    if emoji in REACTOR_EMOJI_PRESETS:
        cur = emoji
        for _ in range(len(REACTOR_EMOJI_PRESETS)):
            cur = cycle_reactor_emoji(cur)
        assert cur == emoji


@given(
    enabled=st.booleans(),
    only_incoming=st.booleans(),
    target_chat_ids=st.lists(st.integers(min_value=-1000, max_value=1000), max_size=10),
    conn_user_id=st.integers(min_value=1, max_value=1000),
    owner_id=st.integers(min_value=1, max_value=1000),
    chat_id=st.integers(min_value=-1000, max_value=1000),
    sender_id=st.one_of(st.none(), st.integers(min_value=1, max_value=1000)),
)
@settings(max_examples=100)
def test_prop_should_react_to_message(
    enabled, only_incoming, target_chat_ids, conn_user_id, owner_id, chat_id, sender_id
):
    """Property test: should_react_to_message enforces owner isolation and filtering invariants."""
    cfg = ReactorConfig(
        owner_id=owner_id,
        enabled=enabled,
        only_incoming=only_incoming,
        target_chat_ids=target_chat_ids,
    )
    decision = should_react_to_message(cfg, conn_user_id, owner_id, chat_id, sender_id)

    if (
        not enabled
        or conn_user_id != owner_id
        or target_chat_ids
        and chat_id not in target_chat_ids
        or only_incoming
        and sender_id == owner_id
    ):
        assert decision is False
    else:
        assert decision is True
