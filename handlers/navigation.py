from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import OWNER_ID
from database.connection import db_query
from database.queries.tasks import get_user_tasks
from handlers.admin.panel import nav_boss
from handlers.channels import nav_my_channels
from handlers.tariffs import nav_tariff
from handlers.tasks.constructor import task_constructor_entrypoint
from keyboards.lang import lang_keyboard
from keyboards.main_menu import main_menu_keyboard
from keyboards.reply import main_menu_reply_keyboard
from keyboards.task_constructor import back_to_main_menu_keyboard
from keyboards.time_selection import timezone_keyboard
from localization.loader import get_text
from localization.texts import TEXTS
from models.tariff import get_tariff_limits
from states.conversation import MAIN_MENU, MY_TASKS, START_SELECT_TZ, START_SELECT_LANG, FREE_DATES
from utils.cleanup import cleanup_temp_messages
from utils.helpers import determine_task_status_color
from utils.logging import logger
from utils.text_utils import generate_smart_name


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывает главное меню с inline и reply кнопками.
    Ensures state transition to MAIN_MENU.
    """
    text = get_text('main_menu', context)

    # Determine chat_id
    chat_id = None
    if update.callback_query:
        chat_id = update.callback_query.message.chat_id
    elif update.message:
        chat_id = update.message.chat_id
    else:
        chat_id = update.effective_chat.id

    if not chat_id:
        logger.error("Не удалось определить chat_id в show_main_menu")
        return MAIN_MENU

    # Cleanup any remaining temporary messages (spinners, etc)
    await cleanup_temp_messages(context, chat_id)

    # 1. Отправляем Inline-меню (основное)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=main_menu_keyboard(context)
    )

    # Store this message ID if you want to auto-delete it later (optional)
    context.user_data['temp_message_ids'] = [msg.message_id]

    # 2. Отправляем Reply-клавиатуру (постоянную навигацию)
    # Only send if we are really refreshing the screen context
    prompt_text = get_text('reply_keyboard_prompt', context) or "⬇️ Menu"

    await context.bot.send_message(
        chat_id=chat_id,
        text=prompt_text,
        reply_markup=main_menu_reply_keyboard(context)
    )

    return MAIN_MENU


async def nav_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Коллбэк 'nav_main_menu'. Возвращает в Главное меню с proper cleanup."""
    query = update.callback_query
    if query:
        await query.answer()

    # Comprehensive cleanup of ALL previous messages
    chat_id = update.effective_chat.id

    # Cleanup all temporary messages
    await cleanup_temp_messages(context, chat_id)

    # Also delete the current message that triggered this callback
    if query and query.message:
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Не удалось удалить текущее сообщение: {e}")

    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    return await show_main_menu(update, context)


