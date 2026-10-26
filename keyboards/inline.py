from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

SETTINGS_EDIT_URL = "tg://settings/edit"


def get_main_keyboard(is_connected: bool, bot_username: str) -> InlineKeyboardMarkup:
    """Builds interactive inline keyboard for main menu."""
    if is_connected:
        connect_btn = InlineKeyboardButton(
            text="🟢 Подключён",
            callback_data="btn_connect_info",
        )
    else:
        connect_btn = InlineKeyboardButton(
            text="🔴 Подключить бота",
            url=SETTINGS_EDIT_URL,
        )

    keyboard = [
        [
            connect_btn,
            InlineKeyboardButton(
                text="📖 Инструкция",
                callback_data="btn_instructions",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Статистика",
                callback_data="btn_stats",
            ),
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data="btn_settings",
            ),
        ],
        [
            InlineKeyboardButton(
                text="❓ Помощь",
                callback_data="btn_help",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_keyboard(include_settings_link: bool = False) -> InlineKeyboardMarkup:
    """Builds a back button to return to main menu, optionally including account settings deep link."""
    buttons = []
    if include_settings_link:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Открыть настройки аккаунта",
                    url=SETTINGS_EDIT_URL,
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="btn_menu")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
