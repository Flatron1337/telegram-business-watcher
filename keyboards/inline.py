from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_keyboard(is_connected: bool, bot_username: str) -> InlineKeyboardMarkup:
    """Builds interactive inline keyboard for main menu."""
    status_text = "🟢 Подключён" if is_connected else "🔴 Подключить бота"

    keyboard = [
        [
            InlineKeyboardButton(
                text=status_text,
                callback_data="btn_connect_info",
            ),
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


def get_back_keyboard() -> InlineKeyboardMarkup:
    """Builds a back button to return to main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="btn_menu")]
        ]
    )
