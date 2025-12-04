from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from localization.loader import get_text


def boss_panel_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура админ-панели (локализованная)"""
    keyboard = [
        [InlineKeyboardButton(get_text('boss_mailing_btn', context), callback_data="boss_mailing")],
        [InlineKeyboardButton(get_text('boss_signature_btn', context), callback_data="boss_signature")],
        [InlineKeyboardButton(get_text('boss_users_btn', context), callback_data="boss_users")],
        [InlineKeyboardButton(get_text('boss_stats_btn', context), callback_data="boss_stats")],
        [InlineKeyboardButton(get_text('boss_ban_btn', context), callback_data="boss_ban")],
        [InlineKeyboardButton(get_text('boss_grant_btn', context), callback_data="boss_grant")],  # NEW
        [InlineKeyboardButton(get_text('boss_money_btn', context), callback_data="boss_money")],
        [InlineKeyboardButton(get_text('boss_logs_btn', context), callback_data="boss_logs")],
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)
