from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application

from database.connection import db_query
from database.queries.schedules import get_task_schedules
from database.queries.task_channels import get_task_channels
from database.queries.tasks import get_task_details
from publication import create_single_publication_job
from utils.logging import logger


def create_publication_jobs_for_task(task_id: int, user_tz: str, application: Application) -> int:
    """
    Creates the FIRST upcoming publication_job for the task.
    FIXED: Allows multiple time slots per day for Weekdays.
    """
    task = get_task_details(task_id)
    schedules = get_task_schedules(task_id)
    channels = get_task_channels(task_id)

    if not schedules or not channels:
        return 0

    try:
        tz = ZoneInfo(user_tz)
    except:
        tz = ZoneInfo('UTC')

    job_count = 0
    now_utc = datetime.now(ZoneInfo('UTC'))
    now_local = now_utc.astimezone(tz)

    # Allow jobs that are up to 60 seconds in the past (processing lag) to run immediately
    buffer_time = now_utc - timedelta(seconds=60)

    for schedule in schedules:
        if not schedule['schedule_time']: continue

        schedule_time = schedule['schedule_time']
        schedule_date = schedule.get('schedule_date')
        schedule_weekday = schedule.get('schedule_weekday')

        # --- Case 1: Specific Date ---
        if schedule_date:
            try:
                naive_dt = datetime.combine(schedule_date, schedule_time)
                local_dt = naive_dt.replace(tzinfo=tz)
                utc_dt = local_dt.astimezone(ZoneInfo('UTC'))

                if utc_dt < buffer_time: continue

                for channel_id in channels:
                    exists = db_query("""
                        SELECT 1 FROM publication_jobs 
                        WHERE task_id=%s AND channel_id=%s AND scheduled_time_utc=%s AND status='scheduled'
                    """, (task['id'], channel_id, utc_dt), fetchone=True)

                    if not exists:
                        if create_single_publication_job(task, channel_id, utc_dt, application):
                            job_count += 1
            except Exception as e:
                logger.error(f"Error scheduling specific date: {e}")

        # --- Case 2: Weekday (e.g., "Friday") ---
        elif schedule_weekday is not None:
            # --- REMOVED THE GENERIC 'future_jobs' CHECK HERE ---
            # We must calculate the specific target time first to allow multiple slots per day.

            # Calculate the next weekday
            current_wd = now_local.weekday()
            days_ahead = (schedule_weekday - current_wd) % 7

            # If it's today, check if the time has passed
            if days_ahead == 0:
                today_target = now_local.replace(
                    hour=schedule_time.hour,
                    minute=schedule_time.minute,
                    second=0,
                    microsecond=0
                )
                # If target is older than now (minus buffer), move to next week
                if today_target < (now_local - timedelta(seconds=60)):
                    days_ahead = 7

            # Calculate target local time
            target_local_date = now_local.date() + timedelta(days=days_ahead)
            target_local_dt = datetime(
                target_local_date.year,
                target_local_date.month,
                target_local_date.day,
                schedule_time.hour,
                schedule_time.minute,
                schedule_time.second,
                tzinfo=tz
            )

            # Convert to UTC
            target_utc_dt = target_local_dt.astimezone(ZoneInfo("UTC"))

            # Double check against buffer just in case calculations were weird
            if target_utc_dt < buffer_time:
                target_utc_dt += timedelta(days=7)

            # Create the job (Check for SPECIFIC duplicates)
            for channel_id in channels:
                # Check if THIS specific time slot is already booked
                exists = db_query("""
                    SELECT 1 FROM publication_jobs 
                    WHERE task_id=%s AND channel_id=%s AND scheduled_time_utc=%s AND status='scheduled'
                """, (task['id'], channel_id, target_utc_dt), fetchone=True)

                if not exists:
                    if create_single_publication_job(task, channel_id, target_utc_dt, application):
                        job_count += 1

    return job_count


