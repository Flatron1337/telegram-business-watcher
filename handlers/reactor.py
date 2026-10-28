import contextlib
import time
from collections.abc import Callable
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, Filter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config.settings import Settings
from database.models import ReactorConfig
from database.repository import BusinessRepository
from utils.logging import setup_logger

logger = setup_logger("business.reactor")
router = Router(name="reactor")

REACTOR_EMOJI_PRESETS: tuple[str, ...] = ("❤️\u200d🔥", "🔥", "❤️", "⚡", "👍")

# Tracks the last /reactor command message_id per owner so 'Hide panel' can wipe both messages
_last_cmd_message_ids: dict[int, int] = {}


class ReactorOwnerFilter(Filter):
    """Strictly allows access only to the configured reactor_owner_id (Zero-Knowledge OPSEC)."""

    async def __call__(
        self, event: Message | CallbackQuery, settings: Settings | None = None
    ) -> bool:
        user = getattr(event, "from_user", None)
        if not user:
            return False
        owner_id = settings.reactor_owner_id if settings else 965180470
        return bool(user.id == owner_id)


def cycle_reactor_emoji(current_emoji: str) -> str:
    """Cycles to the next emoji preset: ❤️‍🔥 -> 🔥 -> ❤️ -> ⚡ -> 👍 -> ❤️‍🔥."""
    normalized = "❤️\u200d🔥" if current_emoji == "❤️🔥" else current_emoji
    try:
        idx = REACTOR_EMOJI_PRESETS.index(normalized)
        return REACTOR_EMOJI_PRESETS[(idx + 1) % len(REACTOR_EMOJI_PRESETS)]
    except ValueError:
        return REACTOR_EMOJI_PRESETS[0]


def toggle_target_chat(config: ReactorConfig, chat_id: int) -> bool:
    """Toggles chat_id in config.target_chat_ids. Returns True if added, False if removed."""
    if chat_id in config.target_chat_ids:
        config.target_chat_ids.remove(chat_id)
        return False
    config.target_chat_ids.append(chat_id)
    return True


def build_reactor_keyboard(config: ReactorConfig) -> InlineKeyboardMarkup:
    """Builds the secret inline control panel keyboard for the auto-reactor."""
    status_text = "🟢 Статус: ВКЛ" if config.enabled else "🔴 Статус: ВЫКЛ"
    emoji_text = f"{config.emoji} Эмодзи: {config.emoji}"
    anim_text = "💥 Анимация: Большая" if config.is_big else "💥 Анимация: Обычная"
    mode_text = (
        "🎯 Режим: Только входящие"
        if config.only_incoming
        else "🎯 Режим: Все сообщения"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                text=status_text, callback_data="reactor:toggle_enabled"
            )
        ],
        [InlineKeyboardButton(text=emoji_text, callback_data="reactor:cycle_emoji")],
        [InlineKeyboardButton(text=anim_text, callback_data="reactor:toggle_big")],
        [InlineKeyboardButton(text=mode_text, callback_data="reactor:toggle_mode")],
        [InlineKeyboardButton(text="🗑 Скрыть пульт", callback_data="reactor:hide")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def format_reactor_panel(config: ReactorConfig) -> str:
    """Formats the secret reactor control panel HTML text."""
    status_str = "🟢 <b>ВКЛ</b>" if config.enabled else "🔴 <b>ВЫКЛ</b>"
    anim_str = "💥 <b>Большая</b>" if config.is_big else "✨ <b>Обычная</b>"
    mode_str = (
        "🎯 <b>Только входящие</b>"
        if config.only_incoming
        else "🌐 <b>Все сообщения</b>"
    )
    if config.target_chat_ids:
        targets_str = ", ".join(f"<code>{cid}</code>" for cid in config.target_chat_ids)
    else:
        targets_str = "<i>Все чаты (без ограничений)</i>"

    return (
        "❤️‍🔥 <b>Стелс-пульт: Авто-реактор</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 <b>Статус:</b> {status_str}\n"
        f"🎭 <b>Эмодзи:</b> {config.emoji}\n"
        f"💥 <b>Анимация:</b> {anim_str}\n"
        f"🎯 <b>Режим:</b> {mode_str}\n"
        f"📋 <b>Цели:</b> {targets_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Цели:</i> <code>/reactor_target &lt;chat_id&gt;</code> "
        "или перешлите сообщение собеседника."
    )


def _extract_forwarded_chat_id(msg: Any) -> int | None:
    """Extracts target user/chat ID from a forwarded message."""
    if not msg:
        return None
    fwd_from = getattr(msg, "forward_from", None)
    if fwd_from and getattr(fwd_from, "id", None) is not None:
        return int(fwd_from.id)
    fwd_chat = getattr(msg, "forward_from_chat", None)
    if fwd_chat and getattr(fwd_chat, "id", None) is not None:
        return int(fwd_chat.id)
    origin = getattr(msg, "forward_origin", None)
    if origin:
        for attr in ("sender_user", "sender_chat", "chat"):
            entity = getattr(origin, attr, None)
            if entity and getattr(entity, "id", None) is not None:
                return int(entity.id)
    return None


@router.message(Command("reactor"), ReactorOwnerFilter())
async def cmd_reactor(
    message: Message,
    repository: BusinessRepository,
    settings: Settings,
) -> None:
    """Opens the secret inline control panel for reactor_owner_id."""
    if not message.from_user or message.from_user.id != settings.reactor_owner_id:
        return

    _last_cmd_message_ids[message.from_user.id] = message.message_id
    config = await repository.get_reactor_config(settings.reactor_owner_id)
    await message.answer(
        format_reactor_panel(config),
        reply_markup=build_reactor_keyboard(config),
        parse_mode="html",
    )


@router.message(Command("reactor_target"), ReactorOwnerFilter())
async def cmd_reactor_target(
    message: Message,
    repository: BusinessRepository,
    settings: Settings,
) -> None:
    """Adds or removes a target chat_id for the auto-reactor."""
    if not message.from_user or message.from_user.id != settings.reactor_owner_id:
        return

    config = await repository.get_reactor_config(settings.reactor_owner_id)
    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if arg.lower() in {"clear", "reset", "all"}:
        config.target_chat_ids.clear()
        config.updated_at = int(time.time())
        await repository.save_reactor_config(config)
        await message.answer(
            "🧹 Список целевых чатов очищен. Реактор работает для <b>всех чатов</b>.",
            parse_mode="html",
        )
        return

    target_id: int | None = None
    if arg and arg.lstrip("-").isdigit():
        target_id = int(arg)
    elif not arg and getattr(message, "reply_to_message", None):
        target_id = _extract_forwarded_chat_id(message.reply_to_message)

    if target_id is None:
        targets = (
            ", ".join(f"<code>{cid}</code>" for cid in config.target_chat_ids)
            if config.target_chat_ids
            else "<i>Все чаты (список пуст)</i>"
        )
        await message.answer(
            f"📋 <b>Текущие цели реактора:</b> {targets}\n\n"
            "Использование:\n"
            "• <code>/reactor_target 123456789</code> — добавить/удалить ID\n"
            "• <code>/reactor_target clear</code> — сбросить фильтр\n"
            "• Или просто перешлите сообщение нужного пользователя боту.",
            parse_mode="html",
        )
        return

    added = toggle_target_chat(config, target_id)
    config.updated_at = int(time.time())
    await repository.save_reactor_config(config)
    action_str = "➕ Добавлен в цели" if added else "➖ Удалён из целей"
    await message.answer(
        f"{action_str}: <code>{target_id}</code>",
        parse_mode="html",
    )


@router.message(
    ReactorOwnerFilter(),
    F.business_connection_id.is_(None),
    F.forward_origin | F.forward_from | F.forward_from_chat,
)
async def on_forwarded_target(
    message: Message,
    repository: BusinessRepository,
    settings: Settings,
) -> None:
    """Toggles target chat_id when the owner forwards a message directly to the bot."""
    if not message.from_user or message.from_user.id != settings.reactor_owner_id:
        return
    if getattr(message, "business_connection_id", None) is not None:
        return

    target_id = _extract_forwarded_chat_id(message)
    if target_id is None:
        return

    config = await repository.get_reactor_config(settings.reactor_owner_id)
    added = toggle_target_chat(config, target_id)
    config.updated_at = int(time.time())
    await repository.save_reactor_config(config)
    action_str = "➕ Чат добавлен в цели" if added else "➖ Чат удалён из целей"
    await message.answer(
        f"{action_str}: <code>{target_id}</code>",
        parse_mode="html",
    )


async def _delete_message_safely(bot: Bot | None, chat_id: int, msg_id: int) -> None:
    """Attempts to delete a message via Bot API without raising on failure."""
    if bot and hasattr(bot, "delete_message"):
        with contextlib.suppress(TelegramAPIError, OSError):
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)


