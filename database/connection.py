from pathlib import Path

import aiosqlite

from utils.logging import setup_logger

logger = setup_logger("business.db.connection")


class Database:
    """Async SQLite Database connection manager for Business Bot."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(str(self.db_path))
            self._connection.row_factory = aiosqlite.Row

            await self._connection.execute("PRAGMA journal_mode=WAL;")
            await self._connection.execute("PRAGMA synchronous=NORMAL;")
            await self._connection.execute("PRAGMA busy_timeout=5000;")

            await self._init_schema()
            logger.info("Database connection established: %s", self.db_path)

        return self._connection

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed.")

    async def _init_schema(self) -> None:
        assert self._connection is not None

        schema = """
        CREATE TABLE IF NOT EXISTS business_connections (
            connection_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            user_chat_id INTEGER NOT NULL,
            can_reply INTEGER NOT NULL,
            is_enabled INTEGER NOT NULL,
            connected_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS business_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_title TEXT,
            chat_username TEXT,
            message_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            sender_name TEXT NOT NULL,
            sender_username TEXT,
            date INTEGER NOT NULL,
            text TEXT,
            media_type TEXT,
            media_file_id TEXT,
            media_file_unique_id TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS business_edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            old_text TEXT NOT NULL,
            new_text TEXT NOT NULL,
            edited_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_b_msg_lookup
            ON business_messages(connection_id, chat_id, message_id);
        CREATE INDEX IF NOT EXISTS idx_b_msg_chat
            ON business_messages(chat_id, message_id);
        CREATE INDEX IF NOT EXISTS idx_b_date
            ON business_messages(date);
        """
        await self._connection.executescript(schema)

        # Migration check for existing SQLite databases without media_file_unique_id column
        cursor = await self._connection.execute("PRAGMA table_info(business_messages);")
        columns = [row["name"] for row in await cursor.fetchall()]
        if "media_file_unique_id" not in columns:
            await self._connection.execute(
                "ALTER TABLE business_messages ADD COLUMN media_file_unique_id TEXT;"
            )

        await self._connection.commit()
