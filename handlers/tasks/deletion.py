from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.tasks import get_task_details
from handlers.navigation import show_main_menu, nav_my_tasks
from handlers.tasks.constructor import show_task_constructor
from localization.loader import get_text
from states.conversation import TASK_DELETE_CONFIRM
from utils.logging import logger


async def task_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Нажатие кнопки 'Удалить задачу' - запрос подтверждения"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        return await show_task_constructor(update, context)  # Failsafe

    task = get_task_details(task_id)
    task_name = task.get('task_name') or get_text('task_default_name', context)

    text = get_text('task_delete_confirm', context).format(name=task_name, id=task_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text('status_yes', context), callback_data="task_delete_confirm_yes"),
            InlineKeyboardButton(get_text('status_no', context), callback_data="task_delete_confirm_no")
        ]
    ])

    # Use reply_text or edit_message_text based on context
    if query.message:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard)

    return TASK_DELETE_CONFIRM


async def task_delete_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Подтверждение удаления задачи"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await query.edit_message_text(get_text('error_generic', context))
        return await show_main_menu(update, context)  # Failsafe

    task = get_task_details(task_id)
    task_name = task.get('task_name') or get_text('task_default_name', context)

    # --- Отмена запланированных задач в JobQueue ---

    # 1. Отмена будущих ПУБЛИКАЦИЙ
    jobs_to_cancel = db_query(
        "SELECT aps_job_id FROM publication_jobs WHERE task_id = %s AND status = 'scheduled' AND aps_job_id IS NOT NULL",
        (task_id,),
        fetchall=True
    )
    if jobs_to_cancel:
        logger.info(f"Отмена {len(jobs_to_cancel)} запланированных публикаций для задачи {task_id}")
        for job_row in jobs_to_cancel:
            job_name = job_row.get('aps_job_id')
            if job_name:
                jobs = context.application.job_queue.get_jobs_by_name(job_name)
                if jobs:
                    jobs[0].schedule_removal()
                    logger.info(f"Удалена задача {job_name} из JobQueue")

    # 2. Отмена будущих АВТО-УДАЛЕНИЙ
    delete_jobs_to_cancel = db_query(
        "SELECT id, posted_message_id FROM publication_jobs WHERE task_id = %s AND status = 'published' AND auto_delete_hours > 0",
        (task_id,),
        fetchall=True
    )
    if delete_jobs_to_cancel:
        logger.info(f"Отмена {len(delete_jobs_to_cancel)} задач на авто-удаление для задачи {task_id}")
        for job_row in delete_jobs_to_cancel:
            job_name = f"del_{job_row['id']}_msg_{job_row['posted_message_id']}"
            jobs = context.application.job_queue.get_jobs_by_name(job_name)
            if jobs:
                jobs[0].schedule_removal()
                logger.info(f"Удалена задача {job_name} из JobQueue")

    # --- Очистка БД ---

    # 3. Сначала удаляем 'publication_jobs' (т.к. у 'tasks' нет ON DELETE CASCADE на них)
    db_query("DELETE FROM publication_jobs WHERE task_id = %s", (task_id,), commit=True)

    # 4. Теперь удаляем саму задачу (это каскадом удалит 'task_channels' и 'task_schedules')
    db_query("DELETE FROM tasks WHERE id = %s", (task_id,), commit=True)

    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    text = get_text('task_delete_success', context).format(name=task_name, id=task_id)
    await query.edit_message_text(text)

    # Возвращаемся в Мои задачи (FIX TASK 2)
    return await nav_my_tasks(update, context)


async def task_delete_confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Отмена удаления задачи"""
    query = update.callback_query
    await query.answer()

    # Просто возвращаемся в конструктор
    return await show_task_constructor(update, context)
