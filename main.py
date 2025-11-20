#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import re
import calendar
from enum import Enum

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler,
    ConversationHandler, PreCheckoutQueryHandler,
)
from telegram.error import TelegramError, Forbidden
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from psycopg2 import errorcodes
from dotenv import load_dotenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

load_dotenv()

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')

# --- Пул соединений с БД ---
try:
    if not DATABASE_URL:
        logger.critical("DATABASE_URL не установлен! Бот не может работать без БД.")
        db_pool = None
    else:
        db_pool = SimpleConnectionPool(1, 20, DATABASE_URL)
        logger.info("Пул соединений с БД успешно создан")
except Exception as e:
    logger.error(f"Не удалось создать пул соединений с БД: {e}")
    db_pool = None

# --- Scheduler для отложенных задач ---
scheduler = AsyncIOScheduler(timezone='UTC')

# --- Состояния для ConversationHandler (FSM) ---
(
    # --- Главные экраны ---
    MAIN_MENU,
    MY_TASKS,
    MY_CHANNELS,
    FREE_DATES,
    TARIFF,
    REPORTS,
    BOSS_PANEL,

    # --- Процесс /start ---
    START_SELECT_LANG,
    START_SELECT_TZ,

    # --- Конструктор Задач ---
    TASK_CONSTRUCTOR,
    TASK_SET_NAME,
    TASK_SELECT_CHANNELS,
    TASK_SET_MESSAGE,
    TASK_SELECT_CALENDAR,
    TASK_SELECT_TIME,
    TASK_SET_PIN,
    TASK_SET_PIN_NOTIFY,
    TASK_SET_DELETE,
    TASK_SET_REPORT,
    TASK_SET_ADVERTISER,
    TASK_SET_POST_TYPE,
    TASK_SET_CUSTOM_TIME,

    # --- Календарь и Время ---
    CALENDAR_VIEW,
    TIME_SELECTION,

    # --- Админка ---
    BOSS_MAILING,
    BOSS_STATS,
    BOSS_USERS,
    BOSS_LIMITS,
    BOSS_TARIFFS,
    BOSS_BAN,
    BOSS_MONEY,
    BOSS_LOGS,

    # --- Boss Panel Extended ---
    BOSS_MAILING_CREATE,
    BOSS_MAILING_MESSAGE,
    BOSS_MAILING_EXCLUDE,
    BOSS_MAILING_CONFIRM,
    BOSS_SIGNATURE_EDIT,
    BOSS_USERS_LIST,
    BOSS_STATS_VIEW,
    BOSS_LIMITS_SELECT_USER,
    BOSS_LIMITS_SET_VALUE,
    BOSS_TARIFFS_EDIT,
    BOSS_BAN_SELECT_USER,
    BOSS_BAN_CONFIRM,
    BOSS_MONEY_VIEW,
    BOSS_LOGS_VIEW,

    # --- НОВОЕ СОСТОЯНИЕ ---
    TASK_DELETE_CONFIRM

) = range(47)

