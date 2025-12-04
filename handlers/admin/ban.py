from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.users import unban_user, ban_user, get_user_by_username
from handlers.admin.panel import nav_boss
from localization.loader import get_text
from states.conversation import BOSS_BAN_SELECT_USER, BOSS_BAN_CONFIRM, BOSS_PANEL


async def boss_ban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Начало процесса бана пользователя. Запрашивает ID или username."""
    query = update.callback_query
    await query.answer()

    # Локализация: текст сообщения
    text = get_text('boss_ban_start_msg', context)

    # Локализация: кнопка "Назад" (уже локализована ранее)
    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_BAN_SELECT_USER


async def boss_ban_receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Получение ID/username для бана, поиск и запрос подтверждения."""
    user_input = update.message.text.strip()
    target_user = None
    if user_input.startswith('@'):
        username = user_input[1:]
        target_user = get_user_by_username(username)
    else:
        try:
            user_id = int(user_input)
            target_user = db_query("SELECT * FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        except ValueError:
            pass

    if not target_user:
        # Локализация: сообщение об ошибке "пользователь не найден"
        await update.message.reply_text(get_text('boss_ban_user_not_found', context))
        return BOSS_BAN_SELECT_USER

    # Сохраняем данные цели
    context.user_data['ban_target_id'] = target_user['user_id']
    context.user_data['ban_target_username'] = target_user['username'] or "N/A"
    context.user_data['ban_target_is_active'] = target_user['is_active']

    # Определяем, баним или разбаниваем (и локализуем текст действия и статуса)
    if target_user['is_active']:
        action_text = get_text('boss_action_ban', context)  # "забанить"
        status_text = get_text('boss_status_active', context)  # "Активен"
        confirm_callback = "boss_ban_confirm_yes"
    else:
        action_text = get_text('boss_action_unban', context)  # "РАЗБАНИТЬ"
        status_text = get_text('boss_status_banned', context)  # "Забанен"
        confirm_callback = "boss_unban_confirm_yes"

    # Локализация: заголовки и текст подтверждения
    confirm_title = get_text('boss_ban_confirm_title', context)
    user_label = get_text('boss_ban_user_label', context)
    id_label = get_text('boss_ban_id_label', context)
    status_label = get_text('boss_ban_status_label', context)
    confirm_prompt = get_text('boss_ban_confirm_prompt', context)

    text = (f"{confirm_title}\n\n"
            f"{user_label} @{target_user['username'] or '???'}\n"
            f"{id_label} {target_user['user_id']}\n"
            f"{status_label} {status_text}\n\n"
            f"{confirm_prompt}").format(action_text=action_text)  # Вставляем локализованный action_text

    # Локализация: кнопки
    yes_prefix = get_text('boss_confirm_yes_prefix', context)  # "✅ Да, "
    cancel_btn_text = get_text('boss_confirm_cancel_btn', context)  # "❌ Нет, отмена"

    keyboard = [
        [InlineKeyboardButton(f"{yes_prefix}{action_text}", callback_data=confirm_callback)],
        [InlineKeyboardButton(cancel_btn_text, callback_data="nav_boss")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_BAN_CONFIRM


async def boss_ban_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Подтверждение бана."""
    query = update.callback_query
    await query.answer()
    target_id = context.user_data.get('ban_target_id')
    target_username = context.user_data.get('ban_target_username', 'N/A')

    if not target_id:
        # Локализация: ошибка сессии
        await query.edit_message_text(get_text('boss_ban_session_error', context))
        return await nav_boss(update, context)

    # Вызываем функцию бана
    ban_user(target_id)

    # Локализация: сообщение об успешном бане
    text = get_text('boss_ban_success', context).format(
        target_username=target_username,
        target_id=target_id
    )

    await query.edit_message_text(
        text,
        # Локализация: кнопка "Назад в Boss" (уже локализована)
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(get_text('boss_back_to_boss', context), callback_data="nav_boss")]])
    )

    # Очистка
    context.user_data.pop('ban_target_id', None)
    context.user_data.pop('ban_target_username', None)
    context.user_data.pop('ban_target_is_active', None)

    return BOSS_PANEL


async def boss_unban_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Boss) Подтверждение РАЗБАНА."""
    query = update.callback_query
    await query.answer()
    target_id = context.user_data.get('ban_target_id')
    target_username = context.user_data.get('ban_target_username', 'N/A')

    if not target_id:
        # Локализация: ошибка сессии
        await query.edit_message_text(get_text('boss_ban_session_error', context))
        return await nav_boss(update, context)

    # Вызываем функцию разбана
    unban_user(target_id)

    # Локализация: сообщение об успешном разбане
    text = get_text('boss_unban_success', context).format(
        target_username=target_username,
        target_id=target_id
    )

    await query.edit_message_text(
        text,
        # Локализация: кнопка "Назад в Boss" (уже локализована)
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(get_text('boss_back_to_boss', context), callback_data="nav_boss")]])
    )

    # Очистка
    context.user_data.pop('ban_target_id', None)
    context.user_data.pop('ban_target_username', None)
    context.user_data.pop('ban_target_is_active', None)

    return BOSS_PANEL