from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, Forbidden
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.channels import get_user_channels, deactivate_channel, add_channel
from database.queries.settings import get_user_settings
from localization.loader import get_text
from models.tariff import get_tariff_limits
from localization.texts import TEXTS
from states.conversation import MY_CHANNELS
from utils.logging import logger


async def nav_my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Мои площадки'"""

    # Figure out where the request came from
    query = update.callback_query
    message = update.message

    # If callback
    if query:
        await query.answer()
        chat_id = query.message.chat_id
    else:
        # If normal text message (reply keyboard)
        chat_id = message.chat_id

    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    text = get_text('my_channels_title', context).format(count=len(channels))
    keyboard = []

    if not channels:
        text += get_text('my_channels_empty', context)
    else:
        for ch in channels:
            title = ch['channel_title'] or ch['channel_username'] or f"ID: {ch['channel_id']}"
            text += f"\n• {title}"
            keyboard.append([InlineKeyboardButton(f"📊 {title}", callback_data=f"channel_manage_{ch['channel_id']}")])

    text += get_text('my_channels_footer', context)
    keyboard.append([InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_main_menu")])

    markup = InlineKeyboardMarkup(keyboard)

    # Edit or send depending on source
    if query:
        await query.edit_message_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)

    return MY_CHANNELS


async def channel_manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления конкретным каналом"""
    query = update.callback_query
    await query.answer()

    channel_id = int(query.data.replace("channel_manage_", ""))

    # Получаем информацию о канале
    channel = db_query("SELECT * FROM channels WHERE channel_id = %s", (channel_id,), fetchone=True)

    if not channel or not channel['is_active']:
        await query.edit_message_text(
            get_text('channel_not_found', context),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_channels")]])
        )
        return MY_CHANNELS

    title = channel['channel_title'] or get_text('no_name', context)
    username = channel['channel_username'] or get_text('no_username', context)

    text = get_text('channel_actions_title', context) + "\n\n"
    text += f"📢 **{title}**\n"
    text += f"🔗 @{username}\n"
    text += f"ID: `{channel_id}`\n\n"
    text += get_text('what_you_wanna_do', context)

    keyboard = [
        [InlineKeyboardButton(get_text('channel_remove_btn', context), callback_data=f"channel_delete_{channel_id}")],
        [InlineKeyboardButton(get_text('channel_back_btn', context), callback_data="nav_channels")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return MY_CHANNELS

async def channel_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление канала из списка (Soft delete)"""
    query = update.callback_query
    await query.answer()

    channel_id = int(query.data.replace("channel_delete_", ""))

    # Проверяем существование
    channel = db_query("SELECT * FROM channels WHERE channel_id = %s", (channel_id,), fetchone=True)
    title = channel['channel_title'] if channel else str(channel_id)

    # Деактивируем канал
    deactivate_channel(channel_id)

    # Удаляем из всех будущих задач (опционально, но желательно)
    db_query("DELETE FROM task_channels WHERE channel_id = %s", (channel_id,), commit=True)

    text = get_text('channel_remove_success', context).format(title=title)

    # Возвращаемся к списку
    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    list_text = get_text('my_channels_title', context).format(count=len(channels))
    keyboard = []

    if not channels:
        list_text += get_text('my_channels_empty', context)
    else:
        for ch in channels:
            t = ch['channel_title'] or ch['channel_username'] or f"ID: {ch['channel_id']}"
            list_text += f"\n• {t}"
            keyboard.append([InlineKeyboardButton(f"📊 {t}", callback_data=f"channel_manage_{ch['channel_id']}")])

    list_text += "\n\n" + text  # Добавляем сообщение об успехе
    keyboard.append([InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_main_menu")])

    await query.edit_message_text(list_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return MY_CHANNELS


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик добавления/удаления бота в канал/чат с проверкой лимитов и уникальности"""
    try:
        member_update = update.my_chat_member
        if not member_update:
            return

        chat = member_update.chat
        new_status = member_update.new_chat_member.status
        user = member_update.from_user

        user_settings = get_user_settings(user.id)
        lang = user_settings.get('language_code', 'en')
        tariff_key = user_settings.get('tariff', 'free')

        def local_get_text(key):
            # Safe localization helper for this handler
            return TEXTS.get(lang, TEXTS['en']).get(key, TEXTS['en'].get(key, key))

        if new_status == "administrator":
            # --- CHECK CHANNEL LIMITS ---
            limits = get_tariff_limits(tariff_key)
            max_channels = limits.get('channels', 1)
            current_channels = get_user_channels(user.id)
            is_existing = any(c['channel_id'] == chat.id for c in current_channels)

            if not is_existing and len(current_channels) >= max_channels:
                logger.warning(f"Channel limit reached for user {user.id}. Leaving chat {chat.id}")
                try:
                    await context.bot.leave_chat(chat.id)
                    error_text = local_get_text('limit_error_channels').format(
                        current=len(current_channels),
                        max=max_channels,
                        tariff=limits['name']
                    )
                    await context.bot.send_message(chat_id=user.id, text=error_text)
                except Exception as e:
                    logger.error(f"Failed to handle channel limit enforcement: {e}")
                return
            # --- END LIMIT CHECK ---

            # --- ADD CHANNEL (With Unique Check) ---
            success, msg = add_channel(
                user_id=user.id,
                channel_id=chat.id,
                title=chat.title,
                username=chat.username
            )

            if not success:
                # Channel occupied by someone else
                logger.warning(f"User {user.id} tried to add occupied channel {chat.id}")
                try:
                    await context.bot.leave_chat(chat.id)
                    # Localized error
                    error_text = local_get_text('channel_occupied_error')
                    await context.bot.send_message(chat_id=user.id, text=error_text)
                except Exception as e:
                    logger.error(f"Failed to leave occupied channel: {e}")
                return

            # Success logic
            try:
                text = local_get_text('channel_add_success').format(title=chat.title)
                await context.bot.send_message(chat_id=user.id, text=text)
            except (TelegramError, Forbidden):
                logger.warning(f"Could not notify user {user.id}")

            logger.info(f"Бот добавлен в {chat.title} (ID: {chat.id}) пользователем {user.id}")

        elif new_status in ["left", "kicked"]:
            deactivate_channel(chat.id)
            try:
                text = local_get_text('channel_removed').format(title=chat.title)
                await context.bot.send_message(chat_id=user.id, text=text)
            except (TelegramError, Forbidden):
                pass
            logger.info(f"Бот удален из {chat.title} (ID: {chat.id})")

    except Exception as e:
        logger.error(f"Error in my_chat_member_handler: {e}", exc_info=True)