# --- Тексты (i18n) ---
TEXTS = {
    'ru': {
        'welcome_lang': """🤖 Добро пожаловать в XSponsorBot!
Я помогаю автоматизировать рекламные публикации в Telegram каналах.
Вы можете создавать задачи, выбирать каналы для размещения, настраивать время публикации, закрепление, автоудаление и отчёты.
Моя цель — сделать ваше сотрудничество с рекламодателями максимально эффективным и удобным.
Давайте начнем! Пожалуйста, выберите ваш язык:""",
        'select_timezone': "Пожалуйста, выберите ваш часовой пояс:",
        'main_menu': "📋 **Главное меню**\n\nВыберите действие:",
        'task_constructor_title': "🎯 Конструктор Задач",
        'task_default_name': " (Название не задано)",
        'task_ask_name': "📝 Введите название для этого задания (напр. 'Реклама Кафе'):",
        'task_ask_message': "📝 Отправьте или перешлите боту сообщение, которое нужно опубликовать.\n(Это может быть текст, фото, видео и т.д.)",
        'task_ask_advertiser': "🔗 Введите username рекламодателя (напр. @username или user123):",
        'task_advertiser_saved': "✅ Рекламодатель сохранен!",
        'task_advertiser_not_found': "❌ Пользователь с таким username не найден...",
        'status_not_selected': "❌ Не выбрано",
        'status_yes': "✅ Да",
        'status_no': "❌ Нет",
        'calendar_entire_month': "Весь месяц",
        'calendar_reset': "Сбросить",
        'time_custom': "🕐 Свое время",
        'time_clear': "Очистить",

        # --- Ключи для клавиатур ---
        'nav_new_task_btn': "🚀 ➕ Новая задача",
        'nav_my_tasks_btn': "📋 Мои задачи",
        'nav_channels_btn': "🧩 Площадки",
        'nav_free_dates_btn': "ℹ️ Свободные даты",
        'nav_tariff_btn': "💳 Тариф",
        'nav_boss_btn': "😎 Boss",
        'nav_language_btn': "🌐 Смена языка",
        'nav_timezone_btn': "🕰️ Смена таймзоны",
        'nav_reports_btn': "☑️ Отчёты",
        'keyboard_main_menu_title': "⌨️ Главное меню:",
        'reply_keyboard_prompt': "Выберите действие на клавиатуре:",
        'task_set_name_btn': "📝 Название задачи",
        'task_select_channels_btn': "📢 Каналы",
        'task_set_message_btn': "📝 Сообщение",
        'task_select_calendar_btn': "📅 Календарь",
        'task_select_time_btn': "🕐 Время",
        'task_set_pin_btn': "📌 Закреплять",
        'task_set_pin_notify_btn': "📌 с Пуш",
        'task_set_delete_btn': "🧹 Авто-удаление",
        'task_set_report_btn': "📊 Отчёт",
        'task_set_advertiser_btn': "🔗 Рекламодатель",
        'task_set_post_type_btn': "📤 Тип поста",
        'task_delete_btn': "🗑️ Удалить задачу",
        'back_to_main_menu_btn': "⬅️ Назад (в Главное меню)",
        'task_activate_btn': "✅ АКТИВИРОВАТЬ ЗАДАЧУ",
        'back_btn': "⬅️ Назад",
        'home_main_menu_btn': "🏠 Главное меню",
        'duration_12h': "12ч",
        'duration_24h': "24ч",
        'duration_48h': "48ч",
        'duration_3d': "3д",
        'duration_7d': "7д",
        'duration_no': "❌ Нет",
        'duration_ask_pin': "📌 Выберите длительность закрепления:",
        'duration_ask_delete': "🧹 Выберите длительность автоудаления:",

        # --- Добавленные локализации ---
        'status_set': "✅ Задано",
        'status_not_set': "❌ Не задано",
        'status_from_bot': "От бота",
        'status_repost': "Репост",
        'error_generic': "❌ Произошла ошибка. Попробуйте снова.",
        'task_message_saved': "✅ Сообщение для публикации сохранено!",
        'task_name_saved': "✅ Название задачи сохранено!",

        'calendar_prev': "⬅️ Пред. месяц",
        'calendar_next': "След. месяц ➡️",
        'calendar_select_all': "Выбрать все",
        'calendar_title': "📅 **Выбор дат для размещения**",
        'calendar_selected_dates': "✅ Выбрано дат: {count}",
        'calendar_weekdays_note': "Пн Вт Ср Чт Пт Сб Вс",

        'time_selection_title': "🕐 **Выбор времени**",
        'time_tz_info': "Ваш часовой пояс: {timezone}",
        'time_slots_limit': "Лимит слотов: {slots}",
        'time_selected_slots': "Выбрано: {count} / {slots}",
        'time_ask_custom': "Введите время в формате ЧЧ:ММ (напр. 14:30):",
        'time_invalid_format': "❌ Неверный формат времени. Попробуйте снова.",
        'time_saved': "✅ Время сохранено!",

        'my_tasks_title': "📋 **Мои задачи** ({count} шт.)",
        'my_tasks_empty': "У вас пока нет созданных задач.",
        'task_actions_title': "🛠️ **Управление задачей** #{task_id}",
        'task_edit_btn': "📝 Редактировать",
        'task_view_btn': "👀 Предпросмотр",
        'task_delete_confirm': "Вы уверены, что хотите удалить задачу **{name}** (#{id})?",
        'task_delete_success': "🗑️ Задача **{name}** (#{id}) удалена.",

        'task_channels_title': "📢 **Выбор каналов для размещения**",
        'channel_not_added': "❌ Канал не найден в вашем списке. Добавьте его через '🧩 Площадки'.",
        'channel_removed': "🗑️ Канал удален из задания.",
        'channel_added': "✅ Канал добавлен к заданию.",
        'channel_is_active_info': "Канал активен",
        'channel_no_channels': "У вас пока нет добавленных каналов.",
        'channel_add_btn': "➕ Добавить канал",
        'channel_remove_btn': "🗑️ Удалить площадку",
        'channel_back_btn': "⬅️ К списку каналов",
        'channel_actions_title': "🛠️ **Управление каналом**",
        'channel_ask_username': "🔗 Введите username канала (напр. @channel_username). Бот должен быть там админом с правом публикации.",
        'channel_username_invalid': "❌ Неверный формат. Пожалуйста, введите username канала, начиная с @ или без.",
        'channel_add_error': "❌ Ошибка при добавлении канала. Убедитесь, что бот является администратором с правами публикации.",
        'channel_add_success': "✅ Канал **{title}** успешно добавлен!",
        'channel_remove_confirm': "Вы уверены, что хотите удалить канал **{title}** из списка ваших площадок?",
        'channel_remove_success': "🗑️ Канал **{title}** удален из ваших площадок.",


        'my_channels_title': "**🧩 Мои площадки**",
        'my_channels_footer': "**Инструкция:**\n1. Добавьте бота в канал или чат с правами админа.\n2. Нажмите на канал для удаления.",
        'my_channels_empty': "❌ У вас пока нет добавленных каналов.",

        'post_type_menu': "📤 **Выбор типа поста**",
        'post_type_from_bot': "От бота (Копирование)",
        'post_type_repost': "Репост (Пересылка)",

        'tariff_title': "💳 **Ваш тариф**",
        'tariff_current_status': "Ваш текущий тариф: **{name}**",
        'tariff_tasks_limit': "Лимит задач: **{current}/{limit}**",
        'tariff_upgrade_prompt': "Вы можете обновить свой тариф:",
        'tariff_details_template': "✅ Лимит задач: **{task_limit}**\n✅ Лимит площадок: **{channel_limit}**",
        'tariff_buy_btn': "Купить",
        'tariff_unlimited': "Безлимитно",
        'reports_title': "☑️ **Отчёты**",

        'boss_menu_title': "😎 **Панель Boss**",
        'boss_mailing_btn': "✉️ Рассылки",
        'boss_signature_btn': "🌵 Подпись (Free)",
        'boss_stats_btn': "📊 Статистика",
        'boss_users_btn': "👥 Пользователи",
        'boss_limits_btn': "🚨 Лимиты",
        'boss_tariffs_btn': "💳 Тарифы",
        'boss_ban_btn': "🚫 Бан",
        'boss_money_btn': "💰 Деньги",
        'boss_logs_btn': "📑 Логи",

        'free_dates_title': "ℹ️ **Свободные даты**",
        'free_dates_info': "Здесь показаны ваши ближайшие запланированные публикации. 'Свободными' считаются все даты и время, *не* указанные ниже.",
        'free_dates_empty': "У вас нет запланированных публикаций. Все даты свободны.",
        'free_dates_list_item': "• **{local_time}** - *{task_name}* (в @{channel_username})",

        # --- НОВЫЕ ЛОКАЛИЗАЦИИ BOSS ПАНЕЛИ ---
        'boss_no_access': "⛔️ У вас нет доступа к этой панели",
        'boss_quick_stats': "📊 Быстрая статистика:",
        'boss_total_users': "👥 Всего пользователей: {total_users}",
        'boss_active_users': "✅ Активных: {active_users}",
        'boss_active_tasks': "📝 Активных задач: {tasks_active}",
        'boss_mailing_constructor': "📣 **Конструктор рассылки**\n\nОтправьте сообщение, которое хотите разослать всем пользователям бота.\n(Можно отправить текст, фото, видео и т.д.)",
        'boss_back_btn': "⬅️ Назад",
        'boss_mailing_saved': "✅ Сообщение сохранено!\n\nХотите исключить каких-то пользователей из рассылки?\nОтправьте их username или ID через запятую (например: @user1, 12345, @user2)\nИли нажмите 'Пропустить' для отправки всем.",
        'boss_mailing_skip_btn': "⏭️ Пропустить",
        'boss_mailing_confirm_title': "📊 **Подтверждение рассылки**",
        'boss_mailing_recipients': "👥 Получателей: {total_recipients}",
        'boss_mailing_excluded': "🚫 Исключено: {excluded_count}",
        'boss_mailing_confirm_prompt': "Подтвердите отправку рассылки:",
        'boss_mailing_send_btn': "✅ Отправить",
        'boss_mailing_cancel_btn': "❌ Отменить",
        'boss_mailing_started': "Рассылка начата...",
        'boss_mailing_sending': "📤 Отправка рассылки...\n{sent} отправлено, {failed} ошибок",
        'boss_mailing_sending_initial': "📤 Отправка рассылки...\n0 / ?",
        'boss_mailing_completed_title': "✅ **Рассылка завершена!**",
        'boss_mailing_sent_count': "📨 Отправлено: {sent}",
        'boss_mailing_failed_count': "❌ Ошибок: {failed}",
        'boss_back_to_boss': "⬅️ Назад в Boss",
        'boss_signature_title': "🌵 **Подпись для FREE тарифа**",
        'boss_signature_info': "Эта подпись будет добавляться к постам пользователей с тарифом FREE.",
        'boss_signature_current': "📝 Текущая подпись:\n{current_text}\n\nОтправьте новый текст подписи или нажмите кнопки ниже:",
        'boss_signature_not_set': "Не установлена",
        'boss_signature_delete_btn': "🗑️ Удалить подпись",
        'boss_signature_too_long': "❌ Подпись слишком длинная (макс 200 символов)",
        'boss_signature_updated': "✅ Подпись обновлена!\n\n📝 Новая подпись:\n{signature}",
        'boss_signature_deleted': "✅ Подпись удалена!",
        'boss_users_title': "👥 **Последние 100 пользователей**",
        'boss_users_no_username': "без username",
        'boss_users_total_shown': "\n📊 Всего показано: {count}",
        'boss_stats_loading': "Загрузка статистики...",
        'boss_stats_title': "📊 **Статистика бота**",
        'boss_stats_total_users': "👥 Всего пользователей: {total_users}",
        'boss_stats_active_users': "✅ Активных пользователей: {active_users}",
        'boss_stats_tasks_today': "📝 Задач создано сегодня: {tasks_today}",
        'boss_stats_tasks_active': "🔄 Задач активно: {tasks_active}",
        'boss_stats_tasks_completed': "✔️ Задач выполнено: {tasks_completed}",
        'boss_stats_tasks_total': "📦 Задач всего в базе: {tasks_total}",
        'boss_stats_users_30d': "📈 Прирост за 30 дней: +{users_30d}",
        'boss_stats_users_60d': "📈 Прирост за 60 дней: +{users_60d}",
        'boss_stats_db_size': "💾 Размер базы данных: {db_size}",
        'boss_stats_db_warning': "\n\n⚠️ **ВНИМАНИЕ**: Размер базы превышает 100MB!",
        'boss_stats_refresh': "🔄 Обновить",
        'boss_money_title': "💰 **Финансовая статистика**",
        'boss_money_tariff_title': "📊 Пользователи по тарифам:",
        'boss_money_tariff_item': "• {name}: {count} чел. ({price}⭐ каждый)",
        'boss_money_estimated_revenue': "\n💵 Ориентировочный доход: {revenue}⭐",
        'boss_money_note': "\n⚠️ Примечание: Это ориентировочный расчет.\nРеальная статистика платежей отслеживается через Telegram Payments.",
        'boss_logs_title': "📝 **Критические ошибки**",
        'boss_logs_no_errors': "✅ Критических ошибок не обнаружено.",
        'boss_logs_info': "\n\nℹ️ Логи записываются в стандартный вывод приложения.\nДля просмотра полных логов используйте систему мониторинга хостинга.",

        # --- НОВЫЕ ЛОКАЛИЗАЦИИ BOSS БАНА ---
        'boss_ban_start_msg': "🚫 **Бан пользователя**\n\nОтправьте ID или @username пользователя, которого хотите забанить (или разбанить).",
        'boss_ban_user_not_found': "❌ Пользователь не найден. Попробуйте снова (ID или @username):",
        'boss_action_ban': "забанить",
        'boss_action_unban': "РАЗБАНИТЬ",
        'boss_status_active': "Активен",
        'boss_status_banned': "Забанен",
        'boss_ban_confirm_title': "**Подтверждение**",
        'boss_ban_user_label': "Пользователь:",
        'boss_ban_id_label': "ID:",
        'boss_ban_status_label': "Текущий статус:",
        'boss_ban_confirm_prompt': "Вы уверены, что хотите **{action_text}** этого пользователя?",
        'boss_confirm_yes_prefix': "✅ Да, ",
        'boss_confirm_cancel_btn': "❌ Нет, отмена",
        'boss_ban_session_error': "❌ Ошибка: ID пользователя не найден в сессии. Начните заново.",
        'boss_ban_success': "🚫 Пользователь @{target_username} (ID: {target_id}) **забанен**. Все его активные задачи отменены.",
        'boss_unban_success': "✅ Пользователь @{target_username} (ID: {target_id}) **разбанен**.",


        'task_activated_title': "✅ Задача #{task_id} успешно активирована!",
        'task_activated_jobs_count': "Создано публикаций: {job_count}",
        'task_activated_schedule_info': "Публикации будут выполнены согласно расписанию",
        'task_advertiser_notify': "📢 Вас указали рекламодателем в задаче \"{task_name}\". Вы будете получать уведомления о публикациях.",

        'payment_success_template': "✅ Оплата прошла успешно!\n\nТариф **{tariff_name}** активирован.",
        'error_notify_user': "❌ Не удалось уведомить пользователя {user_id} о добавлении канала. Возможно, бот заблокирован.",
        'error_invoice_creation': "❌ Не удалось создать счет на оплату. Попробуйте позже.",
        'error_tariff_not_found': "❌ Ошибка: Тариф не найден.",
        'error_tariff_cannot_buy': "❌ Этот тариф нельзя купить.",
        'invoice_title_template': "Оплата тарифа '{tariff_name}'",
        'invoice_description_template': "Доступ к лимитам: {tasks} задач, {time_slots} T, {date_slots} D",
        'precheckout_error': "Что-то пошло не так...",

        'task_message_current_prompt': "Ваше текущее сообщение для публикации:\n\n(Чтобы изменить, просто отправьте новое)",
        'task_delete_message_btn': "🗑️ Удалить это сообщение",
        'task_message_display_error': "❌ Не удалось отобразить сохраненное сообщение (возможно, оно было удалено).",
        'task_message_deleted_alert': "Сообщение удалено!",
        'alert_pin_notify_status': "🔔 Пуш: {status}",
        'alert_report_status': "📊 Отчёт: {status}",
        'alert_post_type_status': "📤 Тип поста: {status}",
        'calendar_header_dates': "📅 {month_year_str}: {dates_str}\n",
        'calendar_header_weekdays': "📅 Дата: {weekdays_str}\n",
        'calendar_info_weekdays': "*При выборе дней недели расписание будет повторяться каждую неделю\n",
        'calendar_info_limit_slots': "*Не более {max_time_slots} слотов времени для тарифа {tariff_name}\n\n",
        'calendar_date_limit_alert': "❌ Лимит тарифа ({limits['name']}): не более {max_dates} дат",
        'calendar_weekdays_short': "Пн,Вт,Ср,Чт,Пт,Сб,Вс",
        'free_dates_header': "📅 **Свободные даты (без постов):**\n{free_dates_str}\n",
        'free_dates_none_60d': "Нет полностью свободных дат в ближайшие 60 дней.",
        'free_dates_schedule_header_30d': "📅 **Ваши авто-постинг задачи на 30 дней:**\n",
        'free_dates_schedule_empty_30d': "У вас нет запланированных публикаций на 30 дней.",
        'error_task_id_not_found': "Ошибка: ID задачи не найден.",
        'error_task_not_found_db': "Ошибка: Задача {task_id} не найдена в БД.",
        'status_count_suffix': "шт.",
        'status_days_suffix': "дн.",
        'status_hours_suffix': "ч",
        'status_hours_suffix_short': "h",
        'status_dates_count': "✅ {count} {suffix}",
        'status_weekdays_count': "✅ {count} {suffix}",
        'status_times_count': "✅ {count} {suffix}",
        'status_pin_duration': "✅ {duration}{suffix}",
        'status_delete_duration': "✅ {duration}{suffix}",
        'status_advertiser_id': "✅ ID: {advertiser_user_id}",
        'header_channels': "📢 Каналы: ",
        'header_message': "📝 Сообщение: ",
        'header_date': "📅 Дата: ",
        'header_weekdays': "📅 Дата: ",
        'header_time': "🕐 Время: ",
        'header_pin': "📌 Закреп: ",
        'header_autodelete': "🗑️ Автоудаление: ",
        'header_post_type': "📤 Тип поста: ",
        'header_pin_notify': "🔔 Пуш: ",
        'header_report': "📊 Отчёт: ",
        'header_advertiser': "🔗 Рекламодатель: ",

        'limit_error_tasks': "❌ Достигнут лимит задач ({current}/{max}) для тарифа {tariff}.\nУдалите старые задачи или обновите тариф.",
        'limit_error_channels': "❌ Достигнут лимит каналов ({current}/{max}) для тарифа {tariff}.\nУдалите старые каналы или обновите тариф.",
        'limit_error_dates': "❌ Достигнут лимит дат ({current}/{max}) для тарифа {tariff}.",
        'limit_error_times': "❌ Достигнут лимит слотов времени ({current}/{max}) для тарифа {tariff}.",
        'limit_error_weekdays': "❌ Достигнут лимит дней недели ({current}/{max}) для тарифа {tariff}.",

        'my_tasks_header': "📋 **Мои задачи** (всего: {count})\n\n{list_text}\n\n**Мини-инструкция:**\n📊 Статусы задач:\n🟢 Активно - выполняется\n🟡 Завершается - ожидает автоудаления\n🔴 Неактивно - остановлено",
        'my_tasks_item_template': "{icon} #{id} • {name} • {status_text}",
        'status_text_active': "Активно",
        'status_text_finishing': "Завершается",
        'status_text_inactive': "Неактивно",
        'task_btn_template': "{icon} #{id} • {name}",
        'task_tariff_info': "⭐ Тариф: {name}. Использовано: {current}/{max}",
        'task_status_label': "Состояние: ",
        'task_btn_deactivate': "🛑 ОТКЛЮЧИТЬ ЗАДАЧУ",
        'task_deactivated_success': "🛑 Задача остановлена. Все будущие публикации отменены.",
        'report_message': "📊 **Отчет о публикации**\n\n✅ Пост успешно опубликован в канале: {channel}\n📌 Закрепление: {pin}\n🗑 Автоудаление: {delete}\n\n🔗 Ссылка (если доступна): {link}",
        'time_current_info': "Ваше текущее время: {current_time}",
        'name_not_set': "Название не задано",

        'task_activating_spinner': "Активация задачи...",
        'task_not_found_error': "❌ Ошибка: задача не найдена.",
        'task_validation_header': "❌ Невозможно активировать задачу:",
        'task_error_no_message': "• Не задано сообщение для публикации",
        'task_error_no_channels': "• Не выбраны каналы для публикации",
        'task_error_no_schedule': "• Не задано расписание (даты и/или время)",
        'task_job_creation_error': "❌ Ошибка при создании заданий публикации: {error}",

        'channel_not_found': '❌ Канал не найден или неактивен.',
        'post_published': '📢 Опубликован пост в канале.',
    },
    'en': {
        'welcome_lang': """🤖 Welcome to XSponsorBot!
I help automate promotional publications in Telegram channels.
You can create tasks, select channels for placement, configure publication time, pinning, auto-deletion, and reports.
My goal is to make your collaboration with advertisers as efficient and convenient as possible.
Let's get started! Please select your language:""",
        'select_timezone': "Please select your timezone:",
        'main_menu': "📋 **Main Menu**\n\nSelect an action:",
        'task_constructor_title': "🎯 Task Constructor",
        'task_default_name': " (Name not set)",
        'task_ask_name': "📝 Enter a name for this task (e.g. 'Coffee Shop Promo'):",
        'task_ask_message': "📝 Send or forward the message you want to publish to the bot.\n(This can be text, photo, video, etc.)",
        'task_ask_advertiser': "🔗 Enter the advertiser's username (e.g. @username or user123):",
        'task_advertiser_saved': "✅ Advertiser saved!",
        'task_advertiser_not_found': "❌ User with this username not found...",
        'status_not_selected': "❌ Not selected",
        'status_yes': "✅ Yes",
        'status_no': "❌ No",
        'calendar_entire_month': "Entire month",
        'calendar_reset': "Reset",
        'time_custom': "🕐 Custom time",
        'time_clear': "Clear",

        # --- Keyboard keys ---
        'nav_new_task_btn': "🚀 ➕ New Task",
        'nav_my_tasks_btn': "📋 My Tasks",
        'nav_channels_btn': "🧩 Platforms",
        'nav_free_dates_btn': "ℹ️ Free Dates",
        'nav_tariff_btn': "💳 Tariff",
        'nav_boss_btn': "😎 Boss",
        'nav_language_btn': "🌐 Change Language",
        'nav_timezone_btn': "🕰️ Change Timezone",
        'nav_reports_btn': "☑️ Reports",
        'keyboard_main_menu_title': "⌨️ Main Menu:",
        'reply_keyboard_prompt': "Choose an action from the menu:",
        'task_set_name_btn': "📝 Task Name",
        'task_select_channels_btn': "📢 Channels",
        'task_set_message_btn': "📝 Message",
        'task_select_calendar_btn': "📅 Calendar",
        'task_select_time_btn': "🕐 Time",
        'task_set_pin_btn': "📌 Pin",
        'task_set_pin_notify_btn': "📌 with Push",
        'task_set_delete_btn': "🧹 Auto-delete",
        'task_set_report_btn': "📊 Report",
        'task_set_advertiser_btn': "🔗 Advertiser",
        'task_set_post_type_btn': "📤 Post Type",
        'task_delete_btn': "🗑️ Delete Task",
        'back_to_main_menu_btn': "⬅️ Back (to Main Menu)",
        'task_activate_btn': "✅ ACTIVATE TASK",
        'back_btn': "⬅️ Back",
        'home_main_menu_btn': "🏠 Main Menu",
        'duration_12h': "12h",
        'duration_24h': "24h",
        'duration_48h': "48h",
        'duration_3d': "3d",
        'duration_7d': "7d",
        'duration_no': "❌ No",
        'duration_ask_pin': "📌 Select pin duration:",
        'duration_ask_delete': "🧹 Select auto-delete duration:",

        # --- Добавленные локализации ---
        'status_set': "✅ Set",
        'status_not_set': "❌ Not set",
        'status_from_bot': "From bot's name",
        'status_repost': "Repost from advertiser",
        'error_generic': "❌ An error occurred. Please try again.",
        'task_message_saved': "✅ Message for publication saved!",
        'task_name_saved': "✅ Task name saved!",

        'calendar_prev': "⬅️ Prev. Month",
        'calendar_next': "Next Month ➡️",
        'calendar_select_all': "Select all",
        'calendar_title': "📅 **Select Dates for Placement**",
        'calendar_selected_dates': "✅ Selected dates: {count}",
        'calendar_weekdays_note': "Mo Tu We Th Fr Sa Su",

        'time_selection_title': "🕐 **Time Selection**",
        'time_tz_info': "Your timezone: {timezone}",
        'time_slots_limit': "Slot limit: {slots}",
        'time_selected_slots': "Selected: {count} / {slots}",
        'time_ask_custom': "Enter time in HH:MM format (e.g. 14:30):",
        'time_invalid_format': "❌ Invalid time format. Try again.",
        'time_saved': "✅ Time saved!",

        'my_tasks_title': "📋 **My Tasks** ({count} items)",
        'my_tasks_empty': "You don't have any created tasks yet.",
        'task_actions_title': "🛠️ **Task Management** #{task_id}",
        'task_edit_btn': "📝 Edit",
        'task_view_btn': "👀 Preview",
        'task_delete_confirm': "Are you sure you want to delete task **{name}** (#{id})?",
        'task_delete_success': "🗑️ Task **{name}** (#{id}) deleted.",

        'task_channels_title': "📢 **Select channels for placement**",
        'channel_not_added': "❌ Channel not found in your list. Add it via '🧩 Platforms'.",
        'channel_removed': "🗑️ Channel removed from task.",
        'channel_added': "✅ Channel added to task.",
        'channel_is_active_info': "Channel is active",
        'channel_no_channels': "You don't have any added channels yet.",
        'channel_add_btn': "➕ Add channel",
        'channel_remove_btn': "🗑️ Remove platform",
        'channel_back_btn': "⬅️ Back to channel list",
        'channel_actions_title': "🛠️ **Channel Management**",
        'channel_ask_username': "🔗 Enter channel username (e.g. @channel_username). The bot must be an admin there with publishing rights.",
        'channel_username_invalid': "❌ Invalid format. Please enter the channel username, starting with @ or without.",
        'channel_add_error': "❌ Error adding channel. Make sure the bot is an administrator with publishing rights.",
        'channel_add_success': "✅ Channel **{title}** successfully added!",
        'channel_remove_confirm': "Are you sure you want to remove channel **{title}** from your platform list?",
        'channel_remove_success': "🗑️ Channel **{title}** removed from your platforms.",

        'my_channels_title': "**🧩 My Platforms**",
        'my_channels_footer': "**Instruction:**\n1. Add a channel where the bot has admin rights.\n2. Click on the channel to manage it.",
        'my_channels_empty': "❌ You have not added any channels yet.",

        'post_type_menu': "📤 **Post Type Selection**",
        'post_type_from_bot': "From bot (Copy)",
        'post_type_repost': "Repost (Forward)",

        'tariff_title': "💳 **Your Tariff**",
        'tariff_current_status': "Your current tariff: **{name}**",
        'tariff_tasks_limit': "Task limit: **{current}/{limit}**",
        'tariff_upgrade_prompt': "You can upgrade your tariff:",
        'tariff_details_template': "✅ Task limit: **{task_limit}**\n✅ Platform limit: **{channel_limit}**",
        'tariff_buy_btn': "Buy",
        'tariff_unlimited': "Unlimited",
        'reports_title': "☑️ **Reports**",

        'boss_menu_title': "😎 **Boss Panel**",
        'boss_mailing_btn': "✉️ Mailings",
        'boss_signature_btn': "🌵 Signature (Free)",
        'boss_stats_btn': "📊 Statistics",
        'boss_users_btn': "👥 Users",
        'boss_limits_btn': "🚨 Limits",
        'boss_tariffs_btn': "💳 Tariffs",
        'boss_ban_btn': "🚫 Ban",
        'boss_money_btn': "💰 Money",
        'boss_logs_btn': "📑 Logs",

        'free_dates_title': "ℹ️ **Free Dates**",
        'free_dates_info': "This shows your nearest planned publications. 'Free' refers to all dates and times *not* listed below.",
        'free_dates_empty': "You have no planned publications. All dates are free.",
        'free_dates_list_item': "• **{local_time}** - *{task_name}* (in @{channel_username})",

        # --- NEW BOSS PANEL LOCALIZATIONS ---
        'boss_no_access': "⛔️ You do not have access to this panel",
        'boss_quick_stats': "📊 Quick Stats:",
        'boss_total_users': "👥 Total users: {total_users}",
        'boss_active_users': "✅ Active: {active_users}",
        'boss_active_tasks': "📝 Active tasks: {tasks_active}",
        'boss_mailing_constructor': "📣 **Mailing Constructor**\n\nSend the message you want to send to all bot users.\n(Can be text, photo, video, etc.)",
        'boss_back_btn': "⬅️ Back",
        'boss_mailing_saved': "✅ Message saved!\n\nDo you want to exclude any users from the mailing?\nSend their username or ID separated by commas (e.g. @user1, 12345, @user2)\nOr press 'Skip' to send to everyone.",
        'boss_mailing_skip_btn': "⏭️ Skip",
        'boss_mailing_confirm_title': "📊 **Mailing Confirmation**",
        'boss_mailing_recipients': "👥 Recipients: {total_recipients}",
        'boss_mailing_excluded': "🚫 Excluded: {excluded_count}",
        'boss_mailing_confirm_prompt': "Confirm mailing submission:",
        'boss_mailing_send_btn': "✅ Send",
        'boss_mailing_cancel_btn': "❌ Cancel",
        'boss_mailing_started': "Mailing started...",
        'boss_mailing_sending': "📤 Sending mailing...\n{sent} sent, {failed} errors",
        'boss_mailing_sending_initial': "📤 Sending mailing...\n0 / ?",
        'boss_mailing_completed_title': "✅ **Mailing completed!**",
        'boss_mailing_sent_count': "📨 Sent: {sent}",
        'boss_mailing_failed_count': "❌ Errors: {failed}",
        'boss_back_to_boss': "⬅️ Back to Boss",
        'boss_signature_title': "🌵 **Signature for FREE tariff**",
        'boss_signature_info': "This signature will be added to posts of users on the FREE tariff.",
        'boss_signature_current': "📝 Current signature:\n{current_text}\n\nSend new signature text or click the buttons below:",
        'boss_signature_not_set': "Not set",
        'boss_signature_delete_btn': "🗑️ Delete Signature",
        'boss_signature_too_long': "❌ Signature is too long (max 200 characters)",
        'boss_signature_updated': "✅ Signature updated!\n\n📝 New signature:\n{signature}",
        'boss_signature_deleted': "✅ Signature deleted!",
        'boss_users_title': "👥 **Last 100 Users**",
        'boss_users_no_username': "no username",
        'boss_users_total_shown': "\n📊 Total shown: {count}",
        'boss_stats_loading': "Loading statistics...",
        'boss_stats_title': "📊 **Bot Statistics**",
        'boss_stats_total_users': "👥 Total users: {total_users}",
        'boss_stats_active_users': "✅ Active users: {active_users}",
        'boss_stats_tasks_today': "📝 Tasks created today: {tasks_today}",
        'boss_stats_tasks_active': "🔄 Active tasks: {tasks_active}",
        'boss_stats_tasks_completed': "✔️ Tasks completed: {tasks_completed}",
        'boss_stats_tasks_total': "📦 Total tasks in database: {tasks_total}",
        'boss_stats_users_30d': "📈 Growth in 30 days: +{users_30d}",
        'boss_stats_users_60d': "📈 Growth in 60 days: +{users_60d}",
        'boss_stats_db_size': "💾 Database size: {db_size}",
        'boss_stats_db_warning': "\n\n⚠️ **WARNING**: Database size exceeds 100MB!",
        'boss_stats_refresh': "🔄 Refresh",
        'boss_money_title': "💰 **Financial Statistics**",
        'boss_money_tariff_title': "📊 Users by tariffs:",
        'boss_money_tariff_item': "• {name}: {count} people ({price}⭐ each)",
        'boss_money_estimated_revenue': "\n💵 Estimated revenue: {revenue}⭐",
        'boss_money_note': "\n⚠️ Note: This is an estimated calculation.\nActual payment statistics are tracked via Telegram Payments.",
        'boss_logs_title': "📝 **Critical Errors**",
        'boss_logs_no_errors': "✅ No critical errors found.",
        'boss_logs_info': "\n\nℹ️ Logs are written to the application's standard output.\nUse your hosting's monitoring system to view full logs.",

        # --- NEW BOSS BAN LOCALIZATIONS ---
        'boss_ban_start_msg': "🚫 **User Ban**\n\nPlease send the ID or @username of the user you want to ban (or unban).",
        'boss_ban_user_not_found': "❌ User not found. Please try again (ID or @username):",
        'boss_action_ban': "ban",
        'boss_action_unban': "UNBAN",
        'boss_status_active': "Active",
        'boss_status_banned': "Banned",
        'boss_ban_confirm_title': "**Confirmation**",
        'boss_ban_user_label': "User:",
        'boss_ban_id_label': "ID:",
        'boss_ban_status_label': "Current Status:",
        'boss_ban_confirm_prompt': "Are you sure you want to **{action_text}** this user?",
        'boss_confirm_yes_prefix': "✅ Yes, ",
        'boss_confirm_cancel_btn': "❌ No, cancel",
        'boss_ban_session_error': "❌ Error: User ID not found in session. Please start over.",
        'boss_ban_success': "🚫 User @{target_username} (ID: {target_id}) has been **banned**. All their active tasks have been cancelled.",
        'boss_unban_success': "✅ User @{target_username} (ID: {target_id}) has been **unbanned**.",

        'task_activated_title': "✅ Task #{task_id} successfully activated!",
        'task_activated_jobs_count': "Publications created: {job_count}",
        'task_activated_schedule_info': "Publications will be executed according to the schedule",
        'task_advertiser_notify': "📢 You have been set as the advertiser for the task \"{task_name}\". You will receive publication notifications.",

        'payment_success_template': "✅ Payment was successful!\n\nTariff **{tariff_name}** activated.",
        'error_notify_user': "❌ Failed to notify user {user_id} about channel addition. The bot might be blocked.",
        'error_invoice_creation': "❌ Failed to create an invoice for payment. Try again later.",
        'error_tariff_not_found': "❌ Error: Tariff not found.",
        'error_tariff_cannot_buy': "❌ This tariff cannot be purchased.",
        'invoice_title_template': "Payment for tariff '{tariff_name}'",
        'invoice_description_template': "Access to limits: {tasks} tasks, {time_slots} T, {date_slots} D",
        'precheckout_error': "Something went wrong...",

        'task_message_current_prompt': "Your current message for publication:\n\n(To change, just send a new one)",
        'task_delete_message_btn': "🗑️ Delete this message",
        'task_message_display_error': "❌ Failed to display the saved message (it might have been deleted).",
        'task_message_deleted_alert': "Message deleted!",
        'alert_pin_notify_status': "🔔 Push: {status}",
        'alert_report_status': "📊 Report: {status}",
        'alert_post_type_status': "📤 Post Type: {status}",
        'calendar_header_dates': "📅 {month_year_str}: {dates_str}\n",
        'calendar_header_weekdays': "📅 Weekdays: {weekdays_str}\n",
        'calendar_info_weekdays': "*If you select weekdays, the schedule will repeat weekly\n",
        'calendar_info_limit_slots': "*No more than {max_time_slots} time slots for tariff {tariff_name}\n\n",
        'calendar_date_limit_alert': "❌ Tariff limit ({limits['name']}): no more than {max_dates} dates",
        'calendar_weekdays_short': "Mo,Tu,We,Th,Fr,Sa,Su",
        'free_dates_header': "📅 **Free dates (no posts):**\n{free_dates_str}\n",
        'free_dates_none_60d': "No completely free dates in the next 60 days.",
        'free_dates_schedule_header_30d': "📅 **Your auto-posting tasks for 30 days:**\n",
        'free_dates_schedule_empty_30d': "You have no scheduled publications for 30 days.",
        'error_task_id_not_found': "Error: Task ID not found.",
        'error_task_not_found_db': "Error: Task {task_id} not found in DB.",
        'status_count_suffix': "items",
        'status_days_suffix': "days",
        'status_hours_suffix': "h",
        'status_hours_suffix_short': "h",
        'status_dates_count': "✅ {count} {suffix}",
        'status_weekdays_count': "✅ {count} {suffix}",
        'status_times_count': "✅ {count} {suffix}",
        'status_pin_duration': "✅ {duration}{suffix}",
        'status_delete_duration': "✅ {duration}{suffix}",
        'status_advertiser_id': "✅ ID: {advertiser_user_id}",
        'header_channels': "📢 Channels: ",
        'header_message': "📝 Message: ",
        'header_date': "📅 Date: ",
        'header_weekdays': "📅 Weekdays: ",
        'header_time': "🕐 Time: ",
        'header_pin': "📌 Pin: ",
        'header_autodelete': "🗑️ Auto-delete: ",
        'header_post_type': "📤 Post Type: ",
        'header_pin_notify': "🔔 Push: ",
        'header_report': "📊 Report: ",
        'header_advertiser': "🔗 Advertiser: ",

        'limit_error_tasks': "❌ Task limit reached ({current}/{max}) for tariff {tariff}.\nPlease delete old tasks or upgrade your tariff.",
        'limit_error_channels': "❌ Channel limit reached ({current}/{max}) for tariff {tariff}.\nPlease remove old channels or upgrade your tariff.",
        'limit_error_dates': "❌ Date limit reached ({current}/{max}) for tariff {tariff}.",
        'limit_error_times': "❌ Time slot limit reached ({current}/{max}) for tariff {tariff}.",
        'limit_error_weekdays': "❌ Weekday limit reached ({current}/{max}) for tariff {tariff}.",

        'my_tasks_header': "📋 **My Tasks** (total: {count})\n\n{list_text}\n\n**Legend:**\n📊 Task Statuses:\n🟢 Active - running\n🟡 Finishing - awaiting auto-delete\n🔴 Inactive - stopped",
        'my_tasks_item_template': "{icon} #{id} • {name} • {status_text}",
        'status_text_active': "Active",
        'status_text_finishing': "Finishing",
        'status_text_inactive': "Inactive",
        'task_btn_template': "{icon} #{id} • {name}",
        'task_tariff_info': "⭐ Tariff: {name}. Used: {current}/{max}",
        'task_status_label': "Status: ",
        'task_btn_deactivate': "🛑 STOP TASK",
        'task_deactivated_success': "🛑 Task stopped. All future posts cancelled.",
        'report_message': "📊 **Publication Report**\n\n✅ Post successfully published in channel: {channel}\n📌 Pin: {pin}\n🗑 Auto-delete: {delete}\n\n🔗 Link (if available): {link}",
        'time_current_info': "Your current time: {current_time}",
        'name_not_set': "No title provided",

        'task_activating_spinner': "Activating task...",
        'task_not_found_error': "❌ Error: task not found.",
        'task_validation_header': "❌ Cannot activate task:",
        'task_error_no_message': "• Publication message not set",
        'task_error_no_channels': "• Channels not selected",
        'task_error_no_schedule': "• Schedule not set (dates and/or time)",
        'task_job_creation_error': "❌ Error creating publication jobs: {error}",
        'channel_not_found': '❌ Channel not found or inactive.',
        'post_published': '📢 Post published in the channel.',
    },
    'es': {
        # ... (existing Spanish localizations) ...
        'welcome_lang': """🤖 ¡Bienvenido a XSponsorBot!
Ayudo a automatizar las publicaciones promocionales en los canales de Telegram.
Puedes crear tareas, seleccionar canales para la colocación, configurar la hora de publicación, el anclaje, la eliminación automática y los informes.
Mi objetivo es hacer que tu colaboración con los anunciantes sea lo más eficiente y cómoda posible.
¡Empecemos! Por favor, selecciona tu idioma:""",
        'select_timezone': "Por favor, selecciona tu zona horaria:",
        'main_menu': "📋 **Menú Principal**\n\nSelecciona una acción:",
        'task_constructor_title': "🎯 Constructor de Tareas",
        'task_default_name': " (Nombre no establecido)",
        'task_ask_name': "📝 Introduce un nombre para esta tarea (ej. 'Promo Cafetería'):",
        'task_ask_message': "📝 Envía o reenvía el mensaje que quieres publicar al bot.\n(Puede ser texto, foto, video, etc.)",
        'task_ask_advertiser': "🔗 Introduce el nombre de usuario del anunciante (ej. @username o user123):",
        'task_advertiser_saved': "✅ Anunciante guardado!",
        'task_advertiser_not_found': "❌ Usuario con este nombre no encontrado...",
        'status_not_selected': "❌ No seleccionado",
        'status_yes': "✅ Sí",
        'status_no': "❌ No",
        'calendar_entire_month': "Mes completo",
        'calendar_reset': "Restablecer",
        'time_custom': "🕐 Hora personalizada",
        'time_clear': "Borrar",

        # --- Claves del teclado ---
        'nav_new_task_btn': "🚀 ➕ Nueva Tarea",
        'nav_my_tasks_btn': "📋 Mis Tareas",
        'nav_channels_btn': "🧩 Plataformas",
        'nav_free_dates_btn': "ℹ️ Fechas Libres",
        'nav_tariff_btn': "💳 Tarifa",
        'nav_boss_btn': "😎 Jefe",
        'nav_language_btn': "🌐 Cambiar Idioma",
        'nav_timezone_btn': "🕰️ Cambiar Zona Horaria",
        'nav_reports_btn': "☑️ Informes",
        'keyboard_main_menu_title': "⌨️ Menú Principal:",
        'reply_keyboard_prompt': "Elige una acción en el teclado:",
        'task_set_name_btn': "📝 Nombre de la Tarea",
        'task_select_channels_btn': "📢 Canales",
        'task_set_message_btn': "📝 Mensaje",
        'task_select_calendar_btn': "📅 Calendario",
        'task_select_time_btn': "🕐 Hora",
        'task_set_pin_btn': "📌 Anclar",
        'task_set_pin_notify_btn': "📌 con Notificación",
        'task_set_delete_btn': "🧹 Eliminación automática",
        'task_set_report_btn': "📊 Informe",
        'task_set_advertiser_btn': "🔗 Anunciante",
        'task_set_post_type_btn': "📤 Tipo de Publicación",
        'task_delete_btn': "🗑️ Eliminar Tarea",
        'back_to_main_menu_btn': "⬅️ Atrás (al Menú Principal)",
        'task_activate_btn': "✅ ACTIVAR TAREA",
        'back_btn': "⬅️ Atrás",
        'home_main_menu_btn': "🏠 Menú Principal",
        'duration_12h': "12h",
        'duration_24h': "24h",
        'duration_48h': "48h",
        'duration_3d': "3d",
        'duration_7d': "7d",
        'duration_no': "❌ No",
        'duration_ask_pin': "📌 Selecciona la duración del anclaje:",
        'duration_ask_delete': "🧹 Selecciona la duración de la eliminación automática:",

        # --- Добавленные локализации ---
        'status_set': "✅ Establecido",
        'status_not_set': "❌ No establecido",
        'status_from_bot': "Desde el nombre del bot",
        'status_repost': "Repost del anunciante",
        'error_generic': "❌ Ha ocurrido un error. Inténtalo de nuevo.",
        'task_message_saved': "✅ Mensaje para publicación guardado!",
        'task_name_saved': "✅ Nombre de la tarea guardado!",

        'calendar_prev': "⬅️ Mes Ant.",
        'calendar_next': "Mes Sig. ➡️",
        'calendar_select_all': "Seleccionar todo",
        'calendar_title': "📅 **Seleccionar Fechas de Colocación**",
        'calendar_selected_dates': "✅ Fechas seleccionadas: {count}",
        'calendar_weekdays_note': "Lu Ma Mi Ju Vi Sá Do",

        'time_selection_title': "🕐 **Selección de Hora**",
        'time_tz_info': "Tu zona horaria: {timezone}",
        'time_slots_limit': "Límite de espacios: {slots}",
        'time_selected_slots': "Seleccionado: {count} / {slots}",
        'time_ask_custom': "Introduce la hora en formato HH:MM (ej. 14:30):",
        'time_invalid_format': "❌ Formato de hora inválido. Inténtalo de nuevo.",
        'time_saved': "✅ Hora guardada!",

        'my_tasks_title': "📋 **Mis Tareas** ({count} elementos)",
        'my_tasks_empty': "Aún no tienes tareas creadas.",
        'task_actions_title': "🛠️ **Gestión de Tarea** #{task_id}",
        'task_edit_btn': "📝 Editar",
        'task_view_btn': "👀 Vista previa",
        'task_delete_confirm': "¿Estás seguro de que quieres eliminar la tarea **{name}** (#{id})?",
        'task_delete_success': "🗑️ Tarea **{name}** (#{id}) eliminada.",

        'task_channels_title': "📢 **Seleccionar canales para la colocación**",
        'channel_not_added': "❌ Canal no encontrado en tu lista. Añádelo a través de '🧩 Plataformas'.",
        'channel_removed': "🗑️ Canal eliminado de la tarea.",
        'channel_added': "✅ Canal añadido a la tarea.",
        'channel_is_active_info': "Canal activo",
        'channel_no_channels': "Aún no tienes canales añadidos.",
        'channel_add_btn': "➕ Añadir canal",
        'channel_remove_btn': "🗑️ Eliminar plataforma",
        'channel_back_btn': "⬅️ Volver a la lista de canales",
        'channel_actions_title': "🛠️ **Gestión del Canal**",
        'channel_ask_username': "🔗 Introduce el username del canal (ej. @channel_username). El bot debe ser administrador allí con derecho a publicar.",
        'channel_username_invalid': "❌ Formato inválido. Por favor, introduce el username del canal, comenzando con @ o sin él.",
        'channel_add_error': "❌ Error al añadir el canal. Asegúrate de que el bot sea administrador con derechos de publicación.",
        'channel_add_success': "✅ Canal **{title}** añadido con éxito!",
        'channel_remove_confirm': "¿Estás seguro de que quieres eliminar el canal **{title}** de tu lista de plataformas?",
        'channel_remove_success': "🗑️ Canal **{title}** eliminado de tus plataformas.",

        'my_channels_title': "**🧩 Mis Plataformas**",
        'my_channels_footer': "**Instrucción:**\n1. Añade un canal donde el bot tenga derechos de administrador.\n2. Haz clic en el canal para gestionarlo.",
        'my_channels_empty': "❌ Aún no tienes canales añadidos.",

        'post_type_menu': "📤 **Selección de Tipo de Publicación**",
        'post_type_from_bot': "Desde el bot (Copia)",
        'post_type_repost': "Repost (Reenvío)",

        'tariff_title': "💳 **Tu Tarifa**",
        'tariff_current_status': "Tu tarifa actual: **{name}**",
        'tariff_tasks_limit': "Límite de tareas: **{current}/{limit}**",
        'tariff_upgrade_prompt': "Puedes actualizar tu tarifa:",
        'tariff_details_template': "✅ Límite de tareas: **{task_limit}**\n✅ Límite de plataformas: **{channel_limit}**",
        'tariff_buy_btn': "Comprar",
        'tariff_unlimited': "Ilimitado",
        'reports_title': "☑️ **Informes**",

        'boss_menu_title': "😎 **Panel Jefe**",
        'boss_mailing_btn': "✉️ Envíos Masivos",
        'boss_signature_btn': "🌵 Firma (Gratis)",
        'boss_stats_btn': "📊 Estadísticas",
        'boss_users_btn': "👥 Usuarios",
        'boss_limits_btn': "🚨 Límites",
        'boss_tariffs_btn': "💳 Tarifas",
        'boss_ban_btn': "🚫 Bloquear",
        'boss_money_btn': "💰 Dinero",
        'boss_logs_btn': "📑 Registros",

        'free_dates_title': "ℹ️ **Fechas Libres**",
        'free_dates_info': "Aquí se muestran tus próximas publicaciones programadas. 'Libres' son todas las fechas y horas *no* listadas a continuación.",
        'free_dates_empty': "No tienes publicaciones programadas. Todas las fechas están libres.",
        'free_dates_list_item': "• **{local_time}** - *{task_name}* (en @{channel_username})",

        # --- NEW BOSS PANEL LOCALIZATIONS ---
        'boss_no_access': "⛔️ No tienes acceso a este panel",
        'boss_quick_stats': "📊 Estadísticas Rápidas:",
        'boss_total_users': "👥 Total de usuarios: {total_users}",
        'boss_active_users': "✅ Activos: {active_users}",
        'boss_active_tasks': "📝 Tareas activas: {tasks_active}",
        'boss_mailing_constructor': "📣 **Constructor de Envío Masivo**\n\nEnvía el mensaje que deseas enviar a todos los usuarios del bot.\n(Puede ser texto, foto, video, etc.)",
        'boss_back_btn': "⬅️ Atrás",
        'boss_mailing_saved': "✅ Mensaje guardado!\n\n¿Quieres excluir a algún usuario del envío?\nEnvía su nombre de usuario o ID separados por comas (ej. @user1, 12345, @user2)\nO haz clic en 'Saltar' para enviar a todos.",
        'boss_mailing_skip_btn': "⏭️ Saltar",
        'boss_mailing_confirm_title': "📊 **Confirmación de Envío Masivo**",
        'boss_mailing_recipients': "👥 Destinatarios: {total_recipients}",
        'boss_mailing_excluded': "🚫 Excluidos: {excluded_count}",
        'boss_mailing_confirm_prompt': "Confirma el envío masivo:",
        'boss_mailing_send_btn': "✅ Enviar",
        'boss_mailing_cancel_btn': "❌ Cancelar",
        'boss_mailing_started': "Envío masivo iniciado...",
        'boss_mailing_sending': "📤 Enviando masivo...\n{sent} enviados, {failed} errores",
        'boss_mailing_sending_initial': "📤 Enviando masivo...\n0 / ?",
        'boss_mailing_completed_title': "✅ **Envío Masivo completado!**",
        'boss_mailing_sent_count': "📨 Enviados: {sent}",
        'boss_mailing_failed_count': "❌ Errores: {failed}",
        'boss_back_to_boss': "⬅️ Volver al Panel Jefe",
        'boss_signature_title': "🌵 **Firma para Tarifa FREE**",
        'boss_signature_info': "Esta firma se añadirá a las publicaciones de los usuarios con tarifa FREE.",
        'boss_signature_current': "📝 Firma actual:\n{current_text}\n\nEnvía el nuevo texto de la firma o haz clic en los botones de abajo:",
        'boss_signature_not_set': "No establecida",
        'boss_signature_delete_btn': "🗑️ Eliminar Firma",
        'boss_signature_too_long': "❌ La firma es demasiado larga (máx 200 caracteres)",
        'boss_signature_updated': "✅ Firma actualizada!\n\n📝 Nueva firma:\n{signature}",
        'boss_signature_deleted': "✅ Firma eliminada!",
        'boss_users_title': "👥 **Últimos 100 Usuarios**",
        'boss_users_no_username': "sin nombre de usuario",
        'boss_users_total_shown': "\n📊 Total mostrado: {count}",
        'boss_stats_loading': "Cargando estadísticas...",
        'boss_stats_title': "📊 **Estadísticas del Bot**",
        'boss_stats_total_users': "👥 Total de usuarios: {total_users}",
        'boss_stats_active_users': "✅ Usuarios activos: {active_users}",
        'boss_stats_tasks_today': "📝 Tareas creadas hoy: {tasks_today}",
        'boss_stats_tasks_active': "🔄 Tareas activas: {tasks_active}",
        'boss_stats_tasks_completed': "✔️ Tareas completadas: {tasks_completed}",
        'boss_stats_tasks_total': "📦 Tareas totales en la base de datos: {tasks_total}",
        'boss_stats_users_30d': "📈 Crecimiento en 30 días: +{users_30d}",
        'boss_stats_users_60d': "📈 Crecimiento en 60 días: +{users_60d}",
        'boss_stats_db_size': "💾 Tamaño de la base de datos: {db_size}",
        'boss_stats_db_warning': "\n\n⚠️ **ADVERTENCIA**: El tamaño de la base de datos supera los 100MB!",
        'boss_stats_refresh': "🔄 Actualizar",
        'boss_money_title': "💰 **Estadísticas Financieras**",
        'boss_money_tariff_title': "📊 Usuarios por tarifas:",
        'boss_money_tariff_item': "• {name}: {count} pers. ({price}⭐ cada uno)",
        'boss_money_estimated_revenue': "\n💵 Ingresos estimados: {revenue}⭐",
        'boss_money_note': "\n⚠️ Nota: Esto es un cálculo estimado.\nLas estadísticas reales de pago se rastrean a través de Telegram Payments.",
        'boss_logs_title': "📝 **Errores Críticos**",
        'boss_logs_no_errors': "✅ No se encontraron errores críticos.",
        'boss_logs_info': "\n\nℹ️ Los registros se escriben en la salida estándar de la aplicación.\nUtiliza el sistema de monitoreo de tu hosting para ver los registros completos.",

        # --- NEW BOSS BAN LOCALIZATIONS ---
        'boss_ban_start_msg': "🚫 **Bloquear Usuario**\n\nEnvía el ID o @username del usuario que deseas bloquear (o desbloquear).",
        'boss_ban_user_not_found': "❌ Usuario no encontrado. Inténtalo de nuevo (ID o @username):",
        'boss_action_ban': "bloquear",
        'boss_action_unban': "DESBLOQUEAR",
        'boss_status_active': "Activo",
        'boss_status_banned': "Bloqueado",
        'boss_ban_confirm_title': "**Confirmación**",
        'boss_ban_user_label': "Usuario:",
        'boss_ban_id_label': "ID:",
        'boss_ban_status_label': "Estado Actual:",
        'boss_ban_confirm_prompt': "¿Estás seguro de que quieres **{action_text}** a este usuario?",
        'boss_confirm_yes_prefix': "✅ Sí, ",
        'boss_confirm_cancel_btn': "❌ No, cancelar",
        'boss_ban_session_error': "❌ Error: ID de usuario no encontrado en la sesión. Por favor, empieza de nuevo.",
        'boss_ban_success': "🚫 El usuario @{target_username} (ID: {target_id}) ha sido **bloqueado**. Todas sus tareas activas han sido canceladas.",
        'boss_unban_success': "✅ El usuario @{target_username} (ID: {target_id}) ha sido **desbloqueado**.",

        'task_activated_title': "✅ Tarea #{task_id} activada con éxito!",
        'task_activated_jobs_count': "Publicaciones creadas: {job_count}",
        'task_activated_schedule_info': "Las publicaciones se ejecutarán según el horario",
        'task_advertiser_notify': "📢 Has sido designado como anunciante de la tarea \"{task_name}\". Recibirás notificaciones de publicación.",

        'payment_success_template': "✅ El pago fue exitoso!\n\nTarifa **{tariff_name}** activada.",
        'error_notify_user': "❌ No se pudo notificar al usuario {user_id} sobre la adición del canal. El bot podría estar bloqueado.",
        'error_invoice_creation': "❌ No se pudo crear una factura para el pago. Inténtalo de nuevo más tarde.",
        'error_tariff_not_found': "❌ Error: Tarifa no encontrada.",
        'error_tariff_cannot_buy': "❌ Esta tarifa no se puede comprar.",
        'invoice_title_template': "Pago de la tarifa '{tariff_name}'",
        'invoice_description_template': "Acceso a límites: {tasks} tareas, {time_slots} T, {date_slots} D",
        'precheckout_error': "Algo salió mal...",

        'task_message_current_prompt': "Tu mensaje actual para publicación:\n\n(Para cambiar, simplemente envía uno nuevo)",
        'task_delete_message_btn': "🗑️ Eliminar este mensaje",
        'task_message_display_error': "❌ No se pudo mostrar el mensaje guardado (podría haber sido eliminado).",
        'task_message_deleted_alert': "¡Mensaje eliminado!",
        'alert_pin_notify_status': "🔔 Notificación: {status}",
        'alert_report_status': "📊 Informe: {status}",
        'alert_post_type_status': "📤 Tipo de post: {status}",
        'calendar_header_dates': "📅 {month_year_str}: {dates_str}\n",
        'calendar_header_weekdays': "📅 Días de la semana: {weekdays_str}\n",
        'calendar_info_weekdays': "*Si seleccionas días de la semana, el horario se repetirá semanalmente\n",
        'calendar_info_limit_slots': "*No más de {max_time_slots} franjas horarias para la tarifa {tariff_name}\n\n",
        'calendar_date_limit_alert': "❌ Límite de tarifa ({limits['name']}): no más de {max_dates} fechas",
        'calendar_weekdays_short': "Lu,Ma,Mi,Ju,Vi,Sá,Do",
        'free_dates_header': "📅 **Fechas libres (sin posts):**\n{free_dates_str}\n",
        'free_dates_none_60d': "No hay fechas completamente libres en los próximos 60 días.",
        'free_dates_schedule_header_30d': "📅 **Tus tareas de auto-publicación para 30 días:**\n",
        'free_dates_schedule_empty_30d': "No tienes publicaciones programadas para 30 días.",
        'error_task_id_not_found': "Error: ID de tarea no encontrado.",
        'error_task_not_found_db': "Error: Tarea {task_id} no encontrada en la BD.",
        'status_count_suffix': "elem.",
        'status_days_suffix': "días",
        'status_hours_suffix': "h",
        'status_hours_suffix_short': "h",
        'status_dates_count': "✅ {count} {suffix}",
        'status_weekdays_count': "✅ {count} {suffix}",
        'status_times_count': "✅ {count} {suffix}",
        'status_pin_duration': "✅ {duration}{suffix}",
        'status_delete_duration': "✅ {duration}{suffix}",
        'status_advertiser_id': "✅ ID: {advertiser_user_id}",
        'header_channels': "📢 Canales: ",
        'header_message': "📝 Mensaje: ",
        'header_date': "📅 Fecha: ",
        'header_weekdays': "📅 Días de la semana: ",
        'header_time': "🕐 Hora: ",
        'header_pin': "📌 Anclar: ",
        'header_autodelete': "🗑️ Auto-eliminar: ",
        'header_post_type': "📤 Tipo de post: ",
        'header_pin_notify': "🔔 Notificación: ",
        'header_report': "📊 Informe: ",
        'header_advertiser': "🔗 Anunciante: ",

        'limit_error_tasks': "❌ Límite de tareas alcanzado ({current}/{max}) para la tarifa {tariff}.\nElimina tareas antiguas o actualiza tu tarifa.",
        'limit_error_channels': "❌ Límite de canales alcanzado ({current}/{max}) para la tarifa {tariff}.\nElimina canales antiguos o actualiza tu tarifa.",
        'limit_error_dates': "❌ Límite de fechas alcanzado ({current}/{max}) para la tarifa {tariff}.",
        'limit_error_times': "❌ Límite de horarios alcanzado ({current}/{max}) para la tarifa {tariff}.",
        'limit_error_weekdays': "❌ Límite de días de la semana alcanzado ({current}/{max}) para la tarifa {tariff}.",

        'my_tasks_header': "📋 **Mis Tareas** (total: {count})\n\n{list_text}\n\n**Mini-Instrucción:**\n📊 Estados de Tarea:\n🟢 Activo - en ejecución\n🟡 Finalizando - esperando auto-eliminación\n🔴 Inactivo - detenido",
        'my_tasks_item_template': "{icon} #{id} • {name} • {status_text}",
        'status_text_active': "Activo",
        'status_text_finishing': "Finalizando",
        'status_text_inactive': "Inactivo",
        'task_btn_template': "{icon} #{id} • {name}",
        'task_tariff_info': "⭐ Tarifa: {name}. Usado: {current}/{max}",
        'task_status_label': "Estado: ",
        'task_btn_deactivate': "🛑 DESACTIVAR TAREA",
        'task_deactivated_success': "🛑 Tarea detenida. Todas las futuras publicaciones han sido canceladas.",
        'report_message': "📊 **Informe de Publicación**\n\n✅ Post publicado exitosamente en el canal: {channel}\n📌 Fijar: {pin}\n🗑 Auto-eliminación: {delete}\n\n🔗 Enlace (si está disponible): {link}",
        'time_current_info': "Su hora actual: {current_time}",
        'name_not_set': "Título no especificado",

        'task_activating_spinner': "Activando tarea...",
        'task_not_found_error': "❌ Error: tarea no encontrada.",
        'task_validation_header': "❌ No se puede activar la tarea:",
        'task_error_no_message': "• Mensaje de publicación no establecido",
        'task_error_no_channels': "• Canales no seleccionados",
        'task_error_no_schedule': "• Horario no establecido (fechas y/o hora)",
        'task_job_creation_error': "❌ Error al crear trabajos de publicación: {error}",
        'channel_not_found': '❌ Canal no encontrado o inactivo.',

        'post_published': '📢 Publicación posteada en el canal.',
    },
    'fr': {
        # ... (existing French localizations) ...
        'welcome_lang': """🤖 Bienvenue sur XSponsorBot!
J'aide à automatiser les publications promotionnelles dans les canaux Telegram.
Vous pouvez créer des tâches, sélectionner des canaux pour le placement, configurer l'heure de publication, l'épinglage, la suppression automatique et les rapports.
Mon objectif est de rendre votre collaboration avec les annonceurs aussi efficace et pratique que possible.
Commençons! Veuillez sélectionner votre langue:""",
        'select_timezone': "Veuillez sélectionner votre fuseau horaire:",
        'main_menu': "📋 **Menu Principal**\n\nSélectionnez une action:",
        'task_constructor_title': "🎯 Constructeur de Tâches",
        'task_default_name': " (Nom non défini)",
        'task_ask_name': "📝 Entrez un nom pour cette tâche (ex. 'Promo Café'):",
        'task_ask_message': "📝 Envoyez ou transférez le message que vous souhaitez publier au bot.\n(Cela peut être du texte, une photo, une vidéo, etc.)",
        'task_ask_advertiser': "🔗 Entrez le nom d'utilisateur de l'annonceur (ex. @username ou user123):",
        'task_advertiser_saved': "✅ Annonceur enregistré!",
        'task_advertiser_not_found': "❌ Utilisateur introuvable. Assurez-vous que l'annonceur a démarré le bot avec /start",
        'status_not_selected': "❌ Non sélectionné",
        'status_yes': "✅ Oui",
        'status_no': "❌ Non",
        'calendar_entire_month': "Mois complet",
        'calendar_reset': "Réinitialiser",
        'time_custom': "🕐 Heure personnalisée",
        'time_clear': "Effacer",

        # --- Clés du clavier ---
        'nav_new_task_btn': "🚀 ➕ Nouvelle Tâche",
        'nav_my_tasks_btn': "📋 Mes Tâches",
        'nav_channels_btn': "🧩 Plateformes",
        'nav_free_dates_btn': "ℹ️ Dates Libres",
        'nav_tariff_btn': "💳 Tarif",
        'nav_boss_btn': "😎 Boss",
        'nav_language_btn': "🌐 Changer Langue",
        'nav_timezone_btn': "🕰️ Changer Fuseau Horaire",
        'nav_reports_btn': "☑️ Rapports",
        'keyboard_main_menu_title': "⌨️ Menu Principal:",
        'reply_keyboard_prompt': "Choisissez une action sur le clavier:",
        'task_set_name_btn': "📝 Nom de la Tâche",
        'task_select_channels_btn': "📢 Canaux",
        'task_set_message_btn': "📝 Message",
        'task_select_calendar_btn': "📅 Calendrier",
        'task_select_time_btn': "🕐 Heure",
        'task_set_pin_btn': "📌 Épingler",
        'task_set_pin_notify_btn': "📌 avec Notification",
        'task_set_delete_btn': "🧹 Suppression auto",
        'task_set_report_btn': "📊 Rapport",
        'task_set_advertiser_btn': "🔗 Annonceur",
        'task_set_post_type_btn': "📤 Type de Publication",
        'task_delete_btn': "🗑️ Supprimer Tâche",
        'back_to_main_menu_btn': "⬅️ Retour (au Menu Principal)",
        'task_activate_btn': "✅ ACTIVER TÂCHE",
        'back_btn': "⬅️ Retour",
        'home_main_menu_btn': "🏠 Menu Principal",
        'duration_12h': "12h",
        'duration_24h': "24h",
        'duration_48h': "48h",
        'duration_3d': "3j",
        'duration_7d': "7j",
        'duration_no': "❌ Non",
        'duration_ask_pin': "📌 Sélectionnez la durée d'épinglage:",
        'duration_ask_delete': "🧹 Sélectionnez la durée de suppression automatique:",

        # --- Добавленные локализации ---
        'status_set': "✅ Défini",
        'status_not_set': "❌ Non défini",
        'status_from_bot': "Au nom du bot",
        'status_repost': "Repost de l'annonceur",
        'error_generic': "❌ Une erreur est survenue. Veuillez réessayer.",
        'task_message_saved': "✅ Message pour publication enregistré!",
        'task_name_saved': "✅ Nom de la tâche enregistré!",

        'calendar_prev': "⬅️ Mois Préc.",
        'calendar_next': "Mois Suiv. ➡️",
        'calendar_select_all': "Tout sélectionner",
        'calendar_title': "📅 **Sélectionner les Dates de Placement**",
        'calendar_selected_dates': "✅ Dates sélectionnées: {count}",
        'calendar_weekdays_note': "Lu Ma Me Je Ve Sa Di",

        'time_selection_title': "🕐 **Sélection de l'Heure**",
        'time_tz_info': "Votre fuseau horaire: {timezone}",
        'time_slots_limit': "Limite de créneaux: {slots}",
        'time_selected_slots': "Sélectionné: {count} / {slots}",
        'time_ask_custom': "Entrez l'heure au format HH:MM (ex. 14:30):",
        'time_invalid_format': "❌ Format d'heure invalide. Réessayez.",
        'time_saved': "✅ Heure enregistrée!",

        'my_tasks_title': "📋 **Mes Tâches** ({count} éléments)",
        'my_tasks_empty': "Vous n'avez pas encore de tâches créées.",
        'task_actions_title': "🛠️ **Gestion de la Tâche** #{task_id}",
        'task_edit_btn': "📝 Modifier",
        'task_view_btn': "👀 Aperçu",
        'task_delete_confirm': "Êtes-vous sûr de vouloir supprimer la tâche **{name}** (#{id})?",
        'task_delete_success': "🗑️ Tâche **{name}** (#{id}) supprimée.",

        'task_channels_title': "📢 **Sélectionner les canaux pour le placement**",
        'channel_not_added': "❌ Canal introuvable dans votre liste. Ajoutez-le via '🧩 Plateformes'.",
        'channel_removed': "🗑️ Canal retiré de la tâche.",
        'channel_added': "✅ Canal ajouté à la tâche.",
        'channel_is_active_info': "Canal est actif",
        'channel_no_channels': "Vous n'avez pas encore de canaux ajoutés.",
        'channel_add_btn': "➕ Ajouter canal",
        'channel_remove_btn': "🗑️ Retirer plateforme",
        'channel_back_btn': "⬅️ Retour à la liste des canaux",
        'channel_actions_title': "🛠️ **Gestion du Canal**",
        'channel_ask_username': "🔗 Entrez le nom d'utilisateur du canal (ex. @channel_username). Le bot doit être admin là avec droit de publier.",
        'channel_username_invalid': "❌ Format invalide. Veuillez entrer le nom d'utilisateur du canal, commençant par @ ou sans.",
        'channel_add_error': "❌ Erreur lors de l'ajout du canal. Assurez-vous que le bot est administrateur avec droits de publication.",
        'channel_add_success': "✅ Canal **{title}** ajouté avec succès!",
        'channel_remove_confirm': "Êtes-vous sûr de vouloir retirer le canal **{title}** de votre liste de plateformes?",
        'channel_remove_success': "🗑️ Canal **{title}** retiré de vos plateformes.",

        'my_channels_title': "**🧩 Mes Plateformes**",
        'my_channels_footer': "**Instruction:**\n1. Ajoutez un canal où le bot a des droits d'administrateur.\n2. Cliquez sur le canal pour le gérer.",
        'my_channels_empty': "❌ Vous n'avez pas encore ajouté de canaux.",

        'post_type_menu': "📤 **Sélection du Type de Publication**",
        'post_type_from_bot': "Du bot (Copie)",
        'post_type_repost': "Repost (Transfert)",

        'tariff_title': "💳 **Votre Tarif**",
        'tariff_current_status': "Votre tarif actuel: **{name}**",
        'tariff_tasks_limit': "Limite de tâches: **{current}/{limit}**",
        'tariff_upgrade_prompt': "Vous pouvez mettre à niveau votre tarif:",
        'tariff_details_template': "✅ Limite de tâches: **{task_limit}**\n✅ Limite de plateformes: **{channel_limit}**",
        'tariff_buy_btn': "Acheter",
        'tariff_unlimited': "Illimité",
        'reports_title': "☑️ **Rapports**",

        'boss_menu_title': "😎 **Panneau Boss**",
        'boss_mailing_btn': "✉️ Mailings",
        'boss_signature_btn': "🌵 Signature (Gratuit)",
        'boss_stats_btn': "📊 Statistiques",
        'boss_users_btn': "👥 Utilisateurs",
        'boss_limits_btn': "🚨 Limites",
        'boss_tariffs_btn': "💳 Tarifs",
        'boss_ban_btn': "🚫 Bannir",
        'boss_money_btn': "💰 Argent",
        'boss_logs_btn': "📑 Journaux",

        'free_dates_title': "ℹ️ **Dates Libres**",
        'free_dates_info': "Ceci affiche vos prochaines publications planifiées. Les dates 'libres' sont toutes les dates et heures *non* listées ci-dessous.",
        'free_dates_empty': "Vous n'avez aucune publication planifiée. Toutes les dates sont libres.",
        'free_dates_list_item': "• **{local_time}** - *{task_name}* (dans @{channel_username})",

        # --- NEW BOSS PANEL LOCALIZATIONS ---
        'boss_no_access': "⛔️ Vous n'avez pas accès à ce panneau",
        'boss_quick_stats': "📊 Statistiques Rapides:",
        'boss_total_users': "👥 Total des utilisateurs: {total_users}",
        'boss_active_users': "✅ Actifs: {active_users}",
        'boss_active_tasks': "📝 Tâches actives: {tasks_active}",
        'boss_mailing_constructor': "📣 **Constructeur d'Envoi**\n\nEnvoyez le message que vous souhaitez envoyer à tous les utilisateurs du bot.\n(Peut être du texte, une photo, une vidéo, etc.)",
        'boss_back_btn': "⬅️ Retour",
        'boss_mailing_saved': "✅ Message enregistré!\n\nVoulez-vous exclure des utilisateurs de l'envoi ?\nEnvoyez leur nom d'utilisateur ou ID séparés par des virgules (ex: @user1, 12345, @user2)\nOu appuyez sur 'Passer' pour envoyer à tout le monde.",
        'boss_mailing_skip_btn': "⏭️ Passer",
        'boss_mailing_confirm_title': "📊 **Confirmation d'Envoi**",
        'boss_mailing_recipients': "👥 Destinataires: {total_recipients}",
        'boss_mailing_excluded': "🚫 Exclus: {excluded_count}",
        'boss_mailing_confirm_prompt': "Confirmez l'envoi:",
        'boss_mailing_send_btn': "✅ Envoyer",
        'boss_mailing_cancel_btn': "❌ Annuler",
        'boss_mailing_started': "Envoi commencé...",
        'boss_mailing_sending': "📤 Envoi en cours...\n{sent} envoyés, {failed} erreurs",
        'boss_mailing_sending_initial': "📤 Envoi en cours...\n0 / ?",
        'boss_mailing_completed_title': "✅ **Envoi terminé!**",
        'boss_mailing_sent_count': "📨 Envoyés: {sent}",
        'boss_mailing_failed_count': "❌ Erreurs: {failed}",
        'boss_back_to_boss': "⬅️ Retour au Boss",
        'boss_signature_title': "🌵 **Signature pour Tarif FREE**",
        'boss_signature_info': "Cette signature sera ajoutée aux publications des utilisateurs en tarif FREE.",
        'boss_signature_current': "📝 Signature actuelle:\n{current_text}\n\nEnvoyez le nouveau texte de la signature ou cliquez sur les boutons ci-dessous:",
        'boss_signature_not_set': "Non définie",
        'boss_signature_delete_btn': "🗑️ Supprimer Signature",
        'boss_signature_too_long': "❌ La signature est trop longue (max 200 caractères)",
        'boss_signature_updated': "✅ Signature mise à jour!\n\n📝 Nouvelle signature:\n{signature}",
        'boss_signature_deleted': "✅ Signature supprimée!",
        'boss_users_title': "👥 **100 Derniers Utilisateurs**",
        'boss_users_no_username': "sans nom d'utilisateur",
        'boss_users_total_shown': "\n📊 Total affiché: {count}",
        'boss_stats_loading': "Chargement des statistiques...",
        'boss_stats_title': "📊 **Statistiques du Bot**",
        'boss_stats_total_users': "👥 Total des utilisateurs: {total_users}",
        'boss_stats_active_users': "✅ Utilisateurs actifs: {active_users}",
        'boss_stats_tasks_today': "📝 Tâches créées aujourd'hui: {tasks_today}",
        'boss_stats_tasks_active': "🔄 Tâches actives: {tasks_active}",
        'boss_stats_tasks_completed': "✔️ Tâches terminées: {tasks_completed}",
        'boss_stats_tasks_total': "📦 Total des tâches dans la base de données: {tasks_total}",
        'boss_stats_users_30d': "📈 Croissance en 30 jours: +{users_30d}",
        'boss_stats_users_60d': "📈 Croissance en 60 jours: +{users_60d}",
        'boss_stats_db_size': "💾 Taille de la base de données: {db_size}",
        'boss_stats_db_warning': "\n\n⚠️ **ATTENTION**: La taille de la base de données dépasse 100MB!",
        'boss_stats_refresh': "🔄 Actualiser",
        'boss_money_title': "💰 **Statistiques Financières**",
        'boss_money_tariff_title': "📊 Utilisateurs par tarifs:",
        'boss_money_tariff_item': "• {name}: {count} pers. ({price}⭐ chacun)",
        'boss_money_estimated_revenue': "\n💵 Revenu estimé: {revenue}⭐",
        'boss_money_note': "\n⚠️ Note: Ceci est un calcul estimé.\nLes statistiques de paiement réelles sont suivies via Telegram Payments.",
        'boss_logs_title': "📝 **Erreurs Critiques**",
        'boss_logs_no_errors': "✅ Aucune erreur critique trouvée.",
        'boss_logs_info': "\n\nℹ️ Les journaux sont écrits dans la sortie standard de l'application.\nUtilisez le système de surveillance de votre hébergement pour consulter les journaux complets.",

        # --- NEW BOSS BAN LOCALIZATIONS ---
        'boss_ban_start_msg': "🚫 **Bannir Utilisateur**\n\nVeuillez envoyer l'ID ou le @nom_utilisateur de l'utilisateur que vous souhaitez bannir (ou débannir).",
        'boss_ban_user_not_found': "❌ Utilisateur introuvable. Veuillez réessayer (ID ou @nom_utilisateur):",
        'boss_action_ban': "bannir",
        'boss_action_unban': "DÉBANNIR",
        'boss_status_active': "Actif",
        'boss_status_banned': "Banni",
        'boss_ban_confirm_title': "**Confirmation**",
        'boss_ban_user_label': "Utilisateur:",
        'boss_ban_id_label': "ID:",
        'boss_ban_status_label': "Statut Actuel:",
        'boss_ban_confirm_prompt': "Êtes-vous sûr de vouloir **{action_text}** cet utilisateur?",
        'boss_confirm_yes_prefix': "✅ Oui, ",
        'boss_confirm_cancel_btn': "❌ Non, annuler",
        'boss_ban_session_error': "❌ Erreur: ID utilisateur introuvable dans la session. Veuillez recommencer.",
        'boss_ban_success': "🚫 L'utilisateur @{target_username} (ID: {target_id}) a été **banni**. Toutes ses tâches actives ont été annulées.",
        'boss_unban_success': "✅ L'utilisateur @{target_username} (ID: {target_id}) a été **débanni**.",

        'task_activated_title': "✅ Tâche #{task_id} activée avec succès !",
        'task_activated_jobs_count': "Publications créées : {job_count}",
        'task_activated_schedule_info': "Les publications seront exécutées selon le calendrier",
        'task_advertiser_notify': "📢 Vous avez été désigné comme annonceur pour la tâche \"{task_name}\". Vous recevrez des notifications de publication.",

        'payment_success_template': "✅ Le paiement a été effectué avec succès !\n\nL'abonnement **{tariff_name}** est activé.",
        'error_notify_user': "❌ Échec de la notification de l'utilisateur {user_id} concernant l'ajout du canal. Le bot pourrait être bloqué.",
        'error_invoice_creation': "❌ Échec de la création d'une facture pour le paiement. Veuillez réessayer plus tard.",
        'error_tariff_not_found': "❌ Erreur : Abonnement non trouvé.",
        'error_tariff_cannot_buy': "❌ Cet abonnement ne peut pas être acheté.",
        'invoice_title_template': "Paiement de l'abonnement '{tariff_name}'",
        'invoice_description_template': "Accès aux limites : {tasks} tâches, {time_slots} T, {date_slots} D",
        'precheckout_error': "Quelque chose s'est mal passé...",

        'task_message_current_prompt': "Votre message actuel pour la publication:\n\n(Pour changer, envoyez simplement un nouveau)",
        'task_delete_message_btn': "🗑️ Supprimer ce message",
        'task_message_display_error': "❌ Échec de l'affichage du message enregistré (il a peut-être été supprimé).",
        'task_message_deleted_alert': "Message supprimé!",
        'alert_pin_notify_status': "🔔 Push: {status}",
        'alert_report_status': "📊 Rapport: {status}",
        'alert_post_type_status': "📤 Type de post: {status}",
        'calendar_header_dates': "📅 {month_year_str}: {dates_str}\n",
        'calendar_header_weekdays': "📅 Jours de la semaine: {weekdays_str}\n",
        'calendar_info_weekdays': "*Si vous sélectionnez des jours de la semaine, le planning se répétera chaque semaine\n",
        'calendar_info_limit_slots': "*Pas plus de {max_time_slots} créneaux horaires pour le tarif {tariff_name}\n\n",
        'calendar_date_limit_alert': "❌ Limite du tarif ({limits['name']}): pas plus de {max_dates} dates",
        'calendar_weekdays_short': "Lu,Ma,Me,Je,Ve,Sa,Di",
        'free_dates_header': "📅 **Dates libres (sans posts):**\n{free_dates_str}\n",
        'free_dates_none_60d': "Aucune date complètement libre dans les 60 prochains jours.",
        'free_dates_schedule_header_30d': "📅 **Vos tâches de publication auto pour 30 jours:**\n",
        'free_dates_schedule_empty_30d': "Vous n'avez aucune publication programmée pour 30 jours.",
        'error_task_id_not_found': "Erreur: ID de tâche non trouvé.",
        'error_task_not_found_db': "Erreur: Tâche {task_id} non trouvée dans la BDD.",
        'status_count_suffix': "élém.",
        'status_days_suffix': "jours",
        'status_hours_suffix': "h",
        'status_hours_suffix_short': "h",
        'status_dates_count': "✅ {count} {suffix}",
        'status_weekdays_count': "✅ {count} {suffix}",
        'status_times_count': "✅ {count} {suffix}",
        'status_pin_duration': "✅ {duration}{suffix}",
        'status_delete_duration': "✅ {duration}{suffix}",
        'status_advertiser_id': "✅ ID: {advertiser_user_id}",
        'header_channels': "📢 Canaux: ",
        'header_message': "📝 Message: ",
        'header_date': "📅 Date: ",
        'header_weekdays': "📅 Jours de la semaine: ",
        'header_time': "🕐 Heure: ",
        'header_pin': "📌 Épingler: ",
        'header_autodelete': "🗑️ Auto-suppression: ",
        'header_post_type': "📤 Type de post: ",
        'header_pin_notify': "🔔 Push: ",
        'header_report': "📊 Rapport: ",
        'header_advertiser': "🔗 Annonceur: ",

        'limit_error_tasks': "❌ Limite de tâches atteinte ({current}/{max}) pour le tarif {tariff}.\nSupprimez les anciennes tâches ou mettez à jour votre tarif.",
        'limit_error_channels': "❌ Limite de canaux atteinte ({current}/{max}) pour le tarif {tariff}.\nSupprimez les anciens canaux ou mettez à jour votre tarif.",
        'limit_error_dates': "❌ Limite de dates atteinte ({current}/{max}) pour le tarif {tariff}.",
        'limit_error_times': "❌ Limite de créneaux horaires atteinte ({current}/{max}) pour le tarif {tariff}.",
        'limit_error_weekdays': "❌ Limite de jours de la semaine atteinte ({current}/{max}) pour le tarif {tariff}.",

        'my_tasks_header': "📋 **Mes Tâches** (total: {count})\n\n{list_text}\n\n**Mini-Instruction:**\n📊 Statuts des Tâches:\n🟢 Actif - en cours d'exécution\n🟡 Finalisation - en attente de suppression automatique\n🔴 Inactif - arrêté",
        'my_tasks_item_template': "{icon} #{id} • {name} • {status_text}",
        'status_text_active': "Actif",
        'status_text_finishing': "Finalisation",
        'status_text_inactive': "Inactif",
        'task_btn_template': "{icon} #{id} • {name}",
        'task_tariff_info': "⭐ Tarif: {name}. Utilisé: {current}/{max}",
        'task_status_label': "Statut: ",
        'task_btn_deactivate': "🛑 DÉSACTIVER LA TÂCHE",
        'task_deactivated_success': "🛑 Tâche arrêtée. Toutes les futures publications annulées.",
        'report_message': "📊 **Rapport de Publication**\n\n✅ Message publié avec succès dans le canal: {channel}\n📌 Épingler: {pin}\n🗑 Suppression auto: {delete}\n\n🔗 Lien (si disponible): {link}",
        'time_current_info': "Votre heure actuelle: {current_time}",
        'name_not_set': "Titre non spécifié",

        'task_activating_spinner': "Activation de la tâche...",
        'task_not_found_error': "❌ Erreur : tâche introuvable.",
        'task_validation_header': "❌ Impossible d'activer la tâche :",
        'task_error_no_message': "• Message de publication non défini",
        'task_error_no_channels': "• Canaux non sélectionnés",
        'task_error_no_schedule': "• Calendrier non défini (dates et/ou heure)",
        'task_job_creation_error': "❌ Erreur lors de la création des tâches de publication : {error}",
        'channel_not_found': '❌ Chaîne non trouvée ou inactive.',

        'post_published': '📢 Publication postée sur la chaîne.',
    },
    'ua': {
        # ... (existing Ukrainian localizations) ...
        'welcome_lang': """🤖 Ласкаво просимо до XSponsorBot!
Я допомагаю автоматизувати рекламні пости в Telegram каналах.
Ви можете створювати завдання, обирати канали для розміщення, налаштовувати час публікації, закріплення, автовидалення та звіти.
Моя мета — зробити вашу співпрацю з рекламодавцями максимально ефективною та зручною.
Давайте почнемо! Оберіть вашу мову:""",
        'select_timezone': "Будь ласка, оберіть ваш часовий пояс:",
        'main_menu': "📋 Головне меню\n\nОберіть дію:",
        'task_constructor_title': "🎯 Створення завдання",
        'task_default_name': " (Назву не задано)",
        'task_ask_name': "📝 Введіть назву завдання (наприклад, 'Реклама кафе'):",
        'task_ask_message': "📝 Надішліть або перешліть боту повідомлення, яке потрібно опублікувати.\n(Це може бути текст, фото, відео тощо)",
        'task_ask_advertiser': "🔗 Введіть username рекламодавця (наприклад, @username або user123):",
        'task_advertiser_saved': "✅ Рекламодавець збережений!",
        'task_advertiser_not_found': "❌ Користувача з таким username не знайдено...",
        'status_not_selected': "❌ Не вибрано",
        'status_yes': "✅ Так",
        'status_no': "❌ Ні",
        'calendar_entire_month': "Весь місяць",
        'calendar_reset': "Скинути",
        'time_custom': "🕐 Свій час",
        'time_clear': "Очистити",

        # --- Ключі для клавіатур ---
        'nav_new_task_btn': "🚀 ➕ Нове завдання",
        'nav_my_tasks_btn': "📋 Мої завдання",
        'nav_channels_btn': "🧩 Майданчики",
        'nav_free_dates_btn': "ℹ️ Вільні дати",
        'nav_tariff_btn': "💳 Тариф",
        'nav_boss_btn': "😎 Boss",
        'nav_language_btn': "🌐 Зміна мови",
        'nav_timezone_btn': "🕰️ Зміна таймзони",
        'nav_reports_btn': "☑️ Звіти",
        'keyboard_main_menu_title': "⌨️ Головне меню:",
        'reply_keyboard_prompt': "Оберіть дію на клавіатурі:",
        'task_set_name_btn': "📝 Назва завдання",
        'task_select_channels_btn': "📢 Канали",
        'task_set_message_btn': "📝 Повідомлення",
        'task_select_calendar_btn': "📅 Календар",
        'task_select_time_btn': "🕐 Час",
        'task_set_pin_btn': "📌 Закріпити",
        'task_set_pin_notify_btn': "📌 з Пуш",
        'task_set_delete_btn': "🧹 Автовидалення",
        'task_set_report_btn': "📊 Звіт",
        'task_set_advertiser_btn': "🔗 Рекламодавець",
        'task_set_post_type_btn': "📤 Тип посту",
        'task_delete_btn': "🗑️ Видалити завдання",
        'back_to_main_menu_btn': "⬅️ Назад (в Головне меню)",
        'task_activate_btn': "✅ АКТИВУВАТИ ЗАВДАННЯ",
        'back_btn': "⬅️ Назад",
        'home_main_menu_btn': "🏠 Головне меню",
        'duration_12h': "12г",
        'duration_24h': "24г",
        'duration_48h': "48г",
        'duration_3d': "3д",
        'duration_7d': "7д",
        'duration_no': "❌ Ні",
        'duration_ask_pin': "📌 Оберіть тривалість закріплення:",
        'duration_ask_delete': "🧹 Оберіть тривалість автовидалення:",

        # --- Добавленные локализации ---
        'status_set': "✅ Задано",
        'status_not_set': "❌ Не задано",
        'status_from_bot': "Від імені бота",
        'status_repost': "Репост від рекламодавця",
        'error_generic': "❌ Сталася помилка. Спробуйте знову.",
        'task_message_saved': "✅ Повідомлення для публікації збережено!",
        'task_name_saved': "✅ Назва завдання збережена!",

        'calendar_prev': "⬅️ Попер. місяць",
        'calendar_next': "Наст. місяць ➡️",
        'calendar_select_all': "Вибрати все",
        'calendar_title': "📅 **Вибір дат для розміщення**",
        'calendar_selected_dates': "✅ Вибрано дат: {count}",
        'calendar_weekdays_note': "Пн Вт Ср Чт Пт Сб Нд",

        'time_selection_title': "🕐 **Вибір часу**",
        'time_tz_info': "Ваш часовий пояс: {timezone}",
        'time_slots_limit': "Ліміт слотів: {slots}",
        'time_selected_slots': "Вибрано: {count} / {slots}",
        'time_ask_custom': "Введіть час у форматі ГГ:ХХ (напр. 14:30):",
        'time_invalid_format': "❌ Невірний формат часу. Спробуйте знову.",
        'time_saved': "✅ Час збережено!",

        'my_tasks_title': "📋 **Мої завдання** ({count} шт.)",
        'my_tasks_empty': "У вас поки що немає створених завдань.",
        'task_actions_title': "🛠️ **Керування завданням** #{task_id}",
        'task_edit_btn': "📝 Редагувати",
        'task_view_btn': "👀 Попередній перегляд",
        'task_delete_confirm': "Ви впевнені, що хочете видалити завдання **{name}** (#{id})?",
        'task_delete_success': "🗑️ Завдання **{name}** (#{id}) видалено.",

        'task_channels_title': "📢 **Вибір каналів для розміщення**",
        'channel_not_added': "❌ Канал не знайдено у вашому списку. Додайте його через '🧩 Майданчики'.",
        'channel_removed': "🗑️ Канал видалено із завдання.",
        'channel_added': "✅ Канал додано до завдання.",
        'channel_is_active_info': "Канал активний",
        'channel_no_channels': "У вас поки що немає доданих каналів.",
        'channel_add_btn': "➕ Додати канал",
        'channel_remove_btn': "🗑️ Видалити майданчик",
        'channel_back_btn': "⬅️ До списку каналів",
        'channel_actions_title': "🛠️ **Керування каналом**",
        'channel_ask_username': "🔗 Введіть username каналу (напр. @channel_username). Бот повинен бути там адміном з правом публікації.",
        'channel_username_invalid': "❌ Невірний формат. Будь ласка, введіть username каналу, починаючи з @ або без.",
        'channel_add_error': "❌ Помилка при додаванні каналу. Переконайтеся, що бот є адміністратором з правами публікації.",
        'channel_add_success': "✅ Канал **{title}** успішно додано!",
        'channel_remove_confirm': "Ви впевнені, що хочете видалити канал **{title}** зі списку ваших майданчиків?",
        'channel_remove_success': "🗑️ Канал **{title}** видалено з ваших майданчиків.",

        'my_channels_title': "**🧩 Мої майданчики**",
        'my_channels_footer': "**Інструкція:**\n1. Додайте канал, де бот має права адміна.\n2. Натисніть на канал для керування.",
        'my_channels_empty': "❌ У вас поки що немає доданих каналів.",

        'post_type_menu': "📤 **Вибір типу посту**",
        'post_type_from_bot': "Від бота (Копіювання)",
        'post_type_repost': "Репост (Пересилання)",

        'tariff_title': "💳 **Ваш тариф**",
        'tariff_current_status': "Ваш поточний тариф: **{name}**",
        'tariff_tasks_limit': "Ліміт завдань: **{current}/{limit}**",
        'tariff_upgrade_prompt': "Ви можете оновити свій тариф:",
        'tariff_details_template': "✅ Ліміт завдань: **{task_limit}**\n✅ Ліміт майданчиків: **{channel_limit}**",
        'tariff_buy_btn': "Купити",
        'tariff_unlimited': "Безлімітно",
        'reports_title': "☑️ **Звіти**",

        'boss_menu_title': "😎 **Панель Boss**",
        'boss_mailing_btn': "✉️ Розсилки",
        'boss_signature_btn': "🌵 Підпис (Free)",
        'boss_stats_btn': "📊 Статистика",
        'boss_users_btn': "👥 Користувачі",
        'boss_limits_btn': "🚨 Ліміти",
        'boss_tariffs_btn': "💳 Тарифи",
        'boss_ban_btn': "🚫 Бан",
        'boss_money_btn': "💰 Гроші",
        'boss_logs_btn': "📑 Логи",

        'free_dates_title': "ℹ️ **Вільні дати**",
        'free_dates_info': "Тут показані ваші найближчі заплановані публікації. 'Вільними' вважаються всі дати та час, *не* вказані нижче.",
        'free_dates_empty': "У вас немає запланованих публікацій. Усі дати вільні.",
        'free_dates_list_item': "• **{local_time}** - *{task_name}* (у @{channel_username})",

        # --- NEW BOSS PANEL LOCALIZATIONS ---
        'boss_no_access': "⛔️ У вас немає доступу до цієї панелі",
        'boss_quick_stats': "📊 Швидка статистика:",
        'boss_total_users': "👥 Всього користувачів: {total_users}",
        'boss_active_users': "✅ Активних: {active_users}",
        'boss_active_tasks': "📝 Активних завдань: {tasks_active}",
        'boss_mailing_constructor': "📣 **Конструктор розсилки**\n\nНадішліть повідомлення, яке хочете розіслати всім користувачам бота.\n(Може бути текст, фото, відео тощо)",
        'boss_back_btn': "⬅️ Назад",
        'boss_mailing_saved': "✅ Повідомлення збережено!\n\nБажаєте виключити деяких користувачів з розсилки?\nНадішліть їх username або ID через кому (наприклад: @user1, 12345, @user2)\nАбо натисніть 'Пропустити' для надсилання всім.",
        'boss_mailing_skip_btn': "⏭️ Пропустити",
        'boss_mailing_confirm_title': "📊 **Підтвердження розсилки**",
        'boss_mailing_recipients': "👥 Отримувачів: {total_recipients}",
        'boss_mailing_excluded': "🚫 Виключено: {excluded_count}",
        'boss_mailing_confirm_prompt': "Підтвердьте надсилання розсилки:",
        'boss_mailing_send_btn': "✅ Надіслати",
        'boss_mailing_cancel_btn': "❌ Скасувати",
        'boss_mailing_started': "Розсилка розпочата...",
        'boss_mailing_sending': "📤 Надсилання розсилки...\n{sent} надіслано, {failed} помилок",
        'boss_mailing_sending_initial': "📤 Надсилання розсилки...\n0 / ?",
        'boss_mailing_completed_title': "✅ **Розсилка завершена!**",
        'boss_mailing_sent_count': "📨 Надіслано: {sent}",
        'boss_mailing_failed_count': "❌ Помилок: {failed}",
        'boss_back_to_boss': "⬅️ Назад в Boss",
        'boss_signature_title': "🌵 **Підпис для FREE тарифу**",
        'boss_signature_info': "Цей підпис буде додаватися до постів користувачів з тарифом FREE.",
        'boss_signature_current': "📝 Поточний підпис:\n{current_text}\n\nНадішліть новий текст підпису або натисніть кнопки нижче:",
        'boss_signature_not_set': "Не встановлено",
        'boss_signature_delete_btn': "🗑️ Видалити підпис",
        'boss_signature_too_long': "❌ Підпис занадто довгий (макс 200 символів)",
        'boss_signature_updated': "✅ Підпис оновлено!\n\n📝 Новий підпис:\n{signature}",
        'boss_signature_deleted': "✅ Підпис видалено!",
        'boss_users_title': "👥 **Останні 100 користувачів**",
        'boss_users_no_username': "без username",
        'boss_users_total_shown': "\n📊 Всього показано: {count}",
        'boss_stats_loading': "Завантаження статистики...",
        'boss_stats_title': "📊 **Статистика бота**",
        'boss_stats_total_users': "👥 Всього користувачів: {total_users}",
        'boss_stats_active_users': "✅ Активних користувачів: {active_users}",
        'boss_stats_tasks_today': "📝 Завдань створено сьогодні: {tasks_today}",
        'boss_stats_tasks_active': "🔄 Завдань активно: {tasks_active}",
        'boss_stats_tasks_completed': "✔️ Завдань виконано: {tasks_completed}",
        'boss_stats_tasks_total': "📦 Завдань всього у базі: {tasks_total}",
        'boss_stats_users_30d': "📈 Приріст за 30 днів: +{users_30d}",
        'boss_stats_users_60d': "📈 Приріст за 60 днів: +{users_60d}",
        'boss_stats_db_size': "💾 Розмір бази даних: {db_size}",
        'boss_stats_db_warning': "\n\n⚠️ **УВАГА**: Розмір бази перевищує 100MB!",
        'boss_stats_refresh': "🔄 Оновити",
        'boss_money_title': "💰 **Фінансова статистика**",
        'boss_money_tariff_title': "📊 Користувачі за тарифами:",
        'boss_money_tariff_item': "• {name}: {count} чол. ({price}⭐ кожен)",
        'boss_money_estimated_revenue': "\n💵 Орієнтовний дохід: {revenue}⭐",
        'boss_money_note': "\n⚠️ Примітка: Це орієнтовний розрахунок.\nРеальна статистика платежів відстежується через Telegram Payments.",
        'boss_logs_title': "📝 **Критичні помилки**",
        'boss_logs_no_errors': "✅ Критичних помилок не виявлено.",
        'boss_logs_info': "\n\nℹ️ Логи записуються у стандартний вивід додатку.\nДля перегляду повних логів використовуйте систему моніторингу хостингу.",

        # --- NEW BOSS BAN LOCALIZATIONS ---
        'boss_ban_start_msg': "🚫 **Бан користувача**\n\nНадішліть ID або @username користувача, якого бажаєте заблокувати (або розблокувати).",
        'boss_ban_user_not_found': "❌ Користувача не знайдено. Спробуйте знову (ID або @username):",
        'boss_action_ban': "заблокувати",
        'boss_action_unban': "РОЗБЛОКУВАТИ",
        'boss_status_active': "Активний",
        'boss_status_banned': "Заблокований",
        'boss_ban_confirm_title': "**Підтвердження**",
        'boss_ban_user_label': "Користувач:",
        'boss_ban_id_label': "ID:",
        'boss_ban_status_label': "Поточний статус:",
        'boss_ban_confirm_prompt': "Ви впевнені, що хочете **{action_text}** цього користувача?",
        'boss_confirm_yes_prefix': "✅ Так, ",
        'boss_confirm_cancel_btn': "❌ Ні, скасувати",
        'boss_ban_session_error': "❌ Помилка: ID користувача не знайдено у сесії. Почніть спочатку.",
        'boss_ban_success': "🚫 Користувача @{target_username} (ID: {target_id}) **заблоковано**. Усі його активні завдання скасовано.",
        'boss_unban_success': "✅ Користувача @{target_username} (ID: {target_id}) **розблоковано**.",

        'task_activated_title': "✅ Завдання #{task_id} успішно активовано!",
        'task_activated_jobs_count': "Створено публікацій: {job_count}",
        'task_activated_schedule_info': "Публікації будуть виконані згідно з розкладом",
        'task_advertiser_notify': "📢 Вас вказано рекламодавцем у завданні \"{task_name}\". Ви будете отримувати сповіщення про публікації.",

        'payment_success_template': "✅ Оплата пройшла успішно!\n\nТариф **{tariff_name}** активовано.",
        'error_notify_user': "❌ Не вдалося сповістити користувача {user_id} про додавання каналу. Можливо, бот заблоковано.",
        'error_invoice_creation': "❌ Не вдалося створити рахунок на оплату. Спробуйте пізніше.",
        'error_tariff_not_found': "❌ Помилка: Тариф не знайдено.",
        'error_tariff_cannot_buy': "❌ Цей тариф не можна купити.",
        'invoice_title_template': "Оплата тарифу '{tariff_name}'",
        'invoice_description_template': "Доступ до лімітів: {tasks} завдань, {time_slots} T, {date_slots} D",
        'precheckout_error': "Щось пішло не так...",

        'task_message_current_prompt': "Ваше поточне повідомлення для публікації:\n\n(Щоб змінити, просто надішліть нове)",
        'task_delete_message_btn': "🗑️ Видалити це повідомлення",
        'task_message_display_error': "❌ Не вдалося відобразити збережене повідомлення (можливо, воно було видалено).",
        'task_message_deleted_alert': "Повідомлення видалено!",
        'alert_pin_notify_status': "🔔 Пуш: {status}",
        'alert_report_status': "📊 Звіт: {status}",
        'alert_post_type_status': "📤 Тип посту: {status}",
        'calendar_header_dates': "📅 {month_year_str}: {dates_str}\n",
        'calendar_header_weekdays': "📅 Дні тижня: {weekdays_str}\n",
        'calendar_info_weekdays': "*При виборі днів тижня розклад буде повторюватися щотижня\n",
        'calendar_info_limit_slots': "*Не більше {max_time_slots} слотів часу для тарифу {tariff_name}\n\n",
        'calendar_date_limit_alert': "❌ Ліміт тарифу ({limits['name']}): не більше {max_dates} дат",
        'calendar_weekdays_short': "Пн,Вт,Ср,Чт,Пт,Сб,Нд",
        'free_dates_header': "📅 **Вільні дати (без постів):**\n{free_dates_str}\n",
        'free_dates_none_60d': "Немає повністю вільних дат у найближчі 60 днів.",
        'free_dates_schedule_header_30d': "📅 **Ваші авто-постинг задачі на 30 днів:**\n",
        'free_dates_schedule_empty_30d': "У вас немає запланованих публікацій на 30 днів.",
        'error_task_id_not_found': "Помилка: ID завдання не знайдено.",
        'error_task_not_found_db': "Помилка: Завдання {task_id} не знайдено в БД.",
        'status_count_suffix': "шт.",
        'status_days_suffix': "дн.",
        'status_hours_suffix': "г",
        'status_hours_suffix_short': "h",
        'status_dates_count': "✅ {count} {suffix}",
        'status_weekdays_count': "✅ {count} {suffix}",
        'status_times_count': "✅ {count} {suffix}",
        'status_pin_duration': "✅ {duration}{suffix}",
        'status_delete_duration': "✅ {duration}{suffix}",
        'status_advertiser_id': "✅ ID: {advertiser_user_id}",
        'header_channels': "📢 Канали: ",
        'header_message': "📝 Повідомлення: ",
        'header_date': "📅 Дата: ",
        'header_weekdays': "📅 Дні тижня: ",
        'header_time': "🕐 Час: ",
        'header_pin': "📌 Закріпити: ",
        'header_autodelete': "🗑️ Автовидалення: ",
        'header_post_type': "📤 Тип посту: ",
        'header_pin_notify': "🔔 Пуш: ",
        'header_report': "📊 Звіт: ",
        'header_advertiser': "🔗 Рекламодавець: ",

        'limit_error_tasks': "❌ Досягнуто ліміт завдань ({current}/{max}) для тарифу {tariff}.\nВидаліть старі завдання або оновіть тариф.",
        'limit_error_channels': "❌ Досягнуто ліміт каналів ({current}/{max}) для тарифу {tariff}.\nВидаліть старі канали або оновіть тариф.",
        'limit_error_dates': "❌ Досягнуто ліміт дат ({current}/{max}) для тарифу {tariff}.",
        'limit_error_times': "❌ Досягнуто ліміт слотів часу ({current}/{max}) для тарифу {tariff}.",
        'limit_error_weekdays': "❌ Досягнуто ліміт днів тижня ({current}/{max}) для тарифу {tariff}.",

        'my_tasks_header': "📋 **Мої завдання** (всього: {count})\n\n{list_text}\n\n**Міні-інструкція:**\n📊 Статуси завдань:\n🟢 Активно - виконується\n🟡 Завершується - очікує автовидалення\n🔴 Неактивно - зупинено",
        'my_tasks_item_template': "{icon} #{id} • {name} • {status_text}",
        'status_text_active': "Активно",
        'status_text_finishing': "Завершується",
        'status_text_inactive': "Неактивно",
        'task_btn_template': "{icon} #{id} • {name}",
        'task_tariff_info': "⭐ Тариф: {name}. Використано: {current}/{max}",
        'task_status_label': "Стан: ",
        'task_btn_deactivate': "🛑 ВИМКНУТИ ЗАВДАННЯ",
        'task_deactivated_success': "🛑 Завдання зупинено. Усі майбутні публікації скасовано.",
        'report_message': "📊 **Звіт про публікацію**\n\n✅ Пост успішно опубліковано в каналі: {channel}\n📌 Закріплення: {pin}\n🗑 Автовидалення: {delete}\n\n🔗 Посилання (якщо доступне): {link}",
        'time_current_info': "Ваш поточний час: {current_time}",
        'name_not_set': "Назва не задана",

        'task_activating_spinner': "Активація завдання...",
        'task_not_found_error': "❌ Помилка: завдання не знайдено.",
        'task_validation_header': "❌ Неможливо активувати завдання:",
        'task_error_no_message': "• Не задано повідомлення для публікації",
        'task_error_no_channels': "• Не обрано канали для публікації",
        'task_error_no_schedule': "• Не задано розклад (дати та/або час)",
        'task_job_creation_error': "❌ Помилка при створенні завдань публікації: {error}",

        'channel_not_found': '❌ Канал не знайдено або неактивний.',
        'post_published': '📢 Опубліковано пост у каналі.',
    },
    'de': {
        # ... (existing German localizations) ...
        'welcome_lang': """🤖 Willkommen beim XSponsorBot!
Ich helfe bei der Automatisierung von Werbebeiträgen in Telegram-Kanälen.
Sie können Aufgaben erstellen, Kanäle für die Platzierung auswählen, Veröffentlichungszeit, Anheften, automatische Löschung und Berichte konfigurieren.
Mein Ziel ist es, Ihre Zusammenarbeit mit Werbepartnern so effizient und bequem wie möglich zu gestalten.
Lassen Sie uns beginnen! Bitte wählen Sie Ihre Sprache:""",
        'select_timezone': "Bitte wählen Sie Ihre Zeitzone:",
        'main_menu': "📋 **Hauptmenü**\n\nWählen Sie eine Aktion:",
        'task_constructor_title': "🎯 Aufgaben-Konstruktor",
        'task_default_name': " (Name nicht festgelegt)",
        'task_ask_name': "📝 Gib einen Namen für diese Aufgabe ein (z.B. 'Café-Aktion'):",
        'task_ask_message': "📝 Sende oder leite die Nachricht, die du veröffentlichen möchtest, an den Bot weiter.\n(Dies kann Text, Foto, Video usw. sein)",
        'task_ask_advertiser': "🔗 Gib den Benutzernamen des Werbepartners ein (z.B. @username oder user123):",
        'task_advertiser_saved': "✅ Werbepartner gespeichert!",
        'task_advertiser_not_found': "❌ Benutzer mit diesem Namen nicht gefunden...",
        'status_not_selected': "❌ Nicht ausgewählt",
        'status_yes': "✅ Ja",
        'status_no': "❌ Nein",
        'calendar_entire_month': "Ganzer Monat",
        'calendar_reset': "Zurücksetzen",
        'time_custom': "🕐 Eigene Uhrzeit",
        'time_clear': "Löschen",

        # --- Tastatur-Schlüssel ---
        'nav_new_task_btn': "🚀 ➕ Neue Aufgabe",
        'nav_my_tasks_btn': "📋 Meine Aufgaben",
        'nav_channels_btn': "🧩 Plattformen",
        'nav_free_dates_btn': "ℹ️ Freie Termine",
        'nav_tariff_btn': "💳 Tarif",
        'nav_boss_btn': "😎 Boss",
        'nav_language_btn': "🌐 Sprache ändern",
        'nav_timezone_btn': "🕰️ Zeitzone ändern",
        'nav_reports_btn': "☑️ Berichte",
        'keyboard_main_menu_title': "⌨️ Hauptmenü:",
        'reply_keyboard_prompt': "Wähle eine Aktion auf der Tastatur:",
        'task_set_name_btn': "📝 Aufgabenname",
        'task_select_channels_btn': "📢 Kanäle",
        'task_set_message_btn': "📝 Nachricht",
        'task_select_calendar_btn': "📅 Kalender",
        'task_select_time_btn': "🕐 Uhrzeit",
        'task_set_pin_btn': "📌 Anheften",
        'task_set_pin_notify_btn': "📌 mit Push",
        'task_set_delete_btn': "🧹 Auto-Löschung",
        'task_set_report_btn': "📊 Bericht",
        'task_set_advertiser_btn': "🔗 Werbepartner",
        'task_set_post_type_btn': "📤 Beitragstyp",
        'task_delete_btn': "🗑️ Aufgabe löschen",
        'back_to_main_menu_btn': "⬅️ Zurück (zum Hauptmenü)",
        'task_activate_btn': "✅ AUFGABE AKTIVIEREN",
        'back_btn': "⬅️ Zurück",
        'home_main_menu_btn': "🏠 Hauptmenü",
        'duration_12h': "12h",
        'duration_24h': "24h",
        'duration_48h': "48h",
        'duration_3d': "3T",
        'duration_7d': "7T",
        'duration_no': "❌ Nein",
        'duration_ask_pin': "📌 Wähle die Dauer des Anheftens:",
        'duration_ask_delete': "🧹 Wähle die Dauer der Auto-Löschung:",

        # --- Добавленные локализации ---
        'status_set': "✅ Festgelegt",
        'status_not_set': "❌ Nicht festgelegt",
        'status_from_bot': "Im Namen des Bots",
        'status_repost': "Repost vom Werbepartner",
        'error_generic': "❌ Es ist ein Fehler aufgetreten. Bitte versuchen Sie es erneut.",
        'task_message_saved': "✅ Nachricht für die Veröffentlichung gespeichert!",
        'task_name_saved': "✅ Aufgabenname gespeichert!",

        'calendar_prev': "⬅️ Vorher. Monat",
        'calendar_next': "Nächster Monat ➡️",
        'calendar_select_all': "Alle auswählen",
        'calendar_title': "📅 **Auswahl der Termine für die Platzierung**",
        'calendar_selected_dates': "✅ Ausgewählte Termine: {count}",
        'calendar_weekdays_note': "Mo Di Mi Do Fr Sa So",

        'time_selection_title': "🕐 **Zeitauswahl**",
        'time_tz_info': "Ihre Zeitzone: {timezone}",
        'time_slots_limit': "Slot-Limit: {slots}",
        'time_selected_slots': "Ausgewählt: {count} / {slots}",
        'time_ask_custom': "Geben Sie die Uhrzeit im Format HH:MM ein (z.B. 14:30):",
        'time_invalid_format': "❌ Ungültiges Zeitformat. Versuchen Sie es erneut.",
        'time_saved': "✅ Uhrzeit gespeichert!",

        'my_tasks_title': "📋 **Meine Aufgaben** ({count} Stk.)",
        'my_tasks_empty': "Sie haben noch keine Aufgaben erstellt.",
        'task_actions_title': "🛠️ **Aufgabenverwaltung** #{task_id}",
        'task_edit_btn': "📝 Bearbeiten",
        'task_view_btn': "👀 Vorschau",
        'task_delete_confirm': "Sind Sie sicher, dass Sie die Aufgabe **{name}** (#{id}) löschen möchten?",
        'task_delete_success': "🗑️ Aufgabe **{name}** (#{id}) gelöscht.",

        'task_channels_title': "📢 **Kanäle für die Platzierung auswählen**",
        'channel_not_added': "❌ Kanal nicht in Ihrer Liste gefunden. Fügen Sie ihn über '🧩 Plattformen' hinzu.",
        'channel_removed': "🗑️ Kanal aus Aufgabe entfernt.",
        'channel_added': "✅ Kanal zur Aufgabe hinzugefügt.",
        'channel_is_active_info': "Kanal ist aktiv",
        'channel_no_channels': "Sie haben noch keine Kanäle hinzugefügt.",
        'channel_add_btn': "➕ Kanal hinzufügen",
        'channel_remove_btn': "🗑️ Plattform entfernen",
        'channel_back_btn': "⬅️ Zurück zur Kanalliste",
        'channel_actions_title': "🛠️ **Kanalverwaltung**",
        'channel_ask_username': "🔗 Geben Sie den Kanal-Benutzernamen ein (z.B. @channel_username). Der Bot muss dort Admin mit Veröffentlichungsrechten sein.",
        'channel_username_invalid': "❌ Ungültiges Format. Bitte geben Sie den Kanal-Benutzernamen ein, beginnend mit @ oder ohne.",
        'channel_add_error': "❌ Fehler beim Hinzufügen des Kanals. Stellen Sie sicher, dass der Bot Administrator mit Veröffentlichungsrechten ist.",
        'channel_add_success': "✅ Kanal **{title}** erfolgreich hinzugefügt!",
        'channel_remove_confirm': "Sind Sie sicher, dass Sie den Kanal **{title}** aus Ihrer Plattformliste entfernen möchten?",
        'channel_remove_success': "🗑️ Kanal **{title}** aus Ihren Plattformen entfernt.",

        'my_channels_title': "**🧩 Meine Plattformen**",
        'my_channels_footer': "**Anleitung:**\n1. Fügen Sie einen Kanal hinzu, in dem der Bot Admin-Rechte hat.\n2. Klicken Sie auf den Kanal zur Verwaltung.",
        'my_channels_empty': "❌ Du hast noch keine Kanäle hinzugefügt.",

        'post_type_menu': "📤 **Beitragstyp auswählen**",
        'post_type_from_bot': "Vom Bot (Kopieren)",
        'post_type_repost': "Repost (Weiterleiten)",

        'tariff_title': "💳 **Ihr Tarif**",
        'tariff_current_status': "Ihr aktueller Tarif: **{name}**",
        'tariff_tasks_limit': "Aufgabenlimit: **{current}/{limit}**",
        'tariff_upgrade_prompt': "Sie können Ihren Tarif upgraden:",
        'tariff_details_template': "✅ Aufgabenlimit: **{task_limit}**\n✅ Plattformlimit: **{channel_limit}**",
        'tariff_buy_btn': "Kaufen",
        'tariff_unlimited': "Unbegrenzt",
        'reports_title': "☑️ **Berichte**",

        'boss_menu_title': "😎 **Boss-Panel**",
        'boss_mailing_btn': "✉️ Mailings",
        'boss_signature_btn': "🌵 Signatur (Kostenlos)",
        'boss_stats_btn': "📊 Statistik",
        'boss_users_btn': "👥 Benutzer",
        'boss_limits_btn': "🚨 Limits",
        'boss_tariffs_btn': "💳 Tarife",
        'boss_ban_btn': "🚫 Sperren",
        'boss_money_btn': "💰 Geld",
        'boss_logs_btn': "📑 Protokolle",

        'free_dates_title': "ℹ️ **Freie Termine**",
        'free_dates_info': "Hier werden Ihre nächsten geplanten Veröffentlichungen angezeigt. 'Frei' sind alle Termine und Zeiten, die *nicht* unten aufgeführt sind.",
        'free_dates_empty': "Sie haben keine geplanten Veröffentlichungen. Alle Termine sind frei.",
        'free_dates_list_item': "• **{local_time}** - *{task_name}* (in @{channel_username})",

        # --- NEW BOSS PANEL LOCALIZATIONS ---
        'boss_no_access': "⛔️ Sie haben keinen Zugriff auf dieses Panel",
        'boss_quick_stats': "📊 Kurze Statistik:",
        'boss_total_users': "👥 Gesamte Benutzer: {total_users}",
        'boss_active_users': "✅ Aktiv: {active_users}",
        'boss_active_tasks': "📝 Aktive Aufgaben: {tasks_active}",
        'boss_mailing_constructor': "📣 **Mailing-Konstruktor**\n\nSenden Sie die Nachricht, die Sie an alle Bot-Benutzer senden möchten.\n(Kann Text, Foto, Video usw. sein)",
        'boss_back_btn': "⬅️ Zurück",
        'boss_mailing_saved': "✅ Nachricht gespeichert!\n\nMöchten Sie Benutzer vom Mailing ausschließen?\nSenden Sie deren Benutzernamen oder IDs durch Kommata getrennt (z.B. @user1, 12345, @user2)\nOder klicken Sie auf 'Überspringen', um an alle zu senden.",
        'boss_mailing_skip_btn': "⏭️ Überspringen",
        'boss_mailing_confirm_title': "📊 **Mailing-Bestätigung**",
        'boss_mailing_recipients': "👥 Empfänger: {total_recipients}",
        'boss_mailing_excluded': "🚫 Ausgeschlossen: {excluded_count}",
        'boss_mailing_confirm_prompt': "Bestätigen Sie den Mailing-Versand:",
        'boss_mailing_send_btn': "✅ Senden",
        'boss_mailing_cancel_btn': "❌ Abbrechen",
        'boss_mailing_started': "Mailing gestartet...",
        'boss_mailing_sending': "📤 Mailing wird gesendet...\n{sent} gesendet, {failed} Fehler",
        'boss_mailing_sending_initial': "📤 Mailing wird gesendet...\n0 / ?",
        'boss_mailing_completed_title': "✅ **Mailing abgeschlossen!**",
        'boss_mailing_sent_count': "📨 Gesendet: {sent}",
        'boss_mailing_failed_count': "❌ Fehler: {failed}",
        'boss_back_to_boss': "⬅️ Zurück zum Boss",
        'boss_signature_title': "🌵 **Signatur für FREE-Tarif**",
        'boss_signature_info': "Diese Signatur wird zu Beiträgen von Benutzern mit dem FREE-Tarif hinzugefügt.",
        'boss_signature_current': "📝 Aktuelle Signatur:\n{current_text}\n\nSenden Sie den neuen Signaturtext oder klicken Sie auf die Schaltflächen unten:",
        'boss_signature_not_set': "Nicht festgelegt",
        'boss_signature_delete_btn': "🗑️ Signatur löschen",
        'boss_signature_too_long': "❌ Signatur ist zu lang (max 200 Zeichen)",
        'boss_signature_updated': "✅ Signatur aktualisiert!\n\n📝 Neue Signatur:\n{signature}",
        'boss_signature_deleted': "✅ Signatur gelöscht!",
        'boss_users_title': "👥 **Letzte 100 Benutzer**",
        'boss_users_no_username': "kein Benutzername",
        'boss_users_total_shown': "\n📊 Insgesamt angezeigt: {count}",
        'boss_stats_loading': "Statistik wird geladen...",
        'boss_stats_title': "📊 **Bot-Statistik**",
        'boss_stats_total_users': "👥 Gesamte Benutzer: {total_users}",
        'boss_stats_active_users': "✅ Aktive Benutzer: {active_users}",
        'boss_stats_tasks_today': "📝 Heute erstellte Aufgaben: {tasks_today}",
        'boss_stats_tasks_active': "🔄 Aktive Aufgaben: {tasks_active}",
        'boss_stats_tasks_completed': "✔️ Abgeschlossene Aufgaben: {tasks_completed}",
        'boss_stats_tasks_total': "📦 Gesamte Aufgaben in der Datenbank: {tasks_total}",
        'boss_stats_users_30d': "📈 Zuwachs der letzten 30 Tage: +{users_30d}",
        'boss_stats_users_60d': "📈 Zuwachs der letzten 60 Tage: +{users_60d}",
        'boss_stats_db_size': "💾 Datenbankgröße: {db_size}",
        'boss_stats_db_warning': "\n\n⚠️ **ACHTUNG**: Die Datenbankgröße überschreitet 100MB!",
        'boss_stats_refresh': "🔄 Aktualisieren",
        'boss_money_title': "💰 **Finanzstatistik**",
        'boss_money_tariff_title': "📊 Benutzer nach Tarifen:",
        'boss_money_tariff_item': "• {name}: {count} Pers. ({price}⭐ jeweils)",
        'boss_money_estimated_revenue': "\n💵 Geschätzter Umsatz: {revenue}⭐",
        'boss_money_note': "\n⚠️ Hinweis: Dies ist eine Schätzung.\nDie tatsächlichen Zahlungsstatistiken werden über Telegram Payments verfolgt.",
        'boss_logs_title': "📝 **Kritische Fehler**",
        'boss_logs_no_errors': "✅ Keine kritischen Fehler gefunden.",
        'boss_logs_info': "\n\nℹ️ Protokolle werden in die Standardausgabe der Anwendung geschrieben.\nVerwenden Sie das Überwachungssystem Ihres Hostings, um die vollständigen Protokolle anzuzeigen.",

        # --- NEW BOSS BAN LOCALIZATIONS ---
        'boss_ban_start_msg': "🚫 **Benutzer Sperren**\n\nSenden Sie die ID oder den @Benutzernamen des Benutzers, den Sie sperren (oder entsperren) möchten.",
        'boss_ban_user_not_found': "❌ Benutzer nicht gefunden. Bitte versuchen Sie es erneut (ID oder @Benutzername):",
        'boss_action_ban': "sperren",
        'boss_action_unban': "ENTSPERREN",
        'boss_status_active': "Aktiv",
        'boss_status_banned': "Gesperrt",
        'boss_ban_confirm_title': "**Bestätigung**",
        'boss_ban_user_label': "Benutzer:",
        'boss_ban_id_label': "ID:",
        'boss_ban_status_label': "Aktueller Status:",
        'boss_ban_confirm_prompt': "Sind Sie sicher, dass Sie diesen Benutzer **{action_text}** möchten?",
        'boss_confirm_yes_prefix': "✅ Ja, ",
        'boss_confirm_cancel_btn': "❌ Nein, abbrechen",
        'boss_ban_session_error': "❌ Fehler: Benutzer-ID nicht in der Sitzung gefunden. Bitte beginnen Sie von vorne.",
        'boss_ban_success': "🚫 Benutzer @{target_username} (ID: {target_id}) wurde **gesperrt**. Alle seine aktiven Aufgaben wurden storniert.",
        'boss_unban_success': "✅ Benutzer @{target_username} (ID: {target_id}) wurde **entsperrt**.",

        'task_activated_title': "✅ Aufgabe #{task_id} erfolgreich aktiviert!",
        'task_activated_jobs_count': "Erstellte Veröffentlichungen: {job_count}",
        'task_activated_schedule_info': "Veröffentlichungen werden gemäß dem Zeitplan ausgeführt",
        'task_advertiser_notify': "📢 Sie wurden als Werbetreibender für die Aufgabe „{task_name}“ festgelegt. Sie erhalten Benachrichtigungen über Veröffentlichungen.",

        'payment_success_template': "✅ Zahlung war erfolgreich!\n\nDer Tarif **{tariff_name}** ist aktiviert.",
        'error_notify_user': "❌ Benachrichtigung des Benutzers {user_id} über die Kanalerstellung fehlgeschlagen. Der Bot ist möglicherweise blockiert.",
        'error_invoice_creation': "❌ Rechnungserstellung für die Zahlung fehlgeschlagen. Bitte versuchen Sie es später erneut.",
        'error_tariff_not_found': "❌ Fehler: Tarif nicht gefunden.",
        'error_tariff_cannot_buy': "❌ Dieser Tarif kann nicht gekauft werden.",
        'invoice_title_template': "Zahlung für Tarif '{tariff_name}'",
        'invoice_description_template': "Zugriff auf Limits: {tasks} Aufgaben, {time_slots} T, {date_slots} D",
        'precheckout_error': "Etwas ist schiefgelaufen...",

        'task_message_current_prompt': "Ihre aktuelle Nachricht zur Veröffentlichung:\n\n(Zum Ändern einfach eine neue senden)",
        'task_delete_message_btn': "🗑️ Diese Nachricht löschen",
        'task_message_display_error': "❌ Gespeicherte Nachricht konnte nicht angezeigt werden (vielleicht wurde sie gelöscht).",
        'task_message_deleted_alert': "Nachricht gelöscht!",
        'alert_pin_notify_status': "🔔 Push: {status}",
        'alert_report_status': "📊 Bericht: {status}",
        'alert_post_type_status': "📤 Beitragstyp: {status}",
        'calendar_header_dates': "📅 {month_year_str}: {dates_str}\n",
        'calendar_header_weekdays': "📅 Wochentage: {weekdays_str}\n",
        'calendar_info_weekdays': "*Bei Auswahl von Wochentagen wiederholt sich der Zeitplan wöchentlich\n",
        'calendar_info_limit_slots': "*Nicht mehr als {max_time_slots} Zeitfenster für Tarif {tariff_name}\n\n",
        'calendar_date_limit_alert': "❌ Tariflimit ({limits['name']}): nicht mehr als {max_dates} Daten",
        'calendar_weekdays_short': "Mo,Di,Mi,Do,Fr,Sa,So",
        'free_dates_header': "📅 **Freie Termine (keine Beiträge):**\n{free_dates_str}\n",
        'free_dates_none_60d': "Keine vollständig freien Termine in den nächsten 60 Tagen.",
        'free_dates_schedule_header_30d': "📅 **Ihre Auto-Posting-Aufgaben für 30 Tage:**\n",
        'free_dates_schedule_empty_30d': "Sie haben keine geplanten Veröffentlichungen für 30 Tage.",
        'error_task_id_not_found': "Fehler: Aufgaben-ID nicht gefunden.",
        'error_task_not_found_db': "Fehler: Aufgabe {task_id} nicht in DB gefunden.",
        'status_count_suffix': "Stk.",
        'status_days_suffix': "Tage",
        'status_hours_suffix': "h",
        'status_hours_suffix_short': "h",
        'status_dates_count': "✅ {count} {suffix}",
        'status_weekdays_count': "✅ {count} {suffix}",
        'status_times_count': "✅ {count} {suffix}",
        'status_pin_duration': "✅ {duration}{suffix}",
        'status_delete_duration': "✅ {duration}{suffix}",
        'status_advertiser_id': "✅ ID: {advertiser_user_id}",
        'header_channels': "📢 Kanäle: ",
        'header_message': "📝 Nachricht: ",
        'header_date': "📅 Datum: ",
        'header_weekdays': "📅 Wochentage: ",
        'header_time': "🕐 Uhrzeit: ",
        'header_pin': "📌 Anheften: ",
        'header_autodelete': "🗑️ Auto-Löschen: ",
        'header_post_type': "📤 Beitragstyp: ",
        'header_pin_notify': "🔔 Push: ",
        'header_report': "📊 Bericht: ",
        'header_advertiser': "🔗 Werbepartner: ",

        'limit_error_tasks': "❌ Aufgabenlimit erreicht ({current}/{max}) für Tarif {tariff}.\nBitte alte Aufgaben löschen oder Tarif upgraden.",
        'limit_error_channels': "❌ Kanallimit erreicht ({current}/{max}) für Tarif {tariff}.\nBitte alte Kanäle entfernen oder Tarif upgraden.",
        'limit_error_dates': "❌ Datumslimit erreicht ({current}/{max}) für Tarif {tariff}.",
        'limit_error_times': "❌ Zeitfensterlimit erreicht ({current}/{max}) für Tarif {tariff}.",
        'limit_error_weekdays': "❌ Wochentagslimit erreicht ({current}/{max}) für Tarif {tariff}.",

        'my_tasks_header': "📋 **Meine Aufgaben** (Gesamt: {count})\n\n{list_text}\n\n**Kurzanleitung:**\n📊 Aufgabenstatus:\n🟢 Aktiv - wird ausgeführt\n🟡 Abschließen - wartet auf Auto-Löschung\n🔴 Inaktiv - gestoppt",
        'my_tasks_item_template': "{icon} #{id} • {name} • {status_text}",
        'status_text_active': "Aktiv",
        'status_text_finishing': "Abschließen",
        'status_text_inactive': "Inaktiv",
        'task_btn_template': "{icon} #{id} • {name}",
        'task_tariff_info': "⭐ Tarif: {name}. Verwendet: {current}/{max}",
        'task_status_label': "Status: ",
        'task_btn_deactivate': "🛑 AUFGABE DEAKTIVIEREN",
        'task_deactivated_success': "🛑 Aufgabe gestoppt. Alle zukünftigen Veröffentlichungen storniert.",
        'report_message': "📊 **Veröffentlichungsbericht**\n\n✅ Beitrag erfolgreich im Kanal veröffentlicht: {channel}\n📌 Anheftung: {pin}\n🗑 Auto-Löschung: {delete}\n\n🔗 Link (falls verfügbar): {link}",
        'time_current_info': "Ihre aktuelle Uhrzeit: {current_time}",
        'name_not_set': "Kein Titel angegeben",

        'task_activating_spinner': "Aufgabe wird aktiviert...",
        'task_not_found_error': "❌ Fehler: Aufgabe nicht gefunden.",
        'task_validation_header': "❌ Aufgabe kann nicht aktiviert werden:",
        'task_error_no_message': "• Nachricht für Veröffentlichung nicht festgelegt",
        'task_error_no_channels': "• Kanäle nicht ausgewählt",
        'task_error_no_schedule': "• Zeitplan nicht festgelegt (Daten und/oder Zeit)",
        'task_job_creation_error': "❌ Fehler beim Erstellen der Veröffentlichungsaufträge: {error}",
        'channel_not_found': '❌ Kanal nicht gefunden oder inaktiv.',
        'post_published': '📢 Beitrag im Kanal veröffentlicht.',
    }
}

