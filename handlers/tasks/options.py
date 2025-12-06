# --- Настройка закрепления ---
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from database.queries.settings import get_user_settings
from database.queries.tasks import get_task_details
from database.queries.users import get_user_by_username
from handlers.tasks.constructor import show_task_constructor, get_task_constructor_text
from handlers.tasks.time import delete_message_after_delay
from keyboards.duration import delete_duration_keyboard, pin_duration_keyboard
from keyboards.task_constructor import task_constructor_keyboard, back_to_constructor_keyboard
from localization.loader import get_text
from services.task_service import update_task_field, can_modify_task_parameter, get_or_create_task_id
from states.conversation import TASK_CONSTRUCTOR, TASK_SET_PIN, TASK_SET_PIN_CUSTOM, TASK_SET_DELETE, \
    TASK_SET_DELETE_CUSTOM, TASK_SET_ADVERTISER
from utils.logging import logger
from utils.time_utils import parse_human_duration


async def task_set_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка закрепления (Вход)"""
    query = update.callback_query

    # Get current value to show checkmark immediately
    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR

    await query.answer()

    task = get_task_details(task_id)
    current_duration = task['pin_duration'] if task else 0

    text = get_text('duration_ask_pin', context)
    await query.edit_message_text(
        text,
        reply_markup=pin_duration_keyboard(context, current_duration)
    )
    return TASK_SET_PIN

async def pin_duration_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор длительности закрепления (Действие)"""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    duration = int(query.data.replace("pin_", ""))

    # Update DB
    await update_task_field(task_id, 'pin_duration', duration, context)

    # STAY on the same screen, but update the keyboard to move the checkmark
    text = get_text('duration_ask_pin', context)
    try:
        await query.edit_message_text(
            text,
            reply_markup=pin_duration_keyboard(context, current_duration=duration)
        )
    except TelegramError:
        # Ignore "Message is not modified" if user clicks the same button
        pass

    # Return the SAME state instead of calling show_task_constructor
    return TASK_SET_PIN


async def pin_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for custom pin duration with instructions"""
    query = update.callback_query
    await query.answer()

    # Updated Text with Instructions
    text = get_text('duration_ask_custom', context)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_set_pin")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    return TASK_SET_PIN_CUSTOM

async def pin_receive_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process custom pin duration with minute precision"""
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    text_input = update.message.text.strip()

    hours = parse_human_duration(text_input)

    if hours is None:
        msg = await update.message.reply_text(get_text('duration_invalid_format', context))
        asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, msg.message_id, 3))
        return TASK_SET_PIN_CUSTOM

    # Update DB (Requires FLOAT column)
    await update_task_field(task_id, 'pin_duration', hours, context)

    # Cleanup input
    try:
        await update.message.delete()
    except Exception:
        pass

    # Format output for display (e.g. if < 1 hour, show minutes)
    if hours < 1:
        display_time = f"{int(hours * 60)}m"
    else:
        display_time = f"{hours:.1f}h".replace(".0h", "h")

    msg = await context.bot.send_message(
        update.effective_chat.id, 
        get_text('duration_pin_set', context).format(duration=display_time)
    )
    asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, msg.message_id, 2))

    return await show_task_constructor(update, context)


async def task_set_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка автоудаления (Вход)"""
    query = update.callback_query

    # Get current value to show checkmark immediately
    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR

    await query.answer()

    task = get_task_details(task_id)
    current_duration = task['auto_delete_hours'] if task else 0

    text = get_text('duration_ask_delete', context)
    await query.edit_message_text(
        text,
        reply_markup=delete_duration_keyboard(context, current_duration)
    )
    return TASK_SET_DELETE

async def delete_duration_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор длительности автоудаления (Действие)"""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    duration = int(query.data.replace("delete_", ""))

    # Update DB
    await update_task_field(task_id, 'auto_delete_hours', duration, context)

    # STAY on the same screen, but update the keyboard to move the checkmark
    text = get_text('duration_ask_delete', context)
    try:
        await query.edit_message_text(
            text,
            reply_markup=delete_duration_keyboard(context, current_duration=duration)
        )
    except TelegramError:
        # Ignore "Message is not modified" if user clicks the same button
        pass

    # Return the SAME state instead of calling show_task_constructor
    return TASK_SET_DELETE


async def delete_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for custom auto-delete duration with instructions"""
    query = update.callback_query
    await query.answer()

    # Updated Text with Instructions
    text = get_text('duration_ask_custom', context)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_set_delete")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    return TASK_SET_DELETE_CUSTOM

async def delete_receive_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process custom auto-delete duration with minute precision"""
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    text_input = update.message.text.strip()

    hours = parse_human_duration(text_input)

    if hours is None:
        msg = await update.message.reply_text(get_text('duration_invalid_format', context))
        asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, msg.message_id, 3))
        return TASK_SET_DELETE_CUSTOM

    # Update DB
    await update_task_field(task_id, 'auto_delete_hours', hours, context)

    try:
        await update.message.delete()
    except Exception:
        pass

    if hours < 1:
        display_time = f"{int(hours * 60)}m"
    else:
        display_time = f"{hours:.1f}h".replace(".0h", "h")

    msg = await context.bot.send_message(
        update.effective_chat.id, 
        get_text('duration_autodelete_set', context).format(duration=display_time)
    )
    asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, msg.message_id, 2))

    return await show_task_constructor(update, context)


