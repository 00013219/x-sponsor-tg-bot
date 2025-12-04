from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.settings import get_user_settings
from utils.logging import logger


async def load_user_settings(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Загружает настройки пользователя в user_data"""
    settings = get_user_settings(user_id)
    context.user_data['user_id'] = user_id
    context.user_data['language_code'] = settings.get('language_code', 'en')
    context.user_data['timezone'] = settings.get('timezone', 'Europe/Moscow')
    context.user_data['tariff'] = settings.get('tariff', 'free')


async def send_or_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                               reply_markup: InlineKeyboardMarkup, cleanup_previous: bool = True):
    """
    Отправляет новое или редактирует существующее сообщение.
    Enhanced: Better error handling when messages are already deleted.
    """
    query = update.callback_query
    chat_id = None
    user_data = context.user_data

    # Get chat_id
    if query and query.message:
        chat_id = query.message.chat_id
    elif update.message:
        chat_id = update.message.chat_id

    # Cleanup previous bot message if requested
    if cleanup_previous and chat_id and 'last_bot_message_id' in user_data:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=user_data['last_bot_message_id'])
        except Exception as e:
            # Message might already be deleted, ignore
            pass

    try:
        if query and query.message:
            # Try to edit existing message
            try:
                await query.edit_message_text(text, reply_markup=reply_markup)
                if cleanup_previous:
                    user_data['last_bot_message_id'] = query.message.message_id
                return
            except BadRequest as e:
                if "Message to edit not found" in str(e) or "Message is not modified" in str(e):
                    # Message was deleted or is the same, send new one
                    raise TelegramError("Message not found for editing")
                else:
                    raise e
        else:
            # This path is for when there's no query (message-based update)
            raise TelegramError("No query message to edit")

    except TelegramError as e:
        # Если "Message is not modified" или "Message to edit not found" - отправляем новое сообщение
        if "Message is not modified" in str(e) or "Message not found" in str(e) or "Message to edit not found" in str(
                e):
            # Send new message instead
            try:
                if update.message:
                    msg = await update.message.reply_text(text, reply_markup=reply_markup)
                else:
                    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

                if cleanup_previous and msg:
                    user_data['last_bot_message_id'] = msg.message_id

            except Exception as send_e:
                logger.error(f"Failed to send fallback message: {send_e}")
        else:
            # Other Telegram errors
            logger.warning(f"Edit failed ({e}), sending new message instead.")
            try:
                if update.message:
                    msg = await update.message.reply_text(text, reply_markup=reply_markup)
                else:
                    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

                if cleanup_previous and msg:
                    user_data['last_bot_message_id'] = msg.message_id

            except Exception as send_e:
                logger.error(f"Failed to send fallback message: {send_e}")

    if query:
        await query.answer()



def determine_task_status_color(task_id: int) -> str:
    """
    UPDATED Logic:
    🟢 Green: Has future scheduled posts
    🟡 Yellow: No future posts, but has posts waiting for auto-deletion
    🔴 Red: All posts are done (published and either deleted or no auto-delete)
    """
    now_utc = datetime.now(ZoneInfo('UTC'))

    # 1. Check for FUTURE schedules
    future_scheduled = db_query("""
        SELECT COUNT(*) as count 
        FROM publication_jobs 
        WHERE task_id = %s 
        AND status = 'scheduled'
    """, (task_id,), fetchone=True)

    if future_scheduled and future_scheduled['count'] > 0:
        return '🟢'  # Active: Has future posts

    # 2. Check for posts WAITING for auto-deletion
    # Condition: Status is published AND Auto-delete is ON (>0) AND Time hasn't passed yet
    pending_delete = db_query("""
        SELECT COUNT(*) as count 
        FROM publication_jobs 
        WHERE task_id = %s 
        AND status = 'published'
        AND auto_delete_hours > 0
        AND published_at + (auto_delete_hours || ' hours')::INTERVAL > %s
    """, (task_id, now_utc), fetchone=True)

    if pending_delete and pending_delete['count'] > 0:
        return '🟡'  # Finishing: Waiting for auto-deletion

    # 3. Default: Finished (Red)
    # This covers: No jobs, jobs finished and deleted, jobs finished with no auto-delete
    return '🔴'