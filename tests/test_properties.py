import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from database.connection import Database
from database.models import BusinessMessageModel
from database.repository import BusinessRepository
from services.formatter import BusinessReportFormatter

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
