import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ChatMemberHandler,
    ConversationHandler, PreCheckoutQueryHandler, TypeHandler, PicklePersistence,
)

from config.settings import BOT_TOKEN, OWNER_ID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database.connection import db_pool
from database.schema import init_db
from database.rate_limit import init_rate_limit_table

from handlers.admin.ban import boss_ban_start, boss_ban_receive_user, boss_ban_confirm_yes, boss_unban_confirm_yes
from handlers.admin.grant import boss_grant_start, boss_grant_receive_input, boss_grant_confirm_yes
from handlers.admin.logs import boss_logs
from handlers.admin.mailing import boss_mailing, boss_mailing_send, boss_mailing_skip_exclude, boss_mailing_exclude, \
    boss_mailing_receive_message
from handlers.admin.money import boss_money
from handlers.admin.panel import nav_boss
from handlers.admin.signature import boss_signature, boss_signature_delete, boss_signature_receive
from handlers.admin.stats import boss_stats, debug_jobs
from handlers.admin.users import boss_users
from handlers.channels import nav_my_channels, channel_manage_menu, channel_delete_confirm, my_chat_member_handler
from handlers.errors import error_handler, cancel
from handlers.navigation import handle_reply_keyboard, nav_main_menu, nav_my_tasks, nav_free_dates, nav_language, nav_timezone
from handlers.payments import successful_payment_callback, precheckout_callback
from handlers.reports import nav_reports
from handlers.start import start_select_lang, start_select_timezone, start_command
from handlers.tariffs import nav_tariff, tariff_buy_select
from handlers.tasks.activation import task_deactivate, task_activate
from handlers.tasks.calendar import calendar_reset, calendar_select_all, calendar_ignore_past, calendar_weekday_select, \
    calendar_day_select, calendar_navigation, task_select_calendar
from handlers.tasks.channels import task_toggle_channel, task_select_channels
from handlers.tasks.constructor import task_constructor_entrypoint, task_edit_entrypoint, task_back_to_constructor
from handlers.tasks.deletion import task_delete_confirm_no, task_delete_confirm_yes, task_delete
from handlers.tasks.message import task_delete_message, task_receive_message, task_ask_message
from handlers.tasks.name import task_receive_name, task_ask_name
from handlers.tasks.options import task_set_delete, delete_receive_custom, delete_custom, delete_duration_select, \
    task_set_pin, pin_receive_custom, pin_custom, pin_duration_select, task_receive_advertiser, task_set_post_type, \
    task_set_advertiser, task_set_report, task_set_pin_notify
from handlers.tasks.time import time_clear, time_custom, time_slot_select, task_select_time, time_receive_custom
from jobs.cleanup import cleanup_past_schedules, cleanup_inactive_tasks, cleanup_rate_limit_records
from jobs.restoration import restore_active_tasks
from middleware.user_loader import global_user_loader
from states.conversation import MAIN_MENU, MY_TASKS, MY_CHANNELS, FREE_DATES, TARIFF, REPORTS, BOSS_PANEL, START_SELECT_LANG, START_SELECT_TZ, TASK_CONSTRUCTOR, TASK_SET_NAME, TASK_SELECT_CHANNELS, TASK_SET_MESSAGE, TASK_SELECT_CALENDAR, TASK_SELECT_TIME, TASK_SET_PIN, TASK_SET_PIN_NOTIFY, TASK_SET_DELETE, TASK_SET_REPORT, TASK_SET_ADVERTISER, TASK_SET_POST_TYPE, TASK_SET_CUSTOM_TIME, CALENDAR_VIEW, TIME_SELECTION, BOSS_MAILING, BOSS_STATS, BOSS_USERS, BOSS_LIMITS, BOSS_TARIFFS, BOSS_BAN, BOSS_MONEY, BOSS_LOGS, BOSS_MAILING_CREATE, BOSS_MAILING_MESSAGE, BOSS_MAILING_EXCLUDE, BOSS_MAILING_CONFIRM, BOSS_SIGNATURE_EDIT, BOSS_USERS_LIST, BOSS_STATS_VIEW, BOSS_LIMITS_SELECT_USER, BOSS_LIMITS_SET_VALUE, BOSS_TARIFFS_EDIT, BOSS_BAN_SELECT_USER, BOSS_BAN_CONFIRM, BOSS_MONEY_VIEW, BOSS_LOGS_VIEW, BOSS_GRANT_TARIFF, BOSS_GRANT_CONFIRM, TASK_SET_PIN_CUSTOM, TASK_SET_DELETE_CUSTOM, TASK_DELETE_CONFIRM
from utils.logging import logger


