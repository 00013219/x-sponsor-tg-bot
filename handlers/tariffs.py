from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.queries.tasks import get_user_tasks
from localization.loader import get_text
from models.tariff import get_tariff_limits, Tariff
from states.conversation import TARIFF
from utils.logging import logger


async def nav_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Тарифы' с кнопками для покупки"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
    else:
        message = update.message

    user_id = context.user_data['user_id']
    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)

    tasks = get_user_tasks(user_id)

    # (Добавьте эти ключи в i18n)
    text = get_text('tariff_title', context) + "\n\n"
    text += (get_text('tariff_current_status', context) or "Ваш текущий тариф: **{name}**").format(
        name=limits['name']) + "\n"
    text += (get_text('tariff_tasks_limit', context) or "Задачи: {current} / {limit}").format(current=len(tasks),
                                                                                              limit=limits['tasks'])
    text += "\n\n"
    text += "Вы можете обновить свой тариф:\n"

    keyboard = []

    # Динамически генерируем кнопки для ВСЕХ тарифов, кроме FREE
    for tariff in Tariff:
        if tariff == Tariff.FREE:
            continue

        t_data = tariff.value
        t_key = tariff.name.lower()  # 'pro1'

        text += f"\n**{t_data['name']}** ({t_data['price']}⭐)\n"
        details_text = (get_text('tariff_details_template',
                                 context) or "✅ Лимит задач: **{task_limit}**\n✅ Лимит площадок: **{channel_limit}**")
        text += details_text.format(task_limit=t_data['tasks'],
                                    channel_limit=get_text('tariff_unlimited', context)) + "\n"

        # Добавляем кнопку, если это не текущий тариф
        if limits['name'] != t_data['name']:
            # --- 🚀 ИЗМЕНЕНИЕ ЗДЕСЬ ---

            # 1. Получаем базовый текст "Купить"
            buy_text = get_text('tariff_buy_btn', context)  # "Купить", "Buy", "Comprar" и т.д.

            # 2. Получаем данные для кнопки
            tariff_name = t_data['name']
            tariff_price = t_data['price']

            # 3. Собираем текст кнопки вручную
            button_text = f"{buy_text} {tariff_name} ({tariff_price}⭐)"

            # 4. Добавляем кнопку
            keyboard.append([
                InlineKeyboardButton(button_text, callback_data=f"tariff_buy_{t_key}")
            ])
            # --- 🚀 КОНЕЦ ИЗМЕНЕНИЯ ---

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="nav_main_menu")])

    # Используем reply_text, т.к. мы могли прийти из ReplyKeyboard
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TARIFF


async def tariff_buy_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатие кнопки 'Купить {Tariff}'"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    # 'tariff_buy_pro1' -> 'pro1'
    tariff_key_str = query.data.replace("tariff_buy_", "")

    # Получаем данные тарифа из Enum
    try:
        tariff_data = get_tariff_limits(tariff_key_str)  # 'pro1' -> {'name': 'Pro 1', ...}
    except (KeyError, AttributeError):
        await query.message.reply_text(get_text('error_tariff_not_found', context))
        return TARIFF

    # --- Параметры инвойса ---
    title = get_text('invoice_title_template', context).format(
        tariff_name=tariff_data['name']
    )

    description = get_text('invoice_description_template', context).format(
        tasks=tariff_data['tasks'],
        time_slots=tariff_data['time_slots'],
        date_slots=tariff_data['date_slots']
    )

    payload = f"tariff_buy_{tariff_key_str}_user_{user_id}"  # e.g. 'tariff_buy_pro1_user_12345'
    currency = "XTR"
    price = tariff_data['price']  # e.g. 300

    if price <= 0:
        await query.message.reply_text(
            get_text('error_tariff_cannot_buy', context)
        )
        return TARIFF

    prices = [
        {"label": title, "amount": price}
    ]

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Token not required for XTR (Stars)
            currency=currency,
            prices=prices,
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке инвойса: {e}", exc_info=True)
        await query.message.reply_text(
            get_text('error_invoice_creation', context)
        )
        return TARIFF