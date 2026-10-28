import json
import time
from typing import Any

import aiosqlite

from utils.logging import setup_logger

from .models import BusinessConnectionModel, BusinessMessageModel, ReactorConfig

try:
    from redis.exceptions import RedisError
except ImportError:

    class RedisError(Exception):
        pass


REDIS_EXCEPTIONS = (RedisError, json.JSONDecodeError, TypeError, OSError, KeyError)

logger = setup_logger("business.repository")


class BusinessRepository:
    """Repository handling database operations for business bot with SQLite and optional Redis cache."""

    def __init__(
        self,
        db_conn: aiosqlite.Connection,
        redis_client: Any | None = None,
    ):
        self.conn = db_conn
        self.redis = redis_client

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
        return BusinessMessageModel.from_dict(dict(row))

    def _row_to_reactor_config(self, row: aiosqlite.Row) -> ReactorConfig:
        return ReactorConfig.from_dict(dict(row))

    async def save_reactor_config(self, config: ReactorConfig) -> None:
        config.updated_at = config.updated_at or int(time.time())
        query = """
        INSERT OR REPLACE INTO reactor_settings (
            owner_id, enabled, emoji, is_big, only_incoming, target_chat_ids, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(
            query,
            (
                config.owner_id,
                1 if config.enabled else 0,
                config.emoji,
                1 if config.is_big else 0,
                1 if config.only_incoming else 0,
                json.dumps(config.target_chat_ids),
                config.updated_at,
            ),
        )
        await self.conn.commit()

        if self.redis:
            try:
                await self.redis.set(
                    f"biz:reactor:{config.owner_id}", json.dumps(config.to_dict())
                )
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache write error (reactor): %s", e)

    async def get_reactor_config(self, owner_id: int) -> ReactorConfig:
        query = "SELECT * FROM reactor_settings WHERE owner_id = ? LIMIT 1"
        cursor = await self.conn.execute(query, (owner_id,))
        row = await cursor.fetchone()
        if row:
            return self._row_to_reactor_config(row)

        if self.redis:
            try:
                raw = await self.redis.get(f"biz:reactor:{owner_id}")
                if raw:
                    model = ReactorConfig.from_dict(json.loads(raw))
                    await self.save_reactor_config(model)
                    return model
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache read error (reactor): %s", e)

        return ReactorConfig(owner_id=owner_id, enabled=False)

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

        if self.redis:
            try:
                conn_data = json.dumps(conn.to_dict())
                await self.redis.set(f"biz:conn:{conn.connection_id}", conn_data)
                if conn.is_enabled:
                    await self.redis.set(f"biz:user_conn:{conn.user_id}", conn_data)
                else:
                    await self.redis.delete(f"biz:user_conn:{conn.user_id}")
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache write error (conn): %s", e)

    async def get_connection_by_id(
        self, connection_id: str
    ) -> BusinessConnectionModel | None:
        query = "SELECT * FROM business_connections WHERE connection_id = ? LIMIT 1"
        cursor = await self.conn.execute(query, (connection_id,))
        row = await cursor.fetchone()
        if row:
            return self._row_to_connection(row)

        if self.redis:
            try:
                raw = await self.redis.get(f"biz:conn:{connection_id}")
                if raw:
                    data = json.loads(raw)
                    model = BusinessConnectionModel.from_dict(data)
                    await self.save_connection(model)
                    return model
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache read error (conn_id): %s", e)

        return None

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
        if row:
            return self._row_to_connection(row)

        if self.redis:
            try:
                raw = await self.redis.get(f"biz:user_conn:{user_id}")
                if raw:
                    data = json.loads(raw)
                    model = BusinessConnectionModel.from_dict(data)
                    await self.save_connection(model)
                    return model
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache read error (user_conn): %s", e)

        return None

    async def save_message(self, msg: BusinessMessageModel) -> None:
        query = """
        INSERT INTO business_messages (
            connection_id, chat_id, chat_title, chat_username, message_id,
            sender_id, sender_name, sender_username, date, text,
            media_type, media_file_id, media_file_unique_id, is_deleted, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        cursor = await self.conn.execute(
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
                msg.media_file_unique_id,
                1 if msg.is_deleted else 0,
                msg.deleted_at,
            ),
        )
        await self.conn.commit()
        if cursor.lastrowid:
            msg.id = cursor.lastrowid

        if self.redis:
            try:
                msg_key = f"biz:msg:{msg.connection_id}:{msg.chat_id}:{msg.message_id}"
                raw_json = json.dumps(msg.to_dict())
                # Cache messages for 14 days in Redis
                await self.redis.set(msg_key, raw_json, ex=14 * 86400)
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache write error (msg): %s", e)

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
        if row:
            return self._row_to_message(row)

        if self.redis:
            try:
                msg_key = f"biz:msg:{connection_id}:{chat_id}:{message_id}"
                raw = await self.redis.get(msg_key)
                if raw:
                    data = json.loads(raw)
                    model = BusinessMessageModel.from_dict(data)
                    # Re-populate local SQLite database cache
                    await self.save_message(model)
                    return model
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache read error (msg): %s", e)

        return None

    async def record_edit(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
        old_text: str | None,
        new_text: str | None,
        edited_at: int,
        new_media_type: str | None = None,
        new_media_file_id: str | None = None,
        new_media_file_unique_id: str | None = None,
    ) -> None:
        edit_query = """
        INSERT INTO business_edits (
            connection_id, chat_id, message_id, old_text, new_text, edited_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(
            edit_query,
            (
                connection_id,
                chat_id,
                message_id,
                old_text or "",
                new_text or "",
                edited_at,
            ),
        )

        update_query = """
        UPDATE business_messages
        SET text = ?, media_type = ?, media_file_id = ?, media_file_unique_id = ?
        WHERE connection_id = ? AND chat_id = ? AND message_id = ?
        """
        await self.conn.execute(
            update_query,
            (
                new_text,
                new_media_type,
                new_media_file_id,
                new_media_file_unique_id,
                connection_id,
                chat_id,
                message_id,
            ),
        )
        await self.conn.commit()

        if self.redis:
            try:
                msg_key = f"biz:msg:{connection_id}:{chat_id}:{message_id}"
                raw = await self.redis.get(msg_key)
                if raw:
                    data = json.loads(raw)
                    data["text"] = new_text
                    data["media_type"] = new_media_type
                    data["media_file_id"] = new_media_file_id
                    data["media_file_unique_id"] = new_media_file_unique_id
                    await self.redis.set(msg_key, json.dumps(data), ex=14 * 86400)
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache update error (edit): %s", e)

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
        query = """
        UPDATE business_messages
        SET is_deleted = 1, deleted_at = ?
        WHERE connection_id = ? AND chat_id = ? AND message_id = ?
        """
        await self.conn.execute(query, (del_time, connection_id, chat_id, message_id))
        await self.conn.commit()

        msg.is_deleted = True
        msg.deleted_at = del_time

        if self.redis:
            try:
                msg_key = f"biz:msg:{connection_id}:{chat_id}:{message_id}"
                raw = await self.redis.get(msg_key)
                if raw:
                    data = json.loads(raw)
                    data["is_deleted"] = True
                    data["deleted_at"] = del_time
                    await self.redis.set(msg_key, json.dumps(data), ex=14 * 86400)
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis cache update error (delete): %s", e)

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
