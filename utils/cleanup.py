from telegram.ext import ContextTypes

from utils.logging import logger


async def cleanup_temp_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Clean up all temporary messages from user_data with better error handling"""
    if not chat_id:
        return

    temp_message_ids = context.user_data.get('temp_message_ids', [])

    # Delete all tracked temporary messages (newest first to avoid gaps)
    for message_id in sorted(temp_message_ids, reverse=True):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            logger.debug(f"✅ Deleted temp message {message_id}")
        except Exception as e:
            # Message might already be deleted, ignore
            logger.debug(f"Message {message_id} already deleted or not found: {e}")

    # Clear the stored message IDs
    context.user_data.pop('temp_message_ids', None)

    # Also cleanup old individual message IDs for backward compatibility
    old_keys = ['temp_task_message_id', 'temp_prompt_message_id', 'last_bot_message_id']
    for key in old_keys:
        message_id = context.user_data.get(key)
        if message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                logger.debug(f"✅ Deleted old temp message {message_id} from key {key}")
            except Exception as e:
                logger.debug(f"Old message {message_id} already deleted: {e}")
            context.user_data.pop(key, None)