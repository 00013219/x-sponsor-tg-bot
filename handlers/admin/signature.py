import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import db_query
from localization.loader import get_text
from states.conversation import BOSS_SIGNATURE_EDIT, BOSS_PANEL
from utils.logging import logger


async def boss_signature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка подписи для FREE тарифа"""
    query = update.callback_query
    await query.answer()

    # Получаем текущую подпись из настроек
    current_signature = db_query("""
        SELECT signature FROM bot_settings WHERE id = 1
    """, fetchone=True)

    current_text = current_signature['signature'] if current_signature and current_signature['signature'] else get_text(
        'boss_signature_not_set', context)

    text = get_text('boss_signature_title', context) + "\n\n"
    text += get_text('boss_signature_info', context) + "\n\n"
    text += get_text('boss_signature_current', context).format(current_text=current_text)

    if current_signature and current_signature.get('signature'):
        delete_btn = [
            InlineKeyboardButton(
                get_text('boss_signature_delete_btn', context),
                callback_data="boss_signature_delete"
            )
        ]
    else:
        delete_btn = None

    keyboard = []
    if delete_btn:
        keyboard.append(delete_btn)
    keyboard.append([
        InlineKeyboardButton(
            get_text('boss_back_btn', context),
            callback_data="nav_boss"
        )
    ])

    # Use HTML parse mode - now it will work because examples are escaped
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML', disable_web_page_preview=True)

    return BOSS_SIGNATURE_EDIT


async def boss_signature_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение новой подписи"""
    signature = update.message.text.strip()
    signature = signature.replace("“", '"').replace("”", '"').replace("’", "'")

    if len(signature) > 200:
        await update.message.reply_text(get_text('boss_signature_too_long', context))
        return BOSS_SIGNATURE_EDIT

    # Создаем таблицу bot_settings если её нет
    db_query("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            signature TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """, commit=True)

    # Сохраняем подпись
    db_query("""
        INSERT INTO bot_settings (id, signature)
        VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE SET signature = EXCLUDED.signature, updated_at = CURRENT_TIMESTAMP
    """, (signature,), commit=True)

    text = get_text('boss_signature_updated', context).format(signature=signature)
    keyboard = [[InlineKeyboardButton(get_text('boss_back_to_boss', context), callback_data="nav_boss")]]

    # Use HTML parse mode so the saved signature displays correctly
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML', disable_web_page_preview=True)

    return BOSS_PANEL


async def boss_signature_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление подписи"""
    query = update.callback_query
    await query.answer()

    db_query("""
        UPDATE bot_settings SET signature = NULL WHERE id = 1
    """, commit=True)

    text = get_text('boss_signature_deleted', context)
    await query.edit_message_text(text)
    await asyncio.sleep(2)

    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete success message: {e}")

    # Send new signature menu
    current_text = get_text('boss_signature_not_set', context)

    menu_text = get_text('boss_signature_title', context) + "\n\n"
    menu_text += get_text('boss_signature_info', context) + "\n\n"
    menu_text += get_text('boss_signature_current', context).format(current_text=current_text)

    current_signature = db_query("""
            SELECT signature FROM bot_settings WHERE id = 1
        """, fetchone=True)

    if current_signature and current_signature.get('signature'):
        delete_btn = [
            InlineKeyboardButton(
                get_text('boss_signature_delete_btn', context),
                callback_data="boss_signature_delete"
            )
        ]
    else:
        delete_btn = None

    keyboard = []
    if delete_btn:
        keyboard.append(delete_btn)
    keyboard.append([
        InlineKeyboardButton(
            get_text('boss_back_btn', context),
            callback_data="nav_boss"
        )
    ])

    # Use HTML parse mode
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=menu_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML',
        disable_web_page_preview=True
    )

    return BOSS_SIGNATURE_EDIT