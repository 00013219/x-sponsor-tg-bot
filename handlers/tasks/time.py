from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.schedules import get_task_schedules, add_task_schedule, remove_task_schedules
from database.queries.tasks import get_task_details
from handlers.tasks.constructor import show_task_constructor
from keyboards.duration import pin_duration_keyboard
from keyboards.time_selection import time_selection_keyboard
from localization.loader import get_text
from models.tariff import get_tariff_limits
from services.task_service import can_modify_task_parameter, get_or_create_task_id, refresh_task_jobs
from states.conversation import TASK_CONSTRUCTOR, TIME_SELECTION, TASK_SET_CUSTOM_TIME
from utils.logging import logger


async def task_select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '🕐 Время' (Задача 3: вывод выбранных слотов)"""
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

    # Получаем выбранное время
    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))
    selected_times.sort()  # Сортируем для красоты

    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz_obj = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz_obj = ZoneInfo('UTC')
        user_tz_str = 'UTC (Default)'

    current_time_str = datetime.now(user_tz_obj).strftime('%H:%M')

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    # Формирование текста
    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz_str)}"
    text += f"\n🕒 **{get_text('time_current_info', context).format(current_time=current_time_str)}**"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=len(selected_times), slots=max_slots)}"

    # --- ИЗМЕНЕНИЕ (Задача 3): Вывод конкретного времени ---
    if selected_times:
        times_str = ", ".join(selected_times)
        # Можно добавить ключ локализации time_list_label, пока хардкод для примера
        label = get_text('selected_time', context)
        text += f"\n\n{label} **{times_str}**"
    # -----------------------------------------------------

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, selected_times)
    )
    return TIME_SELECTION


async def time_slot_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Time slot selection (Task 3: update list display)"""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)

    can_modify, error_msg = can_modify_task_parameter(task_id)
    if not can_modify:
        await query.answer(
            get_text('task_error_no_name_or_message', context),
            show_alert=True
        )
        return TIME_SELECTION

    time_str = query.data.replace("time_select_", "")

    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    if time_str in selected_times:
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_time = %s",
                 (task_id, time_str), commit=True)
        await query.answer()
    else:
        if len(selected_times) >= max_slots:
            alert_text = get_text('limit_error_times', context).format(
                current=len(selected_times), max=max_slots, tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=False)
            return TIME_SELECTION

        # UPDATED: Apply time to existing dates/weekdays independently
        dates = [s for s in schedules if s['schedule_date']]
        weekdays = [s for s in schedules if s['schedule_weekday'] is not None]

        if dates:
            unique_dates_data = {d['schedule_date'] for d in dates}
            for date_val in unique_dates_data:
                add_task_schedule(task_id, 'datetime', schedule_date=date_val, schedule_time=time_str)
        elif weekdays:
            unique_weekdays = {w['schedule_weekday'] for w in weekdays}
            for wd in unique_weekdays:
                add_task_schedule(task_id, 'weekday_and_time', schedule_weekday=wd, schedule_time=time_str)
        else:
            # No dates/weekdays selected yet - just add the time
            add_task_schedule(task_id, 'time', schedule_time=time_str)

        await query.answer()

    await refresh_task_jobs(task_id, context)

    # Update UI with new list
    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))
    selected_times.sort()

    user_tz = context.user_data.get('timezone', 'Europe/Moscow')
    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz)}"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=len(selected_times), slots=max_slots)}"

    if selected_times:
        times_str = ", ".join(selected_times)
        label = get_text('selected_time', context)
        text += f"\n\n{label} **{times_str}**"

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, selected_times)
    )
    return TIME_SELECTION

