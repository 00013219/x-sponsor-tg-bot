import calendar

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from telegram.error import TelegramError
from telegram import Update
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.schedules import get_task_schedules, add_task_schedule, remove_task_schedules
from keyboards.calendar import calendar_keyboard
from localization.loader import get_text
from models.tariff import get_tariff_limits
from services.task_service import can_modify_task_parameter, get_or_create_task_id, refresh_task_jobs
from states.conversation import TASK_CONSTRUCTOR, CALENDAR_VIEW
from utils.logging import logger


async def task_select_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📅 Календарь' (Refreshes the view)"""
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation - Check if name or message is set
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR

    await query.answer()

    task_id = context.user_data.get('current_task_id')
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')

    # Получаем "сегодня" в таймзоне юзера
    today_user = datetime.now(user_tz).date()

    # Получаем лимиты тарифа
    limits = get_tariff_limits(user_tariff)
    max_time_slots = limits['date_slots']

    # Получаем выбранные даты и дни недели из БД
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]
    selected_weekdays = [s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]  # 0-6

    # Устанавливаем текущий месяц
    if 'calendar_year' not in context.user_data:
        now = datetime.now(user_tz)
        context.user_data['calendar_year'] = now.year
        context.user_data['calendar_month'] = now.month

    year = context.user_data['calendar_year']
    month = context.user_data['calendar_month']

    # --- Формирование шапки ---
    header_text = ""
    if selected_dates:
        dates_str = ", ".join(
            sorted([datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in selected_dates])
        )

        month_str = datetime(year, month, 1).strftime("%B")

        header_text = get_text('calendar_header_dates', context).format(
            month_year_str=month_str,
            dates_str=dates_str
        )

    elif selected_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_str = ", ".join(
                sorted([wd_names[day] for day in selected_weekdays], key=lambda x: wd_names.index(x)))
            header_text = get_text('calendar_header_weekdays', context).format(weekdays_str=weekdays_str)
        except (IndexError, AttributeError):
            logger.warning(f"Error parsing calendar_weekdays_short for task {task_id}")
            header_text = get_text('calendar_header_weekdays', context).format(
                weekdays_str=f"{len(selected_weekdays)} days")

    text = header_text  # Шапка (или пусто)

    # Добавляем инфо-текст
    text += get_text('calendar_info_weekdays', context)
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots,
                                                                  tariff_name=limits['name'])

    # --- ERROR HANDLING FIX ---
    try:
        await query.edit_message_text(
            text,
            reply_markup=calendar_keyboard(context, year, month, selected_dates, selected_weekdays, today_user),
            parse_mode='Markdown'
        )
    except TelegramError as e:
        # Ignore "Message is not modified" errors
        if "Message is not modified" not in str(e):
            logger.warning(f"Error updating calendar view: {e}")
            # Optionally try to send a new message if edit failed due to age
            # await query.message.reply_text(text, reply_markup=...)

    return CALENDAR_VIEW


