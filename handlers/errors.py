from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from keyboards.main_menu import main_menu_keyboard
from localization.loader import get_text
from utils.helpers import load_user_settings
from utils.logging import logger


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Общая команда отмены. Возвращает в Главное меню."""
    query = update.callback_query
    user_id = update.effective_user.id

    text = get_text('cancel', context)

    if query:
        await query.answer()
        await query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

    context.user_data.clear()
    await load_user_settings(user_id, context)

    await update.effective_chat.send_message(
        get_text('main_menu', context),
        reply_markup=main_menu_keyboard(context)
    )

    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логирование ошибок"""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)