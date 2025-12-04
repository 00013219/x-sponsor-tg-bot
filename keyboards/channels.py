from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.queries.channels import get_user_channels
from localization.loader import get_text
from utils.text_utils import generate_smart_name


def channels_selection_keyboard(context: ContextTypes.DEFAULT_TYPE, selected_channels: List[int] = None):
    """Клавиатура выбора каналов с галочками"""
    if selected_channels is None:
        selected_channels = []

    user_id = context.user_data.get('user_id')
    channels = get_user_channels(user_id)

    keyboard = []
    for ch in channels:
        channel_id = ch['channel_id']
        raw_title = ch['channel_title'] or ch['channel_username'] or f"ID: {channel_id}"

        # --- FIX: Truncate to 3 words ---
        title = generate_smart_name(raw_title, context, limit=3)

        # Добавляем галочку если канал выбран
        prefix = "✅ " if channel_id in selected_channels else ""
        keyboard.append([
            InlineKeyboardButton(
                f"{prefix}{title}",
                callback_data=f"channel_toggle_{channel_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")]
    )

    return InlineKeyboardMarkup(keyboard)