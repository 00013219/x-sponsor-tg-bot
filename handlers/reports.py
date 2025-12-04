from telegram import Update
from telegram.ext import ContextTypes

from keyboards.task_constructor import back_to_main_menu_keyboard
from localization.loader import get_text
from states.conversation import REPORTS


async def nav_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Отчеты'"""

    # --- ИСПРАВЛЕНИЕ ---
    # Обрабатываем и CallbackQuery, и Message
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
    else:
        message = update.message  # Это Message от ReplyKeyboard
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    text = get_text('reports_title', context)

    # Используем reply_text, чтобы он работал в обоих случаях
    await message.reply_text(
        text,
        reply_markup=back_to_main_menu_keyboard(context)
    )
    return REPORTS