#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import re
import calendar
from enum import Enum

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo, InputMediaAudio, InputMediaDocument
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
from text import TEXTS
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


# Города и их таймзоны с UTC offset
# Города и их таймзоны с UTC offset
TIMEZONES = {
    "Madrid": ("Europe/Madrid", "UTC+1"),
    "Moscow": ("Europe/Moscow", "UTC+3"),
    "Kiev": ("Europe/Kiev", "UTC+2"),
    "Tashkent": ("Asia/Tashkent", "UTC+5"),
    "Berlin": ("Europe/Berlin", "UTC+1"),
    "Paris": ("Europe/Paris", "UTC+1"),
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


async def cancel_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancels all scheduled jobs for a specific task in both JobQueue and DB.
    Used before refreshing a task to avoid duplicates.
    """
    # 1. Find scheduled jobs in DB
    jobs_to_cancel = db_query(
        "SELECT aps_job_id FROM publication_jobs WHERE task_id = %s AND status = 'scheduled' AND aps_job_id IS NOT NULL",
        (task_id,), fetchall=True
    )

    if jobs_to_cancel:
        for job_row in jobs_to_cancel:
            job_name = job_row.get('aps_job_id')
            if job_name:
                # Remove from Telegram JobQueue
                jobs = context.application.job_queue.get_jobs_by_name(job_name)
                for job in jobs:
                    job.schedule_removal()

    # 2. Mark them as cancelled in DB
    db_query(
        "UPDATE publication_jobs SET status = 'cancelled' WHERE task_id = %s AND status = 'scheduled'",
        (task_id,), commit=True
    )
    logger.info(f"Cancelled pending jobs for task {task_id}")


def validate_task(task_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    """
    Validates if a task has all required fields to be Active.
    Used during Hot-Reload to ensure we don't schedule broken tasks.
    """
    task = get_task_details(task_id)
    if not task:
        return False, "Task not found"

    # 1. Check Message
    if not task.get('content_message_id'):
        return False, get_text('task_error_no_message', context)

    # 2. Check Channels
    channels = get_task_channels(task_id)
    if not channels:
        return False, get_text('task_error_no_channels', context)

    # 3. Check Schedule (Dates/Weekdays AND Times)
    schedules = get_task_schedules(task_id)
    has_time = any(s['schedule_time'] for s in schedules)
    if not schedules or not has_time:
        return False, get_text('task_error_no_schedule', context)

    return True, ""


async def refresh_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    HOT RELOAD LOGIC:
    1. Checks if task is ACTIVE. If Inactive (creation mode) -> Do nothing.
    2. If Active (edit mode) -> Cancel ALL previous jobs immediately.
    3. Validate and Reschedule with NEW parameters.
    """
    # 1. Check Status
    task = get_task_details(task_id)
    if not task or task['status'] != 'active':
        # Stop here if we are just creating the task (Constraint: do not auto activate while creating)
        return

    logger.info(f"🔄 Hot-reloading active task {task_id} due to parameter change...")

    # 2. Cancel OLD jobs
    # (Constraint: previous task should be cancelled and not published)
    await cancel_task_jobs(task_id, context)

    # 3. Validate New State
    is_valid, error = validate_task(task_id, context)

    if is_valid:
        # 4. Create NEW jobs
        # (Constraint: the one with new parameters should be published)
        user_settings = get_user_settings(task['user_id'])
        user_tz = user_settings.get('timezone', 'Europe/Moscow')

        count = create_publication_jobs_for_task(task_id, user_tz, context.application)
        logger.info(f"✅ Task {task_id} hot-reloaded. Scheduled {count} jobs.")
    else:
        # If the edit made the task invalid (e.g. removed all times), force deactivate
        logger.warning(f"⚠️ Task {task_id} invalid after edit. Deactivating. Reason: {error}")
        # We use db_query directly to avoid infinite recursion with update_task_field
        db_query("UPDATE tasks SET status = 'inactive' WHERE id = %s", (task_id,), commit=True)
        # Optionally notify user here


async def update_task_field(task_id: int, field: str, value: Any, context: ContextTypes.DEFAULT_TYPE):
    """
    Updates a DB field and triggers the Hot-Reload check.
    """
    allowed_fields = [
        'task_name', 'content_message_id', 'content_chat_id', 'pin_duration',
        'pin_notify', 'auto_delete_hours', 'report_enabled',
        'advertiser_user_id', 'post_type', 'status'
    ]

    if field not in allowed_fields:
        logger.error(f"Attempt to update invalid field: {field}")
        return

    # 1. Update DB
    sql = f"UPDATE tasks SET {field} = %s WHERE id = %s"
    db_query(sql, (value, task_id), commit=True)

    # 2. Trigger Hot Reload (Auto-activate if already active)
    await refresh_task_jobs(task_id, context)


async def refresh_task_jobs(task_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    HOT RELOAD LOGIC:
    1. Checks if task is ACTIVE. If Inactive (creation mode) -> Do nothing.
    2. If Active (edit mode) -> Cancel ALL previous jobs immediately.
    3. Validate and Reschedule with NEW parameters.
    """
    # 1. Check Status
    task = get_task_details(task_id)
    if not task or task['status'] != 'active':
        # Stop here if we are just creating the task (Constraint: do not auto activate while creating)
        return

    logger.info(f"🔄 Hot-reloading active task {task_id} due to parameter change...")

    # 2. Cancel OLD jobs
    # (Constraint: previous task should be cancelled and not published)
    await cancel_task_jobs(task_id, context)

    # 3. Validate New State
    is_valid, error = validate_task(task_id, context)

    if is_valid:
        # 4. Create NEW jobs
        # (Constraint: the one with new parameters should be published)
        user_settings = get_user_settings(task['user_id'])
        user_tz = user_settings.get('timezone', 'Europe/Moscow')

        count = create_publication_jobs_for_task(task_id, user_tz, context.application)
        logger.info(f"✅ Task {task_id} hot-reloaded. Scheduled {count} jobs.")
    else:
        # If the edit made the task invalid (e.g. removed all times), force deactivate
        logger.warning(f"⚠️ Task {task_id} invalid after edit. Deactivating. Reason: {error}")
        # We use db_query directly to avoid infinite recursion with update_task_field
        db_query("UPDATE tasks SET status = 'inactive' WHERE id = %s", (task_id,), commit=True)
        # Optionally notify user here

async def delete_pin_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Task 1: Immediately removes the 'Message Pinned' service message
    if the pin was performed by the bot.
    """
    if not update.message or not update.message.pinned_message:
        return

    # Check if the pinner is the bot itself
    if update.message.from_user.id == context.bot.id:
        try:
            await update.message.delete()
            logger.info(f"Deleted pin service message in chat {update.message.chat_id}")
        except Exception as e:
            logger.warning(f"Failed to delete pin service message: {e}")



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
                    is_active BOOLEAN DEFAULT TRUE,
                    custom_limits JSONB DEFAULT '{}'::jsonb
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
                    task_name VARCHAR(255) NULL,
                    content_message_id BIGINT NULL,
                    content_chat_id BIGINT NULL,

                    -- NEW: JSON field to store media group details (file_ids, types, caption)
                    media_group_data JSONB NULL,

                    pin_duration INTEGER DEFAULT 0,
                    pin_notify BOOLEAN DEFAULT FALSE,
                    auto_delete_hours INTEGER DEFAULT 0,
                    report_enabled BOOLEAN DEFAULT FALSE,
                    advertiser_user_id BIGINT NULL,
                    post_type VARCHAR(50) DEFAULT 'from_bot',
                    status VARCHAR(50) DEFAULT 'inactive',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # --- MIGRATION: Ensure message_snippet column exists ---
            try:
                cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS message_snippet VARCHAR(255)")
            except psycopg2.Error:
                conn.rollback()

            # --- MIGRATION: Ensure media_group_data column exists ---
            try:
                cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS media_group_data JSONB")
            except psycopg2.Error:
                conn.rollback()
            # -----------------------------------------------------

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

            # Таблица настроек бота (для подписи)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    signature TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON publication_jobs(status)")
            conn.commit()
            logger.info("База данных успешно инициализирована")
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


async def update_task_field(task_id: int, field: str, value: Any, context: ContextTypes.DEFAULT_TYPE):
    """
    Updates a DB field and triggers the Hot-Reload check.
    """
    allowed_fields = [
        'task_name', 'content_message_id', 'content_chat_id', 'pin_duration',
        'pin_notify', 'auto_delete_hours', 'report_enabled',
        'advertiser_user_id', 'post_type', 'status'
    ]

    if field not in allowed_fields:
        logger.error(f"Attempt to update invalid field: {field}")
        return

    # 1. Update DB
    sql = f"UPDATE tasks SET {field} = %s WHERE id = %s"
    db_query(sql, (value, task_id), commit=True)

    # 2. Trigger Hot Reload (Auto-activate if already active)
    await refresh_task_jobs(task_id, context)


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


def timezone_keyboard(context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    cities = list(TIMEZONES.keys())

    # Создаем кнопки по 2 в ряд
    for i in range(0, len(cities), 2):
        row = []
        for j in range(2):
            if i + j < len(cities):
                city = cities[i + j]
                tz_name, utc_offset = TIMEZONES[city]

                # Локализация названия города
                city_localized = get_text(f"tz_{city}", context) or city

                row.append(
                    InlineKeyboardButton(
                        f"{city_localized} ({utc_offset})",
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
    """Клавиатура конструктора (Dynamic Labels with Localization)"""
    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # --- Defaults ---
    pin_val = 0
    delete_val = 0
    push_val = False
    report_val = False
    post_type = 'from_bot'
    is_active = False
    has_message = False
    has_channels = False

    if task:
        pin_val = task.get('pin_duration', 0)
        delete_val = task.get('auto_delete_hours', 0)
        push_val = task.get('pin_notify', False)
        report_val = task.get('report_enabled', False)
        post_type = task.get('post_type', 'from_bot')
        is_active = task.get('status') == 'active'
        has_message = bool(task.get('content_message_id'))

        # Check channels cheaply if needed, or rely on variable
        channels = get_task_channels(task_id)
        has_channels = bool(channels)

    # --- Localization Helper ---
    lang = context.user_data.get('language_code', 'en')

    short_days_map = {'ru': 'д', 'en': 'd', 'es': 'd', 'fr': 'j', 'ua': 'д', 'de': 'T'}
    short_hours_map = {'ru': 'ч', 'en': 'h', 'es': 'h', 'fr': 'h', 'ua': 'г', 'de': 'h'}
    s_d = short_days_map.get(lang, 'd')
    s_h = short_hours_map.get(lang, 'h')

    def format_duration(hours):
        if hours <= 0:
            return get_text('duration_no', context)
        if hours % 24 == 0:
            return f"{hours // 24}{s_d}"
        return f"{hours}{s_h}"

    # --- Dynamic Button Labels ---

    # Message Button with ✅/❌
    lbl_msg = get_text('task_set_message_btn', context)
    val_msg = "✅" if has_message else "❌"
    btn_msg = f"{lbl_msg} {val_msg}"

    # Channels Button with ✅/❌ (Optional, consistent style)
    lbl_ch = get_text('task_select_channels_btn', context)
    val_ch = "✅" if has_channels else "❌"
    btn_ch = f"{lbl_ch} {val_ch}"

    # 1. Pin
    lbl_pin = get_text('task_set_pin_btn', context)
    val_pin = format_duration(pin_val)
    btn_pin = f"{lbl_pin}: {val_pin}"

    # 2. Push (Notify)
    lbl_push = get_text('task_set_pin_notify_btn', context)
    val_push = "✅" if push_val else "❌"
    btn_push = f"{lbl_push}: {val_push}"

    # 3. Auto-Delete
    lbl_delete = get_text('task_set_delete_btn', context)
    val_delete = format_duration(delete_val)
    btn_delete = f"{lbl_delete}: {val_delete}"

    # 4. Report
    lbl_report = get_text('task_set_report_btn', context)
    val_report = "✅" if report_val else "❌"
    btn_report = f"{lbl_report}: {val_report}"

    # 5. Post Type
    lbl_type = get_text('task_set_post_type_btn', context)
    val_type = "🤖" if post_type == 'from_bot' else "↪️"
    btn_type = f"{lbl_type}: {val_type}"

    # --- Action Button ---
    if is_active:
        action_btn = InlineKeyboardButton(get_text('task_btn_deactivate', context), callback_data="task_deactivate")
    else:
        action_btn = InlineKeyboardButton(get_text('task_activate_btn', context), callback_data="task_activate")

    # --- Construct Keyboard ---
    keyboard = [
        [InlineKeyboardButton(get_text('task_set_name_btn', context), callback_data="task_set_name")],
        [InlineKeyboardButton(btn_ch, callback_data="task_select_channels")],
        [InlineKeyboardButton(btn_msg, callback_data="task_set_message")],
        [
            InlineKeyboardButton(get_text('task_select_calendar_btn', context), callback_data="task_select_calendar"),
            InlineKeyboardButton(get_text('task_select_time_btn', context), callback_data="task_select_time")
        ],
        [
            InlineKeyboardButton(btn_pin, callback_data="task_set_pin"),
            InlineKeyboardButton(btn_push, callback_data="task_set_pin_notify")
        ],
        [InlineKeyboardButton(btn_delete, callback_data="task_set_delete")],
        [InlineKeyboardButton(btn_report, callback_data="task_set_report")],
        [InlineKeyboardButton(get_text('task_set_advertiser_btn', context), callback_data="task_set_advertiser")],
        [InlineKeyboardButton(btn_type, callback_data="task_set_post_type")],
        [InlineKeyboardButton(get_text('task_delete_btn', context), callback_data="task_delete")],
        [action_btn],
        [
            InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_my_tasks"),
            InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
        ],
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
        raw_title = ch['channel_title'] or ch['channel_username'] or f"ID: {channel_id}"

        # --- FIX: Truncate to 3 words ---
        title = generate_smart_name(raw_title, context, limit=3)

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
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")]
    )

    return InlineKeyboardMarkup(keyboard)


def calendar_keyboard(
        context: ContextTypes.DEFAULT_TYPE,
        year: int,
        month: int,
        selected_dates: List[str] = None,
        selected_weekdays: List[int] = None,
        today_user_date: datetime.date = None
):
    """Клавиатура календаря (Обновленная)"""
    if selected_dates is None:
        selected_dates = []
    if selected_weekdays is None:
        selected_weekdays = []
    if today_user_date is None:
        today_user_date = datetime.now().date()

    cal = calendar.monthcalendar(year, month)

    try:
        weekdays_str = get_text('calendar_weekdays_short', context)
        weekdays = weekdays_str.split(',')
        if len(weekdays) != 7: raise Exception
    except Exception:
        weekdays = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

    keyboard = []

    # 1. Заголовок месяца
    month_name = get_text(f"month_{month}", context) or str(month)
    header_row = [InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore")]
    keyboard.append(header_row)

    # 2. Дни недели (Пн, Вт...) с галочками
    weekday_row = []
    for i, day_name in enumerate(weekdays):
        prefix = "✅" if i in selected_weekdays else ""
        weekday_row.append(InlineKeyboardButton(f"{prefix}{day_name}", callback_data=f"calendar_wd_{i}"))
    keyboard.append(weekday_row)

    # 3. Сетка дней
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                current_date = datetime(year, month, day).date()
                date_str = current_date.strftime('%Y-%m-%d')
                is_past = current_date < today_user_date
                is_selected_date = date_str in selected_dates

                # --- ИЗМЕНЕНИЕ (Задача 1): Убрали отображение 🗓️ для дней недели ---
                # Теперь проверяем только конкретную дату
                prefix = " "
                if is_selected_date:
                    prefix = "✅"

                callback = f"calendar_day_{date_str}"

                if is_past:
                    prefix = "❌"
                    callback = "calendar_ignore_past"

                row.append(InlineKeyboardButton(f"{prefix}{day}", callback_data=callback))
        keyboard.append(row)

    # --- ИЗМЕНЕНИЕ (Задача 2): Кнопка "Весь месяц" ---
    # Добавляем её перед навигацией
    keyboard.append([
        InlineKeyboardButton(get_text('calendar_select_all_btn', context),
                             callback_data="calendar_select_all")
    ])

    # 4. Навигация
    keyboard.append([
        InlineKeyboardButton("⬅️", callback_data="calendar_prev"),
        InlineKeyboardButton(get_text('calendar_reset', context), callback_data="calendar_reset"),
        InlineKeyboardButton("➡️", callback_data="calendar_next")
    ])

    # 5. Выход
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


def pin_duration_keyboard(context: ContextTypes.DEFAULT_TYPE, current_duration: int = None):
    """Клавиатура выбора длительности закрепления (с галочкой)"""
    # Define options: (value, localization_key)
    options = [
        (12, 'duration_12h'),
        (24, 'duration_24h'),
        (48, 'duration_48h'),
        (72, 'duration_3d'),
        (168, 'duration_7d'),
        (0, 'duration_no')
    ]

    keyboard = []
    for value, key in options:
        text = get_text(key, context)
        # Add checkmark if this is the currently selected value
        if current_duration is not None and value == current_duration:
            text = f"✅ {text}"

        keyboard.append([InlineKeyboardButton(text, callback_data=f"pin_{value}")])

    # Navigation buttons
    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
    ])

    return InlineKeyboardMarkup(keyboard)


def delete_duration_keyboard(context: ContextTypes.DEFAULT_TYPE, current_duration: int = None):
    """Клавиатура выбора длительности автоудаления (с галочкой)"""
    # Define options: (value, localization_key)
    options = [
        (12, 'duration_12h'),
        (24, 'duration_24h'),
        (48, 'duration_48h'),
        (72, 'duration_3d'),
        (168, 'duration_7d'),
        (0, 'duration_no')
    ]

    keyboard = []
    for value, key in options:
        text = get_text(key, context)
        # Add checkmark if this is the currently selected value
        if current_duration is not None and value == current_duration:
            text = f"✅ {text}"

        keyboard.append([InlineKeyboardButton(text, callback_data=f"delete_{value}")])

    # Navigation buttons
    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def boss_panel_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура админ-панели (локализованная)"""
    keyboard = [
        [InlineKeyboardButton(get_text('boss_mailing_btn', context), callback_data="boss_mailing")],
        [InlineKeyboardButton(get_text('boss_signature_btn', context), callback_data="boss_signature")],
        # <-- НОВАЯ КНОПКА
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
    """
    Отправляет новое или редактирует существующее сообщение.
    Robust version: Если редактирование невозможно (сообщение удалено или другого типа), отправляет новое.
    """
    query = update.callback_query
    if query and query.message:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except TelegramError as e:
            # Если "Message is not modified" - это не ошибка, игнорируем.
            if "Message is not modified" in str(e):
                await query.answer()
                return

            # Если сообщение не найдено (удалено) или нельзя отредактировать (например, было фото),
            # отправляем новое сообщение.
            logger.warning(f"Edit failed ({e}), sending new message instead.")
            try:
                # Используем effective_chat, так как query.message может быть уже неактуален
                await update.effective_chat.send_message(text, reply_markup=reply_markup)
            except Exception as send_e:
                logger.error(f"Failed to send fallback message: {send_e}")

            await query.answer()
    elif update.message:
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
            TEXTS['ru']['welcome_lang'],  # Показываем на RU, чтобы дать выбор
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


# --- 2. Главное меню и Навигация ---

async def nav_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Коллбэк 'nav_main_menu'. Возвращает в Главное меню."""
    query = update.callback_query
    if query:
        await query.answer()

    # Удаляем временное скопированное сообщение из чата (если оно есть)
    temp_msg_id = context.user_data.get('temp_task_message_id')
    if temp_msg_id and query:
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=temp_msg_id)
        except Exception as e:
            logger.warning(f"Не удалось удалить временное сообщение {temp_msg_id}: {e}")
        context.user_data.pop('temp_task_message_id', None)

    if 'current_task_id' in context.user_data:
        del context.user_data['current_task_id']

    return await show_main_menu(update, context)


async def nav_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает экран 'Мои задачи' (Обновленный дизайн)"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message

        # Удаляем временное скопированное сообщение из чата (если оно есть)
        temp_msg_id = context.user_data.get('temp_task_message_id')
        if temp_msg_id:
            try:
                await context.bot.delete_message(chat_id=query.message.chat_id, message_id=temp_msg_id)
            except Exception as e:
                logger.warning(f"Не удалось удалить временное сообщение {temp_msg_id}: {e}")
            context.user_data.pop('temp_task_message_id', None)
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

        for task in tasks:
            # --- FIX IS HERE: Removed the second argument ---
            icon = determine_task_status_color(task['id'])
            # ------------------------------------------------

            # Определяем текстовый статус для списка
            if icon == '🟢':
                status_txt = get_text('status_text_active', context)
            elif icon == '🟡':
                status_txt = get_text('status_text_finishing', context)
            else:
                status_txt = get_text('status_text_inactive', context)

            # Формируем строку списка
            # Название - первые 4 слова (используем хелпер)
            smart_name = generate_smart_name(task['task_name'] or "", context, limit=4)

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
        await message.reply_text(text, reply_markup=timezone_keyboard(context))
    else:
        text = get_text('select_timezone', context)
        await update.message.reply_text(text, reply_markup=timezone_keyboard(context))
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
    """
    Selects a weekday. Strictly enforces mutual exclusivity:
    If a weekday is picked, ALL specific dates are removed.
    """
    query = update.callback_query
    # We do NOT answer immediately here, let task_select_calendar handle it or do it at the end

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    try:
        weekday = int(query.data.replace("calendar_wd_", ""))
    except ValueError:
        return CALENDAR_VIEW

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)

    # 1. Enforce Mutual Exclusivity: Remove ANY specific dates
    # If we are selecting a weekday, we cannot have specific dates.
    db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_date IS NOT NULL",
             (task_id,), commit=True)

    # 2. Get current weekday schedules
    schedules = get_task_schedules(task_id)
    selected_weekdays = list(set([s['schedule_weekday'] for s in schedules if s['schedule_weekday'] is not None]))

    # 3. Toggle Weekday
    if weekday in selected_weekdays:
        # Remove
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_weekday = %s",
                 (task_id, weekday), commit=True)
        selected_weekdays.remove(weekday)

        # If no weekdays left, cleanup is automatic via db logic usually,
        # but good to ensure we don't leave empty rows if any.
        if not selected_weekdays:
            remove_task_schedules(task_id)  # Safe because dates were already deleted above
    else:
        # Add
        # Check Limits
        max_weekdays = limits.get('date_slots', 7)  # reuse date_slots for weekdays limit
        if max_weekdays > 7: max_weekdays = 7

        if len(selected_weekdays) >= max_weekdays:
            alert_text = get_text('limit_error_weekdays', context).format(
                current=len(selected_weekdays),
                max=max_weekdays,
                tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=True)
            return CALENDAR_VIEW

        # Insert new weekday
        # Preserve times if they exist
        times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

        if times:
            for time_str in times:
                add_task_schedule(task_id, 'weekday_and_time', schedule_weekday=weekday, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'weekday', schedule_weekday=weekday)

    # 4. Refresh View
    # We simply call task_select_calendar, which re-reads the DB and renders the correct view.
    # This ensures what the user sees is exactly what is in the DB.
    return await task_select_calendar(update, context)


