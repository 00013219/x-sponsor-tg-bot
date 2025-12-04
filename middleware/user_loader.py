import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.settings import get_user_settings
from database.queries.users import create_user
from utils.logging import logger


async def global_user_loader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Middleware that loads user data from the database for EVERY update.
    FIXED: Uses asyncio.to_thread for non-blocking synchronous DB calls.
    """
    user = update.effective_user
    if not user or user.is_bot:
        return

    user_id = user.id

    try:
        # Define synchronous wrappers
        def check_user_exists():
            return db_query("SELECT 1 FROM users WHERE user_id = %s", (user_id,), fetchone=True)

        def run_create_user():
            create_user(
                user_id=user_id,
                username=user.username or "",
                first_name=user.first_name or ""
            )

        def run_get_user_settings():
            return get_user_settings(user_id)

        # Execute DB checks and creation using asyncio.to_thread
        user_exists = await asyncio.to_thread(check_user_exists)

        if not user_exists:
            await asyncio.to_thread(run_create_user)
            logger.info(f"Created new user in global loader: {user_id}")

        # Load user settings using asyncio.to_thread
        settings = await asyncio.to_thread(run_get_user_settings)

        # Populate user_data
        context.user_data['user_id'] = user_id
        context.user_data['language_code'] = settings.get('language_code')
        context.user_data['timezone'] = settings.get('timezone')
        context.user_data['tariff'] = settings.get('tariff', 'free')

    except Exception as e:
        logger.error(f"Global loader failed for user {user_id} due to DB error or blocking: {e}")
        # Fallback to prevent crash
        if 'language_code' not in context.user_data:
            context.user_data['language_code'] = 'en'
        if 'tariff' not in context.user_data:
            context.user_data['tariff'] = 'free'