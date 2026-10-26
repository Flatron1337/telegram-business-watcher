import re
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from config.settings import Settings
from database.models import BusinessConnectionModel
from database.repository import BusinessRepository
from keyboards.inline import get_back_keyboard, get_main_keyboard
from utils.logging import setup_logger

logger = setup_logger("business.user_commands")
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


async def _resolve_user_connection(
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
    user_id: int,
) -> bool:
    """Checks if the user has an active business connection, restoring from Telegram API if needed."""
    conn = await repository.get_connection_by_user(user_id)
    if conn and conn.is_enabled:
        return True

    candidate_ids: list[str] = []
    if settings.last_business_connection_id:
        candidate_ids.append(settings.last_business_connection_id)

    try:
        chat = await bot.get_chat(user_id)
        if chat.pinned_message and chat.pinned_message.text:
            match = re.search(
                r"BUSINESS_CONN:([A-Za-z0-9_-]+)", chat.pinned_message.text
            )
            if match:
                candidate_ids.append(match.group(1))
    except (TelegramAPIError, OSError) as e:
        logger.debug("Chat pinned inspect error for user %s: %s", user_id, e)

    for c_id in candidate_ids:
        try:
            tg_conn = await bot.get_business_connection(c_id)
            if (
                tg_conn
                and tg_conn.is_enabled
                and tg_conn.user.id == user_id
                and settings.is_user_allowed(tg_conn.user.id)
            ):
                conn_date = int(
                    tg_conn.date.timestamp()
                    if hasattr(tg_conn.date, "timestamp")
                    else tg_conn.date or time.time()
                )
                conn_model = BusinessConnectionModel(
                    connection_id=tg_conn.id,
                    user_id=tg_conn.user.id,
                    user_chat_id=tg_conn.user_chat_id,
                    can_reply=bool(tg_conn.can_reply),
                    is_enabled=bool(tg_conn.is_enabled),
                    connected_at=conn_date,
                )
                await repository.save_connection(conn_model)
                logger.info(
                    "Auto-restored user connection %s from Telegram API on user command",
                    tg_conn.id,
                )
                return True
        except TelegramAPIError as e:
            logger.debug("Candidate %s validation error: %s", c_id, e)

    return False


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(
    message: Message, bot: Bot, repository: BusinessRepository, settings: Settings
):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    is_connected = await _resolve_user_connection(
        bot, repository, settings, message.from_user.id
    )

    text = _get_welcome_text(message.from_user.first_name, is_connected, bot_username)
    keyboard = get_main_keyboard(is_connected, bot_username)

    await message.answer(text, reply_markup=keyboard, parse_mode="html")


@router.callback_query(F.data == "btn_menu")
async def cb_menu(
    callback: CallbackQuery,
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    is_connected = await _resolve_user_connection(
        bot, repository, settings, callback.from_user.id
    )

    text = _get_welcome_text(callback.from_user.first_name, is_connected, bot_username)
    keyboard = get_main_keyboard(is_connected, bot_username)

    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="html")
    await callback.answer()


@router.callback_query(F.data == "btn_connect_info")
async def cb_connect_info(
    callback: CallbackQuery,
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    is_connected = await _resolve_user_connection(
        bot, repository, settings, callback.from_user.id
    )

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
            text,
            reply_markup=get_back_keyboard(include_settings_link=True),
            parse_mode="html",
        )
    await callback.answer()


@router.callback_query(F.data == "btn_instructions")
async def cb_instructions(callback: CallbackQuery, bot: Bot):
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "bot"

    text = (
        "📖 <b>Пошаговая инструкция по подключению:</b>\n\n"
        "1️⃣ Нажмите кнопку ниже <b>«⚙️ Открыть настройки аккаунта»</b>.\n"
        "2️⃣ Перейдите в <b>Автоматизация чатов</b>.\n"
        f"3️⃣ Введите юзернейм бота: <code>@{bot_username}</code>\n"
        "4️⃣ Выберите, в каких чатах бот будет работать (по умолчанию: <i>Все личные чаты</i>).\n"
        "5️⃣ Сохраните настройки.\n\n"
        "🛡 <i>Безопасность: бот сохраняет сообщения в защищённый персональный кэш, "
        "и ваши данные не передаются посторонним!</i>"
    )
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_keyboard(include_settings_link=True),
            parse_mode="html",
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
            text,
            reply_markup=get_back_keyboard(include_settings_link=True),
            parse_mode="html",
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