scheduler = AsyncIOScheduler(timezone='UTC')


def main():
    """Запуск бота"""
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN':
        logger.critical("BOT_TOKEN не установлен! Бот не может запуститься.")
        return
    if not db_pool:
        logger.critical("Бот не может запуститься без соединения с БД!")
        return

    init_db()

    # Initialize rate limiting table
    init_rate_limit_table()

    async def post_init(app: Application):
        await restore_active_tasks(app)

    # Используем абсолютный путь для persistence (FILE, не директория)
    PERSISTENCE_DIR = "/app/persistence"
    os.makedirs(PERSISTENCE_DIR, exist_ok=True)

    persistence_file = os.path.join(PERSISTENCE_DIR, "state.pkl")

    persistence = PicklePersistence(filepath=persistence_file)
    logger.info(f"Bot persistence file: {persistence_file}")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    # Common handler for reply keyboard buttons (TEXT messages that are not commands)
    reply_button_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_keyboard)

    all_states = {
        # --- Процесс /start ---
        START_SELECT_LANG: [
            CallbackQueryHandler(start_select_lang, pattern="^lang_"),
            reply_button_handler
        ],
        START_SELECT_TZ: [
            CallbackQueryHandler(start_select_timezone, pattern="^tz_"),
            reply_button_handler
        ],

        # --- Главное меню ---
        MAIN_MENU: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_keyboard),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
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

        # --- Экраны меню ---
        MY_TASKS: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(task_constructor_entrypoint, pattern="^nav_new_task$"),
            CallbackQueryHandler(nav_my_channels, pattern="^nav_channels$"),
            CallbackQueryHandler(task_edit_entrypoint, pattern="^task_edit_"),
            CallbackQueryHandler(nav_tariff, pattern="^nav_tariff$"),
            reply_button_handler
        ],
        MY_CHANNELS: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(channel_manage_menu, pattern="^channel_manage_"),
            CallbackQueryHandler(channel_delete_confirm, pattern="^channel_delete_"),
            CallbackQueryHandler(nav_my_channels, pattern="^nav_channels$"),
            reply_button_handler
        ],
        FREE_DATES: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
        TARIFF: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(tariff_buy_select, pattern="^tariff_buy_"),
            reply_button_handler
        ],
        REPORTS: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
        BOSS_PANEL: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            CallbackQueryHandler(boss_mailing, pattern="^boss_mailing$"),
            CallbackQueryHandler(boss_signature, pattern="^boss_signature$"),
            CallbackQueryHandler(boss_users, pattern="^boss_users$"),
            CallbackQueryHandler(boss_stats, pattern="^boss_stats$"),
            CallbackQueryHandler(boss_ban_start, pattern="^boss_ban$"),
            CallbackQueryHandler(boss_grant_start, pattern="^boss_grant$"),
            CallbackQueryHandler(boss_money, pattern="^boss_money$"),
            CallbackQueryHandler(boss_logs, pattern="^boss_logs$"),
            reply_button_handler
        ],

        # --- Boss Sub-states ---
        BOSS_GRANT_TARIFF: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_grant_receive_input),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],
        BOSS_GRANT_CONFIRM: [
            CallbackQueryHandler(boss_grant_confirm_yes, pattern="^boss_grant_confirm_yes$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            reply_button_handler
        ],
        BOSS_BAN_SELECT_USER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_ban_receive_user),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],
        BOSS_BAN_CONFIRM: [
            CallbackQueryHandler(boss_ban_confirm_yes, pattern="^boss_ban_confirm_yes$"),
            CallbackQueryHandler(boss_unban_confirm_yes, pattern="^boss_unban_confirm_yes$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            reply_button_handler
        ],
        BOSS_MAILING_MESSAGE: [
            MessageHandler(filters.ALL & ~filters.COMMAND, boss_mailing_receive_message),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],
        BOSS_MAILING_EXCLUDE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_mailing_exclude),
            CallbackQueryHandler(boss_mailing_skip_exclude, pattern="^boss_mailing_skip_exclude$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],
        BOSS_MAILING_CONFIRM: [
            CallbackQueryHandler(boss_mailing_send, pattern="^boss_mailing_send$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
            reply_button_handler
        ],
        BOSS_SIGNATURE_EDIT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, boss_signature_receive),
            CallbackQueryHandler(boss_signature_delete, pattern="^boss_signature_delete$"),
            CallbackQueryHandler(nav_boss, pattern="^nav_boss$"),
        ],

        # --- Конструктор Задач ---
        TASK_CONSTRUCTOR: [
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            CallbackQueryHandler(nav_my_tasks, pattern="^nav_my_tasks$"),
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
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            reply_button_handler
        ],

        # --- Вложенные состояния конструктора ---
        TASK_SET_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, task_receive_name),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        TASK_SET_MESSAGE: [
            MessageHandler(filters.ALL & ~filters.COMMAND, task_receive_message),
            CallbackQueryHandler(task_delete_message, pattern="^task_delete_message$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        TASK_SELECT_CHANNELS: [
            CallbackQueryHandler(task_toggle_channel, pattern="^channel_toggle_"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
        TASK_SET_ADVERTISER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, task_receive_advertiser),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        TASK_SET_CUSTOM_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, time_receive_custom),
            CallbackQueryHandler(task_select_time, pattern="^task_select_time$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],

        # --- Календарь и время ---
        CALENDAR_VIEW: [
            CallbackQueryHandler(calendar_navigation, pattern="^calendar_prev$"),
            CallbackQueryHandler(calendar_navigation, pattern="^calendar_next$"),
            CallbackQueryHandler(calendar_day_select, pattern="^calendar_day_"),
            CallbackQueryHandler(calendar_weekday_select, pattern="^calendar_wd_"),
            CallbackQueryHandler(calendar_ignore_past, pattern="^calendar_ignore_past$"),
            CallbackQueryHandler(calendar_select_all, pattern="^calendar_select_all$"),
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
            reply_button_handler
        ],

        # --- Настройки закрепления и удаления ---
        TASK_SET_PIN: [
            CallbackQueryHandler(pin_duration_select, pattern=r"^pin_\d+$"),
            CallbackQueryHandler(pin_custom, pattern="^pin_custom$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
        TASK_SET_PIN_CUSTOM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, pin_receive_custom),
            CallbackQueryHandler(task_set_pin, pattern="^task_set_pin$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        TASK_SET_DELETE: [
            CallbackQueryHandler(delete_duration_select, pattern=r"^delete_\d+$"),
            CallbackQueryHandler(delete_custom, pattern="^delete_custom$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
        TASK_SET_DELETE_CUSTOM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, delete_receive_custom),
            CallbackQueryHandler(task_set_delete, pattern="^task_set_delete$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
        ],
        TASK_DELETE_CONFIRM: [
            CallbackQueryHandler(task_delete_confirm_yes, pattern="^task_delete_confirm_yes$"),
            CallbackQueryHandler(task_delete_confirm_no, pattern="^task_delete_confirm_no$"),
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),
            CallbackQueryHandler(nav_main_menu, pattern="^nav_main_menu$"),
            reply_button_handler
        ],
    }

    # Создаем ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states=all_states,
        fallbacks=[CommandHandler("cancel", cancel)],
        name="main_conversation",
        persistent=True,
        allow_reentry=True,
    )

    # Добавляем обработчики в правильном порядке
    # 1. Middleware для загрузки пользователя (должен быть первым, group=-1)
    application.add_handler(TypeHandler(Update, global_user_loader), group=-1)

    # 2. Основной ConversationHandler
    application.add_handler(conv_handler)

    # 3. Обработчики платежей (Stars)
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # 4. Обработчик добавления/удаления бота в каналы
    application.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))

    # 5. Обработчик отладки (только для владельца)
    application.add_handler(CommandHandler("debug_jobs", debug_jobs))

    # 6. Обработчик ошибок
    application.add_error_handler(error_handler)

    logger.info("Бот запускается...")
    logger.info(f"Owner ID: {OWNER_ID}")

    scheduler.add_job(
        cleanup_inactive_tasks,
        CronTrigger(hour=0, minute=0, timezone='UTC'),
        id='cleanup_inactive_tasks',
        name='Daily cleanup of inactive tasks older than 60 days',
        replace_existing=True
    )

    scheduler.add_job(
        cleanup_past_schedules,
        CronTrigger(hour=0, minute=5, timezone='UTC'),
        id='cleanup_past_schedules',
        name='Daily cleanup of past schedule dates',
        replace_existing=True
    )

    scheduler.add_job(
        cleanup_rate_limit_records,
        CronTrigger(hour=1, minute=0, timezone='UTC'),
        id='cleanup_rate_limit',
        name='Daily cleanup of rate limit records older than 1 day',
        replace_existing=True
    )

    scheduler.start()

    logger.info("✅ Scheduled daily cleanup jobs")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
