from telegram import InlineKeyboardButton, Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import OWNER_ID
from database.connection import db_query
from database.queries.users import get_user_by_username
from localization.loader import get_text
from states.conversation import BOSS_MAILING_MESSAGE, BOSS_MAILING_CONFIRM, BOSS_MAILING_EXCLUDE, BOSS_PANEL
from utils.logging import logger


async def boss_mailing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылки - создание"""
    query = update.callback_query
    await query.answer()

    text = get_text('boss_mailing_constructor', context)

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_MAILING_MESSAGE


async def boss_mailing_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение сообщения для рассылки"""
    # Сохраняем сообщение
    context.user_data['mailing_message_id'] = update.message.message_id
    context.user_data['mailing_chat_id'] = update.message.chat_id

    text = get_text('boss_mailing_saved', context)

    keyboard = [
        [InlineKeyboardButton(get_text('boss_mailing_skip_btn', context), callback_data="boss_mailing_skip_exclude")],
        [InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_MAILING_EXCLUDE


async def boss_mailing_exclude(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка списка исключений"""
    exclude_list = update.message.text.strip()

    # Парсим список
    excluded_users = []
    for item in exclude_list.split(','):
        item = item.strip()
        if item.startswith('@'):
            user = get_user_by_username(item[1:])
            if user:
                excluded_users.append(user['user_id'])
        else:
            try:
                excluded_users.append(int(item))
            except ValueError:
                continue
    context.user_data['mailing_exclude'] = excluded_users

    return await boss_mailing_confirm_preview(update, context)


async def boss_mailing_skip_exclude(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пропустить исключения"""
    query = update.callback_query
    await query.answer()

    context.user_data['mailing_exclude'] = []

    return await boss_mailing_confirm_preview(update, context)


async def boss_mailing_confirm_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Предпросмотр и подтверждение рассылки"""
    excluded = context.user_data.get('mailing_exclude', [])

    # Подсчитываем получателей
    all_users = db_query("SELECT COUNT(*) as count FROM users WHERE is_active = TRUE", fetchone=True)
    total_recipients = (all_users['count'] if all_users else 0) - len(excluded)

    text = get_text('boss_mailing_confirm_title', context) + "\n\n"
    text += get_text('boss_mailing_recipients', context).format(total_recipients=total_recipients) + "\n"
    text += get_text('boss_mailing_excluded', context).format(excluded_count=len(excluded)) + "\n\n"
    text += get_text('boss_mailing_confirm_prompt', context)

    keyboard = [
        [InlineKeyboardButton(get_text('boss_mailing_send_btn', context), callback_data="boss_mailing_send")],
        [InlineKeyboardButton(get_text('boss_mailing_cancel_btn', context), callback_data="nav_boss")]
    ]

    if isinstance(update, Update) and update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return BOSS_MAILING_CONFIRM


async def boss_mailing_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнение рассылки"""
    query = update.callback_query
    await query.answer(get_text('boss_mailing_started', context))

    message_id = context.user_data.get('mailing_message_id')
    chat_id = context.user_data.get('mailing_chat_id')
    excluded = context.user_data.get('mailing_exclude', [])

    # Получаем всех активных пользователей
    users = db_query("""
        SELECT user_id FROM users 
        WHERE is_active = TRUE
    """, fetchall=True) or []

    sent = 0
    failed = 0

    await query.edit_message_text(get_text('boss_mailing_sending_initial', context))

    for user in users:
        user_id = user['user_id']

        if user_id in excluded or user_id == OWNER_ID:
            continue

        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=chat_id,
                message_id=message_id
            )
            sent += 1

            # Обновляем прогресс каждые 10 сообщений
            if sent % 10 == 0:
                try:
                    await query.edit_message_text(
                        get_text('boss_mailing_sending', context).format(sent=sent, failed=failed)
                    )
                except:
                    pass

        except Exception as e:
            failed += 1
            logger.warning(f"Failed to send mailing to {user_id}: {e}")

    # Очищаем данные
    context.user_data.pop('mailing_message_id', None)
    context.user_data.pop('mailing_chat_id', None)
    context.user_data.pop('mailing_exclude', None)

    text = get_text('boss_mailing_completed_title', context) + "\n\n"
    text += get_text('boss_mailing_sent_count', context).format(sent=sent) + "\n"
    text += get_text('boss_mailing_failed_count', context).format(failed=failed)

    keyboard = [[InlineKeyboardButton(get_text('boss_back_to_boss', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL
