from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config.settings import OWNER_ID
from localization.loader import get_text


def main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get('user_id', 0)

    keyboard = [
        [InlineKeyboardButton(get_text('nav_new_task_btn', context), callback_data="nav_new_task")],
        [InlineKeyboardButton(get_text('nav_my_tasks_btn', context), callback_data="nav_my_tasks")],
        [InlineKeyboardButton(get_text('nav_channels_btn', context), callback_data="nav_channels")],
        [InlineKeyboardButton(get_text('nav_free_dates_btn', context), callback_data="nav_free_dates")],
        [InlineKeyboardButton(get_text('nav_tariff_btn', context), callback_data="nav_tariff")],
    ]

    if user_id == OWNER_ID:
        keyboard.append([InlineKeyboardButton(get_text('nav_boss_btn', context), callback_data="nav_boss")])

    return InlineKeyboardMarkup(keyboard)