def _toggle_enabled(cfg: ReactorConfig) -> None:
    cfg.enabled = not cfg.enabled


def _cycle_emoji(cfg: ReactorConfig) -> None:
    cfg.emoji = cycle_reactor_emoji(cfg.emoji)


def _toggle_big(cfg: ReactorConfig) -> None:
    cfg.is_big = not cfg.is_big


def _toggle_mode(cfg: ReactorConfig) -> None:
    cfg.only_incoming = not cfg.only_incoming


_REACTOR_MUTATORS: dict[str, Callable[[ReactorConfig], None]] = {
    "toggle_enabled": _toggle_enabled,
    "cycle_emoji": _cycle_emoji,
    "toggle_big": _toggle_big,
    "toggle_mode": _toggle_mode,
}


@router.callback_query(F.data.startswith("reactor:"), ReactorOwnerFilter())
async def cb_reactor(
    callback: CallbackQuery,
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
) -> None:
    """Handles secret inline button callbacks for the auto-reactor panel."""
    if not callback.from_user or callback.from_user.id != settings.reactor_owner_id:
        return

    action = (callback.data or "").split(":", 1)[1]
    if action == "hide":
        if callback.message:
            chat_id = callback.message.chat.id
            await _delete_message_safely(bot, chat_id, callback.message.message_id)
            if hasattr(callback.message, "delete"):
                with contextlib.suppress(TelegramAPIError, OSError):
                    await callback.message.delete()

            cmd_msg_id = _last_cmd_message_ids.pop(callback.from_user.id, None)
            if not cmd_msg_id and getattr(callback.message, "reply_to_message", None):
                cmd_msg_id = callback.message.reply_to_message.message_id
            if cmd_msg_id:
                await _delete_message_safely(bot, chat_id, cmd_msg_id)

        with contextlib.suppress(TelegramAPIError, OSError):
            await callback.answer()
        return

    mutator = _REACTOR_MUTATORS.get(action)
    if not mutator:
        with contextlib.suppress(TelegramAPIError, OSError):
            await callback.answer()
        return

    config = await repository.get_reactor_config(settings.reactor_owner_id)
    mutator(config)
    config.updated_at = int(time.time())
    await repository.save_reactor_config(config)

    if callback.message:
        with contextlib.suppress(TelegramAPIError, OSError):
            await callback.message.edit_text(
                format_reactor_panel(config),
                reply_markup=build_reactor_keyboard(config),
                parse_mode="html",
            )
    with contextlib.suppress(TelegramAPIError, OSError):
        await callback.answer()