async def time_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос своего времени"""
    query = update.callback_query
    await query.answer()

    text = get_text('time_ask_custom', context)

    # TASK 2 FIX: Back button leads to Time Selection, not Constructor
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_select_time")],
        [InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)
    return TASK_SET_CUSTOM_TIME

async def delete_message_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay_seconds: int):
    """Utility function to delete a message after a delay"""
    await asyncio.sleep(delay_seconds)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        # Message might already be deleted, ignore
        pass


async def time_receive_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение своего времени с проверкой лимитов и чистый переход UI."""

    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    chat_id = update.effective_chat.id

    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    time_str = update.message.text.strip()

    # Regex check
    time_pattern = re.compile(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$')
    if not time_pattern.match(time_str):
        # 1. Удаляем введенное пользователем сообщение
        try:
            await update.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение пользователя: {e}")

        # 2. Отправляем временное сообщение об ошибке
        error_msg = await context.bot.send_message(chat_id, get_text('time_invalid_format', context))

        # 3. Запланировать удаление сообщения об ошибке через 3 секунды
        async def delete_error_message():
            await asyncio.sleep(3)
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=error_msg.message_id)
            except Exception:
                pass  # Сообщение уже удалено - это нормально

        asyncio.create_task(delete_error_message())
        return TASK_SET_CUSTOM_TIME

    hours, minutes = time_str.split(':')
    time_str = f"{int(hours):02d}:{int(minutes):02d}"

    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    time_added = False
    if time_str not in selected_times:
        # --- CHECK TIME LIMITS ---
        if len(selected_times) >= max_slots:
            # Удаляем сообщение пользователя
            try:
                await update.message.delete()
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение пользователя: {e}")

            error_text = get_text('limit_error_times', context).format(
                current=len(selected_times),
                max=max_slots,
                tariff=limits['name']
            )

            # Отправляем временное сообщение об ошибке
            error_msg = await context.bot.send_message(chat_id, error_text)

            # Удаляем сообщение об ошибке через 3 секунды
            async def delete_error_message():
                await asyncio.sleep(3)
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=error_msg.message_id)
                except Exception:
                    pass

            asyncio.create_task(delete_error_message())
            return TASK_SET_CUSTOM_TIME
        # --- END CHECK ---

        dates = [s for s in schedules if s['schedule_date']]
        weekdays = [s for s in schedules if s['schedule_weekday'] is not None]

        if dates:
            unique_dates_data = {d['schedule_date'] for d in dates}
            for date_val in unique_dates_data:
                add_task_schedule(task_id, 'datetime', schedule_date=date_val, schedule_time=time_str)

        elif weekdays:
            unique_weekdays = {w['schedule_weekday'] for w in weekdays}
            for wd in unique_weekdays:
                add_task_schedule(task_id, 'weekday_and_time', schedule_weekday=wd, schedule_time=time_str)

        else:
            add_task_schedule(task_id, 'time', schedule_time=time_str)

        time_added = True

    # --- TRIGGER HOT RELOAD ---
    if time_added:
        await refresh_task_jobs(task_id, context)

    # 1. Удаляем сообщение пользователя (даже если время уже было в списке)
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение пользователя: {e}")

    # 2. Отправляем временное сообщение-подтверждение
    msg = await context.bot.send_message(chat_id, get_text('time_saved', context))

    # 3. Удаляем подтверждение через 2 секунды
    async def delete_confirmation():
        await asyncio.sleep(2)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception:
            pass  # Сообщение уже удалено - это нормально

    asyncio.create_task(delete_confirmation())

    # 4. Возвращаемся в конструктор (он сам почистит предыдущие сообщения)
    return await show_task_constructor(update, context)


async def time_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """Clear all selected times but PRESERVE dates and weekdays"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    # 1. Capture existing Dates AND Weekdays before wiping
    schedules = get_task_schedules(task_id)
    dates = [s['schedule_date'] for s in schedules if s['schedule_date']]
    weekdays = [s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]

    # 2. Wipe all schedules
    remove_task_schedules(task_id)

    # 3. Restore Dates (without time)
    for date in set(dates):  # Use set to avoid duplicates
        add_task_schedule(task_id, 'date', schedule_date=date)

    # 4. Restore Weekdays (without time) <-- THIS WAS MISSING
    for wd in set(weekdays):
        add_task_schedule(task_id, 'weekday', schedule_weekday=wd)

    # UI Update Logic
    user_tz = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')

    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz)}"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=0, slots=max_slots)}"

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, [])
    )
    return TIME_SELECTION


