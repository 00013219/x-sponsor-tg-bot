import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.settings import get_user_settings
from database.queries.users import create_user
from utils.logging import logger


async def global_user_loader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.is_bot:
        return

    user_id = user.id

    try:
        def check_user_exists():
            return db_query(
                "SELECT 1 FROM users WHERE user_id = %s",
                (user_id,), fetchone=True
            )

        def run_get_user_settings():
            return get_user_settings(user_id)

        user_exists = await asyncio.to_thread(check_user_exists)

        # If user has NOT pressed /start — do nothing
        if not user_exists:
            return

        # Load user settings
        settings = await asyncio.to_thread(run_get_user_settings)

        context.user_data['user_id'] = user_id
        context.user_data['language_code'] = settings.get('language_code')
        context.user_data['timezone'] = settings.get('timezone')
        context.user_data['tariff'] = settings.get('tariff', 'free')

    except Exception as e:
        logger.error(f"Global loader failed for user {user_id}: {e}")