# Города и их таймзоны с UTC offset
TIMEZONES = {
    "Мадрид": ("Europe/Madrid", "UTC+1"),
    "Москва": ("Europe/Moscow", "UTC+3"),
    "Киев": ("Europe/Kiev", "UTC+2"),
    "Ташкент": ("Asia/Tashkent", "UTC+5"),
    "Берлин": ("Europe/Berlin", "UTC+1"),
    "Париж": ("Europe/Paris", "UTC+1"),
}


# --- Тарифы ---
class Tariff(Enum):
    FREE = {"name": "FREE", "time_slots": 2, "date_slots": 7, "tasks": 3, "channels": 1, "price": 0}
    PRO1 = {"name": "Pro 1", "time_slots": 5, "date_slots": 10, "tasks": 10, "channels": 3, "price": 300}
    PRO2 = {"name": "Pro 2", "time_slots": 10, "date_slots": 20, "tasks": 15, "channels": 5, "price": 500}
    PRO3 = {"name": "Pro 3", "time_slots": 20, "date_slots": 31, "tasks": 25, "channels": 10, "price": 800}
    PRO4 = {"name": "Pro 4", "time_slots": 24, "date_slots": 31, "tasks": 100, "channels": 50, "price": 2000}


def get_tariff_limits(tariff_name: str) -> dict:
    """Получает лимиты для указанного тарифа, с фолбэком на FREE."""
    # В БД хранится 'free', 'pro1', 'pro2'
    # В Enum ключи 'FREE', 'PRO1', 'PRO2'
    tariff_key = tariff_name.upper()

    if hasattr(Tariff, tariff_key):
        return getattr(Tariff, tariff_key).value
    else:
        logger.warning(f"Не найден тариф '{tariff_name}' (key: {tariff_key}) в Enum, используется FREE.")
        return Tariff.FREE.value