def get_task_constructor_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Form text for task constructor with Dynamic Traffic Light Status and Smart Duration Formatting"""
    task_id = context.user_data.get('current_task_id')

    # --- HANDLE NEW TASK (No ID) ---
    if not task_id:
        title = get_text('task_constructor_title', context)
        status_val = f"🔴 {get_text('status_text_inactive', context)}"
        task_name = get_text('task_default_name', context)

        channels_status = get_text('status_not_selected', context)
        message_status = get_text('status_not_set', context)
        dates_text = get_text('status_not_selected', context)
        weekdays_text = get_text('status_not_selected', context)
        times_text = get_text('status_not_selected', context)
        pin_text = get_text('status_no', context)
        delete_text = get_text('status_no', context)
        post_type_status = get_text('status_repost', context)
        pin_notify_status = get_text('status_no', context)
        report_status = get_text('status_no', context)
        advertiser_text = get_text('status_not_set', context)

        text = f"{title}\n\n"
        text += f"**{get_text('task_status_label', context)}{status_val}**\n\n"
        text += f"{task_name}\n"
        text += f"{get_text('header_channels', context)}{channels_status}\n"
        text += f"{get_text('header_message', context)}{message_status}\n"
        text += f"{get_text('header_weekdays', context)}{weekdays_text}\n"
        text += f"{get_text('header_time', context)}{times_text}\n"
        text += f"{get_text('header_pin', context)}{pin_text}\n"
        text += f"{get_text('header_autodelete', context)}{delete_text}\n"
        text += f"{get_text('header_post_type', context)}{post_type_status}\n"
        text += f"{get_text('header_pin_notify', context)}{pin_notify_status}\n"
        text += f"{get_text('header_report', context)}{report_status}\n"
        text += f"{get_text('header_advertiser', context)}{advertiser_text}\n"
        return text

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

    # --- Smart Name Truncation ---
    raw_name = task['task_name'] if task['task_name'] else get_text('task_default_name', context)
    display_name = generate_smart_name(raw_name, context, limit=4) if task['task_name'] else raw_name

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
            weekdays_text = get_text('status_weekdays_count', context).format(count=len(unique_weekdays),
                                                                              suffix=days_suffix)

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
        advertiser_user = db_query("SELECT username FROM users WHERE user_id = %s", (task['advertiser_user_id'],),
                                   fetchone=True)
        if advertiser_user and advertiser_user.get('username'):
            advertiser_text = f"✅ @{advertiser_user['username']}"
        else:
            advertiser_text = get_text('status_advertiser_id', context).format(
                advertiser_user_id=task['advertiser_user_id'])

    # Pin Duration
    pin_text = get_text('status_no', context)
    if task['pin_duration'] > 0:
        if task['pin_duration'] % 24 == 0:
            val = task['pin_duration'] // 24
            pin_text = get_text('status_pin_duration', context).format(duration=val, suffix=days_suffix)
        else:
            pin_text = get_text('status_pin_duration', context).format(duration=task['pin_duration'],
                                                                       suffix=hours_suffix)

    # Auto Delete
    delete_text = get_text('status_no', context)
    if task['auto_delete_hours'] > 0:
        if task['auto_delete_hours'] % 24 == 0:
            val = task['auto_delete_hours'] // 24
            delete_text = get_text('status_delete_duration', context).format(duration=val, suffix=days_suffix)
        else:
            delete_text = get_text('status_delete_duration', context).format(duration=task['auto_delete_hours'],
                                                                             suffix=hours_suffix_short)

    status_yes = get_text('status_yes', context)
    status_no = get_text('status_no', context)

    pin_notify_status = status_yes if task['pin_notify'] else status_no
    report_status = status_yes if task['report_enabled'] else status_no
    post_type_status = get_text('status_from_bot', context) if task['post_type'] == 'from_bot' else get_text(
        'status_repost', context)

    channels_status = get_text('status_dates_count', context).format(count=channels_count,
                                                                     suffix=count_suffix) if channels_count > 0 else get_text(
        'status_not_selected', context)

    # --- MESSAGE STATUS: Show snippet if available ---
    if task['content_message_id']:
        if task.get('message_snippet'):
            message_status = f"✅ {task['message_snippet']}"
        else:
            message_status = get_text('status_set', context)
    else:
        message_status = get_text('status_not_set', context)
    # -------------------------------------------------

    title = get_text('task_constructor_title', context)
    if task_id:
        title += f" #{task_id}"

    text = f"{title}\n\n"
    text += f"**{status_label}{status_val}**\n\n"
    text += f"{display_name}\n"
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


async def show_task_constructor(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False):
    """
    Показывает главный экран конструктора задач.
    Added force_new_message: для принудительной отправки нового сообщения (например, после удаления превью).
    """
    chat_id = None
    if update.callback_query:
        chat_id = update.callback_query.message.chat_id
    elif update.message:
        chat_id = update.message.chat_id

    if chat_id:
        # Cleanup PREVIEW message
        temp_msg_id = context.user_data.get('temp_task_message_id')
        if temp_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=temp_msg_id)
            except Exception:
                pass
            context.user_data.pop('temp_task_message_id', None)

        # Cleanup PROMPT message
        temp_prompt_id = context.user_data.get('temp_prompt_message_id')
        if temp_prompt_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=temp_prompt_id)
            except Exception:
                pass
            context.user_data.pop('temp_prompt_message_id', None)

    text = get_task_constructor_text(context)

    # Если запрошено новое сообщение или у нас нет query для редактирования
    if force_new_message and chat_id:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=task_constructor_keyboard(context))
    else:
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


async def ensure_task_and_refresh(user_id: int, context: ContextTypes.DEFAULT_TYPE, auto_activate: bool = False) -> int:
    """
    Creates a task in DB if it doesn't exist (Lazy Creation).
    Updates status to 'active' if required.
    Triggers Hot-Reload of the scheduler.
    """
    task_id = get_or_create_task_id(user_id, context)

    if auto_activate:
        # If adding a time/date, we assume the user wants it active
        await update_task_field(task_id, 'status', 'active', context)

    # Hot-reload: Cancel old jobs and reschedule based on new params immediately
    await refresh_task_jobs(task_id, context)

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

    # Мы возвращаемся с экрана (превью), который удаляется внутри show_task_constructor (cleanup).
    # Поэтому мы должны принудительно отправить новое сообщение, так как старое (кнопка Назад) исчезнет.
    return await show_task_constructor(update, context, force_new_message=True)


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
    """Updates name and triggers hot-reload if active"""
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)

    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    task_name = update.message.text.strip()

    # This triggers the Hot Reload via update_task_field -> refresh_task_jobs
    await update_task_field(task_id, 'task_name', task_name, context)

    await update.message.reply_text(get_text('task_name_saved', context))
    return await show_task_constructor(update, context)


# --- Установка Сообщения ---
async def task_ask_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📝 Сообщение'"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # Cleanup previous temp message if any
    previous_msg_id = context.user_data.get('temp_task_message_id')
    if previous_msg_id:
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=previous_msg_id)
        except Exception:
            pass
        context.user_data.pop('temp_task_message_id', None)

    # Cleanup previous prompt message if any
    previous_prompt_id = context.user_data.get('temp_prompt_message_id')
    if previous_prompt_id:
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=previous_prompt_id)
        except Exception:
            pass
        context.user_data.pop('temp_prompt_message_id', None)


    if task and task['content_message_id']:
        # --- EDIT MODE ---
        text = get_text('task_message_current_prompt', context)

        # 1. Edit the prompt message (remove buttons from here)
        await query.delete_message()

        # Save ID of the prompt message to delete it later on "Back"
        context.user_data['temp_prompt_message_id'] = query.message.message_id

        # 2. Define Keyboard for the PREVIEW (Delete & Back)
        keyboard = [
            [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
            [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor")]
        ]

        # 3. Check for Media Group (Album)
        media_group_json = task.get('media_group_data')

        if media_group_json:
            # === SHOWING MEDIA GROUP ===
            try:
                # Parse JSON if it's a string
                media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(media_group_json)

                input_media = []
                caption_to_use = media_data.get('caption', '')

                # Reconstruct InputMedia objects
                for i, f in enumerate(media_data['files']):
                    media_obj = None
                    # Assign caption only to the first item
                    current_caption = caption_to_use if i == 0 else None

                    if f['type'] == 'photo':
                        media_obj = InputMediaPhoto(media=f['media'], caption=current_caption,
                                                    has_spoiler=f.get('has_spoiler', False))
                    elif f['type'] == 'video':
                        media_obj = InputMediaVideo(media=f['media'], caption=current_caption,
                                                    has_spoiler=f.get('has_spoiler', False))
                    elif f['type'] == 'document':
                        media_obj = InputMediaDocument(media=f['media'], caption=current_caption)
                    elif f['type'] == 'audio':
                        media_obj = InputMediaAudio(media=f['media'], caption=current_caption)

                    if media_obj:
                        input_media.append(media_obj)

                # Send the album
                if input_media:
                    await context.bot.send_media_group(chat_id=query.message.chat_id, media=input_media)



                # Send separate message for buttons (Albums can't have buttons)
                control_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"{text}\n\n{get_text('choose_options', context)}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

                # Save control message ID for cleanup
                context.user_data['temp_task_message_id'] = control_msg.message_id

            except Exception as e:
                logger.error(f"Failed to preview media group: {e}")
                await query.message.reply_text("⚠️ Error displaying full album preview.")

        else:
            # === SHOWING SINGLE MESSAGE ===
            try:
                # Copy message (Preview) WITH buttons attached
                copied_message = await context.bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=task['content_chat_id'],
                    message_id=task['content_message_id'],
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                # Save ID of preview message
                context.user_data['temp_task_message_id'] = copied_message.message_id

            except Exception as e:
                logger.warning(f"Не удалось скопировать старое сообщение для task {task_id}: {e}")
                await query.message.reply_text(get_text('task_message_display_error', context))

        return TASK_SET_MESSAGE

    else:
        # --- ASK MODE ---
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
        return await show_task_constructor(update, context)

    # Обнуляем данные в БД
    await update_task_field(task_id, 'content_message_id', None, context)
    await update_task_field(task_id, 'content_chat_id', None, context)
    db_query("UPDATE tasks SET message_snippet = NULL WHERE id = %s", (task_id,), commit=True)

    await query.answer(get_text('task_message_deleted_alert', context), show_alert=True)

    # Возвращаемся в конструктор.
    # Так как show_task_constructor выполнит cleanup и удалит превью, нужно отправить новое сообщение.
    return await show_task_constructor(update, context, force_new_message=True)


async def task_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles receiving a message (or media group) for the task.
    """
    user_id = update.message.from_user.id
    chat_id = update.effective_chat.id

    # Check if this message is part of a media group
    if update.message.media_group_id:
        media_group_id = update.message.media_group_id

        # Initialize buffer if not exists
        if 'media_group_buffer' not in context.user_data:
            context.user_data['media_group_buffer'] = {}

        if media_group_id not in context.user_data['media_group_buffer']:
            context.user_data['media_group_buffer'][media_group_id] = []

        # Add the current message object to the buffer
        context.user_data['media_group_buffer'][media_group_id].append(update.message)

        # Schedule the processing job (debounce)
        # We use a unique job name based on media_group_id to prevent duplicates
        job_name = f"process_mg_{media_group_id}"
        existing_jobs = context.job_queue.get_jobs_by_name(job_name)

        if not existing_jobs:
            # Schedule execution in 2 seconds
            # IMPORTANT: We MUST pass user_id and chat_id here so context.user_data is available in the job
            context.job_queue.run_once(
                process_media_group,
                when=2,
                data={'media_group_id': media_group_id},
                name=job_name,
                user_id=user_id,  # <--- FIX: Enables context.user_data in callback
                chat_id=chat_id  # <--- FIX: Enables context.chat_data in callback
            )
        return TASK_SET_MESSAGE

    # --- Standard Single Message Logic (Existing) ---
    return await save_single_task_message(update, context)


