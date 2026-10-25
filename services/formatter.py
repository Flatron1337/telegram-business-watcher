import html
from datetime import UTC, datetime
from typing import ClassVar

from database.models import BusinessMessageModel


class BusinessReportFormatter:
    """Builds rich, elegant HTML cards for Telegram Business audit logs."""

    DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━"

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
        return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%d.%m.%Y %H:%M:%S")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds <= 0:
            return "мгновенно"
        if seconds < 60:
            return f"{seconds} сек."
        if seconds < 3600:
            m, s = divmod(seconds, 60)
            return f"{m} мин. {s} сек." if s else f"{m} мин."
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h} ч. {m} мин."

    @classmethod
    def _format_user(cls, user_id: int, name: str, username: str | None) -> str:
        name_esc = html.escape(name or "Пользователь")
        user_link = f'<a href="tg://user?id={user_id}"><b>{name_esc}</b></a>'
        if username:
            return f"{user_link} (@{html.escape(username)})"
        return user_link

    @classmethod
    def format_deleted_report(
        cls, msg: BusinessMessageModel, has_media: bool = False
    ) -> str:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        del_ts = msg.deleted_at or now_ts
        lifetime = max(0, del_ts - msg.date)
        lifetime_str = cls._format_duration(lifetime)

        sender_str = cls._format_user(
            msg.sender_id, msg.sender_name, msg.sender_username
        )
        chat_name = html.escape(msg.chat_title or "Личный диалог")

        lines = [
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
            lines.extend(["", f"📎 <b>Вложение:</b> {media_label} {status_text}"])

        if msg.text:
            text_esc = html.escape(msg.text)
            lines.extend(
                [
                    cls.DIVIDER,
                    "💬 <b>Содержимое:</b>",
                    f"<blockquote expandable>{text_esc}</blockquote>",
                ]
            )

        return "\n".join(lines)

    @classmethod
    def format_edited_report(
        cls, msg: BusinessMessageModel, new_text: str, edited_at: int
    ) -> str:
        diff_sec = max(0, edited_at - msg.date)
        diff_str = cls._format_duration(diff_sec)

        sender_str = cls._format_user(
            msg.sender_id, msg.sender_name, msg.sender_username
        )
        chat_name = html.escape(msg.chat_title or "Личный диалог")

        old_esc = html.escape(msg.text or "<i>(нет текста)</i>")
        new_esc = html.escape(new_text or "<i>(нет текста)</i>")

        lines = [
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
            cls.DIVIDER,
            "🔻 <b>Было:</b>",
            f"<blockquote expandable>{old_esc}</blockquote>",
            "",
            "🔺 <b>Стало:</b>",
            f"<blockquote expandable>{new_esc}</blockquote>",
        ]

        return "\n".join(lines)