# --- Хелпер i18n ---
def get_text(key: str, context: ContextTypes.DEFAULT_TYPE, lang: str = None) -> str:
    """Получает текст на нужном языке из user_data или по умолчанию (en)."""
    if not lang:
        lang = context.user_data.get('language_code', 'en')

    if lang not in TEXTS:
        lang = 'en'

    return TEXTS.get(lang, {}).get(key) or TEXTS['en'].get(key, f"_{key}_")


def get_bot_statistics():
    """Get bot statistics for admin panel"""
    stats = {}

    # Total users
    result = db_query("SELECT COUNT(*) as count FROM users WHERE is_active = TRUE", fetchone=True)
    stats['total_users'] = result['count'] if result else 0

    # Active users (used bot in last 30 days)
    result = db_query("""
        SELECT COUNT(DISTINCT user_id) as count 
        FROM tasks 
        WHERE created_at > NOW() - INTERVAL '30 days'
    """, fetchone=True)
    stats['active_users'] = result['count'] if result else 0

    # Tasks created today
    result = db_query("""
        SELECT COUNT(*) as count 
        FROM tasks 
        WHERE DATE(created_at) = CURRENT_DATE
    """, fetchone=True)
    stats['tasks_today'] = result['count'] if result else 0

    # Active tasks
    result = db_query("SELECT COUNT(*) as count FROM tasks WHERE status = 'active'", fetchone=True)
    stats['tasks_active'] = result['count'] if result else 0

    # Completed tasks
    result = db_query("SELECT COUNT(*) as count FROM publication_jobs WHERE status = 'published'", fetchone=True)
    stats['tasks_completed'] = result['count'] if result else 0

    # Total tasks in DB
    result = db_query("SELECT COUNT(*) as count FROM tasks", fetchone=True)
    stats['tasks_total'] = result['count'] if result else 0

    # Database size
    result = db_query("""
        SELECT pg_size_pretty(pg_database_size(current_database())) as size
    """, fetchone=True)
    stats['db_size'] = result['size'] if result else 'N/A'

    # User growth (last 30 days)
    result = db_query("""
        SELECT COUNT(*) as count 
        FROM users 
        WHERE created_at > NOW() - INTERVAL '30 days'
    """, fetchone=True)
    stats['users_30d'] = result['count'] if result else 0

    # User growth (last 60 days)
    result = db_query("""
        SELECT COUNT(*) as count 
        FROM users 
        WHERE created_at > NOW() - INTERVAL '60 days'
    """, fetchone=True)
    stats['users_60d'] = result['count'] if result else 0

    return stats


