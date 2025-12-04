from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.settings import get_user_settings
from database.queries.users import get_user_by_username
from handlers.admin.panel import nav_boss
from localization.loader import get_text
from models.tariff import get_tariff_limits
from states.conversation import BOSS_GRANT_TARIFF, BOSS_PANEL, BOSS_GRANT_CONFIRM
from utils.logging import logger


async def boss_grant_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Start grant tariff process - show instructions"""
    query = update.callback_query
    await query.answer()

    text = get_text('boss_grant_title', context) + "\n\n"
    text += get_text('boss_grant_instructions', context)

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return BOSS_GRANT_TARIFF


async def boss_grant_receive_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Receive and parse grant input: @username tariff"""
    user_input = update.message.text.strip()

    # Parse input: @username tariff_name
    parts = user_input.split()

    if len(parts) != 2:
        await update.message.reply_text(get_text('boss_grant_invalid_format', context))
        return BOSS_GRANT_TARIFF

    username_input = parts[0]
    tariff_input = parts[1].lower()

    # Remove @ if present
    if username_input.startswith('@'):
        username_input = username_input[1:]

    # Validate tariff
    valid_tariffs = ['free', 'pro1', 'pro2', 'pro3', 'pro4']
    if tariff_input not in valid_tariffs:
        await update.message.reply_text(get_text('boss_grant_invalid_tariff', context))
        return BOSS_GRANT_TARIFF

    # Find user
    target_user = get_user_by_username(username_input)

    if not target_user:
        await update.message.reply_text(get_text('boss_grant_user_not_found', context))
        return BOSS_GRANT_TARIFF

    # Store data for confirmation
    context.user_data['grant_target_id'] = target_user['user_id']
    context.user_data['grant_target_username'] = target_user['username'] or "N/A"
    context.user_data['grant_current_tariff'] = target_user['tariff']
    context.user_data['grant_new_tariff'] = tariff_input

    # Get tariff names for display
    current_limits = get_tariff_limits(target_user['tariff'])
    new_limits = get_tariff_limits(tariff_input)

    # Confirmation message
    text = get_text('boss_grant_confirm_template', context).format(
        username=target_user['username'] or '???',
        user_id=target_user['user_id'],
        current_tariff=current_limits['name'],
        new_tariff=new_limits['name']
    )

    keyboard = [
        [InlineKeyboardButton(get_text('boss_grant_confirm_yes', context), callback_data="boss_grant_confirm_yes")],
        [InlineKeyboardButton(get_text('boss_grant_confirm_no', context), callback_data="nav_boss")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return BOSS_GRANT_CONFIRM


async def boss_grant_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Confirm and execute tariff grant"""
    query = update.callback_query
    await query.answer()

    target_id = context.user_data.get('grant_target_id')
    target_username = context.user_data.get('grant_target_username', 'N/A')
    new_tariff = context.user_data.get('grant_new_tariff')

    if not target_id or not new_tariff:
        await query.edit_message_text(get_text('boss_ban_session_error', context))
        return await nav_boss(update, context)

    # Update tariff in database
    db_query("UPDATE users SET tariff = %s WHERE user_id = %s", (new_tariff, target_id), commit=True)

    # Get tariff name for display
    limits = get_tariff_limits(new_tariff)
    tariff_name = limits['name']

    # Success message
    text = get_text('boss_grant_success', context).format(
        tariff_name=tariff_name,
        username=target_username,
        user_id=target_id
    )

    # Notify the user
    try:
        user_settings = get_user_settings(target_id)
        user_lang = user_settings.get('language_code', 'en')

        notification = get_text('tariff_success_template', context, lang=user_lang).format(
            tariff_name=tariff_name
        )

        await context.bot.send_message(chat_id=target_id, text=notification)
    except Exception as e:
        logger.error(f"Failed to notify user {target_id} about tariff grant: {e}")
        text += "\n\n⚠️ User notification failed."

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(get_text('boss_back_to_boss', context), callback_data="nav_boss")]]
        ),
        parse_mode='HTML'
    )

    # Cleanup
    context.user_data.pop('grant_target_id', None)
    context.user_data.pop('grant_target_username', None)
    context.user_data.pop('grant_current_tariff', None)
    context.user_data.pop('grant_new_tariff', None)

    return BOSS_PANEL