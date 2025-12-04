from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application

from database.connection import db_query
from database.queries.settings import get_user_settings
from jobs.delete import execute_delete_job
from jobs.scheduler import create_publication_jobs_for_task
from jobs.unpin import execute_unpin_job
from utils.logging import logger


async def restore_active_tasks(application: Application):
    """
    Restores ACTIVE tasks (scheduling future posts) AND
    Restores PENDING ACTIONS (unpin/delete) for already published posts.

    CRASH-RESISTANT: Calculates target times from ORIGINAL publish time,
    not from restart time.
    """
    logger.info("🔄 Restoring active tasks on startup...")

    # 1. Clean up stale scheduled jobs
    db_query("UPDATE publication_jobs SET status = 'cancelled' WHERE status = 'scheduled'", commit=True)

    # 2. Restore FUTURE publication jobs
    active_tasks = db_query("SELECT id, user_id FROM tasks WHERE status = 'active'", fetchall=True) or []
    count = 0
    for task in active_tasks:
        user_settings = get_user_settings(task['user_id'])
        user_tz = user_settings.get('timezone', 'Europe/Moscow')
        count += create_publication_jobs_for_task(task['id'], user_tz, application)

    logger.info(f"✅ Restored {len(active_tasks)} active tasks. Scheduled {count} future publications.")

    # 3. RESTORE PENDING POST ACTIONS (Auto-Delete & Unpin) - CRASH-RESISTANT
    logger.info("🔄 Restoring pending post actions (Auto-Delete/Unpin)...")

    pending_jobs = db_query("""
        SELECT * FROM publication_jobs 
        WHERE status = 'published' 
        AND (auto_delete_hours > 0 OR pin_duration > 0)
    """, fetchall=True) or []

    restored_actions = 0
    immediate_actions = 0
    now_utc = datetime.now(ZoneInfo('UTC'))

    for job in pending_jobs:
        job_id = job['id']
        channel_id = job['channel_id']
        message_id = job['posted_message_id']
        published_at = job['published_at']

        if not published_at or not message_id:
            continue

        # Ensure published_at is timezone-aware
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=ZoneInfo('UTC'))

        # ===============================================
        # A. RESTORE AUTO-DELETE (CRASH-RESISTANT)
        # ===============================================
        if job['auto_delete_hours'] > 0:
            # 🔥 FIX: Calculate from ORIGINAL publish time
            target_delete_time = published_at + timedelta(hours=job['auto_delete_hours'])

            if target_delete_time > now_utc:
                # Future execution - schedule normally
                application.job_queue.run_once(
                    execute_delete_job,
                    when=target_delete_time,
                    data={'channel_id': channel_id, 'message_id': message_id, 'job_id': job_id},
                    name=f"del_{job_id}_{message_id}"
                )
                restored_actions += 1
                logger.debug(f"🕒 Scheduled delete for job {job_id} at {target_delete_time}")
            else:
                # ⚠️ Time already passed during downtime - execute immediately
                application.job_queue.run_once(
                    execute_delete_job,
                    when=5,  # 5 seconds buffer
                    data={'channel_id': channel_id, 'message_id': message_id, 'job_id': job_id},
                    name=f"del_{job_id}_{message_id}_immediate"
                )
                immediate_actions += 1
                logger.warning(
                    f"⚡ Immediate delete scheduled for job {job_id} (missed by {now_utc - target_delete_time})")

        # ===============================================
        # B. RESTORE UNPIN (CRASH-RESISTANT)
        # ===============================================
        if job['pin_duration'] > 0:
            # 🔥 FIX: Calculate from ORIGINAL publish time
            target_unpin_time = published_at + timedelta(hours=job['pin_duration'])

            if target_unpin_time > now_utc:
                # Future execution - schedule normally
                application.job_queue.run_once(
                    execute_unpin_job,
                    when=target_unpin_time,
                    data={'channel_id': channel_id, 'message_id': message_id, 'job_id': job_id},
                    name=f"unpin_{job_id}_{message_id}"
                )
                restored_actions += 1
                logger.debug(f"📌 Scheduled unpin for job {job_id} at {target_unpin_time}")
            else:
                # ⚠️ Time already passed - execute immediately
                application.job_queue.run_once(
                    execute_unpin_job,
                    when=5,
                    data={'channel_id': channel_id, 'message_id': message_id, 'job_id': job_id},
                    name=f"unpin_{job_id}_{message_id}_immediate"
                )
                immediate_actions += 1
                logger.warning(
                    f"⚡ Immediate unpin scheduled for job {job_id} (missed by {now_utc - target_unpin_time})")

    logger.info(f"✅ Restored {restored_actions} pending actions (future execution).")
    logger.info(f"⚡ Scheduled {immediate_actions} missed actions for immediate execution.")