def get_recent_users(limit=100):
    """Get recent users list"""
    return db_query("""
        SELECT user_id, username, first_name, created_at, tariff
        FROM users
        WHERE is_active = TRUE
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,), fetchall=True) or []


def set_user_limit(user_id: int, limit_type: str, value: int):
    """Set custom limit for user (stores in a new table or user field)"""
    # For now, we'll use a simple JSON field approach
    # In production, you might want a separate limits table
    db_query("""
        UPDATE users 
        SET custom_limits = jsonb_set(
            COALESCE(custom_limits, '{}'::jsonb),
            '{%s}',
            '%s'::jsonb
        )
        WHERE user_id = %s
    """ % (limit_type, value, user_id), commit=True)


def ban_user(user_id: int, reason: str = None):
    """Ban a user"""
    db_query("""
        UPDATE users 
        SET is_active = FALSE
        WHERE user_id = %s
    """, (user_id,), commit=True)

    # Cancel all scheduled jobs for this user
    db_query("""
        UPDATE publication_jobs 
        SET status = 'cancelled'
        WHERE user_id = %s AND status = 'scheduled'
    """, (user_id,), commit=True)


def unban_user(user_id: int):
    """Unban a user"""
    db_query("""
        UPDATE users 
        SET is_active = TRUE
        WHERE user_id = %s
    """, (user_id,), commit=True)


def get_money_statistics():
    """Get revenue statistics"""
    stats = {}

    # This is a placeholder - in production you'd track actual payments
    # Count users by tariff
    tariff_counts = db_query("""
        SELECT tariff, COUNT(*) as count
        FROM users
        WHERE is_active = TRUE
        GROUP BY tariff
    """, fetchall=True) or []

    stats['by_tariff'] = {row['tariff']: row['count'] for row in tariff_counts}

    # Calculate estimated revenue (placeholder)
    total_revenue = 0
    for tariff_key, count in stats['by_tariff'].items():
        limits = get_tariff_limits(tariff_key)
        total_revenue += limits['price'] * count

    stats['estimated_revenue'] = total_revenue

    return stats


def get_critical_logs(limit=50):
    """Get recent critical errors from logs"""
    # This is a placeholder - in production you'd log to a table
    # For now, return empty or read from log file
    return []


def generate_smart_name(text: str, context: ContextTypes.DEFAULT_TYPE, limit: int = 4) -> str:
    """
    Генерирует короткое название: первые N информативных слов,
    исключая предлоги, союзы, артикли и числа.
    """
    if not text:
        return get_text('name_not_set', context)

    stop_words = {
        'в', 'на', 'под', 'за', 'к', 'до', 'по', 'из', 'у', 'о', 'об', 'с', 'от', 'для', 'и', 'или', 'но', 'а',
        'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'and', 'or', 'but', 'the', 'a', 'an'
    }

    # Оставляем только буквы, цифры, нижнее подчёркивание и пробелы
    clean_text = re.sub(r"[^\w\s]", "", text)

    words = clean_text.split()
    filtered_words = []

    for w in words:
        lw = w.lower()

        # Пропуск чисел
        if lw.isdigit():
            continue

        # Пропуск стоп-слов
        if lw in stop_words:
            continue

        filtered_words.append(w)

        if len(filtered_words) >= limit:
            break

    # Если после фильтрации ничего не осталось — просто взять первые 3 слова
    if not filtered_words:
        return " ".join(words[:3]) + "..."

    return " ".join(filtered_words) + "..."


def determine_task_status_color(task_id: int) -> str:
    """
    Logic:
    🟢 Green: Future posts exist (Scheduled).
    🟡 Yellow: No future posts, but posts are waiting for auto-deletion.
    🔴 Red: No future posts, no pending deletions.
    """
    # 1. Check for future schedules
    scheduled = db_query(
        "SELECT COUNT(*) as count FROM publication_jobs WHERE task_id = %s AND status = 'scheduled'",
        (task_id,), fetchone=True
    )
    if scheduled and scheduled['count'] > 0:
        return '🟢'

    # 2. Check for pending auto-deletions (Status is published, has auto_delete, not yet deleted)
    pending_delete = db_query("""
        SELECT COUNT(*) as count 
        FROM publication_jobs 
        WHERE task_id = %s 
          AND status = 'published' 
          AND auto_delete_hours > 0
    """, (task_id,), fetchone=True)

    if pending_delete and pending_delete['count'] > 0:
        return '🟡'

    # 3. Default
    return '🔴'



def refresh_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Перезапускает планировщик для задачи, если она активна.
    Отменяет старые джобы и создает новые, чтобы учесть изменения в БД.
    """
    task = get_task_details(task_id)
    if not task or task['status'] != 'active':
        return
    logger.info(f"Hot-reload scheduler for active task {task_id}")
    # 1. Отменяем старые джобы
    cancel_task_jobs(task_id, context)
    # 2. Создаем новые
    user_settings = get_user_settings(task['user_id'])
    user_tz = user_settings.get('timezone', 'Europe/Moscow')
    create_publication_jobs_for_task(task_id, user_tz, context.application)


def cancel_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Отменяет все запланированные публикации для задачи в БД и JobQueue.
    Используется при переактивации или остановке задачи.
    """
    # 1. Ищем запланированные джобы в БД
    jobs_to_cancel = db_query(
        "SELECT aps_job_id FROM publication_jobs WHERE task_id = %s AND status = 'scheduled' AND aps_job_id IS NOT NULL",
        (task_id,), fetchall=True
    )

    if jobs_to_cancel:
        for job_row in jobs_to_cancel:
            job_name = job_row.get('aps_job_id')
            if job_name:
                # Удаляем из планировщика Telegram
                jobs = context.application.job_queue.get_jobs_by_name(job_name)
                for job in jobs:
                    job.schedule_removal()
                    logger.info(f"Job {job_name} removed from scheduler (task refresh).")

    # 2. Маркируем их как отмененные в БД, чтобы они не висели как 'scheduled'
    db_query(
        "UPDATE publication_jobs SET status = 'cancelled' WHERE task_id = %s AND status = 'scheduled'",
        (task_id,), commit=True
    )

# --- Инициализация БД (ПОЛНОСТЬЮ НОВАЯ СХЕМА) ---
def init_db():
    """Создание таблиц в БД, если их нет (Схема под ТЗ)"""
    if not db_pool:
        logger.error("Database pool not available in init_db")
        return

    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            # Таблица пользователей
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    language_code VARCHAR(10) DEFAULT 'en',
                    timezone VARCHAR(100) DEFAULT 'Europe/Moscow',
                    tariff VARCHAR(50) DEFAULT 'free',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)

            # Таблица каналов/площадок
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    channel_id BIGINT UNIQUE,
                    channel_title VARCHAR(255),
                    channel_username VARCHAR(255),
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)

            # Таблица "Задач" (Шаблоны)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    task_name VARCHAR(255),
                    content_message_id BIGINT,
                    content_chat_id BIGINT,
                    pin_duration INTEGER DEFAULT 0,
                    pin_notify BOOLEAN DEFAULT FALSE,
                    auto_delete_hours INTEGER DEFAULT 0,
                    report_enabled BOOLEAN DEFAULT FALSE,
                    advertiser_user_id BIGINT,
                    post_type VARCHAR(50) DEFAULT 'from_bot',
                    status VARCHAR(50) DEFAULT 'inactive',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица связей "Задача <-> Каналы"
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_channels (
                    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                    channel_id BIGINT REFERENCES channels(channel_id) ON DELETE CASCADE,
                    PRIMARY KEY (task_id, channel_id)
                )
            """)

            # Таблица связей "Задача <-> Расписание"
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_schedules (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                    schedule_type VARCHAR(20),
                    schedule_date DATE,
                    schedule_weekday INTEGER,
                    schedule_time TIME
                )
            """)

            # Таблица "Публикаций"
            cur.execute("""
                CREATE TABLE IF NOT EXISTS publication_jobs (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER REFERENCES tasks(id),
                    user_id BIGINT REFERENCES users(user_id),
                    channel_id BIGINT,
                    scheduled_time_utc TIMESTAMP,
                    status VARCHAR(50) DEFAULT 'scheduled',

                    content_message_id BIGINT,
                    content_chat_id BIGINT,
                    pin_duration INTEGER DEFAULT 0,
                    pin_notify BOOLEAN DEFAULT FALSE,
                    auto_delete_hours INTEGER DEFAULT 0,
                    advertiser_user_id BIGINT,

                    published_at TIMESTAMP,
                    posted_message_id INTEGER,
                    views INTEGER DEFAULT 0,
                    forwards INTEGER DEFAULT 0,
                    aps_job_id VARCHAR(255) UNIQUE
                )
            """)

            # Таблица фоновых задач
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id SERIAL PRIMARY KEY,
                    job_id INTEGER REFERENCES publication_jobs(id) ON DELETE CASCADE,
                    task_type VARCHAR(50),
                    execute_at_utc TIMESTAMP,
                    aps_job_id VARCHAR(255) UNIQUE,
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON publication_jobs(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_scheduled_time ON publication_jobs(scheduled_time_utc)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(user_id)")

            conn.commit()
            logger.info("База данных успешно инициализирована (Новая Схема)")
    except (Exception, psycopg2.Error) as e:
        logger.error(f"Ошибка при инициализации БД: {e}")
        conn.rollback()
    finally:
        if db_pool:
            db_pool.putconn(conn)


# --- Функции для работы с БД (НОВЫЕ) ---

def db_query(sql: str, params: tuple = None, fetchone=False, fetchall=False, commit=False) -> Optional[Any]:
    """Универсальный хелпер для запросов к БД"""
    if not db_pool:
        logger.error("DB pool not available in db_query")
        return None

    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())

            if commit:
                conn.commit()
                if fetchone:
                    return dict(cur.fetchone()) if cur.rowcount else None
                if "RETURNING" in sql.upper() and cur.rowcount:
                    row = cur.fetchone()
                    return dict(row) if row else None
                return None

            if fetchone:
                row = cur.fetchone()
                return dict(row) if row else None
            if fetchall:
                return [dict(row) for row in cur.fetchall()]

            # Для INSERT ... RETURNING id
            if "RETURNING" in sql.upper() and cur.rowcount:
                row = cur.fetchone()
                return dict(row) if row else None

    except (Exception, psycopg2.Error) as e:
        logger.error(f"DB error in db_query (SQL: {sql[:100]}...): {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn and db_pool:
            db_pool.putconn(conn)


# --- Пользователи ---
def create_user(user_id: int, username: str, first_name: str):
    db_query("""
        INSERT INTO users (user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            is_active = TRUE
    """, (user_id, username, first_name), commit=True)


def set_user_lang_tz(user_id: int, lang: str = None, tz: str = None):
    if lang:
        db_query("UPDATE users SET language_code = %s WHERE user_id = %s", (lang, user_id), commit=True)
    if tz:
        db_query("UPDATE users SET timezone = %s WHERE user_id = %s", (tz, user_id), commit=True)


def get_user_settings(user_id: int) -> Dict:
    return db_query("SELECT language_code, timezone, tariff FROM users WHERE user_id = %s", (user_id,),
                    fetchone=True) or {}


def get_user_by_username(username: str) -> Optional[Dict]:
    return db_query("SELECT * FROM users WHERE lower(username) = lower(%s)", (username,), fetchone=True)


def persistent_reply_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Постоянная клавиатура (ReplyKeyboard), отображаемая во всех состояниях"""
    user_id = context.user_data.get('user_id', 0)
    lang = context.user_data.get('language_code', 'en')

    keyboard = [
        [
            KeyboardButton(get_text('nav_new_task_btn', context, lang)),
            KeyboardButton(get_text('nav_my_tasks_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_language_btn', context, lang)),
            KeyboardButton(get_text('nav_timezone_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_tariff_btn', context, lang)),
            KeyboardButton(get_text('nav_reports_btn', context, lang))
        ]
    ]

    # Добавляем кнопку "Boss" только владельцу
    if user_id == OWNER_ID:
        keyboard.append([KeyboardButton(get_text('nav_boss_btn', context, lang))])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )

# --- Каналы ---
def get_user_channels(user_id: int) -> List[Dict]:
    return db_query("""
        SELECT * FROM channels
        WHERE user_id = %s AND is_active = TRUE
        ORDER BY added_at DESC
    """, (user_id,), fetchall=True) or []


def add_channel(user_id: int, channel_id: int, title: str, username: str = None):
    db_query("""
        INSERT INTO channels (user_id, channel_id, channel_title, channel_username, is_active)
        VALUES (%s, %s, %s, %s, TRUE)
        ON CONFLICT (channel_id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            channel_title = EXCLUDED.channel_title,
            channel_username = EXCLUDED.channel_username,
            is_active = TRUE
    """, (user_id, channel_id, title, username), commit=True)
    logger.info(f"Канал {title} (ID: {channel_id}) добавлен/обновлен для user {user_id}")


def deactivate_channel(channel_id: int):
    db_query("UPDATE channels SET is_active = FALSE WHERE channel_id = %s", (channel_id,), commit=True)
    logger.info(f"Канал {channel_id} деактивирован")


def get_or_create_task_id(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    Получает ID текущей задачи из context.user_data или создает новую задачу,
    если она не задана, и сохраняет ID в context.user_data.
    """
    task_id = context.user_data.get('current_task_id')
    if task_id:
        return task_id

    # Задача еще не существует, создаем ее.
    # Предполагается, что create_task(user_id) возвращает ID созданной задачи
    new_task_id = create_task(user_id)
    if new_task_id:
        context.user_data['current_task_id'] = new_task_id
    return new_task_id


# --- Задачи (Tasks) ---
def create_task(user_id: int) -> Optional[int]:
    """Создает новую пустую задачу (черновик)"""
    result = db_query("""
        INSERT INTO tasks (user_id, status) 
        VALUES (%s, 'inactive') 
        RETURNING id
    """, (user_id,), commit=True)

    if result and 'id' in result:
        logger.info(f"Создана новая задача ID: {result['id']} для user {user_id}")
        return result['id']
    else:
        logger.error(f"Не удалось создать задачу для user {user_id}")
        return None


def get_task_details(task_id: int) -> Optional[Dict]:
    """Получает все данные о задаче для конструктора"""
    return db_query("SELECT * FROM tasks WHERE id = %s", (task_id,), fetchone=True)


def update_task_field(task_id: int, field: str, value: Any):
    """Обновляет одно поле задачи (для конструктора)"""
    # Валидация поля для безопасности
    allowed_fields = [
        'task_name', 'content_message_id', 'content_chat_id', 'pin_duration',
        'pin_notify', 'auto_delete_hours', 'report_enabled',
        'advertiser_user_id', 'post_type', 'status'
    ]

    if field not in allowed_fields:
        logger.error(f"Попытка обновить недопустимое поле: {field}")
        return

    sql = f"UPDATE tasks SET {field} = %s WHERE id = %s"
    db_query(sql, (value, task_id), commit=True)
    logger.info(f"Задача {task_id}: поле {field} = {value}")


def get_user_tasks(user_id: int) -> List[Dict]:
    """Получает список задач для экрана 'Мои задачи'"""
    return db_query("""
        SELECT id, task_name, status, created_at
        FROM tasks 
        WHERE user_id = %s 
        ORDER BY created_at DESC
    """, (user_id,), fetchall=True) or []


def get_task_channels(task_id: int) -> List[int]:
    """Получает список channel_id для задачи"""
    result = db_query("""
        SELECT channel_id FROM task_channels WHERE task_id = %s
    """, (task_id,), fetchall=True)
    return [row['channel_id'] for row in result] if result else []


def add_task_channel(task_id: int, channel_id: int):
    """Добавляет канал к задаче"""
    db_query("""
        INSERT INTO task_channels (task_id, channel_id)
        VALUES (%s, %s)
        ON CONFLICT (task_id, channel_id) DO NOTHING
    """, (task_id, channel_id), commit=True)


def remove_task_channel(task_id: int, channel_id: int):
    """Удаляет канал из задачи"""
    db_query("""
        DELETE FROM task_channels WHERE task_id = %s AND channel_id = %s
    """, (task_id, channel_id), commit=True)


# --- Расписание ---
def get_task_schedules(task_id: int) -> List[Dict]:
    """Получает расписание для задачи"""
    return db_query("""
        SELECT * FROM task_schedules WHERE task_id = %s
    """, (task_id,), fetchall=True) or []


def add_task_schedule(task_id: int, schedule_type: str, schedule_date: str = None,
                      schedule_weekday: int = None, schedule_time: str = None):
    """Добавляет расписание для задачи"""
    db_query("""
        INSERT INTO task_schedules (task_id, schedule_type, schedule_date, schedule_weekday, schedule_time)
        VALUES (%s, %s, %s, %s, %s)
    """, (task_id, schedule_type, schedule_date, schedule_weekday, schedule_time), commit=True)


def remove_task_schedules(task_id: int):
    """Удаляет все расписания для задачи"""
    db_query("DELETE FROM task_schedules WHERE task_id = %s", (task_id,), commit=True)


# --- Клавиатуры ---

def lang_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang_ru"),
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang_en"),
            InlineKeyboardButton("🇪🇸 ES", callback_data="lang_es"),
        ],
        [
            InlineKeyboardButton("🇫🇷 FR", callback_data="lang_fr"),
            InlineKeyboardButton("🇺🇦 UA", callback_data="lang_ua"),
            InlineKeyboardButton("🇩🇪 DE", callback_data="lang_de"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def timezone_keyboard():
    keyboard = []
    cities = list(TIMEZONES.keys())

    # Создаем кнопки по 2 в ряд
    for i in range(0, len(cities), 2):
        row = []
        for j in range(2):
            if i + j < len(cities):
                city = cities[i + j]
                tz_name, utc_offset = TIMEZONES[city]
                row.append(
                    InlineKeyboardButton(
                        f"{city} ({utc_offset})",
                        callback_data=f"tz_{tz_name}"
                    )
                )
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


def main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get('user_id', 0)

    keyboard = [
        [InlineKeyboardButton(get_text('nav_new_task_btn', context), callback_data="nav_new_task")],
        [InlineKeyboardButton(get_text('nav_my_tasks_btn', context), callback_data="nav_my_tasks")],
        [InlineKeyboardButton(get_text('nav_channels_btn', context), callback_data="nav_channels")],
        [InlineKeyboardButton(get_text('nav_free_dates_btn', context), callback_data="nav_free_dates")],
        [InlineKeyboardButton(get_text('nav_tariff_btn', context), callback_data="nav_tariff")],
    ]

    if user_id == OWNER_ID:
        keyboard.append([InlineKeyboardButton(get_text('nav_boss_btn', context), callback_data="nav_boss")])

    return InlineKeyboardMarkup(keyboard)


def bottom_navigation_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура навигации внизу экрана (как на изображении)"""
    keyboard = [
        [
            InlineKeyboardButton(get_text('nav_new_task_btn', context), callback_data="nav_new_task"),
            InlineKeyboardButton(get_text('nav_my_tasks_btn', context), callback_data="nav_my_tasks")
        ],
        [
            InlineKeyboardButton(get_text('nav_language_btn', context), callback_data="nav_language"),
            InlineKeyboardButton(get_text('nav_timezone_btn', context), callback_data="nav_timezone")
        ],
        [
            InlineKeyboardButton(get_text('nav_tariff_btn', context), callback_data="nav_tariff"),
            InlineKeyboardButton(get_text('nav_reports_btn', context), callback_data="nav_reports")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def task_constructor_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура конструктора (С кнопкой Стоп/Старт)"""
    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)
    is_active = task and task['status'] == 'active'

    # Нижняя кнопка действия
    if is_active:
        action_btn = InlineKeyboardButton(get_text('task_btn_deactivate', context), callback_data="task_deactivate")
    else:
        action_btn = InlineKeyboardButton(get_text('task_activate_btn', context), callback_data="task_activate")

    keyboard = [
        [InlineKeyboardButton(get_text('task_set_name_btn', context), callback_data="task_set_name")],
        [InlineKeyboardButton(get_text('task_select_channels_btn', context), callback_data="task_select_channels")],
        [InlineKeyboardButton(get_text('task_set_message_btn', context), callback_data="task_set_message")],
        [
            InlineKeyboardButton(get_text('task_select_calendar_btn', context), callback_data="task_select_calendar"),
            InlineKeyboardButton(get_text('task_select_time_btn', context), callback_data="task_select_time")
        ],
        [
            InlineKeyboardButton(get_text('task_set_pin_btn', context), callback_data="task_set_pin"),
            InlineKeyboardButton(get_text('task_set_pin_notify_btn', context), callback_data="task_set_pin_notify")
        ],
        [InlineKeyboardButton(get_text('task_set_delete_btn', context), callback_data="task_set_delete")],
        [InlineKeyboardButton(get_text('task_set_report_btn', context), callback_data="task_set_report")],
        [InlineKeyboardButton(get_text('task_set_advertiser_btn', context), callback_data="task_set_advertiser")],
        [InlineKeyboardButton(get_text('task_set_post_type_btn', context), callback_data="task_set_post_type")],
        [InlineKeyboardButton(get_text('task_delete_btn', context), callback_data="task_delete")],
        [InlineKeyboardButton(get_text('back_to_main_menu_btn', context), callback_data="nav_main_menu")],
        [action_btn],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_to_constructor_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Кнопки 'Назад' и 'Главное меню' (согласно ТЗ)"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
            InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
        ]
    ])


def back_to_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, prefix: str = "nav"):
    """Кнопка 'Назад' в Главное меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text('back_btn', context), callback_data=f"{prefix}_main_menu")]
    ])


