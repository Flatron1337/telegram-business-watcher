import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


def _read_credential(env_key: str, prompt_text: str) -> str:
    val = os.getenv(env_key, "").strip()
    if val:
        return val
    return input(prompt_text).strip()


async def generate_session() -> None:
    """Interactive CLI helper to generate a Telethon StringSession for the MTProto reactor."""
    raw_api_id = _read_credential(
        "REACTOR_API_ID", "Enter Telegram API_ID (from my.telegram.org): "
    )
    api_hash = _read_credential(
        "REACTOR_API_HASH", "Enter Telegram API_HASH (from my.telegram.org): "
    )
    if not raw_api_id.isdigit() or not api_hash:
        raise SystemExit("Error: Valid numeric API_ID and API_HASH are required.")

    api_id = int(raw_api_id)
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_str = client.session.save()
        me = await client.get_me()
        username = getattr(me, "username", None) or getattr(me, "id", "user")
        print(f"\nSuccessfully authenticated as @{username}!")
        print("\nAdd these variables to your .env and Render Environment:\n")
        print(f"REACTOR_API_ID={api_id}")
        print(f"REACTOR_API_HASH={api_hash}")
        print(f"REACTOR_STRING_SESSION={session_str}\n")


if __name__ == "__main__":
    asyncio.run(generate_session())
