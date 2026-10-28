import contextlib

from telethon import TelegramClient, functions, types
from telethon.errors import RPCError
from telethon.sessions import StringSession

from config.settings import Settings
from utils.logging import setup_logger

logger = setup_logger("business.services.mtproto")


class ReactorMTProtoClient:
    """Lightweight Telethon MTProto bridge for setting reactions in 1-on-1 private chats."""

    def __init__(
        self,
        api_id: int | None,
        api_hash: str | None,
        string_session: str | None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.string_session = (string_session or "").strip() or None
        self._client: TelegramClient | None = None
        self._dialogs_warmed: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.api_id and self.api_hash and self.string_session)

    async def connect(self) -> bool:
        """Connects the MTProto user session and caches active dialog InputPeers."""
        if not self.is_configured:
            return False

        if self._client is None:
            assert self.api_id is not None and self.api_hash is not None
            self._client = TelegramClient(
                StringSession(self.string_session),
                self.api_id,
                self.api_hash,
                receive_updates=False,
            )

        try:
            if not self._client.is_connected():
                await self._client.connect()

            if not await self._client.is_user_authorized():
                logger.warning("MTProto session is not authorized for reactor.")
                return False

            if not self._dialogs_warmed:
                await self._client.get_dialogs(limit=100)
                self._dialogs_warmed = True
                logger.info("MTProto reactor connected and cached top 100 dialogs.")

            return True
        except (RPCError, ValueError, OSError) as e:
            logger.warning("MTProto reactor connect error: %s", e)
            return False

    async def send_reaction(
        self,
        chat_id: int,
        message_id: int,
        emoji: str,
        is_big: bool = True,
    ) -> bool:
        """Sends an emoji reaction directly from the owner account via MTProto."""
        if not await self.connect() or self._client is None:
            return False

        request = functions.messages.SendReactionRequest(
            peer=chat_id,
            msg_id=message_id,
            big=is_big,
            add_to_recent=True,
            reaction=[types.ReactionEmoji(emoticon=emoji)],
        )

        try:
            await self._client(request)
            return True
        except ValueError:
            try:
                await self._client.get_dialogs(limit=50)
                await self._client(request)
                return True
            except (RPCError, ValueError, OSError) as retry_err:
                logger.warning(
                    "MTProto reaction retry failed (chat=%s, msg=%s): %s",
                    chat_id,
                    message_id,
                    retry_err,
                )
                return False
        except (RPCError, OSError) as e:
            logger.warning(
                "MTProto reaction error (chat=%s, msg=%s): %s",
                chat_id,
                message_id,
                e,
            )
            return False

    async def close(self) -> None:
        """Disconnects the underlying Telethon client cleanly."""
        if self._client is not None:
            with contextlib.suppress(RPCError, OSError):
                await self._client.disconnect()
            self._client = None
            self._dialogs_warmed = False


_singleton_client: ReactorMTProtoClient | None = None


def get_mtproto_client(settings: Settings) -> ReactorMTProtoClient | None:
    """Returns or initializes the shared MTProto client if credentials are configured."""
    global _singleton_client
    if not (
        settings.reactor_api_id
        and settings.reactor_api_hash
        and settings.reactor_string_session
    ):
        return None
    if _singleton_client is None:
        _singleton_client = ReactorMTProtoClient(
            api_id=settings.reactor_api_id,
            api_hash=settings.reactor_api_hash,
            string_session=settings.reactor_string_session,
        )
    return _singleton_client


async def close_mtproto_client() -> None:
    """Closes the shared MTProto client if initialized."""
    global _singleton_client
    if _singleton_client is not None:
        await _singleton_client.close()
        _singleton_client = None
