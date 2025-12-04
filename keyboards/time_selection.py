from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.timezones import TIMEZONES
from localization.loader import get_text


def timezone_keyboard(context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    cities = list(TIMEZONES.keys())

    # Создаем кнопки по 2 в ряд
    for i in range(0, len(cities), 2):
        row = []
        for j in range(2):
            if i + j < len(cities):
                city = cities[i + j]
                tz_name, utc_offset = TIMEZONES[city]

                # Локализация названия города
                city_localized = get_text(f"tz_{city}", context) or city

                row.append(
                    InlineKeyboardButton(
                        f"{city_localized} ({utc_offset})",
                        callback_data=f"tz_{tz_name}"
                    )
                )
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)

def time_selection_keyboard(context: ContextTypes.DEFAULT_TYPE, selected_times: List[str] = None):
    """Клавиатура выбора времени как на изображении"""
    if selected_times is None:
        selected_times = []

    keyboard = []

    # Создаем сетку 6x4 для времени
    times = []
    for hour in range(24):
        times.append(f"{hour:02d}:00")

    # Разбиваем на 6 строк по 4 столбца
    for i in range(0, 24, 4):
        row = []
        for j in range(4):
            if i + j < 24:
                time_str = times[i + j]
                is_selected = time_str in selected_times
                prefix = "✅" if is_selected else ""
                row.append(InlineKeyboardButton(f"{prefix}{time_str}", callback_data=f"time_select_{time_str}"))
        keyboard.append(row)

    # Кнопка для ввода своего времени
    keyboard.append([
        InlineKeyboardButton(get_text('time_custom', context), callback_data="time_custom")
    ])

    # Кнопки управления
    keyboard.append([
        InlineKeyboardButton(get_text('time_clear', context), callback_data="time_clear"),
        InlineKeyboardButton("⬅️ Назад", callback_data="task_back_to_constructor"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="nav_main_menu")
    ])

    return InlineKeyboardMarkup(keyboard)