from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from localization.loader import get_text
from models.tariff import get_tariff_limits
from states.conversation import BOSS_PANEL


async def boss_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика по доходам"""
    query = update.callback_query
    await query.answer()

    stats = get_money_statistics()

    text = get_text('boss_money_title', context) + "\n\n"
    text += get_text('boss_money_tariff_title', context) + "\n"

    for tariff, count in stats['by_tariff'].items():
        limits = get_tariff_limits(tariff)
        text += get_text('boss_money_tariff_item', context).format(name=limits['name'], count=count,
                                                                   price=limits['price']) + "\n"

    text += get_text('boss_money_estimated_revenue', context).format(revenue=stats['estimated_revenue'])
    text += get_text('boss_money_note', context)

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL

from database.connection import db_query


def get_money_statistics():
    """Get revenue statistics"""
    stats = {}

    # This is a placeholder - in production you'd track actual payments
    # Count users by tariff
    tariff_counts = db_query("""
        SELECT tariff, COUNT(*) as count
        FROM users
        WHERE is_active = TRUE
        GROUP BY tariff
    """, fetchall=True) or []

    stats['by_tariff'] = {row['tariff']: row['count'] for row in tariff_counts}

    # Calculate estimated revenue (placeholder)
    total_revenue = 0
    for tariff_key, count in stats['by_tariff'].items():
        limits = get_tariff_limits(tariff_key)
        total_revenue += limits['price'] * count

    stats['estimated_revenue'] = total_revenue

    return stats