async def calendar_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по месяцам в календаре"""
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')

    # Task 3: Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')

    today_user = datetime.now(user_tz).date()

    limits = get_tariff_limits(user_tariff)
    max_time_slots = limits['date_slots']

    action = query.data

    year = context.user_data.get('calendar_year', datetime.now(user_tz).year)
    month = context.user_data.get('calendar_month', datetime.now(user_tz).month)

    if action == "calendar_prev":
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    elif action == "calendar_next":
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    context.user_data['calendar_year'] = year
    context.user_data['calendar_month'] = month

    # Получаем выбранные даты и дни недели из БД
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]
    selected_weekdays = [s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]

    # --- Формирование шапки ---
    header_text = ""
    if selected_dates:
        dates_str = ", ".join(sorted([datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in selected_dates]))
        month_year_str = datetime(year, month, 1).strftime("%B %Y")
        header_text = get_text('calendar_header_dates', context).format(month_year_str=month_year_str,
                                                                        dates_str=dates_str)

    elif selected_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_str = ", ".join(
                sorted([wd_names[day] for day in selected_weekdays], key=lambda x: wd_names.index(x)))
            header_text = get_text('calendar_header_weekdays', context).format(weekdays_str=weekdays_str)
        except (IndexError, AttributeError):
            logger.warning(f"Error parsing calendar_weekdays_short for task {task_id}")
            header_text = get_text('calendar_header_weekdays', context).format(
                weekdays_str=f"{len(selected_weekdays)} days")

    text = header_text  # Шапка (или пусто)

    # Добавляем инфо-текст
    text += get_text('calendar_info_weekdays', context)
    # --- ⬇️ FIXED LINE ⬇️ ---
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots,
                                                                  tariff_name=limits['name'])
    # --- ⬆️ FIXED LINE ⬆️ ---

    try:
        await query.edit_message_text(
            text,
            reply_markup=calendar_keyboard(context, year, month, selected_dates, selected_weekdays, today_user),
            parse_mode='Markdown'
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Error in calendar navigation: {e}")
    return CALENDAR_VIEW


async def calendar_ignore_past(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатие на прошедшую дату в календаре"""
    query = update.callback_query
    await query.answer("Эта дата уже прошла и недоступна для выбора.", show_alert=True)
    return CALENDAR_VIEW


async def calendar_day_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Selects a specific date. Strictly removes any Weekdays."""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)

    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=True
        )
        return CALENDAR_VIEW

    date_str = query.data.replace("calendar_day_", "")

    # 1. Enforce Mutual Exclusivity: Remove ANY weekdays
    db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_weekday IS NOT NULL",
             (task_id,), commit=True)

    # 2. Toggle Date
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_dates = limits['date_slots']

    if date_str in selected_dates:
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_date = %s",
                 (task_id, date_str), commit=True)
        await query.answer()
    else:
        if len(selected_dates) >= max_dates:
            alert_text = get_text('limit_error_dates', context).format(
                current=len(selected_dates),
                max=max_dates,
                tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=False)
            return CALENDAR_VIEW

        # UPDATED: Preserve times independently
        times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))
        if times:
            for time_str in times:
                add_task_schedule(task_id, 'datetime', schedule_date=date_str, schedule_time=time_str)
        else:
            # No times selected yet - just add the date
            add_task_schedule(task_id, 'date', schedule_date=date_str)

        await query.answer()

    await refresh_task_jobs(task_id, context)
    return await task_select_calendar(update, context)


async def calendar_select_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Selects the whole month (Remaining Future Days) with limit checks.
    """
    query = update.callback_query
    # Do not answer query immediately to allow alerts

    task_id = context.user_data.get('current_task_id')
    year = context.user_data.get('calendar_year', datetime.now().year)
    month = context.user_data.get('calendar_month', datetime.now().month)

    # 1. Get User Timezone and "Today"
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except:
        user_tz = ZoneInfo('UTC')

    # Current date for the user
    today_user = datetime.now(user_tz).date()

    # 2. Get User Limits
    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['date_slots']

    # 3. Calculate Days in Month
    _, num_days = calendar.monthrange(year, month)

    # 4. Filter: Collect only valid future dates
    valid_dates_to_add = []

    for day in range(1, num_days + 1):
        # Create date object for the specific day in the calendar
        current_date_obj = datetime(year, month, day).date()

        # SKIP PAST DAYS: If the day is before today, don't include it
        if current_date_obj < today_user:
            continue

        valid_dates_to_add.append(current_date_obj)

    count_to_add = len(valid_dates_to_add)

    # --- EDGE CASE: Month is completely in the past ---
    if count_to_add == 0:
        await query.answer(get_text('calendar_ignore_past', context),
                           show_alert=True)
        return CALENDAR_VIEW

    # --- CHECK LIMIT (Against remaining days only) ---
    if count_to_add > max_slots:
        alert_text = get_text('limit_error_dates', context).format(
            current=0,
            max=max_slots,
            tariff=limits['name']
        )
        # Custom explanation
        alert_text += get_text('days_alert_text', context).format(
            count_to_add=count_to_add,
            max_slots=max_slots
        )

        await query.answer(alert_text, show_alert=True)
        return CALENDAR_VIEW
    # -----------------------

    await query.answer()  # Valid, close loading animation

    # 5. Apply Changes
    # Remove old schedules
    remove_task_schedules(task_id)

    # Add only the valid future days
    for date_obj in valid_dates_to_add:
        date_str = date_obj.strftime("%Y-%m-%d")
        add_task_schedule(task_id, 'date', schedule_date=date_str)

    # Hot-reload (if task is active)
    await refresh_task_jobs(task_id, context)

    # 6. Update UI
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]

    month_year = datetime(year, month, 1).strftime("%B %Y")

    # Message Text
    text = get_text('calendar_header_dates', context).format(
        month_year_str=month_year,
        dates_str=f"{len(selected_dates)} days selected"
    )
    text += get_text('calendar_info_weekdays', context)
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_slots, tariff_name=limits['name'])

    await query.edit_message_text(
        text,
        reply_markup=calendar_keyboard(context, year, month, selected_dates, [], today_user),
        parse_mode='Markdown'
    )
    return CALENDAR_VIEW


