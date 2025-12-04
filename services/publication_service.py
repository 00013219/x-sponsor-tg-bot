from datetime import datetime
from typing import Optional

from telegram.ext import Application

from database.connection import db_query
from jobs.publication import execute_publication_job
from utils.logging import logger


def create_single_publication_job(task: dict, channel_id: int, utc_dt: datetime, application: Application) -> Optional[int]:
    """Helper function to create a single publication job in DB and JobQueue"""

    # 1. Insert into DB
    job_data = db_query("""
        INSERT INTO publication_jobs (
            task_id, user_id, channel_id, scheduled_time_utc,
            content_message_id, content_chat_id, pin_duration,
            pin_notify, auto_delete_hours, advertiser_user_id, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')
        RETURNING id
    """, (
        task['id'], task['user_id'], channel_id, utc_dt,
        task['content_message_id'], task['content_chat_id'],
        task['pin_duration'], task['pin_notify'],
        task['auto_delete_hours'], task['advertiser_user_id']
    ), commit=True)

    if job_data and 'id' in job_data:
        job_id = job_data['id']
        job_name = f"pub_{job_id}"

        try:
            # 2. Schedule in Telegram JobQueue
            application.job_queue.run_once(
                execute_publication_job,
                when=utc_dt,
                data={'job_id': job_id},
                name=job_name,
                job_kwargs={'misfire_grace_time': 300}  # 5 minutes grace period
            )

            # 3. Update DB with job name
            db_query(
                "UPDATE publication_jobs SET aps_job_id = %s WHERE id = %s",
                (job_name, job_id),
                commit=True
            )
            logger.info(f"✅ Scheduled job {job_id} at {utc_dt} (channel {channel_id})")
            return job_id

        except Exception as e:
            logger.error(f"❌ Failed to schedule job {job_id} via job_queue: {e}", exc_info=True)
            db_query("UPDATE publication_jobs SET status = 'failed' WHERE id = %s", (job_id,), commit=True)
            return None
    else:
        logger.error(f"Failed to insert publication_job in DB for task {task['id']}")
        return None