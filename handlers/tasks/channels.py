from telegram import Update
from telegram.ext import ContextTypes

from database.queries.channels import get_user_channels
from database.queries.task_channels import get_task_channels, add_task_channel, remove_task_channel
from keyboards.channels import channels_selection_keyboard
from keyboards.task_constructor import back_to_constructor_keyboard
from localization.loader import get_text
from services.task_service import get_or_create_task_id, can_modify_task_parameter, refresh_task_jobs
from states.conversation import TASK_SELECT_CHANNELS


async def task_select_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📢 Каналы'"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    selected_channels = get_task_channels(task_id)

    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    if not channels:
        await query.edit_message_text(
            get_text('dont_have_channels', context),
            reply_markup=back_to_constructor_keyboard(context)
        )
        return TASK_SELECT_CHANNELS

    text = get_text('choose_channel', context)
    await query.edit_message_text(
        text,
        reply_markup=channels_selection_keyboard(context, selected_channels)
    )
    return TASK_SELECT_CHANNELS


async def task_toggle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggling a channel selection"""
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation - Check if name or message is set
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_SELECT_CHANNELS

    await query.answer()

    channel_id = int(query.data.replace("channel_toggle_", ""))

    selected_channels = get_task_channels(task_id)

    if channel_id in selected_channels:
        remove_task_channel(task_id, channel_id)
    else:
        add_task_channel(task_id, channel_id)

    # --- HOT RELOAD ---
    await refresh_task_jobs(task_id, context)

    # ... (rest of the function: updating keyboard) ...
    selected_channels = get_task_channels(task_id)
    # --- FIX: Use Localized Text ---
    text = get_text('task_channels_title', context)
    await query.edit_message_text(
        text,
        reply_markup=channels_selection_keyboard(context, selected_channels)
    )
    return TASK_SELECT_CHANNELS