from telegram.ext import ContextTypes

from database.connection import db_query
from utils.logging import logger


async def cancel_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancels all scheduled jobs for a specific task in both JobQueue and DB.
    Used before refreshing a task to avoid duplicates.
    """
    # 1. Find scheduled jobs in DB
    jobs_to_cancel = db_query(
        "SELECT aps_job_id FROM publication_jobs WHERE task_id = %s AND status = 'scheduled' AND aps_job_id IS NOT NULL",
        (task_id,), fetchall=True
    )

    if jobs_to_cancel:
        for job_row in jobs_to_cancel:
            job_name = job_row.get('aps_job_id')
            if job_name:
                # Remove from Telegram JobQueue
                jobs = context.application.job_queue.get_jobs_by_name(job_name)
                for job in jobs:
                    job.schedule_removal()

    # 2. Mark them as cancelled in DB
    db_query(
        "UPDATE publication_jobs SET status = 'cancelled' WHERE task_id = %s AND status = 'scheduled'",
        (task_id,), commit=True
    )
    logger.info(f"Cancelled pending jobs for task {task_id}")