async def task_set_advertiser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка рекламодателя"""
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR

    await query.answer()

    text = get_text('task_ask_advertiser', context)
    await query.edit_message_text(
        text,
        reply_markup=back_to_constructor_keyboard(context)
    )
    return TASK_SET_ADVERTISER


async def task_receive_advertiser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение username рекламодателя и уведомление"""
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    username = update.message.text.strip()

    if username.startswith('@'):
        username = username[1:]

    advertiser_user = get_user_by_username(username)

    if not advertiser_user:
        await update.message.reply_text(get_text('task_advertiser_not_found', context))
        return TASK_SET_ADVERTISER

    # Save to DB
    await update_task_field(task_id, 'advertiser_user_id', advertiser_user['user_id'], context)

    # --- NOTIFY ADVERTISER (Task 1) ---
    try:
        # Get advertiser's language settings
        adv_settings = get_user_settings(advertiser_user['user_id'])
        adv_lang = adv_settings.get('language_code', 'en')

        task = get_task_details(task_id)
        task_name = task.get('task_name', 'Unknown')

        # Localized notification
        notify_text = get_text('advertiser_notification', context, lang=adv_lang).format(
            task_name=task_name,
            task_id=task_id
        )

        await context.bot.send_message(chat_id=advertiser_user['user_id'], text=notify_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to notify advertiser {advertiser_user['user_id']}: {e}")
    # ----------------------------------

    confirmation = get_text('task_advertiser_saved', context) + "\n"
    confirmation += get_text('advertiser_will_be_notified', context).format(username=username)

    await update.message.reply_text(confirmation)

    return await show_task_constructor(update, context)



async def task_set_pin_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the toggle for the 'Pin Sound' feature.
    """
    query = update.callback_query

    # --- FIX: Prevent Timeout Crash ---
    try:
        await query.answer()
    except Exception:
        pass
        # ----------------------------------

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        return TASK_CONSTRUCTOR

    task = get_task_details(task_id)
    current_status = task.get('pin_notify', False)

    # Toggle status
    new_status = not current_status

    await update_task_field(task_id, 'pin_notify', new_status, context)

    # Refresh screen
    text = get_task_constructor_text(context)
    keyboard = task_constructor_keyboard(context)

    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    except Exception:
        pass

    return TASK_CONSTRUCTOR

async def task_set_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle report status with immediate answer to prevent timeout"""
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')

    # Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(get_text('task_error_no_name_or_message', context), show_alert=False)
        return TASK_CONSTRUCTOR

    task = get_task_details(task_id)
    new_value = not task['report_enabled']

    # --- FIX: Calculate text and Answer IMMEDIATELY ---
    status_text = get_text('status_yes', context) if new_value else get_text('status_no', context)
    alert_text = get_text('alert_report_status', context).format(status=status_text)

    try:
        await query.answer(alert_text)
    except Exception:
        pass  # Ignore if already answered or timed out, proceed with logic

    # --- Perform Logic after answering ---
    await update_task_field(task_id, 'report_enabled', new_value, context)

    return await show_task_constructor(update, context)


async def task_set_post_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение типа поста с валидацией длины сообщения"""
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR

    task = get_task_details(task_id)

    # Переключаем между from_bot и repost
    new_value = 'repost' if task['post_type'] == 'from_bot' else 'from_bot'

    # --- Validation when switching from repost to from_bot ---
    if task['post_type'] == 'repost' and new_value == 'from_bot':
        import json
        MAX_MEDIA_CAPTION_LENGTH = 1024

        # Check media group data - captions are limited to 1024 chars
        media_group_data = task.get('media_group_data')
        if media_group_data:
            # Parse JSON if string
            if isinstance(media_group_data, str):
                try:
                    media_group_data = json.loads(media_group_data)
                except json.JSONDecodeError:
                    media_group_data = None

            if media_group_data:
                caption = media_group_data.get('caption', '')
                if caption and len(caption) > MAX_MEDIA_CAPTION_LENGTH:
                    await query.answer(
                        get_text('error_mediagroup_caption_limit_alert', context),
                        show_alert=True
                    )
                    return TASK_CONSTRUCTOR
        # Note: Single media messages (one photo/video) support up to 4096 chars caption
        # so no validation needed for them - only media groups are limited to 1024

    # Update the post type
    await update_task_field(task_id, 'post_type', new_value, context)

    # Show success alert
    type_text = get_text('status_from_bot', context) if new_value == 'from_bot' else get_text('status_repost', context)
    alert_text = get_text('alert_post_type_status', context).format(status=type_text)
    await query.answer(alert_text)

    return await show_task_constructor(update, context)