async def save_single_task_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper to save a standard single message (Refactored from original)"""
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)

    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    message = update.message
    content_text = message.text or message.caption or ""

    # ... [Existing Snippet Generation Code] ...
    if not content_text:
        if message.photo:
            content_text = "🖼 [Photo]"
        elif message.video:
            content_text = "📹 [Video]"
        elif message.document:
            content_text = "📄 [File]"
        elif message.audio:
            content_text = "🎵 [Audio]"
        elif message.voice:
            content_text = "🎤 [Voice]"
        elif message.sticker:
            content_text = "👾 [Sticker]"
        else:
            content_text = "📦 [Media]"

    # Generate snippet
    words = content_text.split()
    snippet = " ".join(words[:4]) + ("..." if len(words) > 4 else "")

    # Set Task Name if empty
    task = get_task_details(task_id)
    if not task.get('task_name'):
        new_name = snippet[:200] if snippet else "New Task"
        await update_task_field(task_id, 'task_name', new_name, context)

    # --- 🚀 NEW LOGIC START: Auto-detect Post Type ---
    # Check if the message is forwarded
    # We check forward_date (legacy/standard) or forward_origin (new API)
    is_forward = (message.forward_date is not None) or \
                 (hasattr(message, 'forward_origin') and message.forward_origin is not None)

    new_post_type = 'repost' if is_forward else 'from_bot'

    # Update the post_type in the database
    await update_task_field(task_id, 'post_type', new_post_type, context)
    # --- 🚀 NEW LOGIC END ---

    # Save to DB (Clear media_group_data if switching to single message)
    content_message_id = message.message_id
    content_chat_id = message.chat_id

    await update_task_field(task_id, 'content_message_id', content_message_id, context)
    await update_task_field(task_id, 'content_chat_id', content_chat_id, context)

    # Directly update fields that update_task_field doesn't handle specifically
    db_query("UPDATE tasks SET message_snippet = %s, media_group_data = NULL WHERE id = %s",
             (snippet, task_id), commit=True)

    # UI Feedback
    await send_task_preview(user_id, task_id, context, is_group=False)
    return TASK_SET_MESSAGE


async def process_media_group(context: ContextTypes.DEFAULT_TYPE):
    """
    Job that runs after a short delay to process a buffered media group.
    Includes logic to auto-detect if the album is a Forward or Direct Upload.
    """
    job = context.job
    job_data = job.data
    media_group_id = job_data['media_group_id']

    # User ID is now attached to the job itself because we passed it in run_once
    user_id = job.user_id

    # Safety check
    if not context.user_data:
        logger.error(f"context.user_data is None for job {job.name}. Ensure user_id was passed to run_once.")
        return

    # Retrieve messages from buffer
    buffer = context.user_data.get('media_group_buffer', {})
    messages = buffer.pop(media_group_id, [])

    # Save the cleaned buffer back to user_data
    if not buffer:
        context.user_data.pop('media_group_buffer', None)

    if not messages:
        logger.warning(f"No messages found for media group {media_group_id}")
        return

    # Sort messages by message_id to ensure correct order
    messages.sort(key=lambda m: m.message_id)

    task_id = get_or_create_task_id(user_id, context)

    # Extract data
    media_list = []
    caption = ""

    for msg in messages:
        # Capture caption from the first message that has one
        if msg.caption and not caption:
            caption = msg.caption

        file_id = None
        file_type = None

        if msg.photo:
            file_id = msg.photo[-1].file_id  # Best quality
            file_type = 'photo'
        elif msg.video:
            file_id = msg.video.file_id
            file_type = 'video'
        elif msg.document:
            file_id = msg.document.file_id
            file_type = 'document'
        elif msg.audio:
            file_id = msg.audio.file_id
            file_type = 'audio'

        if file_id:
            media_list.append({
                'type': file_type,
                'media': file_id,
                'has_spoiler': msg.has_media_spoiler if hasattr(msg, 'has_media_spoiler') else False
            })

    # Prepare JSON data
    media_group_data = {
        'caption': caption,
        'files': media_list
    }

    # Generate Snippet
    if caption:
        words = caption.split()
        short_caption = " ".join(words[:4])
        if len(words) > 4:
            short_caption += "..."
        snippet = f"📸 {short_caption}"
    else:
        snippet = "📸"

    # Set Task Name if empty
    task = get_task_details(task_id)
    if not task.get('task_name'):
        new_name = snippet[:200]
        await update_task_field(task_id, 'task_name', new_name, context)

    # --- 🚀 NEW LOGIC: Auto-detect Post Type (Forward vs Direct) ---
    # We check the first message in the sorted list.
    first_msg = messages[0]

    # Check for forward_date (standard) or forward_origin (new API)
    is_forward = (first_msg.forward_date is not None) or \
                 (hasattr(first_msg, 'forward_origin') and first_msg.forward_origin is not None)

    new_post_type = 'repost' if is_forward else 'from_bot'

    # Update the post_type field in the database
    await update_task_field(task_id, 'post_type', new_post_type, context)
    # -----------------------------------------------------------------

    # Save to DB
    first_msg_id = messages[0].message_id
    chat_id = messages[0].chat_id

    json_data = json.dumps(media_group_data)

    await update_task_field(task_id, 'content_message_id', first_msg_id, context)
    await update_task_field(task_id, 'content_chat_id', chat_id, context)

    db_query(
        "UPDATE tasks SET message_snippet = %s, media_group_data = %s WHERE id = %s",
        (snippet, json_data, task_id),
        commit=True
    )

    # Trigger UI update
    await send_task_preview(user_id, task_id, context, is_group=True, media_data=media_group_data)



async def send_task_preview(user_id, task_id, context, is_group=False, media_data=None):
    """Helper to send the saved confirmation and preview"""

    # Send PREVIEW
    if is_group and media_data:
        try:
            input_media = []
            caption_to_use = media_data.get('caption', '')

            for i, f in enumerate(media_data['files']):
                media = None

                # Determine InputMedia class based on file type
                media_class = None
                if f['type'] == 'photo':
                    media_class = InputMediaPhoto
                elif f['type'] == 'video':
                    media_class = InputMediaVideo
                elif f['type'] == 'document':
                    media_class = InputMediaDocument  # <--- Use InputMediaDocument
                elif f['type'] == 'audio':
                    media_class = InputMediaAudio  # <--- Use InputMediaAudio

                if media_class:
                    kwargs = {'media': f['media']}

                    # Only the first item gets the caption
                    if i == 0:
                        kwargs['caption'] = caption_to_use

                    # Photos and Videos support has_spoiler
                    if media_class in (InputMediaPhoto, InputMediaVideo):
                        kwargs['has_spoiler'] = f.get('has_spoiler', False)

                    media = media_class(**kwargs)
                    input_media.append(media)

            # --- FIX: Handle non-standard media groups ---
            # If the group contains mixed types (e.g., photo/video mixed with document/audio)
            # send_media_group will fail. We must split it or handle it carefully.
            # Telegram Bot API generally only allows photo/video groups.
            # For simplicity in preview, we use the first message ID as a fallback if the group fails.

            if input_media:
                try:
                    msgs = await context.bot.send_media_group(chat_id=user_id, media=input_media)
                    # Save ID of first message for deletion logic later
                    context.user_data['temp_task_message_id'] = msgs[0].message_id
                except TelegramError as te:
                    # If send_media_group fails (often due to mixed types), fall back to copying the first message
                    logger.warning(f"send_media_group failed (likely mixed types): {te}. Falling back to copy_message.")

                    # Fallback: Copy the original first message in the group
                    if media_data['files']:
                        first_file_id = media_data['files'][0]['media']

                        # Note: We need the original message_id, which we saved in task_message_id
                        task = get_task_details(task_id)

                        fallback_msg = await context.bot.copy_message(
                            chat_id=user_id,
                            from_chat_id=task['content_chat_id'],
                            message_id=task['content_message_id']
                        )
                        context.user_data['temp_task_message_id'] = fallback_msg.message_id
                    else:
                        raise te  # Re-raise if no files found

            else:
                await context.bot.send_message(chat_id=user_id,
                                               text="⚠️ Error: Could not compile media group for preview.")

        except Exception as e:
            logger.error(f"Group preview failed: {e}")
            await context.bot.send_message(chat_id=user_id, text="⚠️ Critical error generating group preview.")
    else:
        # Standard Single Message Preview (Existing logic)
        task = get_task_details(task_id)
        try:
            preview_msg = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=task['content_chat_id'],
                message_id=task['content_message_id']
            )
            context.user_data['temp_task_message_id'] = preview_msg.message_id
        except Exception as e:
            logger.error(f"Preview failed: {e}")

    success_text = get_text('task_message_saved', context)


    # Footer
    footer_text = get_text('task_message_preview_footer', context)
    keyboard = [
        [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor")]
    ]

    await context.bot.send_message(
        chat_id=user_id,
        text=f"{success_text}\n\n{footer_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# --- Выбор Каналов ---
async def task_select_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📢 Каналы'"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    selected_channels = get_task_channels(task_id)

    user_id = context.user_data['user_id']
    channels = get_user_channels(user_id)

    if not channels:
        await query.edit_message_text(
            get_text('dont_have_channels', context),
            reply_markup=back_to_constructor_keyboard(context)
        )
        return TASK_SELECT_CHANNELS

    text = get_text('choose_channel', context)
    await query.edit_message_text(
        text,
        reply_markup=channels_selection_keyboard(context, selected_channels)
    )
    return TASK_SELECT_CHANNELS


async def task_toggle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggling a channel selection"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    channel_id = int(query.data.replace("channel_toggle_", ""))

    selected_channels = get_task_channels(task_id)

    if channel_id in selected_channels:
        remove_task_channel(task_id, channel_id)
    else:
        add_task_channel(task_id, channel_id)

    # --- HOT RELOAD ---
    await refresh_task_jobs(task_id, context)

    # ... (rest of the function: updating keyboard) ...
    selected_channels = get_task_channels(task_id)
    # --- FIX: Use Localized Text ---
    text = get_text('task_channels_title', context)
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
        dates_str = ", ".join(
            sorted([datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m') for d in selected_dates])
        )

        month_str = datetime(year, month, 1).strftime("%B")

        header_text = get_text('calendar_header_dates', context).format(
            month_year_str=month_str,
            dates_str=dates_str
        )

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
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots,
                                                                  tariff_name=limits['name'])
    # --- ⬆️ FIXED LINE ⬆️ ---

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
    """Selects a specific date. Strictly removes any Weekdays."""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    date_str = query.data.replace("calendar_day_", "")

    # 1. Enforce Mutual Exclusivity: Remove ANY weekdays
    db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_weekday IS NOT NULL",
             (task_id,), commit=True)

    # 2. Toggle Date
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
                current=len(selected_dates),
                max=max_dates,
                tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=False)
            return CALENDAR_VIEW

        # Preserve times
        times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))
        if times:
            for time_str in times:
                add_task_schedule(task_id, 'datetime', schedule_date=date_str, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'date', schedule_date=date_str)

        await query.answer()

    # 3. Hot Reload & Refresh
    await refresh_task_jobs(task_id, context)
    return await task_select_calendar(update, context)


async def calendar_select_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Selects the whole month (Remaining Future Days) with limit checks.
    """
    query = update.callback_query
    # Do not answer query immediately to allow alerts

    task_id = context.user_data.get('current_task_id')
    year = context.user_data.get('calendar_year', datetime.now().year)
    month = context.user_data.get('calendar_month', datetime.now().month)

    # 1. Get User Timezone and "Today"
    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz = ZoneInfo(user_tz_str)
    except:
        user_tz = ZoneInfo('UTC')

    # Current date for the user
    today_user = datetime.now(user_tz).date()

    # 2. Get User Limits
    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['date_slots']

    # 3. Calculate Days in Month
    _, num_days = calendar.monthrange(year, month)

    # 4. Filter: Collect only valid future dates
    valid_dates_to_add = []

    for day in range(1, num_days + 1):
        # Create date object for the specific day in the calendar
        current_date_obj = datetime(year, month, day).date()

        # SKIP PAST DAYS: If the day is before today, don't include it
        if current_date_obj < today_user:
            continue

        valid_dates_to_add.append(current_date_obj)

    count_to_add = len(valid_dates_to_add)

    # --- EDGE CASE: Month is completely in the past ---
    if count_to_add == 0:
        await query.answer(get_text('calendar_ignore_past', context),
                           show_alert=True)
        return CALENDAR_VIEW

    # --- CHECK LIMIT (Against remaining days only) ---
    if count_to_add > max_slots:
        alert_text = get_text('limit_error_dates', context).format(
            current=0,
            max=max_slots,
            tariff=limits['name']
        )
        # Custom explanation
        alert_text += get_text('days_alert_text', context).format(
            count_to_add=count_to_add,
            max_slots=max_slots
        )

        await query.answer(alert_text, show_alert=True)
        return CALENDAR_VIEW
    # -----------------------

    await query.answer()  # Valid, close loading animation

    # 5. Apply Changes
    # Remove old schedules
    remove_task_schedules(task_id)

    # Add only the valid future days
    for date_obj in valid_dates_to_add:
        date_str = date_obj.strftime("%Y-%m-%d")
        add_task_schedule(task_id, 'date', schedule_date=date_str)

    # Hot-reload (if task is active)
    await refresh_task_jobs(task_id, context)

    # 6. Update UI
    schedules = get_task_schedules(task_id)
    selected_dates = [s['schedule_date'].strftime('%Y-%m-%d') for s in schedules if s['schedule_date']]

    month_year = datetime(year, month, 1).strftime("%B %Y")

    # Message Text
    text = get_text('calendar_header_dates', context).format(
        month_year_str=month_year,
        dates_str=f"{len(selected_dates)} days selected"
    )
    text += get_text('calendar_info_weekdays', context)
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_slots, tariff_name=limits['name'])

    await query.edit_message_text(
        text,
        reply_markup=calendar_keyboard(context, year, month, selected_dates, [], today_user),
        parse_mode='Markdown'
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
    text += get_text('calendar_info_limit_slots', context).format(max_time_slots=max_time_slots,
                                                                  tariff_name=limits['name'])
    # --- ⬆️ FIXED LINE ⬆️ ---

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
    """Нажата кнопка '🕐 Время' (Задача 3: вывод выбранных слотов)"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')

    # Получаем выбранное время
    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))
    selected_times.sort()  # Сортируем для красоты

    user_tz_str = context.user_data.get('timezone', 'Europe/Moscow')
    try:
        user_tz_obj = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        user_tz_obj = ZoneInfo('UTC')
        user_tz_str = 'UTC (Default)'

    current_time_str = datetime.now(user_tz_obj).strftime('%H:%M')

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    # Формирование текста
    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz_str)}"
    text += f"\n🕒 **{get_text('time_current_info', context).format(current_time=current_time_str)}**"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=len(selected_times), slots=max_slots)}"

    # --- ИЗМЕНЕНИЕ (Задача 3): Вывод конкретного времени ---
    if selected_times:
        times_str = ", ".join(selected_times)
        # Можно добавить ключ локализации time_list_label, пока хардкод для примера
        label = get_text('selected_time', context)
        text += f"\n\n{label} **{times_str}**"
    # -----------------------------------------------------

    await query.edit_message_text(
        text,
        reply_markup=time_selection_keyboard(context, selected_times)
    )
    return TIME_SELECTION


async def time_slot_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор временного слота (Задача 3: обновление списка)"""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    time_str = query.data.replace("time_select_", "")

    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))

    user_tariff = context.user_data.get('tariff', 'free')
    limits = get_tariff_limits(user_tariff)
    max_slots = limits['time_slots']

    if time_str in selected_times:
        db_query("DELETE FROM task_schedules WHERE task_id = %s AND schedule_time = %s",
                 (task_id, time_str), commit=True)
        await query.answer()
    else:
        if len(selected_times) >= max_slots:
            alert_text = get_text('limit_error_times', context).format(
                current=len(selected_times), max=max_slots, tariff=limits['name']
            )
            await query.answer(alert_text, show_alert=False)
            return TIME_SELECTION

        dates = [s for s in schedules if s['schedule_date']]
        if dates:
            unique_dates_data = {d['schedule_date'] for d in dates}
            for date_val in unique_dates_data:
                add_task_schedule(task_id, 'datetime', schedule_date=date_val, schedule_time=time_str)
        else:
            add_task_schedule(task_id, 'time', schedule_time=time_str)
        await query.answer()

    await refresh_task_jobs(task_id, context)

    # --- Обновление UI с новым списком ---
    schedules = get_task_schedules(task_id)
    selected_times = list(set([s['schedule_time'].strftime('%H:%M') for s in schedules if s['schedule_time']]))
    selected_times.sort()  # Сортировка

    user_tz = context.user_data.get('timezone', 'Europe/Moscow')
    text = get_text('time_selection_title', context)
    text += f"\n{get_text('time_tz_info', context).format(timezone=user_tz)}"
    text += f"\n{get_text('time_slots_limit', context).format(slots=max_slots)} (Тариф: {limits['name']})"
    text += f"\n{get_text('time_selected_slots', context).format(count=len(selected_times), slots=max_slots)}"

    # Вывод списка
    if selected_times:
        times_str = ", ".join(selected_times)
        label = get_text('selected_time', context)
        text += f"\n\n{label} **{times_str}**"

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
            logger.error(
                f"Ошибка комбинирования datetime для задачи {task_id}: {schedule_date} {schedule_time} с tz {user_tz}. Ошибка: {e}")
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
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)

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

    # --- TRIGGER HOT RELOAD ---
    await refresh_task_jobs(task_id, context)

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
    """Настройка закрепления (Вход)"""
    query = update.callback_query
    await query.answer()

    # Get current value to show checkmark immediately
    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)
    current_duration = task['pin_duration'] if task else 0

    text = get_text('duration_ask_pin', context)
    await query.edit_message_text(
        text,
        reply_markup=pin_duration_keyboard(context, current_duration)
    )
    return TASK_SET_PIN


