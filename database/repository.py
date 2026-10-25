import time

import aiosqlite

from .models import BusinessConnectionModel, BusinessMessageModel


class BusinessRepository:
    """Repository handling database operations for business bot."""

    def __init__(self, db_conn: aiosqlite.Connection):
        self.conn = db_conn

    def _row_to_connection(self, row: aiosqlite.Row) -> BusinessConnectionModel:
        return BusinessConnectionModel(
            connection_id=row["connection_id"],
            user_id=row["user_id"],
            user_chat_id=row["user_chat_id"],
            can_reply=bool(row["can_reply"]),
            is_enabled=bool(row["is_enabled"]),
            connected_at=row["connected_at"],
        )

    def _row_to_message(self, row: aiosqlite.Row) -> BusinessMessageModel:
        return BusinessMessageModel(
            id=row["id"],
            connection_id=row["connection_id"],
            chat_id=row["chat_id"],
            chat_title=row["chat_title"],
            chat_username=row["chat_username"],
            message_id=row["message_id"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            sender_username=row["sender_username"],
            date=row["date"],
            text=row["text"],
            media_type=row["media_type"],
            media_file_id=row["media_file_id"],
            is_deleted=bool(row["is_deleted"]),
            deleted_at=row["deleted_at"],
        )

    async def save_connection(self, conn: BusinessConnectionModel) -> None:
        query = """
        INSERT OR REPLACE INTO business_connections (
            connection_id, user_id, user_chat_id, can_reply, is_enabled, connected_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(
            query,
            (
                conn.connection_id,
                conn.user_id,
                conn.user_chat_id,
                1 if conn.can_reply else 0,
                1 if conn.is_enabled else 0,
                conn.connected_at,
            ),
        )
        await self.conn.commit()

    async def get_connection_by_id(
        self, connection_id: str
    ) -> BusinessConnectionModel | None:
        query = "SELECT * FROM business_connections WHERE connection_id = ? LIMIT 1"
        cursor = await self.conn.execute(query, (connection_id,))
        row = await cursor.fetchone()
        return self._row_to_connection(row) if row else None

    async def get_connection_by_user(
        self, user_id: int
    ) -> BusinessConnectionModel | None:
        query = """
        SELECT * FROM business_connections
        WHERE user_id = ? AND is_enabled = 1
        ORDER BY connected_at DESC LIMIT 1
        """
        cursor = await self.conn.execute(query, (user_id,))
        row = await cursor.fetchone()
        return self._row_to_connection(row) if row else None

    async def save_message(self, msg: BusinessMessageModel) -> None:
        query = """
        INSERT INTO business_messages (
            connection_id, chat_id, chat_title, chat_username, message_id,
            sender_id, sender_name, sender_username, date, text,
            media_type, media_file_id, is_deleted, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(
            query,
            (
                msg.connection_id,
                msg.chat_id,
                msg.chat_title,
                msg.chat_username,
                msg.message_id,
                msg.sender_id,
                msg.sender_name,
                msg.sender_username,
                msg.date,
                msg.text,
                msg.media_type,
                msg.media_file_id,
                1 if msg.is_deleted else 0,
                msg.deleted_at,
            ),
        )
        await self.conn.commit()

    async def get_message(
        self, connection_id: str, chat_id: int, message_id: int
    ) -> BusinessMessageModel | None:
        query = """
        SELECT * FROM business_messages
        WHERE connection_id = ? AND chat_id = ? AND message_id = ?
        ORDER BY id DESC LIMIT 1
        """
        cursor = await self.conn.execute(query, (connection_id, chat_id, message_id))
        row = await cursor.fetchone()
        return self._row_to_message(row) if row else None

    async def record_edit(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
        old_text: str,
        new_text: str,
        edited_at: int,
    ) -> None:
        edit_query = """
        INSERT INTO business_edits (
            connection_id, chat_id, message_id, old_text, new_text, edited_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(
            edit_query,
            (connection_id, chat_id, message_id, old_text, new_text, edited_at),
        )

        update_query = """
        UPDATE business_messages
        SET text = ?
        WHERE connection_id = ? AND chat_id = ? AND message_id = ?
        """
        await self.conn.execute(
            update_query, (new_text, connection_id, chat_id, message_id)
        )
        await self.conn.commit()

    async def mark_deleted(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
        deleted_at: int | None = None,
    ) -> BusinessMessageModel | None:
        msg = await self.get_message(connection_id, chat_id, message_id)
        if not msg:
            return None

        del_time = deleted_at or int(time.time())
        query = (
            "UPDATE business_messages SET is_deleted = 1, deleted_at = ? WHERE id = ?"
        )
        await self.conn.execute(query, (del_time, msg.id))
        await self.conn.commit()

        msg.is_deleted = True
        msg.deleted_at = del_time
        return msg

    async def get_stats(self, user_id: int) -> dict[str, int]:
        conn = await self.get_connection_by_user(user_id)
        if not conn:
            return {"total": 0, "deleted": 0, "edited": 0}

        c_id = conn.connection_id
        cur_total = await self.conn.execute(
            "SELECT COUNT(*) FROM business_messages WHERE connection_id = ?", (c_id,)
        )
        total = (await cur_total.fetchone())[0]

        cur_del = await self.conn.execute(
            "SELECT COUNT(*) FROM business_messages WHERE connection_id = ? AND is_deleted = 1",
            (c_id,),
        )
        deleted = (await cur_del.fetchone())[0]

        cur_edit = await self.conn.execute(
            "SELECT COUNT(*) FROM business_edits WHERE connection_id = ?", (c_id,)
        )
        edited = (await cur_edit.fetchone())[0]

        return {"total": total, "deleted": deleted, "edited": edited}

    async def cleanup_old(self, days: int) -> int:
        cutoff = int(time.time()) - (days * 86400)
        del_cursor = await self.conn.execute(
            "DELETE FROM business_messages WHERE date < ?", (cutoff,)
        )
        deleted_count = del_cursor.rowcount
        await self.conn.execute(
            "DELETE FROM business_edits WHERE edited_at < ?", (cutoff,)
        )
        await self.conn.commit()
        return deleted_count
