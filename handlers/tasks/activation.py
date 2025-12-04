from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from database.queries.publications import cancel_task_jobs
from database.queries.schedules import get_task_schedules
from database.queries.task_channels import get_task_channels
from database.queries.tasks import get_task_details
from handlers.tasks.constructor import show_task_constructor
from jobs.scheduler import create_publication_jobs_for_task
from keyboards.task_constructor import back_to_main_menu_keyboard, back_to_constructor_keyboard
from localization.loader import get_text
from services.task_service import update_task_field, get_or_create_task_id, refresh_task_jobs, validate_task
from states.conversation import TASK_CONSTRUCTOR, MAIN_MENU
from utils.logging import logger


async def task_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активация задачи: Валидация -> Очистка старых -> Создание новых -> Уведомление"""
    query = update.callback_query
    await query.answer(get_text('task_activating_spinner', context))

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await query.edit_message_text(
            get_text('task_not_found_error', context),
            reply_markup=back_to_main_menu_keyboard(context)
        )
        return MAIN_MENU

    # --- 1. Validation using shared function ---
    is_valid, error_text = validate_task(task_id, context)
    if not is_valid:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
            InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
        ]])
        await query.edit_message_text(
            get_text('task_validation_header', context) + f"\n\n{error_text}",
            reply_markup=keyboard
        )
        return TASK_CONSTRUCTOR

    # --- 2. Activation ---
    await update_task_field(task_id, 'status', 'active', context)
    await cancel_task_jobs(task_id, context)

    user_tz = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        job_count = create_publication_jobs_for_task(task_id, user_tz, context.application)
        logger.info(f"Task {task_id} activated. Jobs created: {job_count}")
    except Exception as e:
        logger.error(f"Error creating jobs for task {task_id}: {e}", exc_info=True)
        await update_task_field(task_id, 'status', 'inactive', context)
        await query.edit_message_text(
            f"Error: {str(e)}",
            reply_markup=back_to_constructor_keyboard(context)
        )
        return TASK_CONSTRUCTOR

    # --- 3. Success UI ---
    success_text = get_text('task_activated_title', context).format(task_id=task_id) + "\n\n"
    success_text += get_text('task_activated_jobs_count', context).format(job_count=job_count)

    await query.edit_message_text(success_text, reply_markup=back_to_main_menu_keyboard(context))
    context.user_data.pop('current_task_id', None)

    return MAIN_MENU


async def task_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка задачи"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data['current_task_id']

    # 1. Статус Inactive
    await update_task_field(task_id, 'status', 'inactive', context)

    # 2. Отмена джобов
    await cancel_task_jobs(task_id, context)

    await query.answer(get_text('task_deactivated_success', context), show_alert=True)

    # Обновляем вид конструктора
    return await show_task_constructor(update, context)


async def ensure_task_and_refresh(user_id: int, context: ContextTypes.DEFAULT_TYPE, auto_activate: bool = False) -> int:
    """
    Creates a task in DB if it doesn't exist (Lazy Creation).
    Updates status to 'active' if required.
    Triggers Hot-Reload of the scheduler.
    """
    task_id = get_or_create_task_id(user_id, context)

    if auto_activate:
        # If adding a time/date, we assume the user wants it active
        await update_task_field(task_id, 'status', 'active', context)

    # Hot-reload: Cancel old jobs and reschedule based on new params immediately
    await refresh_task_jobs(task_id, context)

    return task_id


