from telegram import Update
from telegram.ext import ContextTypes

from config.settings import OWNER_ID
from handlers.admin.stats import get_bot_statistics
from keyboards.boss import boss_panel_keyboard
from localization.loader import get_text
from states.conversation import MAIN_MENU, BOSS_PANEL


async def nav_boss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает админ-панель"""

    query = update.callback_query
    message = update.message

    # 🔥 Case 1: ReplyKeyboard or text ("Boss") → update.message
    if message:
        if message.from_user.id != OWNER_ID:
            await message.reply_text(get_text('boss_no_access', context))
            return MAIN_MENU

        # Send new Boss menu
        text = get_text('boss_menu_title', context)
        text += "\n\n" + get_text('boss_quick_stats', context) + "\n"

        stats = get_bot_statistics()
        text += get_text('boss_total_users', context).format(total_users=stats['total_users']) + "\n"
        text += get_text('boss_active_users', context).format(active_users=stats['active_users']) + "\n"
        text += get_text('boss_active_tasks', context).format(tasks_active=stats['tasks_active']) + "\n"

        await message.reply_text(
            text,
            reply_markup=boss_panel_keyboard(context)
        )
        return BOSS_PANEL

    # 🔥 Case 2: InlineKeyboard → update.callback_query
    if query:
        await query.answer()

        if query.from_user.id != OWNER_ID:
            await query.answer(get_text('boss_no_access', context))
            return MAIN_MENU

        text = get_text('boss_menu_title', context)
        text += "\n\n" + get_text('boss_quick_stats', context) + "\n"

        stats = get_bot_statistics()
        text += get_text('boss_total_users', context).format(total_users=stats['total_users']) + "\n"
        text += get_text('boss_active_users', context).format(active_users=stats['active_users']) + "\n"
        text += get_text('boss_active_tasks', context).format(tasks_active=stats['tasks_active']) + "\n"

        await query.edit_message_text(
            text,
            reply_markup=boss_panel_keyboard(context)
        )
        return BOSS_PANEL