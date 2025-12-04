from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import db_query
from localization.loader import get_text
from states.conversation import BOSS_PANEL


async def boss_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей"""
    query = update.callback_query
    await query.answer()

    users = get_recent_users(100)

    text = get_text('boss_users_title', context) + "\n\n"

    for user in users:
        username = f"@{user['username']}" if user['username'] else get_text('boss_users_no_username', context)
        text += f"• {username} (ID: {user['user_id']}) - {user['tariff']}\n"

    text += get_text('boss_users_total_shown', context).format(count=len(users))

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL

def get_recent_users(limit=100):
    """Get recent users list"""
    return db_query("""
        SELECT user_id, username, first_name, created_at, tariff
        FROM users
        WHERE is_active = TRUE
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,), fetchall=True) or []