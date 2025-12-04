from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from localization.loader import get_text


def pin_duration_keyboard(context: ContextTypes.DEFAULT_TYPE, current_duration: int = None):
    """Classe selection keyboard + Custom button"""
    options = [
        (12, 'duration_12h'),
        (24, 'duration_24h'),
        (48, 'duration_48h'),
        (72, 'duration_3d'),
        (168, 'duration_7d'),
        (0, 'duration_no')
    ]

    keyboard = []
    for value, key in options:
        text = get_text(key, context)
        if current_duration is not None and value == current_duration:
            text = f"✅ {text}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"pin_{value}")])

    # --- NEW: Custom Button ---
    keyboard.append([InlineKeyboardButton(get_text('time_custom', context), callback_data="pin_custom")])

    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
    ])

    return InlineKeyboardMarkup(keyboard)

def delete_duration_keyboard(context: ContextTypes.DEFAULT_TYPE, current_duration: int = None):
    """Auto-delete selection keyboard + Custom button"""
    options = [
        (12, 'duration_12h'),
        (24, 'duration_24h'),
        (48, 'duration_48h'),
        (72, 'duration_3d'),
        (168, 'duration_7d'),
        (0, 'duration_no')
    ]

    keyboard = []
    for value, key in options:
        text = get_text(key, context)
        if current_duration is not None and value == current_duration:
            text = f"✅ {text}"
        keyboard.append([InlineKeyboardButton(text, callback_data=f"delete_{value}")])

    # --- NEW: Custom Button ---
    keyboard.append([InlineKeyboardButton(get_text('time_custom', context), callback_data="delete_custom")])

    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)