async def handle_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки ReplyKeyboard"""
    text = update.message.text
    lang = context.user_data.get('language_code', 'en')

    # Map button text to callbacks
    if text == get_text('nav_new_task_btn', context, lang):
        return await task_constructor_entrypoint(update, context)
    elif text == get_text('nav_my_tasks_btn', context, lang):
        return await nav_my_tasks(update, context)
    elif text == get_text('nav_language_btn', context, lang):
        return await nav_language(update, context)
    elif text == get_text('nav_timezone_btn', context, lang):
        return await nav_timezone(update, context)
    elif text == get_text('nav_tariff_btn', context, lang):
        return await nav_tariff(update, context)
    elif text == get_text('nav_channels_btn', context, lang):
        return await nav_my_channels(update, context)
    elif text == get_text('nav_boss_btn', context, lang):
        # Add check to ensure only owner can use this button
        if context.user_data.get('user_id') == OWNER_ID:
            return await nav_boss(update, context)


async def nav_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Мои задачи' (Обновленный дизайн)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message

        # Cleanup previous menu messages
        await cleanup_temp_messages(context, query.message.chat_id)

        # Also cleanup the main menu message that called this
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Не удалось удалить меню сообщение: {e}")
    else:
        message = update.message
        # Cleanup for message-based navigation
        if update.message:
            await cleanup_temp_messages(context, update.message.chat_id)

    user_id = context.user_data['user_id']
    tasks = get_user_tasks(user_id)

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_tasks = limits['tasks']

    keyboard = []
    list_text_items = []

    if not tasks:
        list_text = get_text('my_tasks_empty', context)
    else:
        for task in tasks:
            icon = determine_task_status_color(task['id'])

            # Определяем текстовый статус для списка
            if icon == '🟢':
                status_txt = get_text('status_text_active', context)
            elif icon == '🟡':
                status_txt = get_text('status_text_finishing', context)
            else:
                status_txt = get_text('status_text_inactive', context)

            # Формируем строку списка
            smart_name = generate_smart_name(task['task_name'] or "", context, limit=4)

            item_str = get_text('my_tasks_item_template', context).format(
                icon=icon,
                id=task['id'],
                name=smart_name,
                status_text=status_txt
            )
            list_text_items.append(item_str)

            # Формируем КНОПКУ (кратко, первые 3 слова)
            btn_name = generate_smart_name(task['task_name'] or "", context, limit=3)
            btn_str = get_text('task_btn_template', context).format(
                icon=icon,
                id=task['id'],
                name=btn_name
            )

            keyboard.append([
                InlineKeyboardButton(btn_str, callback_data=f"task_edit_{task['id']}")
            ])

        list_text = "\n".join(list_text_items)

    # Шапка + Список + Легенда
    full_text = get_text('my_tasks_header', context).format(
        count=len(tasks),
        list_text=list_text
    )

    # Доп кнопки
    keyboard.append([InlineKeyboardButton(get_text('nav_new_task_btn', context), callback_data="nav_new_task")])

    # Плашка тарифа (неактивная кнопка или callback на тариф)
    tariff_info = get_text('task_tariff_info', context).format(
        name=limits['name'],
        current=len(tasks),
        max=max_tasks
    )
    keyboard.append([InlineKeyboardButton(tariff_info, callback_data="nav_tariff")])

    keyboard.append([InlineKeyboardButton(get_text('back_to_main_menu_btn', context), callback_data="nav_main_menu")])

    # Send new message and store its ID for cleanup
    msg = await message.reply_text(full_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    # Store this message ID for future cleanup
    if 'temp_message_ids' not in context.user_data:
        context.user_data['temp_message_ids'] = []
    context.user_data['temp_message_ids'].append(msg.message_id)

    return MY_TASKS


async def nav_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает смену таймзоны"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
        text = get_text('select_timezone', context)
        await message.reply_text(text, reply_markup=timezone_keyboard(context))
    else:
        text = get_text('select_timezone', context)
        await update.message.reply_text(text, reply_markup=timezone_keyboard(context))
    return START_SELECT_TZ


async def nav_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает смену языка"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
        await message.reply_text(
            TEXTS['ru']['welcome_lang'],
            reply_markup=lang_keyboard()
        )
    else:
        await update.message.reply_text(
            TEXTS['ru']['welcome_lang'],
            reply_markup=lang_keyboard()
        )
    return START_SELECT_LANG



async def nav_free_dates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Свободные даты' (НОВАЯ ЛОГИКА)"""
    query = update.callback_query
    await query.answer()

    user_id = context.user_data.get('user_id')
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')

    now_utc = datetime.now(ZoneInfo('UTC'))
    today_user = now_utc.astimezone(user_tz).date()

    # Даты для верхнего списка (текущий + следующий месяц, ~60 дней)
    start_date_free = today_user
    end_date_free = today_user + timedelta(days=60)

    # Даты для нижнего списка (30 дней)
    start_date_schedule = today_user
    end_date_schedule = today_user + timedelta(days=30)

    # --- 1. Верхняя часть (Свободные даты) ---

    scheduled_jobs_60d = db_query("""
        SELECT scheduled_time_utc 
        FROM publication_jobs 
        WHERE user_id = %s 
          AND status = 'scheduled' 
          AND scheduled_time_utc >= %s 
          AND scheduled_time_utc < %s
    """, (user_id, now_utc, end_date_free), fetchall=True)

    scheduled_dates_set = set()
    if scheduled_jobs_60d:
        for job in scheduled_jobs_60d:
            local_date = job['scheduled_time_utc'].astimezone(user_tz).date()
            scheduled_dates_set.add(local_date)

    all_dates_set = set()
    current_date = start_date_free
    while current_date < end_date_free:
        all_dates_set.add(current_date)
        current_date += timedelta(days=1)

    free_dates = sorted(list(all_dates_set - scheduled_dates_set))

    free_dates_str = ", ".join([d.strftime('%d/%m') for d in free_dates])
    if not free_dates_str:
        free_dates_str = get_text('free_dates_none_60d', context)

    text = get_text('free_dates_header', context).format(free_dates_str=free_dates_str)
    text += "--------------------\n"

    # --- 2. Нижняя часть (Задачи на 30 дней) ---

    text += get_text('free_dates_schedule_header_30d', context)

    jobs_30_days = db_query("""
        SELECT scheduled_time_utc, task_id, pin_duration 
        FROM publication_jobs 
        WHERE user_id = %s 
          AND status = 'scheduled' 
          AND scheduled_time_utc >= %s 
          AND scheduled_time_utc < %s 
        ORDER BY scheduled_time_utc
    """, (user_id, now_utc, end_date_schedule), fetchall=True)

    if not jobs_30_days:
        text += get_text('free_dates_schedule_empty_30d', context)
    else:
        grouped_jobs = {}
        for job in jobs_30_days:
            local_dt = job['scheduled_time_utc'].astimezone(user_tz)
            date_key = local_dt.date()

            time_str = local_dt.strftime('%H:%M')
            pin_str = "📌" if job['pin_duration'] > 0 else ""
            task_id = job['task_id']

            job_str = f"{time_str} ({pin_str}#{task_id})"

            if date_key not in grouped_jobs:
                grouped_jobs[date_key] = []
            grouped_jobs[date_key].append(job_str)

        for date_key in sorted(grouped_jobs.keys()):
            date_str = date_key.strftime('%d.%m.%Y')
            jobs_str = "; ".join(grouped_jobs[date_key])
            text += f"{date_str} {jobs_str}\n"

    await query.edit_message_text(
        text,
        reply_markup=back_to_main_menu_keyboard(context),
        parse_mode='Markdown'
    )
    return FREE_DATES





