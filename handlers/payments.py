from telegram import Update
from telegram.ext import ContextTypes

from config.settings import OWNER_ID
from database.connection import db_query
from keyboards.reply import main_menu_reply_keyboard
from localization.loader import get_text
from models.tariff import get_tariff_limits
from utils.logging import logger


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ответ на запрос PreCheckout.
    Здесь вы должны проверить, можете ли вы "продать" товар.
    Например, не закончился ли он на складе.
    Для тарифов мы просто подтверждаем.
    """
    query = update.pre_checkout_query

    # Проверяем, что токен провайдера совпадает (на всякий случай)
    if query.invoice_payload.startswith('tariff_'):
        await query.answer(ok=True)
    else:
        # Отклоняем неизвестные платежи
        await query.answer(ok=False, error_message=get_text('precheckout_error', context))
        logger.warning(f"Получен неизвестный precheckout: {query.invoice_payload}")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Вызывается ПОСЛЕ успешной оплаты.
    Здесь вы должны выдать "товар" - т.е. обновить тариф пользователю в БД.
    """
    payment_info = update.message.successful_payment
    payload = payment_info.invoice_payload  # 'tariff_buy_pro1_user_12345'
    telegram_charge_id = payment_info.telegram_payment_charge_id
    user_id = update.effective_user.id

    logger.info(
        f"Успешный платеж от {user_id}. Payload: {payload}. "
        f"Telegram payment charge ID: {telegram_charge_id}"
    )

    try:
        # --- Динамическая обработка payload ---
        # 'tariff_buy_pro1_user_12345'
        if payload.startswith('tariff_buy_') and payload.endswith(f'_user_{user_id}'):

            # 'pro1'
            tariff_key_str = payload.split('_')[2]

            # Получаем имя тарифа, 'Pro 1'
            limits = get_tariff_limits(tariff_key_str)
            tariff_name = limits['name']

            # 1. Обновить тариф в БД (сохраняем 'pro1', 'pro2' и т.д.)
            db_query("UPDATE users SET tariff = %s WHERE user_id = %s", (tariff_key_str, user_id), commit=True)

            # 2. Обновить тариф в context.user_data
            context.user_data['tariff'] = tariff_key_str

            # 3. Сообщить пользователю
            await update.message.reply_text(
                text=get_text('payment_success_template', context).format(
                    tariff_name=tariff_name
                ),
                reply_markup=main_menu_reply_keyboard(context),
            )

            # 4. (Опционально) Уведомить админа
            if OWNER_ID != user_id:
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=f"💰 Пользователь {user_id} (@{update.effective_user.username}) "
                         f"оплатил тариф '{tariff_name}' ({payment_info.total_amount} {payment_info.currency}) "
                         f"через Stars."
                )
        # --- КОНЕЦ ДИНАМИЧЕСКОЙ ОБРАБОТКИ ---
        else:
            logger.warning(f"Неизвестный payload в successful_payment: {payload}")

    except Exception as e:
        logger.error(f"Ошибка при обработке успешного платежа {payload}: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при обновлении вашего тарифа. Свяжитесь с поддержкой.")