def channels_selection_keyboard(context: ContextTypes.DEFAULT_TYPE, selected_channels: List[int] = None):
    """Клавиатура выбора каналов с галочками"""
    if selected_channels is None:
        selected_channels = []

    user_id = context.user_data.get('user_id')
    channels = get_user_channels(user_id)

    keyboard = []
    for ch in channels:
        channel_id = ch['channel_id']
        title = ch['channel_title'] or ch['channel_username'] or f"ID: {channel_id}"

        # Добавляем галочку если канал выбран
        prefix = "✅ " if channel_id in selected_channels else ""
        keyboard.append([
            InlineKeyboardButton(
                f"{prefix}{title}",
                callback_data=f"channel_toggle_{channel_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu") ]
    )

    return InlineKeyboardMarkup(keyboard)


def calendar_keyboard(
        context: ContextTypes.DEFAULT_TYPE,
        year: int,
        month: int,
        selected_dates: List[str] = None,
        selected_weekdays: List[int] = None,  # <-- ADDED
        today_user_date: datetime.date = None  # <-- ADDED
):
    """Клавиатура календаря как на изображении"""
    if selected_dates is None:
        selected_dates = []
    if selected_weekdays is None:  # <-- ADDED
        selected_weekdays = []
    if today_user_date is None:  # <-- ADDED
        today_user_date = datetime.now().date()

    # Получаем календарь на месяц
    cal = calendar.monthcalendar(year, month)

    # --- ИСПРАВЛЕНИЕ: Локализация дней недели ---
    try:
        weekdays_str = get_text('calendar_weekdays_short', context)  # "Пн,Вт,Ср..."
        weekdays = weekdays_str.split(',')  # ['Пн', 'Вт', 'Ср'...]
        if len(weekdays) != 7:  # Failsafe
            raise Exception("Invalid weekday format")
    except Exception:
        weekdays = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    keyboard = []

    # --- ИСПРАВЛЕНИЕ: Добавляем выбор дней недели (вместо статического заголовка) ---
    weekday_row = []
    for i, day_name in enumerate(weekdays):  # i will be 0-6 (Пн=0, Вс=6)
        prefix = "✅" if i in selected_weekdays else ""
        weekday_row.append(InlineKeyboardButton(f"{prefix}{day_name}", callback_data=f"calendar_wd_{i}"))
    keyboard.append(weekday_row)
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    # Добавляем дни месяца
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                # Пустая кнопка для дней другого месяца
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                # --- НОВАЯ ЛОГИКА ОТОБРАЖЕНИЯ ---
                current_date = datetime(year, month, day).date()
                date_str = current_date.strftime('%Y-%m-%d')

                is_past = current_date < today_user_date
                is_selected_date = date_str in selected_dates
                # calendar.weekday() -> Пн=0, Вс=6
                is_selected_weekday = current_date.weekday() in selected_weekdays

                prefix = " "
                if is_selected_date:
                    prefix = "✅"  # Выбранная дата имеет приоритет
                elif is_selected_weekday and not is_past:
                    prefix = "🗓️"  # Показываем, что день недели выбран (и не в прошлом)

                callback = f"calendar_day_{date_str}"

                # Блокируем прошедшие даты
                if is_past:
                    prefix = "❌"
                    callback = "calendar_ignore_past"

                row.append(InlineKeyboardButton(f"{prefix}{day}", callback_data=callback))
                # --- КОНЕЦ НОВОЙ ЛОГИКИ ---
        keyboard.append(row)

    # Кнопки управления (month navigation)
    # (Кнопка 'calendar_select_all' была убрана из ConversationHandler,
    # поэтому мы ее не добавляем, а ставим 'reset' в центр)
    keyboard.append([
        InlineKeyboardButton("⬅️", callback_data="calendar_prev"),
        InlineKeyboardButton(get_text('calendar_reset', context), callback_data="calendar_reset"),
        InlineKeyboardButton("➡️", callback_data="calendar_next")
    ])

    # Кнопки Назад / Главное меню
    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")]
    )

    return InlineKeyboardMarkup(keyboard)

def time_selection_keyboard(context: ContextTypes.DEFAULT_TYPE, selected_times: List[str] = None):
    """Клавиатура выбора времени как на изображении"""
    if selected_times is None:
        selected_times = []

    keyboard = []

    # Создаем сетку 6x4 для времени
    times = []
    for hour in range(24):
        times.append(f"{hour:02d}:00")

    # Разбиваем на 6 строк по 4 столбца
    for i in range(0, 24, 4):
        row = []
        for j in range(4):
            if i + j < 24:
                time_str = times[i + j]
                is_selected = time_str in selected_times
                prefix = "✅" if is_selected else ""
                row.append(InlineKeyboardButton(f"{prefix}{time_str}", callback_data=f"time_select_{time_str}"))
        keyboard.append(row)

    # Кнопка для ввода своего времени
    keyboard.append([
        InlineKeyboardButton(get_text('time_custom', context), callback_data="time_custom")
    ])

    # Кнопки управления
    keyboard.append([
        InlineKeyboardButton(get_text('time_clear', context), callback_data="time_clear"),
        InlineKeyboardButton("⬅️ Назад", callback_data="task_back_to_constructor"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="nav_main_menu")
    ])

    return InlineKeyboardMarkup(keyboard)


def pin_duration_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура выбора длительности закрепления"""
    keyboard = [
        [InlineKeyboardButton(get_text('duration_12h', context), callback_data="pin_12")],
        [InlineKeyboardButton(get_text('duration_24h', context), callback_data="pin_24")],
        [InlineKeyboardButton(get_text('duration_48h', context), callback_data="pin_48")],
        [InlineKeyboardButton(get_text('duration_3d', context), callback_data="pin_72")],
        [InlineKeyboardButton(get_text('duration_7d', context), callback_data="pin_168")],
        [InlineKeyboardButton(get_text('duration_no', context), callback_data="pin_0")],
        [
            InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
            InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def delete_duration_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура выбора длительности автоудаления"""
    # Assuming the structure is similar to pin_duration_keyboard
    keyboard = [
        [InlineKeyboardButton(get_text('duration_12h', context), callback_data="delete_12")],
        [InlineKeyboardButton(get_text('duration_24h', context), callback_data="delete_24")],
        [InlineKeyboardButton(get_text('duration_48h', context), callback_data="delete_48")],
        [InlineKeyboardButton(get_text('duration_3d', context), callback_data="delete_72")],
        [InlineKeyboardButton(get_text('duration_7d', context), callback_data="delete_168")],
        [InlineKeyboardButton(get_text('duration_no', context), callback_data="delete_0")],
        [
            InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
            InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def boss_panel_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура админ-панели (локализованная)"""
    keyboard = [
        [InlineKeyboardButton(get_text('boss_mailing_btn', context), callback_data="boss_mailing")],
        [InlineKeyboardButton(get_text('boss_signature_btn', context), callback_data="boss_signature")], # <-- НОВАЯ КНОПКА
        [InlineKeyboardButton(get_text('boss_users_btn', context), callback_data="boss_users")],
        [InlineKeyboardButton(get_text('boss_stats_btn', context), callback_data="boss_stats")],
        # [InlineKeyboardButton(get_text('boss_limits_btn', context), callback_data="boss_limits")],
        # [InlineKeyboardButton(get_text('boss_tariffs_btn', context), callback_data="boss_tariffs")],
        [InlineKeyboardButton(get_text('boss_ban_btn', context), callback_data="boss_ban")],
        [InlineKeyboardButton(get_text('boss_money_btn', context), callback_data="boss_money")],
        [InlineKeyboardButton(get_text('boss_logs_btn', context), callback_data="boss_logs")],
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- Хелперы ConversationHandler ---

async def send_or_edit_message(update: Update, text: str, reply_markup: InlineKeyboardMarkup):
    """Отправляет новое или редактирует существующее сообщение."""
    query = update.callback_query
    if query and query.message:
        try:
            # FIXED: Remove parse_mode to avoid Markdown errors
            await query.edit_message_text(text, reply_markup=reply_markup)
        except TelegramError as e:
            if "Message is not modified" not in str(e):
                logger.warning(f"Ошибка редактирования сообщения: {e}")
            await query.answer()
    elif update.message:
        # FIXED: Remove parse_mode to avoid Markdown errors
        await update.message.reply_text(text, reply_markup=reply_markup)


async def load_user_settings(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Загружает настройки пользователя в user_data"""
    settings = get_user_settings(user_id)
    context.user_data['user_id'] = user_id
    context.user_data['language_code'] = settings.get('language_code', 'en')
    context.user_data['timezone'] = settings.get('timezone', 'Europe/Moscow')
    context.user_data['tariff'] = settings.get('tariff', 'free')


def main_menu_reply_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура с кнопками внизу экрана (ReplyKeyboard)"""
    user_id = context.user_data.get('user_id', 0)

    # Получаем язык пользователя ДЛЯ создания кнопок
    lang = context.user_data.get('language_code', 'en')

    keyboard = [
        [
            KeyboardButton(get_text('nav_new_task_btn', context, lang)),
            KeyboardButton(get_text('nav_my_tasks_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_language_btn', context, lang)),
            KeyboardButton(get_text('nav_timezone_btn', context, lang))
        ],
        [
            KeyboardButton(get_text('nav_tariff_btn', context, lang)),
            KeyboardButton(get_text('nav_reports_btn', context, lang))
        ]
    ]

    if user_id == OWNER_ID:
        keyboard.append([KeyboardButton(get_text('nav_boss_btn', context, lang))])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает главное меню с inline и reply кнопками"""
    text = get_text('main_menu', context)

    query = update.callback_query
    chat_id = None

    if query:
        # Если мы пришли из callback (напр. кнопка "Назад"),
        # удаляем старое сообщение, чтобы меню не дублировались.
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение в show_main_menu: {e}")

        chat_id = query.message.chat_id

    elif update.message:
        chat_id = update.message.chat_id

    else:
        # На случай, если не удалось определить chat_id (например, при /start)
        chat_id = update.effective_chat.id

    if not chat_id:
        logger.error("Не удалось определить chat_id в show_main_menu")
        return MAIN_MENU

    # 1. Отправляем Inline-меню
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=main_menu_keyboard(context)
    )

    # 2. Отправляем Reply-клавиатуру (кнопки внизу)
    await context.bot.send_message(
        chat_id=chat_id,
        text=get_text('reply_keyboard_prompt', context),  # <-- ИСПРАВЛЕНО
        reply_markup=main_menu_reply_keyboard(context)  # <-- Теперь кнопки на нужном языке
    )

    return MAIN_MENU


async def handle_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки ReplyKeyboard"""
    text = update.message.text
    lang = context.user_data.get('language_code', 'en')

    # Map button text to callbacks
    if text == get_text('nav_new_task_btn', context, lang):
        return await task_constructor_entrypoint(update, context)
    elif text == get_text('nav_my_tasks_btn', context, lang):
        return await nav_my_tasks(update, context)
    elif text == get_text('nav_language_btn', context, lang):
        return await nav_language(update, context)
    elif text == get_text('nav_timezone_btn', context, lang):
        return await nav_timezone(update, context)
    elif text == get_text('nav_tariff_btn', context, lang):
        return await nav_tariff(update, context)
    elif text == get_text('nav_reports_btn', context, lang):
        return await nav_reports(update, context)
    elif text == get_text('nav_boss_btn', context, lang):
        # Add check to ensure only owner can use this button
        if context.user_data.get('user_id') == OWNER_ID:
            return await nav_boss(update, context)

# --- 1. Процесс /start ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /start.
    1. Создает/обновляет юзера.
    2. Если у юзера НЕ дефолтные настройки, показывает Главное меню.
    3. Иначе, показывает выбор языка.
    """
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    create_user(user.id, user.username, user.first_name)
    # Загружаем настройки. get_user_settings вернет дефолтные из БД (en/Moscow) или сохраненные.
    await load_user_settings(user.id, context)

    user_lang = context.user_data.get('language_code')
    user_tz = context.user_data.get('timezone')

    # Проверяем, отличаются ли настройки от дефолтных
    # (дефолтные в init_db: 'en' и 'Europe/Moscow')
    if user_lang != 'en' or user_tz != 'Europe/Moscow':
        # Если юзер уже что-то выбирал (не дефолт), сразу показываем меню
        return await show_main_menu(update, context)
    else:
        # Если у юзера дефолтные настройки (либо он новый,
        # либо выбрал en/Moscow), показываем выбор языка.
        await update.message.reply_text(
            TEXTS['ru']['welcome_lang'], # Показываем на RU, чтобы дать выбор
            reply_markup=lang_keyboard()
        )
        return START_SELECT_LANG


async def start_select_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Состояние START_SELECT_LANG. 1. Юзер нажал кнопку языка. 2. Сохраняем язык. 3. Показываем выбор таймзоны."""
    query = update.callback_query
    await query.answer()

    lang = query.data.replace("lang_", "")
    if lang not in TEXTS:
        lang = 'en'

    set_user_lang_tz(user_id=query.from_user.id, lang=lang)
    context.user_data['language_code'] = lang

    text = get_text('select_timezone', context)
    await query.edit_message_text(text, reply_markup=timezone_keyboard())
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


# --- 2. Главное меню и Навигация ---

async def nav_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Коллбэк 'nav_main_menu'. Возвращает в Главное меню."""
    query = update.callback_query
    if query:
        await query.answer()

    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    return await show_main_menu(update, context)


async def nav_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Мои задачи' (Обновленный дизайн)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
    else:
        message = update.message

    user_id = context.user_data['user_id']
    tasks = get_user_tasks(user_id)

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_tasks = limits['tasks']

    keyboard = []
    list_text_items = []

    if not tasks:
        list_text = get_text('my_tasks_empty', context)
    else:
        # Сортируем: сначала Активные, потом Завершающиеся, потом Неактивные
        # (Для простоты сортируем по ID desc, как в SQL, а цвета определяем на лету)

        for task in tasks:
            icon = determine_task_status_color(task['id'], task['status'])

            # Определяем текстовый статус для списка
            if icon == '🟢':
                status_txt = get_text('status_text_active', context)
            elif icon == '🟡':
                status_txt = get_text('status_text_finishing', context)
            else:
                status_txt = get_text('status_text_inactive', context)

            # Формируем строку списка
            # Название - первые 4 слова (используем хелпер)
            smart_name = generate_smart_name(task['task_name'] or "",context, limit=4)

            item_str = get_text('my_tasks_item_template', context).format(
                icon=icon,
                id=task['id'],
                name=smart_name,
                status_text=status_txt
            )
            list_text_items.append(item_str)

            # Формируем КНОПКУ (кратко, первые 3 слова)
            btn_name = generate_smart_name(task['task_name'] or "", context, limit=3)
            btn_str = get_text('task_btn_template', context).format(
                icon=icon,
                id=task['id'],
                name=btn_name
            )

            keyboard.append([
                InlineKeyboardButton(btn_str, callback_data=f"task_edit_{task['id']}")
            ])

        list_text = "\n".join(list_text_items)

    # Шапка + Список + Легенда
    full_text = get_text('my_tasks_header', context).format(
        count=len(tasks),
        list_text=list_text
    )

    # Доп кнопки
    keyboard.append([InlineKeyboardButton(get_text('nav_new_task_btn', context), callback_data="nav_new_task")])

    # Плашка тарифа (неактивная кнопка или callback на тариф)
    tariff_info = get_text('task_tariff_info', context).format(
        name=limits['name'],
        current=len(tasks),
        max=max_tasks
    )
    keyboard.append([InlineKeyboardButton(tariff_info, callback_data="nav_tariff")])

    keyboard.append([InlineKeyboardButton(get_text('back_to_main_menu_btn', context), callback_data="nav_main_menu")])

    # Используем edit_message_text если возможно, иначе send
    try:
        await message.edit_message_text(full_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception:
        await message.reply_text(full_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    return MY_TASKS


async def channel_manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления конкретным каналом"""
    query = update.callback_query
    await query.answer()

    channel_id = int(query.data.replace("channel_manage_", ""))

    # Получаем информацию о канале
    channel = db_query("SELECT * FROM channels WHERE channel_id = %s", (channel_id,), fetchone=True)

    if not channel or not channel['is_active']:
        await query.edit_message_text(
            get_text('channel_not_found', context),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_channels")]])
        )
        return MY_CHANNELS

    title = channel['channel_title'] or "Без названия"
    username = channel['channel_username'] or "нет юзернейма"

    text = get_text('channel_actions_title', context) + "\n\n"
    text += f"📢 **{title}**\n"
    text += f"🔗 @{username}\n"
    text += f"ID: `{channel_id}`\n\n"
    text += "Что вы хотите сделать?"

    keyboard = [
        [InlineKeyboardButton(get_text('channel_remove_btn', context), callback_data=f"channel_delete_{channel_id}")],
        [InlineKeyboardButton(get_text('channel_back_btn', context), callback_data="nav_channels")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return MY_CHANNELS


async def channel_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление канала из списка (Soft delete)"""
    query = update.callback_query
    await query.answer()

    channel_id = int(query.data.replace("channel_delete_", ""))

    # Проверяем существование
    channel = db_query("SELECT * FROM channels WHERE channel_id = %s", (channel_id,), fetchone=True)
    title = channel['channel_title'] if channel else str(channel_id)

    # Деактивируем канал
    deactivate_channel(channel_id)

    # Удаляем из всех будущих задач (опционально, но желательно)
    db_query("DELETE FROM task_channels WHERE channel_id = %s", (channel_id,), commit=True)

    text = get_text('channel_remove_success', context).format(title=title)

    # Возвращаемся к списку
    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    list_text = get_text('my_channels_title', context).format(count=len(channels))
    keyboard = []

    if not channels:
        list_text += get_text('my_channels_empty', context)
    else:
        for ch in channels:
            t = ch['channel_title'] or ch['channel_username'] or f"ID: {ch['channel_id']}"
            list_text += f"\n• {t}"
            keyboard.append([InlineKeyboardButton(f"📊 {t}", callback_data=f"channel_manage_{ch['channel_id']}")])

    list_text += "\n\n" + text  # Добавляем сообщение об успехе
    keyboard.append([InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_main_menu")])

    await query.edit_message_text(list_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return MY_CHANNELS


async def nav_my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Мои площадки'"""
    query = update.callback_query
    await query.answer()

    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    text = get_text('my_channels_title', context).format(count=len(channels))
    keyboard = []

    if not channels:
        text += get_text('my_channels_empty', context)
    else:
        for ch in channels:
            title = ch['channel_title'] or ch['channel_username'] or f"ID: {ch['channel_id']}"
            text += f"\n• {title}"
            keyboard.append([InlineKeyboardButton(f"📊 {title}", callback_data=f"channel_manage_{ch['channel_id']}")])

    text += get_text('my_channels_footer', context)
    # keyboard.append([InlineKeyboardButton("📌 Добавить чат/канал (ЗАГЛУШКА)", callback_data="channel_add_info")])
    keyboard.append([InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_main_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return MY_CHANNELS


async def nav_free_dates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Свободные даты' (НОВАЯ ЛОГИКА)"""
    query = update.callback_query
    await query.answer()

    user_id = context.user_data.get('user_id')
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')

    now_utc = datetime.now(ZoneInfo('UTC'))
    today_user = now_utc.astimezone(user_tz).date()

    # Даты для верхнего списка (текущий + следующий месяц, ~60 дней)
    start_date_free = today_user
    end_date_free = today_user + timedelta(days=60)

    # Даты для нижнего списка (30 дней)
    start_date_schedule = today_user
    end_date_schedule = today_user + timedelta(days=30)

    # --- 1. Верхняя часть (Свободные даты) ---

    scheduled_jobs_60d = db_query("""
        SELECT scheduled_time_utc 
        FROM publication_jobs 
        WHERE user_id = %s 
          AND status = 'scheduled' 
          AND scheduled_time_utc >= %s 
          AND scheduled_time_utc < %s
    """, (user_id, now_utc, end_date_free), fetchall=True)

    scheduled_dates_set = set()
    if scheduled_jobs_60d:
        for job in scheduled_jobs_60d:
            local_date = job['scheduled_time_utc'].astimezone(user_tz).date()
            scheduled_dates_set.add(local_date)

    all_dates_set = set()
    current_date = start_date_free
    while current_date < end_date_free:
        all_dates_set.add(current_date)
        current_date += timedelta(days=1)

    free_dates = sorted(list(all_dates_set - scheduled_dates_set))

    free_dates_str = ", ".join([d.strftime('%d/%m') for d in free_dates])
    if not free_dates_str:
        free_dates_str = get_text('free_dates_none_60d', context)

    text = get_text('free_dates_header', context).format(free_dates_str=free_dates_str)
    text += "--------------------\n"

    # --- 2. Нижняя часть (Задачи на 30 дней) ---

    text += get_text('free_dates_schedule_header_30d', context)

    jobs_30_days = db_query("""
        SELECT scheduled_time_utc, task_id, pin_duration 
        FROM publication_jobs 
        WHERE user_id = %s 
          AND status = 'scheduled' 
          AND scheduled_time_utc >= %s 
          AND scheduled_time_utc < %s 
        ORDER BY scheduled_time_utc
    """, (user_id, now_utc, end_date_schedule), fetchall=True)

    if not jobs_30_days:
        text += get_text('free_dates_schedule_empty_30d', context)
    else:
        grouped_jobs = {}
        for job in jobs_30_days:
            local_dt = job['scheduled_time_utc'].astimezone(user_tz)
            date_key = local_dt.date()

            time_str = local_dt.strftime('%H:%M')
            pin_str = "📌" if job['pin_duration'] > 0 else ""
            task_id = job['task_id']

            job_str = f"{time_str} ({pin_str}#{task_id})"

            if date_key not in grouped_jobs:
                grouped_jobs[date_key] = []
            grouped_jobs[date_key].append(job_str)

        for date_key in sorted(grouped_jobs.keys()):
            date_str = date_key.strftime('%d.%m.%Y')
            jobs_str = "; ".join(grouped_jobs[date_key])
            text += f"{date_str} {jobs_str}\n"

    await query.edit_message_text(
        text,
        reply_markup=back_to_main_menu_keyboard(context),
        parse_mode='Markdown'
    )
    return FREE_DATES


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


async def nav_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Отчеты'"""

    # --- ИСПРАВЛЕНИЕ ---
    # Обрабатываем и CallbackQuery, и Message
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
    else:
        message = update.message  # Это Message от ReplyKeyboard
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    text = get_text('reports_title', context)

    # Используем reply_text, чтобы он работал в обоих случаях
    await message.reply_text(
        text,
        reply_markup=back_to_main_menu_keyboard(context)
    )
    return REPORTS


async def nav_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает смену языка"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
        await message.reply_text(
            TEXTS['ru']['welcome_lang'],
            reply_markup=lang_keyboard()
        )
    else:
        await update.message.reply_text(
            TEXTS['ru']['welcome_lang'],
            reply_markup=lang_keyboard()
        )
    return START_SELECT_LANG


async def nav_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает смену таймзоны"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
        text = get_text('select_timezone', context)
        await message.reply_text(text, reply_markup=timezone_keyboard())
    else:
        text = get_text('select_timezone', context)
        await update.message.reply_text(text, reply_markup=timezone_keyboard())
    return START_SELECT_TZ


async def boss_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логи"""
    query = update.callback_query
    await query.answer("Функция просмотра логов в разработке")
    return BOSS_PANEL


# --- НОВАЯ ФУНКЦИЯ-СТАБ ---
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

    keyboard = [
        [InlineKeyboardButton(get_text('boss_signature_delete_btn', context), callback_data="boss_signature_delete")],
        [InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_SIGNATURE_EDIT


async def boss_signature_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение новой подписи"""
    signature = update.message.text.strip()

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

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


async def boss_signature_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление подписи"""
    query = update.callback_query
    await query.answer()

    db_query("""
        UPDATE bot_settings SET signature = NULL WHERE id = 1
    """, commit=True)

    text = get_text('boss_signature_deleted', context)
    keyboard = [[InlineKeyboardButton(get_text('boss_back_to_boss', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


async def nav_boss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает админ-панель"""
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer(get_text('boss_no_access', context))
        return MAIN_MENU

    text = get_text('boss_menu_title', context)
    text += "\n\n" + get_text('boss_quick_stats', context) + "\n"

    stats = get_bot_statistics()
    text += get_text('boss_total_users', context).format(total_users=stats['total_users']) + "\n"
    text += get_text('boss_active_users', context).format(active_users=stats['active_users']) + "\n"
    text += get_text('boss_active_tasks', context).format(tasks_active=stats['tasks_active']) + "\n"

    await query.edit_message_text(
        text,
        reply_markup=boss_panel_keyboard(context)
    )
    return BOSS_PANEL


# --- 3. Конструктор Задач ---

def escape_markdown(text: str) -> str:
    """Escape special Markdown characters"""
    if not text:
        return text
    # Escape Markdown special characters
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    return text


async def calendar_weekday_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор дня недели в календаре с проверкой лимитов"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    try:
        weekday = int(query.data.replace("calendar_wd_", ""))
    except ValueError:
        return CALENDAR_VIEW

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)

    # Сброс дат
    db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_date IS NOT NULL",
             (task_id,), commit=True)

    schedules = get_task_schedules(task_id)
    selected_weekdays = list(set([s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]))

    if weekday in selected_weekdays:
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_weekday = %s",
                 (task_id, weekday), commit=True)
        selected_weekdays.remove(weekday)
        if not selected_weekdays:
            remove_task_schedules(task_id)
    else:
        # --- CHECK WEEKDAY LIMITS ---
        # Using date_slots as a proxy for max weekdays if not explicitly defined, capped at 7
        max_weekdays = limits.get('date_slots', 7)
        if max_weekdays > 7: max_weekdays = 7

        if len(selected_weekdays) >= max_weekdays:
            alert_text = get_text('limit_error_weekdays', context).format(
                current=len(selected_weekdays),
                max=max_weekdays,
                tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=True)
            return CALENDAR_VIEW
        # --- END CHECK ---

        times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

        if times:
            for time_str in times:
                add_task_schedule(task_id, 'weekday_and_time', schedule_weekday=weekday, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'weekday', schedule_weekday=weekday)

        selected_weekdays.append(weekday)

    # UI Refresh Logic
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')
    today_user = datetime.now(user_tz).date()

    year = context.user_data.get('calendar_year', today_user.year)
    month = context.user_data.get('calendar_month', today_user.month)
    selected_dates = []

    header_text = ""
    if selected_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_str = ", ".join(
                sorted([wd_names[day] for day in selected_weekdays], key=lambda x: wd_names.index(x)))
            header_text = get_text('calendar_header_weekdays', context).format(weekdays_str=weekdays_str)
        except (IndexError, AttributeError):
            header_text = get_text('calendar_header_weekdays', context).format(
                weekdays_str=f"{len(selected_weekdays)} days")

    text = header_text
    text += get_text('calendar_info_weekdays', context)
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=limits['date_slots'],
                                                                  tariff_name=limits['name'])
    text += get_text('calendar_weekdays_note', context)

    await query.edit_message_text(
        text,
        reply_markup=calendar_keyboard(context, year, month, selected_dates, selected_weekdays, today_user),
        parse_mode='Markdown'
    )
    return await task_select_calendar(update, context)


def get_task_constructor_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Form text for task constructor with Dynamic Traffic Light Status"""
    task_id = context.user_data.get('current_task_id')
    if not task_id:
        return get_text('error_task_id_not_found', context)

    task = get_task_details(task_id)
    if not task:
        return get_text('error_task_not_found_db', context).format(task_id=task_id)

    # Get channels
    channels_ids = get_task_channels(task_id)
    channels_count = len(channels_ids)

    # Suffixes
    count_suffix = get_text('status_count_suffix', context)
    days_suffix = get_text('status_days_suffix', context)
    hours_suffix = get_text('status_hours_suffix', context)
    hours_suffix_short = get_text('status_hours_suffix_short', context)

    # --- DETERMINE STATUS (Traffic Light Logic) ---
    status_label = get_text('task_status_label', context)
    status_icon = determine_task_status_color(task_id)

    if status_icon == '🟢':
        status_val = f"🟢 {get_text('status_text_active', context)}"
    elif status_icon == '🟡':
        status_val = f"🟡 {get_text('status_text_finishing', context)}"
    else:
        status_val = f"🔴 {get_text('status_text_inactive', context)}"
    # ---------------------------------------------

    display_name = task['task_name'] if task['task_name'] else get_text('task_default_name', context)

    # Schedules
    schedules = get_task_schedules(task_id)
    dates_text = get_text('status_not_selected', context)
    weekdays_text = get_text('status_not_selected', context)

    unique_dates = sorted(list(set([s['schedule_date'] for s in schedules if s['schedule_date']])))
    unique_weekdays = sorted(list(set([s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None])))

    if unique_dates:
        if len(unique_dates) > 5:
            dates_text = get_text('status_dates_count', context).format(count=len(unique_dates), suffix=count_suffix)
        else:
            dates_text = "✅ " + ", ".join([d.strftime('%d.%m') for d in unique_dates])
    elif unique_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_text = "✅ " + ", ".join([wd_names[day] for day in unique_weekdays])
        except:
            weekdays_text = get_text('status_weekdays_count', context).format(count=len(unique_weekdays), suffix=days_suffix)

    times_text = get_text('status_not_selected', context)
    unique_times = sorted(list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']])))

    if unique_times:
        if len(unique_times) > 5:
            times_text = get_text('status_times_count', context).format(count=len(unique_times), suffix=count_suffix)
        else:
            times_text = "✅ " + ", ".join(unique_times)

    # Advertiser
    advertiser_text = get_text('status_not_set', context)
    if task['advertiser_user_id']:
        advertiser_user = db_query("SELECT username FROM users WHERE user_id = %s", (task['advertiser_user_id'],), fetchone=True)
        if advertiser_user and advertiser_user.get('username'):
            advertiser_text = f"✅ @{advertiser_user['username']}"
        else:
            advertiser_text = get_text('status_advertiser_id', context).format(advertiser_user_id=task['advertiser_user_id'])

    if task['task_name']:
        task_name = task['task_name']
    else:
        task_name = get_text('task_default_name', context)

    pin_text = get_text('status_no', context)
    if task['pin_duration'] > 0:
        pin_text = get_text('status_pin_duration', context).format(duration=task['pin_duration'], suffix=hours_suffix)

    delete_text = get_text('status_no', context)
    if task['auto_delete_hours'] > 0:
        delete_text = get_text('status_delete_duration', context).format(duration=task['auto_delete_hours'], suffix=hours_suffix_short)

    status_yes = get_text('status_yes', context)
    status_no = get_text('status_no', context)

    pin_notify_status = status_yes if task['pin_notify'] else status_no
    report_status = status_yes if task['report_enabled'] else status_no
    post_type_status = get_text('status_from_bot', context) if task['post_type'] == 'from_bot' else get_text('status_repost', context)

    channels_status = get_text('status_dates_count', context).format(count=channels_count, suffix=count_suffix) if channels_count > 0 else get_text('status_not_selected', context)
    message_status = get_text('status_set', context) if task['content_message_id'] else get_text('status_not_set', context)

    title = get_text('task_constructor_title', context)
    text = f"{title}\n\n"
    text += f"**{status_label}{status_val}**\n\n" # Dynamic Status
    text += f"{task_name}\n"
    text += f"{get_text('header_channels', context)}{channels_status}\n"
    text += f"{get_text('header_message', context)}{message_status}\n"

    if unique_dates:
        text += f"{get_text('header_date', context)}{dates_text}\n"
    else:
        text += f"{get_text('header_weekdays', context)}{weekdays_text}\n"

    text += f"{get_text('header_time', context)}{times_text}\n"
    text += f"{get_text('header_pin', context)}{pin_text}\n"
    text += f"{get_text('header_autodelete', context)}{delete_text}\n"
    text += f"{get_text('header_post_type', context)}{post_type_status}\n"
    text += f"{get_text('header_pin_notify', context)}{pin_notify_status}\n"
    text += f"{get_text('header_report', context)}{report_status}\n"
    text += f"{get_text('header_advertiser', context)}{advertiser_text}\n"

    return text


async def show_task_constructor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает главный экран конструктора задач."""
    text = get_task_constructor_text(context)
    await send_or_edit_message(update, text, task_constructor_keyboard(context))
    return TASK_CONSTRUCTOR


async def task_constructor_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Точка входа: Просто очищаем ID текущей задачи.
    Задача будет создана в БД только при первом изменении параметра.
    """
    query = update.callback_query
    if query:
        await query.answer()

    # Очищаем ID, чтобы система знала, что мы в режиме "Новая задача"
    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    return await show_task_constructor(update, context)


def ensure_task_and_refresh(user_id: int, context: ContextTypes.DEFAULT_TYPE, auto_activate: bool = False) -> int:
    """
    Creates a task in DB if it doesn't exist (Lazy Creation).
    Updates status to 'active' if required.
    Triggers Hot-Reload of the scheduler.
    """
    task_id = get_or_create_task_id(user_id, context)

    if auto_activate:
        # If adding a time/date, we assume the user wants it active
        update_task_field(task_id, 'status', 'active')

    # Hot-reload: Cancel old jobs and reschedule based on new params immediately
    refresh_task_jobs(task_id, context)

    return task_id


async def task_edit_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точка входа в 'Редактировать задачу' (из 'Мои задачи')"""
    query = update.callback_query
    await query.answer()

    task_id = int(query.data.replace("task_edit_", ""))
    context.user_data['current_task_id'] = task_id

    return await show_task_constructor(update, context)


async def task_back_to_constructor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка '⬅️ Назад' (возврат в конструктор)"""
    query = update.callback_query
    await query.answer()
    return await show_task_constructor(update, context)


# --- Установка Названия ---
async def task_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📝 Название задачи'"""
    query = update.callback_query
    await query.answer()

    text = get_text('task_ask_name', context)
    await query.edit_message_text(
        text,
        reply_markup=back_to_constructor_keyboard(context)
    )
    return TASK_SET_NAME


async def task_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    # Создаем задачу, если её нет, и обновляем планировщик
    task_id = ensure_task_and_refresh(user_id, context, auto_activate=False)

    task_name = update.message.text.strip()
    update_task_field(task_id, 'task_name', task_name)

    # Повторный рефреш, так как имя поменялось (нужно для логов джобов)
    refresh_task_jobs(task_id, context)

    await update.message.reply_text(get_text('task_name_saved', context))
    return await show_task_constructor(update, context)


# --- Установка Сообщения ---
async def task_ask_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📝 Сообщение'"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    if task and task['content_message_id']:
        # Сообщение УЖЕ установлено. Показываем его.
        text = get_text('task_message_current_prompt', context)

        keyboard = [
            [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
            [
                InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
                InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
            ]
        ]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        try:
            # Копируем сообщение, чтобы юзер его увидел
            await context.bot.copy_message(
                chat_id=query.message.chat_id,
                from_chat_id=task['content_chat_id'],
                message_id=task['content_message_id']
            )
        except Exception as e:
            logger.warning(f"Не удалось скопировать старое сообщение для task {task_id}: {e}")
            await query.message.reply_text(get_text('task_message_display_error', context))

        return TASK_SET_MESSAGE  # Остаемся в том же состоянии, т.к. MessageHandler его поймает

    else:
        # Сообщение НЕ установлено. Просим его.
        text = get_text('task_ask_message', context)
        await query.edit_message_text(
            text,
            reply_markup=back_to_constructor_keyboard(context)
        )
        return TASK_SET_MESSAGE


async def task_delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Удаление сохраненного сообщения"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await query.edit_message_text(get_text('error_generic', context))
        return await show_task_constructor(update, context)  # Failsafe

    update_task_field(task_id, 'content_message_id', None)
    update_task_field(task_id, 'content_chat_id', None)

    await query.answer(get_text('task_message_deleted_alert', context), show_alert=True)

    # Возвращаемся в конструктор
    return await show_task_constructor(update, context)


async def task_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive message, create task if lazy, hot-reload scheduler"""
    user_id = update.effective_user.id

    # Create task if it doesn't exist.
    # auto_activate=False: Changing content shouldn't inherently make it active/green unless it already was.
    task_id = ensure_task_and_refresh(user_id, context, auto_activate=False)

    # Save ID and chat_id
    content_message_id = update.message.message_id
    content_chat_id = update.message.chat_id

    update_task_field(task_id, 'content_message_id', content_message_id)
    update_task_field(task_id, 'content_chat_id', content_chat_id)

    # --- Auto-Generate Name ---
    task = get_task_details(task_id)
    if not task['task_name']:
        msg_text = update.message.text or update.message.caption or "Media"
        smart_name = generate_smart_name(msg_text, context, limit=3)
        update_task_field(task_id, 'task_name', smart_name)
        await update.message.reply_text(get_text('task_message_saved', context) + f"\n📝 Auto-name: {smart_name}")
    else:
        await update.message.reply_text(get_text('task_message_saved', context))

    # HOT RELOAD: Ensure scheduler knows about the new message ID
    refresh_task_jobs(task_id, context)

    return await show_task_constructor(update, context)


# --- Выбор Каналов ---
async def task_select_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📢 Каналы'"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    selected_channels = get_task_channels(task_id)

    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    if not channels:
        await query.edit_message_text(
            "У вас нет добавленных каналов. Сначала добавьте бота администратором в канал.",
            reply_markup=back_to_constructor_keyboard(context)
        )
        return TASK_SELECT_CHANNELS

    text = "📢 Выберите каналы для публикации:\n(Нажмите на канал чтобы выбрать/отменить)"
    await query.edit_message_text(
        text,
        reply_markup=channels_selection_keyboard(context, selected_channels)
    )
    return TASK_SELECT_CHANNELS


async def task_toggle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение выбора канала"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    channel_id = int(query.data.replace("channel_toggle_", ""))

    selected_channels = get_task_channels(task_id)

    if channel_id in selected_channels:
        remove_task_channel(task_id, channel_id)
    else:
        add_task_channel(task_id, channel_id)

    # Обновляем клавиатуру
    selected_channels = get_task_channels(task_id)
    text = "📢 Выберите каналы для публикации:\n(Нажмите на канал чтобы выбрать/отменить)"
    await query.edit_message_text(
        text,
        reply_markup=channels_selection_keyboard(context, selected_channels)
    )
    return TASK_SELECT_CHANNELS


# --- Календарь ---
async def task_select_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📅 Календарь' (Refreshes the view)"""
    query = update.callback_query
    # We do NOT call query.answer() here if it was called in the parent function (like calendar_day_select)
    # But if called directly from menu, we need it.
    # To be safe, we try-catch answer or check if it's a fresh call.
    try:
        await query.answer()
    except:
        pass

    task_id = context.user_data.get('current_task_id')
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')

    # Получаем "сегодня" в таймзоне юзера
    today_user = datetime.now(user_tz).date()

    # Получаем лимиты тарифа
    limits = get_tariff_limits(user_tariff)
    max_time_slots = limits['date_slots']

    # Получаем выбранные даты и дни недели из БД
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]
    selected_weekdays = [s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]  # 0-6

    # Устанавливаем текущий месяц
    if 'calendar_year' not in context.user_data:
        now = datetime.now(user_tz)
        context.user_data['calendar_year'] = now.year
        context.user_data['calendar_month'] = now.month

    year = context.user_data['calendar_year']
    month = context.user_data['calendar_month']

    # --- Формирование шапки ---
    header_text = ""
    if selected_dates:
        dates_str = ", ".join(sorted([datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in selected_dates]))
        month_year_str = datetime(year, month, 1).strftime("%B %Y")
        header_text = get_text('calendar_header_dates', context).format(month_year_str=month_year_str,
                                                                        dates_str=dates_str)

    elif selected_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_str = ", ".join(
                sorted([wd_names[day] for day in selected_weekdays], key=lambda x: wd_names.index(x)))
            header_text = get_text('calendar_header_weekdays', context).format(weekdays_str=weekdays_str)
        except (IndexError, AttributeError):
            logger.warning(f"Error parsing calendar_weekdays_short for task {task_id}")
            header_text = get_text('calendar_header_weekdays', context).format(
                weekdays_str=f"{len(selected_weekdays)} days")

    text = header_text  # Шапка (или пусто)

    # Добавляем инфо-текст
    text += get_text('calendar_info_weekdays', context)
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots,
                                                                  tariff_name=limits['name'])
    text += get_text('calendar_weekdays_note', context)  # Пн Вт Ср...

    # --- ERROR HANDLING FIX ---
    try:
        await query.edit_message_text(
            text,
            reply_markup=calendar_keyboard(context, year, month, selected_dates, selected_weekdays, today_user),
            parse_mode='Markdown'
        )
    except TelegramError as e:
        # Ignore "Message is not modified" errors
        if "Message is not modified" not in str(e):
            logger.warning(f"Error updating calendar view: {e}")
            # Optionally try to send a new message if edit failed due to age
            # await query.message.reply_text(text, reply_markup=...)

    return CALENDAR_VIEW


async def calendar_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по месяцам в календаре"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')

    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')

    today_user = datetime.now(user_tz).date()

    limits = get_tariff_limits(user_tariff)
    max_time_slots = limits['date_slots']

    action = query.data

    year = context.user_data.get('calendar_year', datetime.now(user_tz).year)
    month = context.user_data.get('calendar_month', datetime.now(user_tz).month)

    if action == "calendar_prev":
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    elif action == "calendar_next":
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    context.user_data['calendar_year'] = year
    context.user_data['calendar_month'] = month

    # Получаем выбранные даты и дни недели из БД
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]
    selected_weekdays = [s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]

    # --- Формирование шапки ---
    header_text = ""
    if selected_dates:
        dates_str = ", ".join(sorted([datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in selected_dates]))
        month_year_str = datetime(year, month, 1).strftime("%B %Y")
        header_text = get_text('calendar_header_dates', context).format(month_year_str=month_year_str,
                                                                        dates_str=dates_str)

    elif selected_weekdays:
        try:
            wd_names_str = get_text('calendar_weekdays_short', context)
            wd_names = wd_names_str.split(',')
            weekdays_str = ", ".join(
                sorted([wd_names[day] for day in selected_weekdays], key=lambda x: wd_names.index(x)))
            header_text = get_text('calendar_header_weekdays', context).format(weekdays_str=weekdays_str)
        except (IndexError, AttributeError):
            logger.warning(f"Error parsing calendar_weekdays_short for task {task_id}")
            header_text = get_text('calendar_header_weekdays', context).format(
                weekdays_str=f"{len(selected_weekdays)} days")

    text = header_text  # Шапка (или пусто)

    # Добавляем инфо-текст
    text += get_text('calendar_info_weekdays', context)
    # --- ⬇️ FIXED LINE ⬇️ ---
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots, tariff_name=limits['name'])
    # --- ⬆️ FIXED LINE ⬆️ ---
    text += get_text('calendar_weekdays_note', context)  # Пн Вт Ср...

    try:
        await query.edit_message_text(
            text,
            reply_markup=calendar_keyboard(context, year, month, selected_dates, selected_weekdays, today_user),
            parse_mode='Markdown'
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Error in calendar navigation: {e}")
    return CALENDAR_VIEW

async def calendar_ignore_past(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатие на прошедшую дату в календаре"""
    query = update.callback_query
    await query.answer("Эта дата уже прошла и недоступна для выбора.", show_alert=True)
    return CALENDAR_VIEW


async def calendar_day_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select day in calendar with hot-reload and auto-activation"""
    query = update.callback_query
    user_id = query.from_user.id

    # AUTO ACTIVATE: Adding a date implies intent to schedule.
    task_id = ensure_task_and_refresh(user_id, context, auto_activate=True)

    date_str = query.data.replace("calendar_day_", "")

    db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_weekday IS NOT NULL",
             (task_id,), commit=True)

    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_dates = limits['date_slots']

    if date_str in selected_dates:
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_date = %s",
                 (task_id, date_str), commit=True)
        await query.answer()
    else:
        if len(selected_dates) >= max_dates:
            alert_text = get_text('limit_error_dates', context).format(
                current=len(selected_dates), max=max_dates, tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=False)
            return CALENDAR_VIEW

        times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

        if times:
            for time_str in times:
                add_task_schedule(task_id, 'datetime', schedule_date=date_str, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'date', schedule_date=date_str)

        await query.answer()

    # HOT RELOAD: Apply changes to scheduler immediately
    refresh_task_jobs(task_id, context)

    # --- Update Calendar UI ---
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')
    today_user = datetime.now(user_tz).date()

    year = context.user_data.get('calendar_year', today_user.year)
    month = context.user_data.get('calendar_month', today_user.month)

    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]
    selected_weekdays = []

    header_text = ""
    if selected_dates:
        dates_str = ", ".join(sorted([datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in selected_dates]))
        month_year_str = datetime(year, month, 1).strftime("%B %Y")
        header_text = get_text('calendar_header_dates', context).format(month_year_str=month_year_str,
                                                                        dates_str=dates_str)

    text = header_text
    text += get_text('calendar_info_weekdays', context)
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_dates, tariff_name=limits['name'])
    text += get_text('calendar_weekdays_note', context)

    await query.edit_message_text(
        text,
        reply_markup=calendar_keyboard(context, year, month, selected_dates, selected_weekdays, today_user),
        parse_mode='Markdown'
    )
    return await task_select_calendar(update, context)


