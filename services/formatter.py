import html
from datetime import UTC, datetime
from typing import ClassVar

from database.models import BusinessMessageModel


class BusinessReportFormatter:
    """Builds rich, elegant HTML cards for Telegram Business audit logs."""

    DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━"
    MAX_MESSAGE_LENGTH = 4096
    TRUNCATION_MARKER = "... <i>[обрезано]</i>"
    EMPTY_TEXT_PLACEHOLDER = "<i>(нет текста)</i>"

    MEDIA_ICONS: ClassVar[dict[str, str]] = {
        "photo": "🖼 Фотография",
        "video": "🎬 Видеозапись",
        "video_note": "⭕️ Видеосообщение (кружок)",
        "voice": "🎙 Голосовое сообщение",
        "audio": "🎵 Аудиозапись",
        "document": "📄 Документ / Файл",
        "sticker": "🎭 Стикер",
        "animation": "🎞 GIF анимация",
        "contact": "👤 Контакт",
        "location": "📍 Геолокация",
    }

    @staticmethod
    def _format_date(timestamp: int) -> str:
        safe_ts = max(0, min(timestamp, 253402300799))
        try:
            return datetime.fromtimestamp(safe_ts, tz=UTC).strftime("%d.%m.%Y %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return "01.01.1970 00:00:00"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        safe_seconds = max(0, int(seconds))
        if safe_seconds == 0:
            return "мгновенно"
        if safe_seconds < 60:
            return f"{safe_seconds} сек."
        if safe_seconds < 3600:
            m, s = divmod(safe_seconds, 60)
            return f"{m} мин. {s} сек." if s else f"{m} мин."
        h, rem = divmod(safe_seconds, 3600)
        m = rem // 60
        if h < 24:
            return f"{h} ч. {m} мин." if m else f"{h} ч."
        d, h = divmod(h, 24)
        return f"{d} дн. {h} ч."

    @classmethod
    def _format_user(cls, user_id: int, name: str, username: str | None) -> str:
        safe_name = (name or "Пользователь")[:128]
        name_esc = html.escape(safe_name)
        user_link = f'<a href="tg://user?id={user_id}"><b>{name_esc}</b></a>'
        if username:
            safe_user = username[:64]
            return f"{user_link} (@{html.escape(safe_user)})"
        return user_link

    @classmethod
    def _escape_and_truncate(cls, text: str | None, max_len: int) -> str:
        """Escapes raw user text and ensures the result does not exceed max_len.

        If text is None or empty, returns unescaped EMPTY_TEXT_PLACEHOLDER.
        """
        if not text:
            return cls.EMPTY_TEXT_PLACEHOLDER

        escaped = html.escape(text)
        if len(escaped) <= max_len:
            return escaped

        marker_len = len(cls.TRUNCATION_MARKER)
        target_len = max(0, max_len - marker_len)
        if target_len <= 0:
            return cls.TRUNCATION_MARKER

        low = 0
        high = len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            cand = html.escape(text[:mid])
            if len(cand) <= target_len:
                best = cand
                low = mid + 1
            else:
                high = mid - 1

        return f"{best.rstrip()}{cls.TRUNCATION_MARKER}"

    @classmethod
    def format_deleted_report(
        cls, msg: BusinessMessageModel, has_media: bool = False
    ) -> str:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        del_ts = msg.deleted_at or now_ts
        lifetime = max(0, int(del_ts - msg.date))
        lifetime_str = cls._format_duration(lifetime)

        sender_str = cls._format_user(
            msg.sender_id, msg.sender_name, msg.sender_username
        )
        chat_name = html.escape((msg.chat_title or "Личный диалог")[:128])

        header_lines = [
            "🗑 <b>УДАЛЁННОЕ СООБЩЕНИЕ</b>",
            cls.DIVIDER,
            f"👤 <b>Собеседник:</b> {sender_str}",
            f"💬 <b>Чат:</b> <b>{chat_name}</b> · <i>Личные сообщения</i>",
            (
                f"🆔 <b>ID сообщения:</b> <code>{msg.message_id}</code> · "
                f"<b>ID автора:</b> <code>{msg.sender_id}</code>"
            ),
            "",
            "⏱ <b>Хронология:</b>",
            f" ├ 🕒 Отправлено: <code>{cls._format_date(msg.date)}</code>",
            f" └ 🗑 Удалено: <code>{cls._format_date(del_ts)}</code> <i>(спустя {lifetime_str})</i>",
        ]

        if msg.media_type:
            media_label = cls.MEDIA_ICONS.get(msg.media_type, f"📎 {msg.media_type}")
            status_text = "<i>(восстановлено ниже)</i>" if has_media else ""
            header_lines.extend(
                ["", f"📎 <b>Вложение:</b> {media_label} {status_text}"]
            )

        if not msg.text:
            return "\n".join(header_lines)

        header_str = "\n".join(header_lines)
        block_overhead = len(
            f"\n{cls.DIVIDER}\n💬 <b>Содержимое:</b>\n<blockquote expandable></blockquote>"
        )
        budget = max(50, cls.MAX_MESSAGE_LENGTH - len(header_str) - block_overhead)

        escaped_content = cls._escape_and_truncate(msg.text, budget)
        body_lines = [
            cls.DIVIDER,
            "💬 <b>Содержимое:</b>",
            f"<blockquote expandable>{escaped_content}</blockquote>",
        ]

        return f"{header_str}\n" + "\n".join(body_lines)

    @classmethod
    def _format_attachment_change(
        cls,
        msg: BusinessMessageModel,
        new_media_type: str | None,
        new_media_unique_id: str | None,
    ) -> list[str]:
        has_old = bool(msg.media_type)
        has_new = bool(new_media_type)
        if not (has_old or has_new):
            return []

        same_type = msg.media_type == new_media_type
        same_uid = (
            bool(new_media_unique_id)
            and new_media_unique_id == msg.media_file_unique_id
        )
        is_same = (
            has_old
            and has_new
            and same_type
            and (
                same_uid
                or (new_media_unique_id is None and msg.media_file_unique_id is None)
            )
        )

        if is_same:
            lbl = cls.MEDIA_ICONS.get(msg.media_type or "", f"📎 {msg.media_type}")
            return ["", f"📎 <b>Вложение:</b> {lbl} <i>(восстановлено ниже)</i>"]
        if has_old and has_new:
            o_lbl = cls.MEDIA_ICONS.get(msg.media_type or "", f"📎 {msg.media_type}")
            n_lbl = cls.MEDIA_ICONS.get(new_media_type or "", f"📎 {new_media_type}")
            return [
                "",
                f"📎 <b>Вложение заменено:</b> {o_lbl} ➔ {n_lbl} <i>(файлы ниже)</i>",
            ]
        if has_old and not has_new:
            o_lbl = cls.MEDIA_ICONS.get(msg.media_type or "", f"📎 {msg.media_type}")
            return ["", f"📎 <b>Вложение:</b> {o_lbl} <i>(удалено, файл ниже)</i>"]
        n_lbl = cls.MEDIA_ICONS.get(new_media_type or "", f"📎 {new_media_type}")
        return ["", f"📎 <b>Вложение:</b> {n_lbl} <i>(добавлено, файл ниже)</i>"]

    @classmethod
    def _allocate_budget_pair(
        cls, available_budget: int, old_len: int, new_len: int
    ) -> tuple[int, int]:
        if old_len + new_len <= available_budget:
            return old_len + 10, new_len + 10
        half = available_budget // 2
        if old_len <= half:
            return old_len, available_budget - old_len
        if new_len <= half:
            return available_budget - new_len, new_len
        return half, available_budget - half

    @classmethod
    def format_edited_report(
        cls,
        msg: BusinessMessageModel,
        new_text: str | None,
        edited_at: int,
        new_media_type: str | None = None,
        new_media_unique_id: str | None = None,
    ) -> str:
        diff_sec = max(0, int(edited_at - msg.date))
        diff_str = cls._format_duration(diff_sec)
        sender_str = cls._format_user(
            msg.sender_id, msg.sender_name, msg.sender_username
        )
        chat_name = html.escape((msg.chat_title or "Личный диалог")[:128])

        header_lines = [
            "✏️ <b>ИЗМЕНЁННОЕ СООБЩЕНИЕ</b>",
            cls.DIVIDER,
            f"👤 <b>Собеседник:</b> {sender_str}",
            f"💬 <b>Чат:</b> <b>{chat_name}</b> · <i>Личные сообщения</i>",
            (
                f"🆔 <b>ID сообщения:</b> <code>{msg.message_id}</code> · "
                f"<b>ID автора:</b> <code>{msg.sender_id}</code>"
            ),
            "",
            "⏱ <b>Хронология:</b>",
            f" ├ 🕒 Отправлено: <code>{cls._format_date(msg.date)}</code>",
            f" └ ✏️ Изменено: <code>{cls._format_date(edited_at)}</code> <i>(спустя {diff_str})</i>",
        ]
        header_lines.extend(
            cls._format_attachment_change(msg, new_media_type, new_media_unique_id)
        )
        header_lines.append(cls.DIVIDER)

        header_str = "\n".join(header_lines)
        overhead = len(
            "\n🔻 <b>Было:</b>\n<blockquote expandable></blockquote>\n\n🔺 <b>Стало:</b>\n<blockquote expandable></blockquote>"
        )
        budget = max(100, cls.MAX_MESSAGE_LENGTH - len(header_str) - overhead)

        old_raw = msg.text or ""
        new_raw = new_text or ""
        len_old = (
            len(html.escape(old_raw)) if old_raw else len(cls.EMPTY_TEXT_PLACEHOLDER)
        )
        len_new = (
            len(html.escape(new_raw)) if new_raw else len(cls.EMPTY_TEXT_PLACEHOLDER)
        )

        b_old, b_new = cls._allocate_budget_pair(budget, len_old, len_new)
        old_content = cls._escape_and_truncate(msg.text, b_old)
        new_content = cls._escape_and_truncate(new_text, b_new)

        report_lines = [
            header_str,
            "🔻 <b>Было:</b>",
            f"<blockquote expandable>{old_content}</blockquote>",
            "",
            "🔺 <b>Стало:</b>",
            f"<blockquote expandable>{new_content}</blockquote>",
        ]
        return "\n".join(report_lines)
