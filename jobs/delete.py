import json

from telegram.ext import ContextTypes

from database.connection import db_query
from utils.logging import logger


async def execute_delete_job(context: ContextTypes.DEFAULT_TYPE):
    """
    ИСПОЛНИТЕЛЬ: Удаляет сообщение (или группу сообщений) и обновляет статус в БД.
    """
    bot = context.bot
    channel_id = context.job.data.get('channel_id')
    fallback_message_id = context.job.data.get('message_id')
    direct_message_ids = context.job.data.get('message_ids')  # Direct IDs from publication job
    job_id = context.job.data.get('job_id')

    if not channel_id:
        return

    messages_to_delete = []

    # 0. First priority: Use message_ids passed directly from publication job
    if direct_message_ids and isinstance(direct_message_ids, list):
        messages_to_delete = direct_message_ids
        logger.debug(f"Using direct message_ids from job data: {messages_to_delete}")

    # 1. Try to fetch the full list of IDs from DB
    if not messages_to_delete and job_id:
        job_data = db_query(
            "SELECT posted_message_ids, posted_message_id FROM publication_jobs WHERE id = %s",
            (job_id,), fetchone=True
        )

        if job_data:
            # Try to get the list from posted_message_ids (JSONB field)
            if job_data.get('posted_message_ids'):
                try:
                    ids = job_data['posted_message_ids']
                    # Handle both string and list types
                    if isinstance(ids, str):
                        messages_to_delete = json.loads(ids)
                    elif isinstance(ids, list):
                        messages_to_delete = ids
                    else:
                        logger.warning(f"Unexpected type for posted_message_ids: {type(ids)}")
                except Exception as e:
                    logger.error(f"Error parsing posted_message_ids for job {job_id}: {e}")

            # Fallback to single ID from DB if list is empty
            if not messages_to_delete and job_data.get('posted_message_id'):
                messages_to_delete = [job_data['posted_message_id']]

    # 2. Final fallback to the ID passed in the job data
    if not messages_to_delete and fallback_message_id:
        messages_to_delete = [fallback_message_id]

    # 3. Execute Deletion
    if not messages_to_delete:
        logger.warning(f"No messages to delete for job {job_id}")
        return

    deleted_count = 0
    failed_ids = []

    for msg_id in messages_to_delete:
        try:
            await bot.delete_message(chat_id=channel_id, message_id=msg_id)
            deleted_count += 1
            logger.debug(f"✅ Deleted message {msg_id} from channel {channel_id}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to delete message {msg_id} from {channel_id}: {e}")
            failed_ids.append(msg_id)

    logger.info(f"🗑️ Deleted {deleted_count}/{len(messages_to_delete)} messages for job {job_id}")

    if failed_ids:
        logger.warning(f"Failed to delete message IDs: {failed_ids}")

    # 4. Update Status
    if job_id:
        db_query("UPDATE publication_jobs SET status = 'deleted' WHERE id = %s", (job_id,), commit=True)
