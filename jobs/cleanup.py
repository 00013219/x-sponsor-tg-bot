from datetime import datetime
from zoneinfo import ZoneInfo

from database.connection import db_query
from database.queries.settings import get_user_settings
from database.rate_limit import cleanup_old_rate_limit_records
from utils.logging import logger


def cleanup_inactive_tasks():
    """
    Task 2: Removes tasks that have been inactive for more than 60 days.
    Called daily via scheduled job.
    Cascading deletes will automatically remove related records from:
    - task_channels
    - task_schedules
    - publication_jobs
    - scheduled_tasks
    """
    try:
        result = db_query("""
            DELETE FROM tasks 
            WHERE status = 'inactive' 
            AND created_at < NOW() - INTERVAL '60 days'
            RETURNING id
        """, fetchall=True, commit=True)

        deleted_count = len(result) if result else 0
        if deleted_count > 0:
            logger.info(f"🗑️ Cleaned up {deleted_count} inactive tasks older than 60 days")
        else:
            logger.debug("No inactive tasks older than 60 days found")
    except Exception as e:
        logger.error(f"Error during inactive task cleanup: {e}", exc_info=True)

def cleanup_past_schedules():
    """
    Removes schedule entries for dates that have already passed.
    Should be run daily or when task is accessed.
    """
    try:
        now_utc = datetime.now(ZoneInfo('UTC'))

        # Get all tasks with specific dates
        tasks_with_dates = db_query("""
            SELECT DISTINCT ts.task_id, t.user_id
            FROM task_schedules ts
            JOIN tasks t ON ts.task_id = t.id
            WHERE ts.schedule_date IS NOT NULL
        """, fetchall=True)

        for task_row in tasks_with_dates or []:
            task_id = task_row['task_id']
            user_id = task_row['user_id']

            # Get user timezone
            user_settings = get_user_settings(user_id)
            user_tz_str = user_settings.get('timezone', 'Europe/Moscow')

            try:
                user_tz = ZoneInfo(user_tz_str)
            except:
                user_tz = ZoneInfo('UTC')

            today_user = now_utc.astimezone(user_tz).date()

            # Delete schedules with dates before today
            db_query("""
                DELETE FROM task_schedules
                WHERE task_id = %s 
                AND schedule_date < %s
            """, (task_id, today_user), commit=True)

        logger.info("✅ Cleaned up past schedule dates")
    except Exception as e:
        logger.error(f"Error during past schedule cleanup: {e}", exc_info=True)


def cleanup_rate_limit_records():
    """
    Removes rate limit records older than 1 day.
    These records are only needed for tracking within the 10-minute window,
    so older records are not needed.
    """
    try:
        cleanup_old_rate_limit_records(days=1)
        logger.info("✅ Cleaned up old rate limit records")
    except Exception as e:
        logger.error(f"Error during rate limit cleanup: {e}", exc_info=True)