async def calendar_select_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор всего месяца"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    year = context.user_data.get('calendar_year', datetime.now().year)
    month = context.user_data.get('calendar_month', datetime.now().month)

    # Удаляем старые расписания
    remove_task_schedules(task_id)

    # Добавляем все дни месяца
    _, num_days = calendar.monthrange(year, month)
    for day in range(1, num_days + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        add_task_schedule(task_id, 'date', schedule_date=date_str)

    # Обновляем календарь
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]

    month_year = datetime(year, month, 1).strftime("%B %Y")
    text = get_text('calendar_title', context).format(month_year=month_year)
    text += f"\n{get_text('calendar_selected_dates', context).format(count=len(selected_dates))}"
    text += f"\n{get_text('calendar_weekdays_note', context)}"

    await query.edit_message_text(
        text,
        reply_markup=calendar_keyboard(context, year, month, selected_dates)
    )
    return CALENDAR_VIEW


async def calendar_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс выбранных дат"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    remove_task_schedules(task_id)

    # --- Обновляем календарь (Копи-паст из task_select_calendar) ---
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo('UTC')
    today_user = datetime.now(user_tz).date()

    limits = get_tariff_limits(user_tariff)
    max_time_slots = limits['date_slots']

    year = context.user_data.get('calendar_year', today_user.year)
    month = context.user_data.get('calendar_month', today_user.month)

    text = ""  # Шапка пустая
    text += get_text('calendar_info_weekdays', context)
    # --- ⬇️ FIXED LINE ⬇️ ---
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots, tariff_name=limits['name'])
    # --- ⬆️ FIXED LINE ⬆️ ---
    text += get_text('calendar_weekdays_note', context)

    try:
        await query.edit_message_text(
            text,
            reply_markup=calendar_keyboard(context, year, month, [], [], today_user),
            parse_mode='Markdown'
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.warning(f"Error in calendar reset: {e}")
    return CALENDAR_VIEW


# --- Выбор времени ---
async def task_select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '🕐 Время' (Исправлена ошибка с таймзоной)"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    # Получаем выбранное время из БД
    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    # --- ИСПРАВЛЕНИЕ ТАЙМЗОНЫ ---
    # 1. Получаем название таймзоны (строка)
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')

    # 2. Создаем объект таймзоны
    try:
        user_tz_obj = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz_obj = ZoneInfo('UTC')
        user_tz_str = 'UTC (Default)'  # Fallback для отображения

    # 3. Вычисляем текущее время, используя ОБЪЕКТ
    current_time_str = datetime.now(user_tz_obj).strftime('%H:%M')
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    # Лимиты тарифа
    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    # Формирование текста
    text = get_text('time_selection_title', context)
    # Здесь используем СТРОКУ для отображения названия
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz_str)}"
    # Здесь выводим вычисленное время
    text += f"\n🕒 **{get_text('time_current_info', context).format(current_time=current_time_str)}**"

    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=len(selected_times), slots=max_slots)}"

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, selected_times)
    )
    return TIME_SELECTION


async def time_slot_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select time slot with hot-reload and auto-activation"""
    query = update.callback_query

    user_id = query.from_user.id
    # AUTO ACTIVATE: Adding time implies intent to schedule.
    # ensure_task_and_refresh creates the task AND refreshes jobs.
    task_id = ensure_task_and_refresh(user_id, context, auto_activate=True)

    time_str = query.data.replace("time_select_", "")

    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    # ... (Limits Check Logic remains the same) ...
    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    if time_str in selected_times:
        # Remove time
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_time = %s",
                 (task_id, time_str), commit=True)
        await query.answer()
    else:
        # Check limit
        if len(selected_times) >= max_slots:
            alert_text = get_text('limit_error_times', context).format(
                current=len(selected_times), max=max_slots, tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=False)
            return TIME_SELECTION

        # Logic to add time to existing dates or as standalone time
        # Remove old schedules to re-insert with new time matrix
        # (Simplification: Cleanest way is to read dates, clear all, re-insert all combinations)
        # For specific toggle implementation:
        dates = [s for s in schedules if s['schedule_date']]

        # Remove previous specific entries to avoid duplicates if we are re-adding
        # But here we are adding a NEW time.

        if dates:
            unique_dates_data = {d['schedule_date'] for d in dates}
            for date_val in unique_dates_data:
                add_task_schedule(task_id, 'datetime', schedule_date=date_val, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'time', schedule_time=time_str)

        await query.answer()

    # HOT RELOAD: Apply changes to scheduler immediately
    refresh_task_jobs(task_id, context)

    # Update UI
    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    user_tz = context.user_data.get('timezone', 'Europe/Moscow')
    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz)}"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=len(selected_times), slots=max_slots)}"

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, selected_times)
    )
    return TIME_SELECTION


def create_publication_jobs_for_task(task_id: int, user_tz: str, application: Application) -> int:
    """
    Создает publication_jobs и планирует их с помощью application.job_queue.
    Возвращает количество успешно созданных задач.
    """
    task = get_task_details(task_id)
    if not task:
        logger.error(f"Task {task_id} не найдена в create_publication_jobs_for_task")
        return 0

    schedules = get_task_schedules(task_id)
    channels = get_task_channels(task_id)

    if not schedules or not channels:
        logger.error(f"Нет расписания или каналов для задачи {task_id}")
        return 0

    try:
        tz = ZoneInfo(user_tz)
    except ZoneInfoNotFoundError:
        logger.warning(f"Неверная таймзона {user_tz} для user {task['user_id']}. Используется UTC.")
        tz = ZoneInfo('UTC')

    job_count = 0
    now_utc = datetime.now(ZoneInfo('UTC'))

    for schedule in schedules:
        # Пропускаем, если не установлено время
        if not schedule['schedule_time']:
            continue

        # Если дата не установлена, используем сегодняшнюю дату в таймзоне юзера
        schedule_date = schedule['schedule_date']
        if not schedule_date:
            schedule_date = datetime.now(tz).date()

        schedule_time = schedule['schedule_time']

        # Комбинируем дату и время
        try:
            naive_dt = datetime.combine(schedule_date, schedule_time)
            # Привязываем таймзону пользователя
            local_dt = naive_dt.replace(tzinfo=tz)
        except Exception as e:
            logger.error(f"Ошибка комбинирования datetime для задачи {task_id}: {schedule_date} {schedule_time} с tz {user_tz}. Ошибка: {e}")
            continue

        # Конвертируем в UTC
        utc_dt = local_dt.astimezone(ZoneInfo('UTC'))

        # Пропускаем задачи в прошлом
        if utc_dt < now_utc:
            logger.warning(f"Пропуск задачи в прошлом для task {task_id} в {utc_dt} (сейчас {now_utc})")
            continue

        # Создаем задачи на публикацию для каждого канала
        for channel_id in channels:
            job_data = db_query("""
                INSERT INTO publication_jobs (
                    task_id, user_id, channel_id, scheduled_time_utc,
                    content_message_id, content_chat_id, pin_duration,
                    pin_notify, auto_delete_hours, advertiser_user_id, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')
                RETURNING id
            """, (
                task_id, task['user_id'], channel_id, utc_dt,
                task['content_message_id'], task['content_chat_id'],
                task['pin_duration'], task['pin_notify'],
                task['auto_delete_hours'], task['advertiser_user_id']
            ), commit=True)

            if job_data and 'id' in job_data:
                job_id = job_data['id']
                job_name = f"pub_{job_id}"

                # Планируем через application.job_queue
                try:
                    # ***** ИЗМЕНЕНИЕ ЗДЕСЬ *****
                    # Было: application.job_queue.add_job(
                    #           trigger=DateTrigger(run_date=utc_dt),
                    #           kwargs={'job_id': job_id}, ...
                    #       )
                    # Стало:
                    application.job_queue.run_once(
                        execute_publication_job,
                        when=utc_dt,
                        data={'job_id': job_id},
                        name=job_name,
                        job_kwargs={'misfire_grace_time': 300}  # 5 минут
                    )
                    # ***** КОНЕЦ ИЗМЕНЕНИЯ *****

                    # Обновляем aps_job_id
                    db_query(
                        "UPDATE publication_jobs SET aps_job_id = %s WHERE id = %s",
                        (job_name, job_id),
                        commit=True
                    )
                    job_count += 1
                    logger.info(f"Запланирована задача {job_id} на {utc_dt} (канал {channel_id})")

                except Exception as e:
                    logger.error(f"Не удалось запланировать задачу {job_id} через job_queue: {e}", exc_info=True)
                    db_query(
                        "UPDATE publication_jobs SET status = 'failed' WHERE id = %s",
                        (job_id,),
                        commit=True
                    )
            else:
                logger.error(f"Не удалось вставить publication_job в БД для задачи {task_id}")

    return job_count


async def time_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос своего времени"""
    query = update.callback_query
    await query.answer()

    text = get_text('time_ask_custom', context)
    await query.edit_message_text(
        text,
        reply_markup=back_to_constructor_keyboard(context)
    )
    return TASK_SET_CUSTOM_TIME


async def time_receive_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение своего времени с проверкой лимитов"""
    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    time_str = update.message.text.strip()

    # Regex check
    time_pattern = re.compile(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$')
    if not time_pattern.match(time_str):
        await update.message.reply_text(get_text('time_invalid_format', context))
        return TASK_SET_CUSTOM_TIME

    hours, minutes = time_str.split(':')
    time_str = f"{int(hours):02d}:{int(minutes):02d}"

    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    if time_str not in selected_times:
        # --- CHECK TIME LIMITS ---
        if len(selected_times) >= max_slots:
            error_text = get_text('limit_error_times', context).format(
                current=len(selected_times),
                max=max_slots,
                tariff=limits['name']
            )
            await update.message.reply_text(error_text)
            return TASK_SET_CUSTOM_TIME
        # --- END CHECK ---

        dates = [s for s in schedules if s['schedule_date']]
        if dates:
            unique_dates_data = {d['schedule_date'] for d in dates}
            for date_val in unique_dates_data:
                add_task_schedule(task_id, 'datetime', schedule_date=date_val, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'time', schedule_time=time_str)

    await update.message.reply_text(get_text('time_saved', context))
    return await show_task_constructor(update, context)


async def time_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all selected times"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    # FIXED: Keep dates but remove times
    schedules = get_task_schedules(task_id)
    dates = [s['schedule_date'] for s in schedules if s['schedule_date']]

    remove_task_schedules(task_id)

    # Re-add dates without times
    for date in set(dates):  # Use set to avoid duplicates
        add_task_schedule(task_id, 'date', schedule_date=date)

    db_query("UPDATE task_schedules SET schedule_time = NULL WHERE task_id = %s",
             (task_id,), commit=True)


    # --- ИСПРАВЛЕНИЕ ЛОГИКИ ЛИМИТОВ ---
    user_tz = context.user_data.get('timezone', 'Europe/Moscow')
    user_tariff = context.user_data.get('tariff', 'free')

    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz)}"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=0, slots=max_slots)}"
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, [])
    )
    return TIME_SELECTION


# --- Настройка закрепления ---
async def task_set_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка закрепления"""
    query = update.callback_query
    await query.answer()
    text = get_text('duration_ask_pin', context) # Localized
    await query.edit_message_text(
        text,
        reply_markup=pin_duration_keyboard(context)
    )
    return TASK_SET_PIN


async def pin_duration_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор длительности закрепления"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    duration = int(query.data.replace("pin_", ""))

    update_task_field(task_id, 'pin_duration', duration)

    await query.answer(get_text('task_pin_saved', context))
    return await show_task_constructor(update, context)


# --- Настройка автоудаления ---
async def task_set_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка автоудаления"""
    query = update.callback_query
    await query.answer()
    text = get_text('duration_ask_delete', context) # Localized
    await query.edit_message_text(
        text,
        reply_markup=delete_duration_keyboard(context)
    )
    return TASK_SET_DELETE


async def delete_duration_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор длительности автоудаления"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    duration = int(query.data.replace("delete_", ""))

    update_task_field(task_id, 'auto_delete_hours', duration)

    await query.answer(get_text('task_delete_saved', context))
    return await show_task_constructor(update, context)


# --- Настройка рекламодателя ---
async def task_set_advertiser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка рекламодателя"""
    query = update.callback_query
    await query.answer()

    text = get_text('task_ask_advertiser', context)
    await query.edit_message_text(
        text,
        reply_markup=back_to_constructor_keyboard(context)
    )
    return TASK_SET_ADVERTISER


async def task_receive_advertiser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение username рекламодателя"""
    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    username = update.message.text.strip()

    # Убираем @ если есть
    if username.startswith('@'):
        username = username[1:]

    # Ищем пользователя в БД
    advertiser_user = get_user_by_username(username)

    if not advertiser_user:
        await update.message.reply_text(get_text('task_advertiser_not_found', context))
        return TASK_SET_ADVERTISER

    # Сохраняем advertiser_user_id в задачу
    update_task_field(task_id, 'advertiser_user_id', advertiser_user['user_id'])

    # FIXED: Send confirmation without formatting issues
    confirmation = get_text('task_advertiser_saved', context) + "\n"
    confirmation += f"📢 Рекламодатель @{username} будет уведомлен о публикациях"

    await update.message.reply_text(confirmation)

    # Возвращаемся в конструктор
    return await show_task_constructor(update, context)


# --- Остальные настройки ---
async def task_set_pin_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пуш уведомление"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # Переключаем значение
    new_value = not task['pin_notify']
    update_task_field(task_id, 'pin_notify', new_value)

    status_text = get_text('status_yes', context) if new_value else get_text('status_no', context)
    alert_text = get_text('alert_pin_notify_status', context).format(status=status_text)
    await query.answer(alert_text)

    return await show_task_constructor(update, context)


async def task_set_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение отчета"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # Переключаем значение
    new_value = not task['report_enabled']
    update_task_field(task_id, 'report_enabled', new_value)

    status_text = get_text('status_yes', context) if new_value else get_text('status_no', context)
    alert_text = get_text('alert_report_status', context).format(status=status_text)
    await query.answer(alert_text)

    return await show_task_constructor(update, context)


async def task_set_post_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение типа поста"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # Переключаем между from_bot и repost
    new_value = 'repost' if task['post_type'] == 'from_bot' else 'from_bot'
    update_task_field(task_id, 'post_type', new_value)

    type_text = get_text('status_from_bot', context) if new_value == 'from_bot' else get_text('status_repost', context)
    alert_text = get_text('alert_post_type_status', context).format(status=type_text)
    await query.answer(alert_text)

    return await show_task_constructor(update, context)


async def task_delete_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Подтверждение удаления задачи"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await query.edit_message_text(get_text('error_generic', context))
        return await show_main_menu(update, context)  # Failsafe

    task = get_task_details(task_id)
    task_name = task.get('task_name') or get_text('task_default_name', context)

    # --- Отмена запланированных задач в JobQueue ---

    # 1. Отмена будущих ПУБЛИКАЦИЙ
    jobs_to_cancel = db_query(
        "SELECT aps_job_id FROM publication_jobs WHERE task_id = %s AND status = 'scheduled' AND aps_job_id IS NOT NULL",
        (task_id,),
        fetchall=True
    )
    if jobs_to_cancel:
        logger.info(f"Отмена {len(jobs_to_cancel)} запланированных публикаций для задачи {task_id}")
        for job_row in jobs_to_cancel:
            job_name = job_row.get('aps_job_id')
            if job_name:
                jobs = context.application.job_queue.get_jobs_by_name(job_name)
                if jobs:
                    jobs[0].schedule_removal()
                    logger.info(f"Удалена задача {job_name} из JobQueue")

    # 2. Отмена будущих АВТО-УДАЛЕНИЙ
    delete_jobs_to_cancel = db_query(
        "SELECT id, posted_message_id FROM publication_jobs WHERE task_id = %s AND status = 'published' AND auto_delete_hours > 0",
        (task_id,),
        fetchall=True
    )
    if delete_jobs_to_cancel:
        logger.info(f"Отмена {len(delete_jobs_to_cancel)} задач на авто-удаление для задачи {task_id}")
        for job_row in delete_jobs_to_cancel:
            job_name = f"del_{job_row['id']}_msg_{job_row['posted_message_id']}"
            jobs = context.application.job_queue.get_jobs_by_name(job_name)
            if jobs:
                jobs[0].schedule_removal()
                logger.info(f"Удалена задача {job_name} из JobQueue")

    # --- Очистка БД ---

    # 3. Сначала удаляем 'publication_jobs' (т.к. у 'tasks' нет ON DELETE CASCADE на них)
    db_query("DELETE FROM publication_jobs WHERE task_id = %s", (task_id,), commit=True)

    # 4. Теперь удаляем саму задачу (это каскадом удалит 'task_channels' и 'task_schedules')
    db_query("DELETE FROM tasks WHERE id = %s", (task_id,), commit=True)

    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    text = get_text('task_delete_success', context).format(name=escape_markdown(task_name), id=task_id)
    await query.edit_message_text(text)

    # Возвращаемся в главное меню
    return await show_main_menu(update, context)


async def task_delete_confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Отмена удаления задачи"""
    query = update.callback_query
    await query.answer()

    # Просто возвращаемся в конструктор
    return await show_task_constructor(update, context)


async def task_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Нажатие кнопки 'Удалить задачу' - запрос подтверждения"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        return await show_task_constructor(update, context)  # Failsafe

    task = get_task_details(task_id)
    task_name = task.get('task_name') or get_text('task_default_name', context)

    text = get_text('task_delete_confirm', context).format(name=escape_markdown(task_name), id=task_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text('status_yes', context), callback_data="task_delete_confirm_yes"),
            InlineKeyboardButton(get_text('status_no', context), callback_data="task_delete_confirm_no")
        ]
    ])

    # Use reply_text or edit_message_text based on context
    if query.message:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard)

    return TASK_DELETE_CONFIRM


