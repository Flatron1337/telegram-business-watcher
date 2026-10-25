from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from database.repository import BusinessRepository
from keyboards.inline import get_back_keyboard, get_main_keyboard

router = Router(name="user_commands")


def _get_welcome_text(
    user_first_name: str, is_connected: bool, bot_username: str
) -> str:
    status_icon = "🟢 <b>Подключен</b>" if is_connected else "🔴 <b>Не подключен</b>"

    return (
        f"👋 Привет, <b>{user_first_name}</b>!\n\n"
        f"🕵️ <b>Свидетель | Ваш личный секретарь</b>\n"
        f"Бот фиксирует удалённые и отредактированные сообщения в ваших личных чатах "
        f"и мгновенно присылает их сюда вместе с медиафайлами.\n\n"
        f"📡 <b>Статус автоматизации:</b> {status_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Для работы бота подключите его в настройках Telegram:</i>\n"
        f"<b>Настройки ➔ Автоматизация чатов\n"
        f"(или Telegram для бизнеса ➔ Чат-боты) ➔ Добавить @{bot_username}</b>"
    )


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, bot: Bot, repository: BusinessRepository):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    conn = await repository.get_connection_by_user(message.from_user.id)
    is_connected = bool(conn and conn.is_enabled)

    text = _get_welcome_text(message.from_user.first_name, is_connected, bot_username)
    keyboard = get_main_keyboard(is_connected, bot_username)

    await message.answer(text, reply_markup=keyboard, parse_mode="html")


@router.callback_query(F.data == "btn_menu")
async def cb_menu(callback: CallbackQuery, bot: Bot, repository: BusinessRepository):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    conn = await repository.get_connection_by_user(callback.from_user.id)
    is_connected = bool(conn and conn.is_enabled)

    text = _get_welcome_text(callback.from_user.first_name, is_connected, bot_username)
    keyboard = get_main_keyboard(is_connected, bot_username)

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="html")
    await callback.answer()


@router.callback_query(F.data == "btn_connect_info")
async def cb_connect_info(
    callback: CallbackQuery, bot: Bot, repository: BusinessRepository
):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    conn = await repository.get_connection_by_user(callback.from_user.id)
    is_connected = bool(conn and conn.is_enabled)

    if is_connected:
        text = (
            "✅ <b>Бот уже успешно подключен!</b>\n\n"
            "Все личные сообщения под надёжной защитой. Если кто-то удалит "
            "или изменит сообщение — вы моментально получите уведомление сюда."
        )
    else:
        text = (
            "🔗 <b>Как подключить бота:</b>\n\n"
            "1. Откройте <b>Настройки</b> в Telegram на телефоне\n"
            "2. Перейдите в <b>Автоматизация чатов</b> "
            "(или <i>Telegram для бизнеса ➔ Чат-боты</i>)\n"
            f"3. В поле поиска введите: <code>@{bot_username}</code>\n"
            "4. Включите разрешения на чтение сообщений и нажмите <b>Добавить</b>\n\n"
            "<i>После подключения бот сразу активируется!</i>"
        )

    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=get_back_keyboard(), parse_mode="html"
        )
    await callback.answer()


@router.callback_query(F.data == "btn_instructions")
async def cb_instructions(callback: CallbackQuery, bot: Bot):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    text = (
        "📖 <b>Пошаговая инструкция по подключению:</b>\n\n"
        "1️⃣ Зайдите в официальный клиент Telegram (или exteraGram) на смартфоне.\n"
        "2️⃣ Откройте <b>Настройки ➔ Автоматизация чатов</b>.\n"
        f"3️⃣ Введите юзернейм бота: <code>@{bot_username}</code>\n"
        "4️⃣ Выберите, в каких чатах бот будет работать (по умолчанию: <i>Все личные чаты</i>).\n"
        "5️⃣ Сохраните настройки.\n\n"
        "🛡 <i>Безопасность: бот работает локально на вашем компьютере, "
        "и ваши сообщения не передаются третьим лицам!</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=get_back_keyboard(), parse_mode="html"
        )
    await callback.answer()


@router.callback_query(F.data == "btn_stats")
async def cb_stats(callback: CallbackQuery, repository: BusinessRepository):
    stats = await repository.get_stats(callback.from_user.id)
    text = (
        "📊 <b>Ваша статистика активности:</b>\n\n"
        f"💬 Всего сообщений обработано: <b>{stats['total']}</b>\n"
        f"🗑 Удалённых сообщений перехвачено: <b>{stats['deleted']}</b>\n"
        f"✏️ Отредактированных сообщений зафиксировано: <b>{stats['edited']}</b>\n"
    )
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=get_back_keyboard(), parse_mode="html"
        )
    await callback.answer()


@router.callback_query(F.data == "btn_settings")
async def cb_settings(callback: CallbackQuery):
    text = (
        "⚙️ <b>Настройки уведомлений:</b>\n\n"
        "• Уведомления об удалении: 🟢 <b>Включены</b>\n"
        "• Уведомления о правках: 🟢 <b>Включены</b>\n"
        "• Восстановление медиа: 🟢 <b>Включено</b>\n\n"
        "<i>Для управления списком чатов перейдите в Настройки Telegram ➔ Автоматизация чатов.</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=get_back_keyboard(), parse_mode="html"
        )
    await callback.answer()


@router.callback_query(F.data == "btn_help")
async def cb_help(callback: CallbackQuery):
    text = (
        "❓ <b>Часто задаваемые вопросы:</b>\n\n"
        "• <b>Как бот восстанавливает медиа?</b>\n"
        "Когда вам присылают фото, кружок или голосовое, бот запоминает ID в Telegram. "
        "При удалении бот отправляет оригинал файла прямо в этот чат.\n\n"
        "• <b>Работает ли в группах?</b>\n"
        "Режим Telegram Business рассчитан на личные диалоги (1 на 1). "
        "Для групп работает MTProto юзербот."
    )
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=get_back_keyboard(), parse_mode="html"
        )
    await callback.answer()