async def pin_duration_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор длительности закрепления (Действие)"""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    duration = int(query.data.replace("pin_", ""))

    # Update DB
    await update_task_field(task_id, 'pin_duration', duration, context)

    # STAY on the same screen, but update the keyboard to move the checkmark
    text = get_text('duration_ask_pin', context)
    try:
        await query.edit_message_text(
            text,
            reply_markup=pin_duration_keyboard(context, current_duration=duration)
        )
    except TelegramError:
        # Ignore "Message is not modified" if user clicks the same button
        pass

    # Return the SAME state instead of calling show_task_constructor
    return TASK_SET_PIN


# --- Настройка автоудаления ---
async def task_set_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка автоудаления (Вход)"""
    query = update.callback_query
    await query.answer()

    # Get current value to show checkmark immediately
    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)
    current_duration = task['auto_delete_hours'] if task else 0

    text = get_text('duration_ask_delete', context)
    await query.edit_message_text(
        text,
        reply_markup=delete_duration_keyboard(context, current_duration)
    )
    return TASK_SET_DELETE


async def delete_duration_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор длительности автоудаления (Действие)"""
    query = update.callback_query

    user_id = query.from_user.id
    task_id = get_or_create_task_id(user_id, context)
    duration = int(query.data.replace("delete_", ""))

    # Update DB
    await update_task_field(task_id, 'auto_delete_hours', duration, context)

    # STAY on the same screen, but update the keyboard to move the checkmark
    text = get_text('duration_ask_delete', context)
    try:
        await query.edit_message_text(
            text,
            reply_markup=delete_duration_keyboard(context, current_duration=duration)
        )
    except TelegramError:
        # Ignore "Message is not modified" if user clicks the same button
        pass

    # Return the SAME state instead of calling show_task_constructor
    return TASK_SET_DELETE


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
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)
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
    await update_task_field(task_id, 'advertiser_user_id', advertiser_user['user_id'], context)

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
    await update_task_field(task_id, 'pin_notify', new_value, context)

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
    await update_task_field(task_id, 'report_enabled', new_value, context)

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
    await update_task_field(task_id, 'post_type', new_value, context)

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

    # Возвращаемся в Мои задачи (FIX TASK 2)
    return await nav_my_tasks(update, context)


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

        # Используем правильную клавиатуру - возврат в конструктор для исправления ошибок
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
                InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
            ]
        ])

        await query.edit_message_text(
            error_text,
            reply_markup=keyboard
        )
        return TASK_CONSTRUCTOR

    # --- 2. Активация ---

    # Обновляем статус в БД
    await update_task_field(task_id, 'status', 'active', context)

    # ВАЖНО: Очищаем старые джобы перед созданием новых (на случай повторной активации)
    await cancel_task_jobs(task_id, context)

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
        await update_task_field(task_id, 'status', 'inactive', context)
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
    await update_task_field(task_id, 'status', 'inactive', context)

    # 2. Отмена джобов
    await cancel_task_jobs(task_id, context)

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
    Publishes the post (Single or Media Group).
    UPDATED: Now notifies the Advertiser and handles logic for Pin notifications.
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

    # Fetch Job info
    job_data = db_query("SELECT * FROM publication_jobs WHERE id = %s AND status = 'scheduled'", (job_id,),
                        fetchone=True)

    if not job_data:
        logger.error(f"Job {job_id} not found in DB or already executed.")
        return

    # Fetch Task info (specifically for media group data)
    task_data = db_query("SELECT media_group_data FROM tasks WHERE id = %s", (job_data['task_id'],), fetchone=True)
    media_group_json = task_data.get('media_group_data')

    user_id = job_data['user_id']
    channel_id = job_data['channel_id']
    content_message_id = job_data['content_message_id']
    content_chat_id = job_data['content_chat_id']
    auto_delete_hours = job_data['auto_delete_hours']
    pin_duration = job_data['pin_duration']
    advertiser_user_id = job_data['advertiser_user_id']  # Get advertiser ID

    posted_message_id = None

    try:
        # --- SENDING LOGIC ---
        if media_group_json:
            # === OPTION A: SEND MEDIA GROUP ===
            media_data = media_group_json
            if isinstance(media_data, str):
                media_data = json.loads(media_data)

            input_media = []
            caption_to_use = media_data.get('caption')

            for i, f in enumerate(media_data['files']):
                media_obj = None

                # Check file type and create the correct InputMedia object
                if f['type'] == 'photo':
                    media_obj = InputMediaPhoto(
                        media=f['media'],
                        caption=caption_to_use if i == 0 else None,
                        has_spoiler=f.get('has_spoiler', False)
                    )
                elif f['type'] == 'video':
                    media_obj = InputMediaVideo(
                        media=f['media'],
                        caption=caption_to_use if i == 0 else None,
                        has_spoiler=f.get('has_spoiler', False)
                    )
                # (Add Audio/Document handling if needed)

                if media_obj:
                    input_media.append(media_obj)

            if input_media:
                sent_messages = await bot.send_media_group(
                    chat_id=channel_id,
                    media=input_media,
                    disable_notification=not job_data['pin_notify']
                )
                posted_message_id = sent_messages[0].message_id
                logger.info(f"Media Group published in {channel_id}, first msg_id: {posted_message_id}")
            else:
                raise Exception("Media group data found but input list empty")

        else:
            # === OPTION B: SEND SINGLE MESSAGE (Copy) ===
            sent_message = await bot.copy_message(
                chat_id=channel_id,
                from_chat_id=content_chat_id,
                message_id=content_message_id,
                disable_notification=not job_data['pin_notify']
            )
            posted_message_id = sent_message.message_id
            logger.info(f"Single Job published in {channel_id}, msg_id: {posted_message_id}")

        # --- PREPARE DATA FOR NOTIFICATIONS ---
        channel_title = str(channel_id)
        task_name = f"#{job_data['task_id']}"
        try:
            channel_info = db_query("SELECT channel_title FROM channels WHERE channel_id = %s", (channel_id,),
                                    fetchone=True)
            if channel_info and channel_info.get('channel_title'):
                channel_title = channel_info['channel_title']

            task_info = db_query("SELECT task_name FROM tasks WHERE id = %s", (job_data['task_id'],), fetchone=True)
            if task_info and task_info.get('task_name'):
                task_name = task_info['task_name']
        except Exception as e:
            logger.warning(f"Error fetching metadata for notifications: {e}")

        # --- NOTIFY USER (CREATOR) ---
        try:
            user_settings = get_user_settings(user_id)
            lang = user_settings.get('language_code', 'en')

            title_txt = get_text('notify_post_published_title', context, lang=lang)
            channel_lbl = get_text('notify_post_published_channel', context, lang=lang)
            task_lbl = get_text('notify_post_published_task', context, lang=lang)

            notify_text = f"{title_txt}\n{channel_lbl} {channel_title}\n{task_lbl} {task_name}"
            await bot.send_message(chat_id=user_id, text=notify_text, disable_notification=True)
        except Exception as e:
            logger.warning(f"Failed to notify user {user_id}: {e}")

        # --- TASK 2: NOTIFY ADVERTISER ---
        if advertiser_user_id and advertiser_user_id != user_id:
            try:
                adv_settings = get_user_settings(advertiser_user_id)
                adv_lang = adv_settings.get('language_code', 'en')

                adv_title = get_text('notify_post_published_title', context, lang=adv_lang)
                adv_channel = get_text('notify_post_published_channel', context, lang=adv_lang)
                adv_task = get_text('notify_post_published_task', context, lang=adv_lang)

                adv_notify_text = f"{adv_title}\n{adv_channel} {channel_title}\n{adv_task} {task_name}"

                # Send notification to advertiser
                await bot.send_message(chat_id=advertiser_user_id, text=adv_notify_text, disable_notification=True)
                logger.info(f"Advertiser {advertiser_user_id} notified for task {job_data['task_id']}")
            except Exception as e:
                logger.warning(f"Failed to notify advertiser {advertiser_user_id}: {e}")

        # --- PINNING LOGIC ---
        if pin_duration > 0 and posted_message_id:
            try:
                await bot.pin_chat_message(
                    chat_id=channel_id,
                    message_id=posted_message_id,
                    disable_notification=not job_data['pin_notify']
                )

                # Note: The 'Message Pinned' service message is deleted by 'delete_pin_service_message' handler

                if auto_delete_hours == 0 or pin_duration < auto_delete_hours:
                    unpin_time_utc = datetime.now(ZoneInfo('UTC')) + timedelta(hours=pin_duration)
                    unpin_job_name = f"unpin_{job_id}_msg_{posted_message_id}"

                    context.application.job_queue.run_once(
                        execute_unpin_job,
                        when=unpin_time_utc,
                        data={'channel_id': channel_id, 'message_id': posted_message_id, 'job_id': job_id},
                        name=unpin_job_name,
                        job_kwargs={'misfire_grace_time': 600}
                    )
            except TelegramError as e:
                logger.error(f"Error pinning job {job_id}: {e}")

        # --- AUTO DELETE LOGIC ---
        if auto_delete_hours > 0 and posted_message_id:
            delete_time_utc = datetime.now(ZoneInfo('UTC')) + timedelta(hours=auto_delete_hours)
            delete_job_name = f"del_{job_id}_msg_{posted_message_id}"

            context.application.job_queue.run_once(
                execute_delete_job,
                when=delete_time_utc,
                data={'channel_id': channel_id, 'message_id': posted_message_id, 'job_id': job_id},
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


async def restore_active_tasks(application: Application):
    """
    Run on startup:
    1. Cleans up 'stuck' jobs in DB from previous run.
    2. Finds all ACTIVE tasks.
    3. Re-schedules them in the JobQueue.
    """
    logger.info("🔄 Restoring active tasks on startup...")

    # 1. Clean up: Mark old 'scheduled' jobs as cancelled because the JobQueue memory is empty now
    db_query("UPDATE publication_jobs SET status = 'cancelled' WHERE status = 'scheduled'", commit=True)

    # 2. Get all ACTIVE tasks
    active_tasks = db_query("SELECT id, user_id FROM tasks WHERE status = 'active'", fetchall=True) or []

    count = 0
    for task in active_tasks:
        task_id = task['id']
        user_id = task['user_id']

        # Get user timezone
        user_settings = get_user_settings(user_id)
        user_tz = user_settings.get('timezone', 'Europe/Moscow')

        # 3. Re-create jobs
        # Note: create_publication_jobs_for_task is synchronous in your code,
        # but we are inside an async function. It's safe to call as long as it doesn't block heavily.
        jobs_created = create_publication_jobs_for_task(task_id, user_tz, application)
        count += jobs_created

    logger.info(f"✅ Restored {len(active_tasks)} active tasks. Scheduled {count} future publications.")


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

    async def post_init(app: Application):
        await restore_active_tasks(app)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

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
            CallbackQueryHandler(nav_tariff, pattern="^nav_tariff$"),  # FIX TASK 1
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
            CallbackQueryHandler(nav_my_tasks, pattern="^nav_my_tasks$"),  # FIX TASK 3
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
            CallbackQueryHandler(task_back_to_constructor, pattern="^task_back_to_constructor$"),  # Для возврата из ошибок валидации
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
            CallbackQueryHandler(calendar_select_all, pattern="^calendar_select_all$"), # <-- УДАЛЕНО (или закомментировано)
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

    application.add_handler(MessageHandler(filters.StatusUpdate.PINNED_MESSAGE, delete_pin_service_message))

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
    # Restore scheduled tasks automatically

    # Then start receiving updates
    application.run_polling()


if __name__ == "__main__":
    main()
