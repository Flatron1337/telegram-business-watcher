import asyncio

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


async def generate_session() -> None:
    """Interactive CLI helper to generate a Telethon StringSession via QR login and 2FA."""
    client = TelegramClient(
        StringSession(),
        DEFAULT_API_ID,
        DEFAULT_API_HASH,
        device_model="Desktop",
        system_version="Windows 11",
        app_version="5.10.3 x64",
        lang_code="ru",
        system_lang_code="ru-RU",
    )
    await client.connect()
    try:
        qr_login = await client.qr_login()
        print(
            "\nОтсканируйте QR-код с телефона: "
            "Настройки -> Устройства -> Подключить устройство\n"
        )
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_login.url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)

        try:
            await qr_login.wait(timeout=120)
        except SessionPasswordNeededError as exc:
            print(f"\nТребуется двухфакторная защита ({exc.__class__.__name__}).")
            password = input("Введите ваш облачный пароль 2FA: ").strip()
            await client.sign_in(password=password)

        session_str = client.session.save()
        me = await client.get_me()
        username = getattr(me, "username", None) or getattr(me, "id", "user")
        print(f"\nУспешный вход в аккаунт @{username}!")
        print("\nДобавьте эти строки в .env и Render Environment:\n")
        print(f"REACTOR_API_ID={DEFAULT_API_ID}")
        print(f"REACTOR_API_HASH={DEFAULT_API_HASH}")
        print(f"REACTOR_STRING_SESSION={session_str}\n")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(generate_session())