async def task_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активация задачи: Валидация -> Очистка старых -> Создание новых -> Уведомление"""
    query = update.callback_query
    # Показываем спиннер на языке пользователя
    await query.answer(get_text('task_activating_spinner', context))

    task_id = context.user_data.get('current_task_id')

    # --- 1. Загрузка данных и Валидация ---
    task = get_task_details(task_id)
    if not task:
        await query.edit_message_text(
            get_text('task_not_found_error', context),
            reply_markup=back_to_main_menu_keyboard(context)
        )
        return MAIN_MENU

    errors = []

    # Проверка сообщения
    if not task['content_message_id']:
        errors.append(get_text('task_error_no_message', context))

    # Проверка каналов
    channels = get_task_channels(task_id)
    if not channels:
        errors.append(get_text('task_error_no_channels', context))

    # Проверка расписания
    schedules = get_task_schedules(task_id)
    # Проверяем, что есть расписания И в них есть ВРЕМЯ (так как дата без времени не сработает)
    has_time = any(s['schedule_time'] for s in schedules)
    if not schedules or not has_time:
        errors.append(get_text('task_error_no_schedule', context))

    # Если есть ошибки, показываем их и не активируем
    if errors:
        header = get_text('task_validation_header', context)
        error_text = f"{header}\n\n" + "\n".join(errors)

        await query.edit_message_text(
            error_text,
            reply_markup=back_to_constructor_keyboard(context)
        )
        return TASK_CONSTRUCTOR

    # --- 2. Активация ---

    # Обновляем статус в БД
    update_task_field(task_id, 'status', 'active')

    # ВАЖНО: Очищаем старые джобы перед созданием новых (на случай повторной активации)
    cancel_task_jobs(task_id, context)

    # Создаем новые задания (Jobs)
    user_tz = context.user_data.get('timezone', 'Europe/Moscow')

    try:
        # create_publication_jobs_for_task должна быть определена в вашем коде
        job_count = create_publication_jobs_for_task(task_id, user_tz, context.application)
        logger.info(f"Task {task_id} activated. Jobs created: {job_count}")

    except Exception as e:
        logger.error(f"Error creating publication jobs for task {task_id}: {e}", exc_info=True)
        error_msg = get_text('task_job_creation_error', context).format(error=str(e))
        await query.edit_message_text(
            error_msg,
            reply_markup=back_to_constructor_keyboard(context)
        )
        # Откатываем статус, если не удалось создать джобы
        update_task_field(task_id, 'status', 'inactive')
        return TASK_CONSTRUCTOR

    # --- 3. Уведомление рекламодателя (если есть) ---
    if task['advertiser_user_id']:
        try:
            # Генерируем или берем имя задачи
            task_name = task['task_name']
            if not task_name:
                # Пытаемся сгенерировать, если нет имени (используя вашу функцию generate_smart_name)
                # Если функции нет в скоупе, используем дефолт
                task_name = get_text('task_default_name', context)

            # Получаем настройки рекламодателя, чтобы отправить уведомление на ЕГО языке
            advertiser_settings = get_user_settings(task['advertiser_user_id'])
            adv_lang = advertiser_settings.get('language_code', 'en') if advertiser_settings else 'en'

            notify_text = get_text('task_advertiser_notify', context, lang=adv_lang).format(
                task_name=task_name
            )

            await context.bot.send_message(
                chat_id=task['advertiser_user_id'],
                text=notify_text
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить рекламодателя {task['advertiser_user_id']}: {e}")

    # --- 4. Финальный экран успеха ---
    success_text = get_text('task_activated_title', context).format(task_id=task_id) + "\n\n"
    success_text += get_text('task_activated_jobs_count', context).format(job_count=job_count) + "\n"
    success_text += get_text('task_activated_schedule_info', context)

    await query.edit_message_text(
        success_text,
        reply_markup=back_to_main_menu_keyboard(context)
    )

    # Очищаем текущий ID задачи из сессии, так как мы закончили
    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    return MAIN_MENU


async def task_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка задачи"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data['current_task_id']

    # 1. Статус Inactive
    update_task_field(task_id, 'status', 'inactive')

    # 2. Отмена джобов
    cancel_task_jobs(task_id, context)

    await query.answer(get_text('task_deactivated_success', context), show_alert=True)

    # Обновляем вид конструктора
    return await show_task_constructor(update, context)


# --- Админ-панель ---
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


async def boss_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список пользователей"""
    query = update.callback_query
    await query.answer()

    users = get_recent_users(100)

    text = get_text('boss_users_title', context) + "\n\n"

    for user in users:
        username = f"@{user['username']}" if user['username'] else get_text('boss_users_no_username', context)
        text += f"• {username} (ID: {user['user_id']}) - {user['tariff']}\n"

    text += get_text('boss_users_total_shown', context).format(count=len(users))

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


async def boss_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика бота"""
    query = update.callback_query
    await query.answer(get_text('boss_stats_loading', context))

    stats = get_bot_statistics()

    text = get_text('boss_stats_title', context) + "\n\n"
    text += get_text('boss_stats_total_users', context).format(total_users=stats['total_users']) + "\n"
    text += get_text('boss_stats_active_users', context).format(active_users=stats['active_users']) + "\n"
    text += get_text('boss_stats_tasks_today', context).format(tasks_today=stats['tasks_today']) + "\n"
    text += get_text('boss_stats_tasks_active', context).format(tasks_active=stats['tasks_active']) + "\n"
    text += get_text('boss_stats_tasks_completed', context).format(tasks_completed=stats['tasks_completed']) + "\n"
    text += get_text('boss_stats_tasks_total', context).format(tasks_total=stats['tasks_total']) + "\n\n"
    text += get_text('boss_stats_users_30d', context).format(users_30d=stats['users_30d']) + "\n"
    text += get_text('boss_stats_users_60d', context).format(users_60d=stats['users_60d']) + "\n\n"
    text += get_text('boss_stats_db_size', context).format(db_size=stats['db_size'])

    if stats['db_size'] and 'MB' in stats['db_size']:
        try:
            size_mb = float(stats['db_size'].split()[0])
            if size_mb > 100:
                text += get_text('boss_stats_db_warning', context)
        except:
            pass

    keyboard = [[InlineKeyboardButton(get_text('boss_stats_refresh', context), callback_data="boss_stats")],
                [InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


# async def boss_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Лимиты"""
#     query = update.callback_query
#     await query.answer("Функция управления лимитами в разработке")
#     return BOSS_PANEL
#
#
# async def boss_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Тарифы"""
#     query = update.callback_query
#     await query.answer("Функция управления тарифами в разработке")
#     return BOSS_PANEL


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


async def boss_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика по доходам"""
    query = update.callback_query
    await query.answer()

    stats = get_money_statistics()

    text = get_text('boss_money_title', context) + "\n\n"
    text += get_text('boss_money_tariff_title', context) + "\n"

    for tariff, count in stats['by_tariff'].items():
        limits = get_tariff_limits(tariff)
        text += get_text('boss_money_tariff_item', context).format(name=limits['name'], count=count,
                                                                   price=limits['price']) + "\n"

    text += get_text('boss_money_estimated_revenue', context).format(revenue=stats['estimated_revenue'])
    text += get_text('boss_money_note', context)

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


async def boss_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Критические ошибки"""
    query = update.callback_query
    await query.answer()

    logs = get_critical_logs(50)

    text = get_text('boss_logs_title', context) + "\n\n"

    if not logs:
        text += get_text('boss_logs_no_errors', context)
        text += get_text('boss_logs_info', context)
    else:
        for log in logs:
            text += f"• {log}\n"

    keyboard = [[InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL


# --- 4. Отмена и ошибки ---

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Общая команда отмены. Возвращает в Главное меню."""
    query = update.callback_query
    user_id = update.effective_user.id

    text = get_text('cancel', context)

    if query:
        await query.answer()
        await query.edit_message_text(text)
    else:
        await update.message.reply_text(text)

    context.user_data.clear()
    await load_user_settings(user_id, context)

    await update.effective_chat.send_message(
        get_text('main_menu', context),
        reply_markup=main_menu_keyboard(context)
    )

    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логирование ошибок"""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


# --- 5. Фоновые задачи (Исполнение) ---

async def execute_delete_job(context: ContextTypes.DEFAULT_TYPE):
    """
    ИСПОЛНИТЕЛЬ: Удаляет сообщение и обновляет статус в БД,
    чтобы задача перестала быть 'Желтой', если это был последний пост.
    """
    bot = context.bot
    channel_id = context.job.data.get('channel_id')
    message_id = context.job.data.get('message_id')
    job_id = context.job.data.get('job_id')  # ID записи в publication_jobs

    if not channel_id or not message_id:
        return

    try:
        await bot.delete_message(chat_id=channel_id, message_id=message_id)
        logger.info(f"Удаление успешно: {message_id} из {channel_id}")

        # --- ОБНОВЛЕНИЕ СТАТУСА В БД ---
        if job_id:
            db_query("UPDATE publication_jobs SET status = 'deleted' WHERE id = %s", (job_id,), commit=True)

    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        # Все равно помечаем как удаленное/завершенное, чтобы не висело вечно желтым
        if job_id:
            db_query("UPDATE publication_jobs SET status = 'deleted' WHERE id = %s", (job_id,), commit=True)

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


async def execute_publication_job(context: ContextTypes.DEFAULT_TYPE):
    """
    EXECUTOR (called by JobQueue)
    Publishes the post using ID from publication_jobs
    """
    bot = context.bot
    job_id = context.job.data.get('job_id')

    if not job_id:
        try:
            job_id = int(context.job.name.replace('pub_', ''))
        except:
            logger.error("Could not determine job_id")
            return

    logger.info(f"Starting execute_publication_job for job_id: {job_id}")

    job_data = db_query("SELECT * FROM publication_jobs WHERE id = %s AND status = 'scheduled'", (job_id,),
                        fetchone=True)

    if not job_data:
        logger.error(f"Job {job_id} not found in DB or already executed.")
        return

    user_id = job_data['user_id']
    channel_id = job_data['channel_id']
    content_message_id = job_data['content_message_id']
    content_chat_id = job_data['content_chat_id']
    auto_delete_hours = job_data['auto_delete_hours']
    pin_duration = job_data['pin_duration']

    try:
        # Send message
        sent_message = await bot.copy_message(
            chat_id=channel_id,
            from_chat_id=content_chat_id,
            message_id=content_message_id,
            disable_notification=not job_data['pin_notify']
        )
        posted_message_id = sent_message.message_id
        logger.info(f"Job {job_id} published in {channel_id}, msg_id: {posted_message_id}")

        # --- NOTIFY USER (NEW) ---
        try:
            channel_info = db_query("SELECT channel_title FROM channels WHERE channel_id = %s", (channel_id,),
                                    fetchone=True)
            channel_title = channel_info['channel_title'] if channel_info else str(channel_id)

            notify_text = (
                f"✅ **Post Published!**\n"
                f"📢 Channel: {channel_title}\n"
                f"🔗 ID: {posted_message_id}"
            )
            # Send silently to not disturb too much
            await bot.send_message(chat_id=user_id, text=notify_text, disable_notification=True)
        except Exception as e:
            logger.warning(f"Failed to notify user {user_id}: {e}")
        # -------------------------

        # --- PINNING LOGIC ---
        if pin_duration > 0:
            try:
                await bot.pin_chat_message(
                    chat_id=channel_id,
                    message_id=posted_message_id,
                    disable_notification=not job_data['pin_notify']
                )

                # SCHEDULE UNPIN
                if auto_delete_hours == 0 or pin_duration < auto_delete_hours:
                    unpin_time_utc = datetime.now(ZoneInfo('UTC')) + timedelta(hours=pin_duration)
                    unpin_job_name = f"unpin_{job_id}_msg_{posted_message_id}"

                    context.application.job_queue.run_once(
                        execute_unpin_job,
                        when=unpin_time_utc,
                        data={
                            'channel_id': channel_id,
                            'message_id': posted_message_id,
                            'job_id': job_id
                        },
                        name=unpin_job_name,
                        job_kwargs={'misfire_grace_time': 600}
                    )
            except TelegramError as e:
                logger.error(f"Error pinning job {job_id}: {e}")

        # --- REPORT LOGIC ---
        task_info = db_query("SELECT report_enabled FROM tasks WHERE id = %s", (job_data['task_id'],), fetchone=True)
        if task_info and task_info['report_enabled']:
            # ... (Report logic similar to existing code) ...
            pass

            # --- AUTO DELETE LOGIC ---
        if auto_delete_hours > 0:
            delete_time_utc = datetime.now(ZoneInfo('UTC')) + timedelta(hours=auto_delete_hours)
            delete_job_name = f"del_{job_id}_msg_{posted_message_id}"

            context.application.job_queue.run_once(
                execute_delete_job,
                when=delete_time_utc,
                data={
                    'channel_id': channel_id,
                    'message_id': posted_message_id,
                    'job_id': job_id
                },
                name=delete_job_name,
                job_kwargs={'misfire_grace_time': 600}
            )

        # Update Status to 'published'
        db_query("""
            UPDATE publication_jobs
            SET status = 'published', published_at = NOW(), posted_message_id = %s
            WHERE id = %s
        """, (posted_message_id, job_id), commit=True)

    except Forbidden as e:
        logger.error(f"Forbidden: Could not execute job {job_id} in {channel_id}: {e}")
        db_query("UPDATE publication_jobs SET status = 'failed' WHERE id = %s", (job_id,), commit=True)

    except Exception as e:
        logger.error(f"Critical error executing job {job_id}: {e}", exc_info=True)
        db_query("UPDATE publication_jobs SET status = 'failed' WHERE id = %s", (job_id,), commit=True)

# --- 6. Логика платежей (Stars) ---

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
    user_id = update.effective_user.id

    logger.info(f"Успешный платеж от {user_id}. Payload: {payload}")

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


# --- 6. Обработчик добавления/удаления бота ---
async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик добавления/удаления бота в канал/чат с проверкой лимитов"""
    try:
        member_update = update.my_chat_member
        if not member_update:
            return

        chat = member_update.chat
        new_status = member_update.new_chat_member.status
        user = member_update.from_user

        user_settings = get_user_settings(user.id)
        lang = user_settings.get('language_code', 'en')
        tariff_key = user_settings.get('tariff', 'free')

        # Helper specifically for this handler since context.user_data might be empty
        def local_get_text(key):
            return TEXTS.get(lang, TEXTS['en']).get(key, TEXTS['en'].get(key))

        if new_status == "administrator":
            # --- CHECK CHANNEL LIMITS ---
            limits = get_tariff_limits(tariff_key)
            max_channels = limits.get('channels', 1)

            # Get current active channels count
            current_channels = get_user_channels(user.id)

            # Check if this specific channel is already in the list (re-adding doesn't count as new)
            is_existing = any(c['channel_id'] == chat.id for c in current_channels)

            if not is_existing and len(current_channels) >= max_channels:
                # Limit reached
                logger.warning(f"Channel limit reached for user {user.id}. Leaving chat {chat.id}")
                try:
                    # Leave the chat
                    await context.bot.leave_chat(chat.id)

                    # Notify user
                    error_text = local_get_text('limit_error_channels').format(
                        current=len(current_channels),
                        max=max_channels,
                        tariff=limits['name']
                    )
                    await context.bot.send_message(chat_id=user.id, text=error_text)
                except Exception as e:
                    logger.error(f"Failed to handle channel limit enforcement: {e}")
                return
            # --- END CHECK ---

            add_channel(
                user_id=user.id,
                channel_id=chat.id,
                title=chat.title,
                username=chat.username
            )
            try:
                text = local_get_text('channel_added').format(title=chat.title)
                await context.bot.send_message(chat_id=user.id, text=text)
            except (TelegramError, Forbidden):
                logger.warning(f"Could not notify user {user.id}")

            logger.info(f"Бот добавлен в {chat.title} (ID: {chat.id}) пользователем {user.id}")

        elif new_status in ["left", "kicked"]:
            deactivate_channel(chat.id)
            try:
                text = local_get_text('channel_removed').format(title=chat.title)
                await context.bot.send_message(chat_id=user.id, text=text)
            except (TelegramError, Forbidden):
                pass
            logger.info(f"Бот удален из {chat.title} (ID: {chat.id})")

    except Exception as e:
        logger.error(f"Error in my_chat_member_handler: {e}", exc_info=True)


async def debug_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to check scheduled jobs - add as command handler"""
    if update.effective_user.id != OWNER_ID:
        return

    # Check scheduler jobs
    # ***** MODIFIED HERE *****
    jobs = context.application.job_queue.get_jobs()
    text = f"📊 Scheduler jobs (job_queue): {len(jobs)}\n\n"

    for job in jobs[:10]:  # Show first 10
        text += f"ID: {job.id}\n"
        text += f"Name: {job.name}\n"
        text += f"Next run: {job.next_run_time}\n\n"

    # Check DB jobs
    db_jobs = db_query(
        "SELECT COUNT(*) as count, status FROM publication_jobs GROUP BY status",
        fetchall=True
    )

    text += "\n📚 DB Jobs:\n"
    if db_jobs:
        for row in db_jobs:
            text += f"{row['status']}: {row['count']}\n"
    else:
        text += "No jobs in DB."

    await update.message.reply_text(text)

# --- 7. Основная функция (main) ---
def main():
    """Запуск бота"""
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN':
        logger.critical("BOT_TOKEN не установлен! Бот не может запуститься.")
        return
    if not db_pool:
        logger.critical("Бот не может запуститься без соединения с БД!")
        return

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    # --- ConversationHandler ---

    # --- ИЗМЕНЕНИЕ: ДОБАВЛЯЕМ MessageHandler ВО ВСЕ СОСТОЯНИЯ,
    #     ГДЕ НЕТ ДРУГОГО ОБРАБОТЧИКА ТЕКСТА ---
    reply_button_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_keyboard)

    all_states = {
        # --- Процесс /start ---
        START_SELECT_LANG: [
            CallbackQueryHandler(start_select_lang, pattern="^lang_"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        START_SELECT_TZ: [
            CallbackQueryHandler(start_select_timezone, pattern="^tz_"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],

        # --- Главное меню ---
        MAIN_MENU: [
            # Этот обработчик УЖЕ ЗДЕСЬ, все верно
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_keyboard),
            CallbackQueryHandler(task_constructor_entrypoint, pattern="^nav_new_task$"),
            CallbackQueryHandler(nav_my_tasks, pattern="^nav_my_tasks$"),
            CallbackQueryHandler(nav_my_channels, pattern="^nav_channels$"),
            CallbackQueryHandler(nav_free_dates, pattern="^nav_free_dates$"),
            CallbackQueryHandler(nav_tariff, pattern="^nav_tariff$"),
            CallbackQueryHandler(nav_reports, pattern="^nav_reports$"),
            CallbackQueryHandler(nav_language, pattern="^nav_language$"),
            CallbackQueryHandler(nav_timezone, pattern="^nav_timezone$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],

        # --- Экраны меню (возврат в главное) ---
        MY_TASKS: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(task_constructor_entrypoint, pattern="^nav_new_task$"),
            CallbackQueryHandler(task_edit_entrypoint, pattern="^task_edit_"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        MY_CHANNELS: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(channel_manage_menu, pattern="^channel_manage_"),
            CallbackQueryHandler(channel_delete_confirm, pattern="^channel_delete_"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        FREE_DATES: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        TARIFF: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(tariff_buy_select, pattern="^tariff_buy_"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        REPORTS: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        BOSS_PANEL: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(boss_mailing, pattern="^boss_mailing$"),
            CallbackQueryHandler(boss_signature, pattern="^boss_signature$"),
            CallbackQueryHandler(boss_users, pattern="^boss_users$"),
            CallbackQueryHandler(boss_stats, pattern="^boss_stats$"),
            CallbackQueryHandler(boss_ban_start, pattern="^boss_ban$"),
            CallbackQueryHandler(boss_money, pattern="^boss_money$"),
            CallbackQueryHandler(boss_logs, pattern="^boss_logs$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],

        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        BOSS_BAN_SELECT_USER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_ban_receive_user),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],
        BOSS_BAN_CONFIRM: [
            CallbackQueryHandler(boss_ban_confirm_yes, pattern="^boss_ban_confirm_yes$"),
            CallbackQueryHandler(boss_unban_confirm_yes, pattern="^boss_unban_confirm_yes$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],

        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        BOSS_MAILING_MESSAGE: [
            MessageHandler(filters.ALL & ~filters.COMMAND, boss_mailing_receive_message),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],

        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        BOSS_MAILING_EXCLUDE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_mailing_exclude),
            CallbackQueryHandler(boss_mailing_skip_exclude, pattern="^boss_mailing_skip_exclude$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],
        BOSS_MAILING_CONFIRM: [
            CallbackQueryHandler(boss_mailing_send, pattern="^boss_mailing_send$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],

        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        BOSS_SIGNATURE_EDIT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_signature_receive),
            CallbackQueryHandler(boss_signature_delete, pattern="^boss_signature_delete$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],

        # --- Конструктор Задач ---
        TASK_CONSTRUCTOR: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(task_activate, pattern="^task_activate$"),
            CallbackQueryHandler(task_ask_name, pattern="^task_set_name$"),
            CallbackQueryHandler(task_ask_message, pattern="^task_set_message$"),
            CallbackQueryHandler(task_select_channels, pattern="^task_select_channels$"),
            CallbackQueryHandler(task_select_calendar, pattern="^task_select_calendar$"),
            CallbackQueryHandler(task_select_time, pattern="^task_select_time$"),
            CallbackQueryHandler(task_deactivate, pattern="^task_deactivate$"),
            CallbackQueryHandler(task_set_pin, pattern="^task_set_pin$"),
            CallbackQueryHandler(task_set_pin_notify, pattern="^task_set_pin_notify$"),
            CallbackQueryHandler(task_set_delete, pattern="^task_set_delete$"),
            CallbackQueryHandler(task_set_report, pattern="^task_set_report$"),
            CallbackQueryHandler(task_set_advertiser, pattern="^task_set_advertiser$"),
            CallbackQueryHandler(task_set_post_type, pattern="^task_set_post_type$"),
            CallbackQueryHandler(task_delete, pattern="^task_delete$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],

        # --- Вложенные состояния конструктора ---

        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        TASK_SET_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, task_receive_name),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        TASK_SET_MESSAGE: [
            MessageHandler(filters.ALL & ~filters.COMMAND, task_receive_message),
            CallbackQueryHandler(task_delete_message, pattern="^task_delete_message$"),  # <-- ДОБАВЛЕНО
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        TASK_SELECT_CHANNELS: [
            CallbackQueryHandler(task_toggle_channel, pattern="^channel_toggle_"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        TASK_SET_ADVERTISER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, task_receive_advertiser),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        # --- НЕ ДОБАВЛЯЕМ т.к. есть MessageHandler ---
        TASK_SET_CUSTOM_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, time_receive_custom),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],

        # --- Календарь и время ---
        CALENDAR_VIEW: [
            CallbackQueryHandler(calendar_navigation, pattern="^calendar_prev$"),
            CallbackQueryHandler(calendar_navigation, pattern="^calendar_next$"),
            CallbackQueryHandler(calendar_day_select, pattern="^calendar_day_"),
            CallbackQueryHandler(calendar_weekday_select, pattern="^calendar_wd_"),  # <-- ДОБАВЛЕНО
            CallbackQueryHandler(calendar_ignore_past, pattern="^calendar_ignore_past$"),  # <-- ДОБАВЛЕНО
            # CallbackQueryHandler(calendar_select_all, pattern="^calendar_select_all$"), # <-- УДАЛЕНО (или закомментировано)
            CallbackQueryHandler(calendar_reset, pattern="^calendar_reset$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
        TIME_SELECTION: [
            CallbackQueryHandler(time_slot_select, pattern="^time_select_"),
            CallbackQueryHandler(time_custom, pattern="^time_custom$"),
            CallbackQueryHandler(time_clear, pattern="^time_clear$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],

        # --- Настройки закрепления и удаления ---
        TASK_SET_PIN: [
            CallbackQueryHandler(pin_duration_select, pattern="^pin_"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        TASK_SET_DELETE: [
            CallbackQueryHandler(delete_duration_select, pattern="^delete_"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
        TASK_DELETE_CONFIRM: [
            CallbackQueryHandler(task_delete_confirm_yes, pattern="^task_delete_confirm_yes$"),
            CallbackQueryHandler(task_delete_confirm_no, pattern="^task_delete_confirm_no$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler  # <--- ДОБАВЛЕНО
        ],
    }
    # ... (rest of the main() function is unchanged) ...

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states=all_states,
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("jobs", debug_jobs))

    # --- НОВЫЕ ОБРАБОТЧИКИ ПЛАТЕЖЕЙ ---
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    # --- КОНЕЦ НОВОГО БЛОКА ---

    application.add_handler(ChatMemberHandler(
        my_chat_member_handler,
        ChatMemberHandler.MY_CHAT_MEMBER
    ))

    application.add_error_handler(error_handler)

    logger.info("Бот запускается...")
    logger.info(f"Owner ID: {OWNER_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
