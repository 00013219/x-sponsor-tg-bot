from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from localization.loader import get_text
from states.conversation import BOSS_PANEL


async def boss_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Критические ошибки"""
    query = update.callback_query
    await query.answer()

    logs = get_critical_logs(50)

    text = get_text('boss_logs_title', context) + "\n\n"

    if not logs:
        text += get_text('boss_logs_no_errors', context)
        text += get_text('boss_logs_info', context)
    else:
        for log in logs:
            text += f"• {log}\n"

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


def get_critical_logs(limit=50):
    """Get recent critical errors from logs"""
    # This is a placeholder - in production you'd log to a table
    # For now, return empty or read from log file
    return []