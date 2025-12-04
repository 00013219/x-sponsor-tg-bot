from telegram import KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from config.settings import OWNER_ID
from localization.loader import get_text


def persistent_reply_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Постоянная клавиатура (ReplyKeyboard), отображаемая во всех состояниях"""
    user_id = context.user_data.get('user_id', 0)
    lang = context.user_data.get('language_code', 'en')

    keyboard = [
        [
            KeyboardButton(get_text('nav_new_task_btn', context, lang)),
            KeyboardButton(get_text('nav_my_tasks_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_language_btn', context, lang)),
            KeyboardButton(get_text('nav_timezone_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_tariff_btn', context, lang)),
            KeyboardButton(get_text('nav_channels_btn', context, lang))
        ]
    ]

    # Добавляем кнопку "Boss" только владельцу
    if user_id == OWNER_ID:
        keyboard.append([KeyboardButton(get_text('nav_boss_btn', context, lang))])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )

def main_menu_reply_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура с кнопками внизу экрана (ReplyKeyboard)"""
    user_id = context.user_data.get('user_id', 0)

    # Получаем язык пользователя ДЛЯ создания кнопок
    lang = context.user_data.get('language_code', 'en')

    keyboard = [
        [
            KeyboardButton(get_text('nav_new_task_btn', context, lang)),
            KeyboardButton(get_text('nav_my_tasks_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_language_btn', context, lang)),
            KeyboardButton(get_text('nav_timezone_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_tariff_btn', context, lang)),
            KeyboardButton(get_text('nav_channels_btn', context, lang))
        ]
    ]

    if user_id == OWNER_ID:
        keyboard.append([KeyboardButton(get_text('nav_boss_btn', context, lang))])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )