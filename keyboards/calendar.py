from datetime import datetime
from typing import List
import calendar
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from localization.loader import get_text


def calendar_keyboard(
        context: ContextTypes.DEFAULT_TYPE,
        year: int,
        month: int,
        selected_dates: List[str] = None,
        selected_weekdays: List[int] = None,
        today_user_date: datetime.date = None
):
    """Клавиатура календаря (Обновленная)"""
    if selected_dates is None:
        selected_dates = []
    if selected_weekdays is None:
        selected_weekdays = []
    if today_user_date is None:
        today_user_date = datetime.now().date()

    cal = calendar.monthcalendar(year, month)

    try:
        weekdays_str = get_text('calendar_weekdays_short', context)
        weekdays = weekdays_str.split(',')
        if len(weekdays) != 7: raise Exception
    except Exception:
        weekdays = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

    keyboard = []

    # 1. Заголовок месяца
    month_name = get_text(f"month_{month}", context) or str(month)
    header_row = [InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore")]
    keyboard.append(header_row)

    # 2. Дни недели (Пн, Вт...) с галочками
    weekday_row = []
    for i, day_name in enumerate(weekdays):
        prefix = "✅" if i in selected_weekdays else ""
        weekday_row.append(InlineKeyboardButton(f"{prefix}{day_name}", callback_data=f"calendar_wd_{i}"))
    keyboard.append(weekday_row)

    # 3. Сетка дней
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                current_date = datetime(year, month, day).date()
                date_str = current_date.strftime('%Y-%m-%d')
                is_past = current_date < today_user_date
                is_selected_date = date_str in selected_dates

                # --- ИЗМЕНЕНИЕ (Задача 1): Убрали отображение 🗓️ для дней недели ---
                # Теперь проверяем только конкретную дату
                prefix = " "
                if is_selected_date:
                    prefix = "✅"

                callback = f"calendar_day_{date_str}"

                if is_past:
                    prefix = "❌"
                    callback = "calendar_ignore_past"

                row.append(InlineKeyboardButton(f"{prefix}{day}", callback_data=callback))
        keyboard.append(row)

    # --- ИЗМЕНЕНИЕ (Задача 2): Кнопка "Весь месяц" ---
    # Добавляем её перед навигацией
    keyboard.append([
        InlineKeyboardButton(get_text('calendar_select_all_btn', context),
                             callback_data="calendar_select_all")
    ])

    # 4. Навигация
    keyboard.append([
        InlineKeyboardButton("⬅️", callback_data="calendar_prev"),
        InlineKeyboardButton(get_text('calendar_reset', context), callback_data="calendar_reset"),
        InlineKeyboardButton("➡️", callback_data="calendar_next")
    ])

    # 5. Выход
    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")]
    )

    return InlineKeyboardMarkup(keyboard)