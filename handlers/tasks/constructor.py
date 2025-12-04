from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from database.rate_limit import check_task_creation_rate_limit, record_task_creation
from states.conversation import TASK_CONSTRUCTOR
from utils.helpers import send_or_edit_message, determine_task_status_color
from utils.time_utils import format_hours_to_dhms
from database.connection import db_query
from database.queries.schedules import get_task_schedules
from database.queries.task_channels import get_task_channels
from database.queries.tasks import get_user_task_count, get_task_details

from keyboards.task_constructor import task_constructor_keyboard
from localization.loader import get_text
from models.tariff import get_tariff_limits
from utils.cleanup import cleanup_temp_messages
from utils.text_utils import generate_smart_name
from utils.logging import logger


async def task_constructor_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Точка входа: Проверяет лимиты (тариф + rate limit на 10 задач за 10 минут).
    Если лимит не превышен, очищает ID текущей задачи и перенаправляет в конструктор.
    """
    query = update.callback_query
    if query:
        await query.answer()

    user_id = context.user_data['user_id']
    user_tariff = context.user_data.get('tariff', 'free')

    # 1. Проверка Rate Limit (max 10 задач за 10 минут)
    rate_limit = check_task_creation_rate_limit(user_id, max_tasks=10, time_window_minutes=10)
    
    if not rate_limit['allowed']:
        reset_at = rate_limit['reset_at']
        reset_text = reset_at.strftime('%H:%M:%S') if reset_at else 'N/A'
        
        error_text = get_text('rate_limit_error_tasks', context).format(
            remaining=rate_limit['remaining'],
            reset_at=reset_text
        )
        
        logger.warning(f"Rate limit exceeded for user {user_id}: {rate_limit['current_count']}/10 in 10 min")
        
        if query:
            await query.message.reply_text(error_text)
        else:
            await update.message.reply_text(error_text)
        
        from ..navigation import nav_my_tasks
        return await nav_my_tasks(update, context)

    # 2. Проверка лимита тарифа
    limits = get_tariff_limits(user_tariff)
    max_tasks = limits['tasks']
    current_task_count = get_user_task_count(user_id)

    if current_task_count >= max_tasks:
        error_text = get_text('limit_error_tasks', context).format(
            current=current_task_count,
            max=max_tasks,
            tariff=limits['name']
        )

        logger.warning(f"Tariff limit exceeded for user {user_id}: {current_task_count}/{max_tasks}")

        if query:
            await query.message.reply_text(error_text)
        else:
            await update.message.reply_text(error_text)

        from ..navigation import nav_my_tasks
        return await nav_my_tasks(update, context)

    # 3. Лимиты не превышены - продолжаем
    # Записываем создание новой задачи в rate limit
    record_task_creation(user_id)

    # Очищаем ID, чтобы система знала, что мы в режиме "Новая задача"
    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    return await show_task_constructor(update, context)

async def show_task_constructor(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """
    Показывает главный экран конструктора задач.
    Enhanced: Properly cleans up ALL previous messages including media groups.
    """
    chat_id = None
    if update.callback_query:
        chat_id = update.callback_query.message.chat_id
    elif update.message:
        chat_id = update.message.chat_id

    if chat_id:
        # Cleanup ALL temporary messages including media groups
        await cleanup_temp_messages(context, chat_id)

    text = get_task_constructor_text(context)

    # If we need to force a new message or there's no query to edit, send new message
    if force_new_message and chat_id:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=task_constructor_keyboard(context))
        # Store this new message for future cleanup
        context.user_data['temp_message_ids'] = [msg.message_id]
    else:
        # Try to edit existing message
        await send_or_edit_message(update, context, text, task_constructor_keyboard(context))

    return TASK_CONSTRUCTOR

def get_task_constructor_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Form text for task constructor with Dynamic Traffic Light Status and Smart Duration Formatting"""
    task_id = context.user_data.get('current_task_id')



    # --- HANDLE NEW TASK (No ID) ---
    if not task_id:
        title = get_text('task_constructor_title', context)
        status_val = f"🔴 {get_text('status_text_inactive', context)}"
        task_name = get_text('task_default_name', context)

        channels_status = get_text('status_not_selected', context)
        message_status = get_text('status_not_set', context)
        dates_text = get_text('status_not_selected', context)
        weekdays_text = get_text('status_not_selected', context)
        times_text = get_text('status_not_selected', context)
        pin_text = get_text('status_no', context)
        delete_text = get_text('status_no', context)
        post_type_status = get_text('status_repost', context)
        pin_notify_status = get_text('status_no', context)
        report_status = get_text('status_no', context)
        advertiser_text = get_text('status_not_set', context)

        text = f"{title}\n\n"
        text += f"**{get_text('task_status_label', context)}{status_val}**\n\n"
        text += f"{task_name}\n"
        text += f"{get_text('header_channels', context)}{channels_status}\n"
        text += f"{get_text('header_message', context)}{message_status}\n"
        text += f"{get_text('header_weekdays', context)}{weekdays_text}\n"
        text += f"{get_text('header_time', context)}{times_text}\n"
        text += f"{get_text('header_pin', context)}{pin_text}\n"
        text += f"{get_text('header_autodelete', context)}{delete_text}\n"
        text += f"{get_text('header_post_type', context)}{post_type_status}\n"
        text += f"{get_text('header_pin_notify', context)}{pin_notify_status}\n"
        text += f"{get_text('header_report', context)}{report_status}\n"
        text += f"{get_text('header_advertiser', context)}{advertiser_text}\n"
        return text

    task = get_task_details(task_id)
    if not task:
        return get_text('error_task_not_found_db', context).format(task_id=task_id)

    # Get channels
    channels_ids = get_task_channels(task_id)
    channels_count = len(channels_ids)

    # Suffixes
    count_suffix = get_text('status_count_suffix', context)
    days_suffix = get_text('status_days_suffix', context)
    hours_suffix = get_text('status_hours_suffix', context)

    # --- DETERMINE STATUS (Traffic Light Logic) ---
    status_label = get_text('task_status_label', context)
    status_icon = determine_task_status_color(task_id, context)

    if status_icon == '🟢':
        status_val = f"🟢 {get_text('status_text_active', context)}"
    elif status_icon == '🟡':
        status_val = f"🟡 {get_text('status_text_finishing', context)}"
    else:
        status_val = f"🔴 {get_text('status_text_inactive', context)}"

    # --- Smart Name Truncation ---
    raw_name = task['task_name'] if task['task_name'] else get_text('task_default_name', context)

    if task['task_name']:
        display_name = generate_smart_name(raw_name, context, limit=3)
    else:
        display_name = raw_name

    # Schedules
    schedules = get_task_schedules(task_id)
    dates_text = get_text('status_not_selected', context)
    weekdays_text = get_text('status_not_selected', context)

    # --- TASK 4 FIX: Filter out past dates for display ---
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except:
        user_tz = ZoneInfo('UTC')
    today_user = datetime.now(user_tz).date()

    unique_dates = sorted(list(set([s['schedule_date'] for s in schedules if s['schedule_date']])))
    # Filter: Only show dates >= today
    future_dates = [d for d in unique_dates if d >= today_user]

    unique_weekdays = sorted(list(set([s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None])))

    if future_dates:
        if len(future_dates) > 5:
            dates_text = get_text('status_dates_count', context).format(count=len(future_dates), suffix=count_suffix)
        else:
            dates_text = "✅ " + ", ".join([d.strftime('%d.%m') for d in future_dates])
    elif unique_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_text = "✅ " + ", ".join([wd_names[day] for day in unique_weekdays])
        except:
            weekdays_text = get_text('status_weekdays_count', context).format(count=len(unique_weekdays),
                                                                              suffix=days_suffix)
    elif unique_dates and not future_dates:
        # If dates exist but all are in the past
        dates_text = "⚠️ All dates passed"

    times_text = get_text('status_not_selected', context)
    unique_times = sorted(list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']])))

    if unique_times:
        if len(unique_times) > 5:
            times_text = get_text('status_times_count', context).format(count=len(unique_times), suffix=count_suffix)
        else:
            times_text = "✅ " + ", ".join(unique_times)

    # Advertiser
    advertiser_text = get_text('status_not_set', context)
    if task['advertiser_user_id']:
        advertiser_user = db_query("SELECT username FROM users WHERE user_id = %s", (task['advertiser_user_id'],),
                                   fetchone=True)
        if advertiser_user and advertiser_user.get('username'):
            advertiser_text = f"✅ @{advertiser_user['username']}"
        else:
            advertiser_text = get_text('status_advertiser_id', context).format(
                advertiser_user_id=task['advertiser_user_id'])

    # --- UPDATED: Pin Duration using new format function ---
    pin_text = get_text('status_no', context)
    if task['pin_duration'] > 0:
        formatted = format_hours_to_dhms(task['pin_duration'], context)
        pin_text = f"✅ {formatted}"

    # --- UPDATED: Auto Delete using new format function ---
    delete_text = get_text('status_no', context)
    if task['auto_delete_hours'] > 0:
        formatted = format_hours_to_dhms(task['auto_delete_hours'], context)
        delete_text = f"✅ {formatted}"

    status_yes = get_text('status_yes', context)
    status_no = get_text('status_no', context)

    # Pin Notify Status Text
    pin_notify_status = status_yes if task['pin_notify'] else status_no

    report_status = status_yes if task['report_enabled'] else status_no
    post_type_status = get_text('status_from_bot', context) if task['post_type'] == 'from_bot' else get_text(
        'status_repost', context)

    channels_status = get_text('status_dates_count', context).format(count=channels_count,
                                                                     suffix=count_suffix) if channels_count > 0 else get_text(
        'status_not_selected', context)

    # Message Status
    if task['content_message_id']:
        if task.get('message_snippet'):
            message_status = f"✅ {task['message_snippet']}"
        else:
            message_status = get_text('status_set', context)
    else:
        message_status = get_text('status_not_set', context)

    title = get_text('task_constructor_title', context)
    if task_id:
        title += f" #{task_id}"

    text = f"{title}\n\n"
    text += f"**{status_label}{status_val}**\n\n"
    text += f"{display_name}\n"
    text += f"{get_text('header_channels', context)}{channels_status}\n"
    text += f"{get_text('header_message', context)}{message_status}\n"

    if future_dates:
        text += f"{get_text('header_date', context)}{dates_text}\n"
    else:
        text += f"{get_text('header_weekdays', context)}{weekdays_text}\n"

    text += f"{get_text('header_time', context)}{times_text}\n"
    text += f"{get_text('header_pin', context)}{pin_text}\n"
    text += f"{get_text('header_autodelete', context)}{delete_text}\n"
    text += f"{get_text('header_post_type', context)}{post_type_status}\n"

    # TASK 7: Only show Pin Notify Header if Pin Duration > 0
    if task['pin_duration'] > 0:
        text += f"{get_text('header_pin_notify', context)}{pin_notify_status}\n"

    text += f"{get_text('header_report', context)}{report_status}\n"
    text += f"{get_text('header_advertiser', context)}{advertiser_text}\n"

    return text

async def task_back_to_constructor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка '⬅️ Назад' (возврат в конструктор) с полной очисткой"""
    query = update.callback_query
    await query.answer()

    # Cleanup ALL temporary messages before showing constructor
    if query and query.message:
        await cleanup_temp_messages(context, query.message.chat_id)

    # Also delete the current message that has the back button
    try:
        await query.delete_message()
    except Exception as e:
        from utils.logging import logger
        logger.warning(f"Не удалось удалить текущее сообщение: {e}")

    # We return to constructor which will send a new clean message
    return await show_task_constructor(update, context, force_new_message=True)


async def task_edit_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точка входа в 'Редактировать задачу' (из 'Мои задачи')"""
    query = update.callback_query
    await query.answer()

    task_id = int(query.data.replace("task_edit_", ""))
    context.user_data['current_task_id'] = task_id

    # Cleanup any existing messages before showing constructor
    if query and query.message:
        await cleanup_temp_messages(context, query.message.chat_id)

    return await show_task_constructor(update, context, force_new_message=True)

async def task_back_to_constructor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка '⬅️ Назад' (возврат в конструктор)"""
    query = update.callback_query
    await query.answer()

    # TASK 2: Do NOT delete the message, just edit it.
    # We call show_task_constructor with force_new_message=False.
    # We also avoid calling cleanup_temp_messages for the CURRENT message ID
    # to prevent it from being deleted if it was stored.

    return await show_task_constructor(update, context, force_new_message=False)

