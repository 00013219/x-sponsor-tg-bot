from telegram.error import TelegramError
from telegram.ext import ContextTypes

from utils.logging import logger


async def execute_unpin_job(context: ContextTypes.DEFAULT_TYPE):
    """
    ИСПОЛНИТЕЛЬ (вызывается JobQueue)
    Открепляет сообщение (Unpin).
    """
    bot = context.bot
    channel_id = context.job.data.get('channel_id')
    message_id = context.job.data.get('message_id')
    job_id = context.job.data.get('job_id', 'N/A')

    if not channel_id or not message_id:
        return

    logger.info(f"Запуск execute_unpin_job для job_id: {job_id} -> Unpin {message_id} в {channel_id}")

    try:
        await bot.unpin_chat_message(chat_id=channel_id, message_id=message_id)
        logger.info(f"Сообщение {message_id} успешно откреплено в {channel_id}")
    except TelegramError as e:
        logger.warning(f"Не удалось открепить сообщение {message_id} в {channel_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при откреплении {message_id}: {e}")