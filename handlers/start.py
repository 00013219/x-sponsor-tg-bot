import asyncio
from zoneinfo import ZoneInfoNotFoundError, ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from database.queries.users import set_user_lang_tz
from handlers.admin.panel import nav_boss
from handlers.navigation import show_main_menu
from keyboards.lang import lang_keyboard
from keyboards.time_selection import timezone_keyboard
from localization.loader import get_text
from localization.texts import TEXTS
from states.conversation import START_SELECT_LANG, START_SELECT_TZ
from utils.logging import logger


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user.is_bot:
        return ConversationHandler.END

    logger.info(f"User {user.id} called /start. Username: {user.username}")

    if context.args and context.args[0] == 'boss_panel':
        return await nav_boss(update, context)

    # Check settings loaded by global_user_loader
    lang_set = context.user_data.get('language_code')
    tz_set = context.user_data.get('timezone')

    # If settings are missing (None), FORCE setup
    if not lang_set or not tz_set:
        logger.info(f"New or unconfigured user {user.id}. Starting setup.")

        # Use a safe fallback for text
        text = TEXTS.get('ru', {}).get('welcome_lang', "Select Language:")

        await update.message.reply_text(text, reply_markup=lang_keyboard())
        return START_SELECT_LANG

    return await show_main_menu(update, context)

async def start_select_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # 1. Answer immediately to stop the spinner
    try:
        await query.answer()
    except Exception:
        pass

    # 2. Extract and validate language
    lang = query.data.replace("lang_", "")
    if 'TEXTS' in globals() and lang not in TEXTS:
        lang = 'en'

    # 3. Save to DB NON-BLOCKINGLY
    def run_set_lang():
        # Synchronous call
        set_user_lang_tz(user_id=query.from_user.id, lang=lang)

    try:
        await asyncio.to_thread(run_set_lang)
    except Exception as e:
        logger.error(f"Failed to save language non-blocking: {e}")
        # Continue anyway

    # 4. Update local context
    context.user_data['language_code'] = lang

    # 5. Show Timezone selection
    text = get_text('select_timezone', context)
    if not text:
        text = "Select your Timezone:"

    await query.edit_message_text(text, reply_markup=timezone_keyboard(context))
    return START_SELECT_TZ

async def start_select_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Состояние START_SELECT_TZ. 1. Юзер нажал кнопку таймзоны. 2. Сохраняем таймзону. 3. Показываем Главное меню."""
    query = update.callback_query
    await query.answer()

    tz_name = query.data.replace("tz_", "")

    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning(f"Неверная таймзона: {tz_name}")
        tz_name = 'Europe/Moscow'

    set_user_lang_tz(user_id=query.from_user.id, tz=tz_name)
    context.user_data['timezone'] = tz_name

    return await show_main_menu(update, context)