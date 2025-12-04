from typing import Optional, Any

from telegram.ext import ContextTypes
from database.connection import db_query
from database.queries.publications import cancel_task_jobs
from database.queries.schedules import get_task_schedules
from database.queries.settings import get_user_settings
from database.queries.task_channels import get_task_channels
from database.queries.tasks import get_task_details, create_task
from jobs.scheduler import create_publication_jobs_for_task
from localization.loader import get_text
from utils.logging import logger


def get_or_create_task_id(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    Получает ID текущей задачи из context.user_data или создает новую задачу,
    если она не задана, и сохраняет ID в context.user_data.
    """
    task_id = context.user_data.get('current_task_id')
    if task_id:
        return task_id

    # Задача еще не существует, создаем ее.
    # Предполагается, что create_task(user_id) возвращает ID созданной задачи
    new_task_id = create_task(user_id)
    if new_task_id:
        context.user_data['current_task_id'] = new_task_id
    return new_task_id


def validate_task(task_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    """
    Validates if a task has all required fields to be Active.
    UPDATED: Allows weekdays without specific dates.
    """
    task = get_task_details(task_id)
    if not task:
        return False, "Task not found"

    # 1. Check Message
    if not task.get('content_message_id'):
        return False, get_text('task_error_no_message', context)

    # 2. Check Channels
    channels = get_task_channels(task_id)
    if not channels:
        return False, get_text('task_error_no_channels', context)

    # 3. Check Schedule (UPDATED: Accept either dates OR weekdays)
    schedules = get_task_schedules(task_id)

    if not schedules:
        return False, get_text('task_error_no_schedule', context)

    # Must have at least one date OR one weekday
    has_date = any(s.get('schedule_date') is not None for s in schedules)
    has_weekday = any(s.get('schedule_weekday') is not None for s in schedules)
    has_time = any(s.get('schedule_time') is not None for s in schedules)

    if not has_date and not has_weekday:
        return False, "⚠️ Error: Select dates or weekdays"

    if not has_time:
        return False, get_text('task_error_no_schedule', context)

    return True, ""

async def refresh_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    HOT RELOAD LOGIC:
    1. Checks if task is ACTIVE. If Inactive (creation mode) -> Do nothing.
    2. If Active (edit mode) -> Cancel ALL previous jobs immediately.
    3. Validate and Reschedule with NEW parameters.
    """
    # 1. Check Status
    task = get_task_details(task_id)
    if not task or task['status'] != 'active':
        # Stop here if we are just creating the task (Constraint: do not auto activate while creating)
        return

    logger.info(f"🔄 Hot-reloading active task {task_id} due to parameter change...")

    # 2. Cancel OLD jobs
    # (Constraint: previous task should be cancelled and not published)
    await cancel_task_jobs(task_id, context)

    # 3. Validate New State
    is_valid, error = validate_task(task_id, context)

    if is_valid:
        # 4. Create NEW jobs
        # (Constraint: the one with new parameters should be published)
        user_settings = get_user_settings(task['user_id'])
        user_tz = user_settings.get('timezone', 'Europe/Moscow')

        count = create_publication_jobs_for_task(task_id, user_tz, context.application)
        logger.info(f"✅ Task {task_id} hot-reloaded. Scheduled {count} jobs.")
    else:
        # If the edit made the task invalid (e.g. removed all times), force deactivate
        logger.warning(f"⚠️ Task {task_id} invalid after edit. Deactivating. Reason: {error}")
        # We use db_query directly to avoid infinite recursion with update_task_field
        db_query("UPDATE tasks SET status = 'inactive' WHERE id = %s", (task_id,), commit=True)
        # Optionally notify user here


async def update_task_field(task_id: int, field: str, value: Any, context: ContextTypes.DEFAULT_TYPE):
    """
    Updates a DB field and triggers the Hot-Reload check.
    """
    allowed_fields = [
        'task_name', 'content_message_id', 'content_chat_id', 'pin_duration',
        'pin_notify', 'auto_delete_hours', 'report_enabled',
        'advertiser_user_id', 'post_type', 'status'
    ]

    if field not in allowed_fields:
        logger.error(f"Attempt to update invalid field: {field}")
        return

    # 1. Update DB
    sql = f"UPDATE tasks SET {field} = %s WHERE id = %s"
    db_query(sql, (value, task_id), commit=True)

    # 2. Trigger Hot Reload (Auto-activate if already active)
    await refresh_task_jobs(task_id, context)


def can_modify_task_parameter(task_id: int) -> tuple[bool, str]:
    """
    Task 3: Validates if name OR message is set before allowing other parameter modifications.
    Returns: (can_modify: bool, error_message: str)
    """
    task = get_task_details(task_id)
    if not task:
        return False, "Task not found"

    # Allow modification if EITHER task_name OR content_message_id is set
    has_name = task.get('task_name') is not None and task.get('task_name') != ''
    has_message = task.get('content_message_id') is not None

    if has_name or has_message:
        return True, ""
    else:
        return False, "Name or Message should be provided first"