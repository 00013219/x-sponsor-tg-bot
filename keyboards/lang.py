from telegram import InlineKeyboardMarkup, InlineKeyboardButton


def lang_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang_ru"),
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang_en"),
            InlineKeyboardButton("🇪🇸 ES", callback_data="lang_es"),
        ],
        [
            InlineKeyboardButton("🇫🇷 FR", callback_data="lang_fr"),
            InlineKeyboardButton("🇺🇦 UA", callback_data="lang_ua"),
            InlineKeyboardButton("🇩🇪 DE", callback_data="lang_de"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)