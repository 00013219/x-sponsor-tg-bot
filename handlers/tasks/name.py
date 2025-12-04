# --- Установка Названия ---
import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from handlers.tasks.constructor import show_task_constructor
from handlers.tasks.time import delete_message_after_delay
from keyboards.task_constructor import back_to_constructor_keyboard
from localization.loader import get_text
from services.task_service import update_task_field, get_or_create_task_id
from states.conversation import TASK_SET_NAME, TASK_CONSTRUCTOR
from utils.logging import logger


async def task_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📝 Название задачи'"""
    query = update.callback_query
    await query.answer()

    text = get_text('task_ask_name', context)
    await query.edit_message_text(
        text,
        reply_markup=back_to_constructor_keyboard(context)
    )
    return TASK_SET_NAME


async def task_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Updates name and triggers hot-reload if active, with proper cleanup"""
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)

    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    task_name = update.message.text.strip()

    # Delete user's input message
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение пользователя: {e}")

    # This triggers the Hot Reload via update_task_field -> refresh_task_jobs
    await update_task_field(task_id, 'task_name', task_name, context)

    # Send temporary confirmation
    msg = await update.message.reply_text(get_text('task_name_saved', context))
    # Auto-delete confirmation after 2 seconds
    asyncio.create_task(delete_message_after_delay(context, update.message.chat_id, msg.message_id, 2))

    return await show_task_constructor(update, context)