async def calendar_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс выбранных дат"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    remove_task_schedules(task_id)

    # --- Обновляем календарь (Копи-паст из task_select_calendar) ---
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')
    today_user = datetime.now(user_tz).date()

    limits = get_tariff_limits(user_tariff)
    max_time_slots = limits['date_slots']

    year = context.user_data.get('calendar_year', today_user.year)
    month = context.user_data.get('calendar_month', today_user.month)

    text = ""  # Шапка пустая
    text += get_text('calendar_info_weekdays', context)
    # --- ⬇️ FIXED LINE ⬇️ ---
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots,
                                                                  tariff_name=limits['name'])
    # --- ⬆️ FIXED LINE ⬆️ ---

    try:
        await query.edit_message_text(
            text,
            reply_markup=calendar_keyboard(context, year, month, [], [], today_user),
            parse_mode='Markdown'
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Error in calendar reset: {e}")
    return CALENDAR_VIEW

async def calendar_weekday_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Selects a weekday. Strictly enforces mutual exclusivity:
    If a weekday is picked, ALL specific dates are removed.
    """
    query = update.callback_query

    task_id = context.user_data.get('current_task_id')
    # Task 3: Validation
    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=False
        )
        return TASK_CONSTRUCTOR
    await query.answer()

    try:
        weekday = int(query.data.replace("calendar_wd_", ""))
    except ValueError:
        return CALENDAR_VIEW

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)

    # 1. Enforce Mutual Exclusivity: Remove ANY specific dates
    # If we are selecting a weekday, we cannot have specific dates.
    db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_date IS NOT NULL",
             (task_id,), commit=True)

    # 2. Get current weekday schedules
    schedules = get_task_schedules(task_id)
    selected_weekdays = list(set([s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]))

    # 3. Toggle Weekday
    if weekday in selected_weekdays:
        # Remove
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_weekday = %s",
                 (task_id, weekday), commit=True)
        selected_weekdays.remove(weekday)

        # If no weekdays left, cleanup is automatic via db logic usually,
        # but good to ensure we don't leave empty rows if any.
        if not selected_weekdays:
            remove_task_schedules(task_id)  # Safe because dates were already deleted above
    else:
        # Add
        # Check Limits
        max_weekdays = limits.get('date_slots', 7)  # reuse date_slots for weekdays limit
        if max_weekdays > 7: max_weekdays = 7

        if len(selected_weekdays) >= max_weekdays:
            alert_text = get_text('limit_error_weekdays', context).format(
                current=len(selected_weekdays),
                max=max_weekdays,
                tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=True)
            return CALENDAR_VIEW

        # Insert new weekday
        # Preserve times if they exist
        times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

        if times:
            for time_str in times:
                add_task_schedule(task_id, 'weekday_and_time', schedule_weekday=weekday, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'weekday', schedule_weekday=weekday)

    # 4. Refresh View
    # We simply call task_select_calendar, which re-reads the DB and renders the correct view.
    # This ensures what the user sees is exactly what is in the DB.
    return await task_select_calendar(update, context)