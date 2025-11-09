# import os
# import logging
# from datetime import datetime, timedelta
# from typing import Optional, Dict, Any, List
# import re
#
# from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
# from telegram.ext import (
#     Application,
#     CommandHandler,
#     CallbackQueryHandler,
#     MessageHandler,
#     ContextTypes,
#     filters,
#     ChatMemberHandler,
# )
# from telegram.error import TelegramError, Forbidden
# from apscheduler.schedulers.asyncio import AsyncIOScheduler
# from apscheduler.triggers.date import DateTrigger
# import psycopg2
# from psycopg2.extras import RealDictCursor
# from psycopg2.pool import SimpleConnectionPool
# from psycopg2 import errorcodes
# import json
# from dotenv import load_dotenv
#
# load_dotenv()
#
# # --- Настройка логирования ---
# logging.basicConfig(
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     level=logging.INFO
# )
# logger = logging.getLogger(__name__)
#
# # --- Конфигурация ---
# BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
# DATABASE_URL = os.getenv('DATABASE_URL')
# OWNER_ID = int(os.getenv('OWNER_ID', '0'))
#
# # --- Пул соединений с БД ---
# try:
#     if not DATABASE_URL:
#         logger.critical("DATABASE_URL не установлен! Бот не может работать без БД.")
#         db_pool = None
#     else:
#         db_pool = SimpleConnectionPool(1, 20, DATABASE_URL)
#         logger.info("Пул соединений с БД успешно создан")
# except Exception as e:
#     logger.error(f"Не удалось создать пул соединений с БД: {e}")
#     db_pool = None
#
# # --- Scheduler для отложенных задач ---
# scheduler = AsyncIOScheduler()
#
#
# # --- Состояния создания поста (FSM) ---
# class PostState:
#     CONTENT = "content"
#     CHANNEL = "channel"
#     DATE = "date"
#     TIME = "time"
#     PIN = "pin"
#     PIN_DURATION = "pin_duration"
#     NOTIFY = "notify"
#     DELETE = "delete"
#     DELETE_DURATION = "delete_duration"
#     ADVERTISER = "advertiser"
#     ADVERTISER_USERNAME = "advertiser_username"
#
#
# # --- Тексты ---
# TEXTS = {
#     'welcome': """👋 Добро пожаловать в XSponsorBot!
#
# 🎯 Основные возможности:
# • Планирование постов (текст, фото, видео и др.)
# • Автоматический закреп с уведомлениями
# • Автоудаление через заданное время
# • Отчёты для рекламодателей
# • Управление несколькими площадками
#
# Выберите действие:""",
#
#     'main_menu': """📋 Главное меню
#
# Выберите действие:""",
#
#     'create_post': """✍️ Создание нового поста
#
# 1.  **Отправьте боту сообщение, которое нужно опубликовать.**
#     (Это может быть текст, фото, видео, опрос или любой другой тип)
#
# 2.  **Или перешлите (forward) сообщение из любого чата.**
#
# Или нажмите "Отмена" для возврата в меню.""",
#
#     'select_channel': """📺 Выберите площадку
#
# Куда опубликовать этот пост?""",
#
#     'schedule_date': """📅 Установите дату публикации
#
# Выберите дату или отправьте в формате: ДД.ММ.ГГГГ
# Например: 15.12.2024
#
# Или отправьте "сегодня" для публикации сегодня""",
#
#     'schedule_time': """⏰ Установите время публикации
#
# Отправьте время в формате: ЧЧ:ММ
# Например: 14:30""",
#
#     'pin_settings': """📌 Настройки закрепления
#
# Закрепить пост в канале после публикации?""",
#
#     'pin_duration': """⏱ Длительность закрепа
#
# На сколько закрепить пост?""",
#
#     'notify_subscribers': """🔔 Уведомления подписчиков
#
# Отправить push-уведомление подписчикам при публикации?""",
#
#     'auto_delete': """🗑 Автоудаление
#
# Автоматически удалить пост через определённое время?""",
#
#     'delete_duration': """⏱ Время до удаления
#
# Через сколько удалить пост?""",
#
#     'advertiser_report': """📊 Отчёт для рекламодателя
#
# Отправить отчёт рекламодателю после публикации?""",
#
#     'advertiser_username': """👤 Username рекламодателя
#
# Введите @username рекламодателя.
# **Важно:** этот пользователь должен хотя бы раз запустить бота (/start), чтобы бот мог ему написать.
#
# Нажмите "Пропустить", если отчёт не нужен.""",
#
#     'advertiser_not_found': """❌ Пользователь @{} не найден
#
# Этот пользователь еще ни разу не запускал бота.
# Попросите его запустить бота (@{bot_username}) и попробуйте снова.
#
# Вы можете "Пропустить" этот шаг или ввести @username повторно.""",
#
#     'post_created': """✅ Пост успешно создан!
#
# 📅 Дата публикации: {}
# ⏰ Время: {}
# 📺 Площадка: {}
# 📌 Закреп: {}
# 🔔 Уведомления: {}
# 🗑 Автоудаление: {}
# 📊 Отчёт: {}
#
# Пост будет опубликован автоматически!""",
#
#     'no_channels': """❌ У вас нет добавленных площадок
#
# Чтобы начать использовать бота:
# 1. Добавьте бота администратором в ваш канал или чат
# 2. Дайте ему права на **публикацию, удаление и закрепление** сообщений
# 3. Вернитесь сюда и создайте пост""",
#
#     'error_generic': "Произошла непредвиденная ошибка. Попробуйте еще раз. "
#                      "Если проблема повторяется, свяжитесь с поддержкой.",
#     'error_db': "Произошла ошибка базы данных. Попробуйте позже.",
#     'error_date_past': "❌ Дата не может быть в прошлом. Введите снова:",
#     'error_date_format': "❌ Неверный формат. Используйте ДД.ММ.ГГГГ (например, 25.12.2024)",
#     'error_time_format': "❌ Неверный формат времени. Используйте ЧЧ:ММ (например, 14:30)",
#     'error_time_past': "❌ Время для сегодняшней даты не может быть в прошлом. Введите снова:"
# }
#
#
# # --- Инициализация БД ---
# def init_db():
#     """Создание таблиц в БД, если их нет"""
#     if not db_pool:
#         logger.error("Database pool not available in init_db")
#         return
#
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor() as cur:
#             # Таблица пользователей
#             cur.execute("""
#                 CREATE TABLE IF NOT EXISTS users (
#                     user_id BIGINT PRIMARY KEY,
#                     username VARCHAR(255),
#                     first_name VARCHAR(255),
#                     language VARCHAR(10) DEFAULT 'ru',
#                     tariff VARCHAR(50) DEFAULT 'free',
#                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                     is_active BOOLEAN DEFAULT TRUE
#                 )
#             """)
#
#             # Таблица каналов/площадок
#             cur.execute("""
#                 CREATE TABLE IF NOT EXISTS channels (
#                     id SERIAL PRIMARY KEY,
#                     user_id BIGINT REFERENCES users(user_id),
#                     channel_id BIGINT UNIQUE,
#                     channel_title VARCHAR(255),
#                     channel_username VARCHAR(255),
#                     added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                     is_active BOOLEAN DEFAULT TRUE
#                 )
#             """)
#
#             # Таблица постов
#             cur.execute("""
#                 CREATE TABLE IF NOT EXISTS posts (
#                     id SERIAL PRIMARY KEY,
#                     user_id BIGINT REFERENCES users(user_id),
#                     channel_id BIGINT,
#                     content_message_id BIGINT,
#                     scheduled_time TIMESTAMP,
#                     published_at TIMESTAMP,
#                     pin_duration INTEGER DEFAULT 0,
#                     notify_subscribers BOOLEAN DEFAULT FALSE,
#                     auto_delete_hours INTEGER DEFAULT 0,
#                     advertiser_user_id BIGINT,
#                     status VARCHAR(50) DEFAULT 'scheduled',
#                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#                     posted_message_id INTEGER,
#                     views INTEGER DEFAULT 0,
#                     forwards INTEGER DEFAULT 0
#                 )
#             """)
#
#             # --- БЛОК МИГРАЦИИ (Патч v2) ---
#             # Попытка добавить `content_message_id`, если ее нет
#             try:
#                 cur.execute("ALTER TABLE posts ADD COLUMN content_message_id BIGINT;")
#                 logger.info("Патч БД (v2): Успешно добавлена колонка 'content_message_id'")
#                 conn.commit()
#             except psycopg2.Error as e:
#                 if e.pgcode == errorcodes.DUPLICATE_COLUMN:
#                     logger.info("Патч БД (v2): Колонка 'content_message_id' уже существует.")
#                     conn.rollback()
#                 else:
#                     logger.warning(f"Патч БД (v2): Не удалось добавить 'content_message_id': {e}")
#                     conn.rollback()
#
#             # Попытка удалить старую `message_data`, если она есть
#             try:
#                 cur.execute("ALTER TABLE posts DROP COLUMN message_data;")
#                 logger.info("Патч БД (v2): Успешно удалена старая колонка 'message_data'")
#                 conn.commit()
#             except psycopg2.Error as e:
#                 if e.pgcode == errorcodes.UNDEFINED_COLUMN:
#                     logger.info("Патч БД (v2): Колонка 'message_data' уже удалена.")
#                     conn.rollback()
#                 else:
#                     logger.warning(f"Патч БД (v2): Не удалось удалить 'message_data': {e}")
#                     conn.rollback()
#
#             # --- БЛОК МИГРАЦИИ (Патч v3 - Отчеты рекламодателю) ---
#             # Попытка добавить `advertiser_user_id` (BIGINT)
#             try:
#                 cur.execute("ALTER TABLE posts ADD COLUMN advertiser_user_id BIGINT;")
#                 logger.info("Патч БД (v3): Успешно добавлена колонка 'advertiser_user_id'")
#                 conn.commit()
#             except psycopg2.Error as e:
#                 if e.pgcode == errorcodes.DUPLICATE_COLUMN:
#                     logger.info("Патч БД (v3): Колонка 'advertiser_user_id' уже существует.")
#                     conn.rollback()
#                 else:
#                     logger.warning(f"Патч БД (v3): Не удалось добавить 'advertiser_user_id': {e}")
#                     conn.rollback()
#
#             # Попытка удалить `advertiser_username` (VARCHAR)
#             try:
#                 cur.execute("ALTER TABLE posts DROP COLUMN advertiser_username;")
#                 logger.info("Патч БД (v3): Успешно удалена старая колонка 'advertiser_username'")
#                 conn.commit()
#             except psycopg2.Error as e:
#                 if e.pgcode == errorcodes.UNDEFINED_COLUMN:
#                     logger.info("Патч БД (v3): Колонка 'advertiser_username' уже удалена.")
#                     conn.rollback()
#                 else:
#                     logger.warning(f"Патч БД (v3): Не удалось удалить 'advertiser_username': {e}")
#                     conn.rollback()
#             # --- Конец блоков миграции ---
#
#             # Таблица фоновых задач
#             cur.execute("""
#                 CREATE TABLE IF NOT EXISTS scheduled_tasks (
#                     id SERIAL PRIMARY KEY,
#                     post_id INTEGER REFERENCES posts(id),
#                     task_type VARCHAR(50),
#                     execute_at TIMESTAMP,
#                     job_id VARCHAR(255) UNIQUE,
#                     status VARCHAR(50) DEFAULT 'pending',
#                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
#                 )
#             """)
#
#             cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status)")
#             cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_scheduled_time ON posts(scheduled_time)")
#             cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks(status)")
#
#             conn.commit()
#             logger.info("База данных успешно инициализирована")
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"Ошибка при инициализации БД: {e}")
#         conn.rollback()
#     finally:
#         db_pool.putconn(conn)
#
#
# # --- Функции для работы с БД (с валидацией) ---
# def create_user(user_id: int, username: str, first_name: str):
#     """Создать или обновить пользователя"""
#     if not db_pool:
#         logger.error("DB pool not available in create_user")
#         return
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor() as cur:
#             cur.execute("""
#                 INSERT INTO users (user_id, username, first_name)
#                 VALUES (%s, %s, %s)
#                 ON CONFLICT (user_id) DO UPDATE
#                 SET username = EXCLUDED.username,
#                     first_name = EXCLUDED.first_name,
#                     is_active = TRUE
#             """, (user_id, username, first_name))
#             conn.commit()
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in create_user: {e}")
#         conn.rollback()
#     finally:
#         db_pool.putconn(conn)
#
#
# def get_user_channels(user_id: int) -> List[Dict]:
#     """Получить список активных каналов пользователя"""
#     if not db_pool:
#         logger.error("DB pool not available in get_user_channels")
#         return []
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("""
#                 SELECT * FROM channels
#                 WHERE user_id = %s AND is_active = TRUE
#                 ORDER BY added_at DESC
#             """, (user_id,))
#             return [dict(row) for row in cur.fetchall()]
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in get_user_channels: {e}")
#         return []
#     finally:
#         db_pool.putconn(conn)
#
#
# def add_channel(user_id: int, channel_id: int, title: str, username: str = None):
#     """Добавить или активировать канал в БД"""
#     if not db_pool:
#         logger.error("DB pool not available in add_channel")
#         return
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor() as cur:
#             cur.execute("""
#                 INSERT INTO channels (user_id, channel_id, channel_title, channel_username, is_active)
#                 VALUES (%s, %s, %s, %s, TRUE)
#                 ON CONFLICT (channel_id) DO UPDATE
#                 SET user_id = EXCLUDED.user_id,
#                     channel_title = EXCLUDED.channel_title,
#                     channel_username = EXCLUDED.channel_username,
#                     is_active = TRUE
#             """, (user_id, channel_id, title, username))
#             conn.commit()
#             logger.info(f"Канал {title} (ID: {channel_id}) добавлен/обновлен для user {user_id}")
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in add_channel: {e}")
#         conn.rollback()
#     finally:
#         db_pool.putconn(conn)
#
#
# def deactivate_channel(channel_id: int):
#     """Деактивировать канал (когда бота удалили)"""
#     if not db_pool:
#         logger.error("DB pool not available in deactivate_channel")
#         return
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor() as cur:
#             cur.execute("""
#                 UPDATE channels SET is_active = FALSE WHERE channel_id = %s
#             """, (channel_id,))
#             conn.commit()
#             logger.info(f"Канал {channel_id} деактивирован")
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in deactivate_channel: {e}")
#         conn.rollback()
#     finally:
#         db_pool.putconn(conn)
#
#
# # НОВАЯ ФУНКЦИЯ
# def get_user_by_username(username: str) -> Optional[Dict]:
#     """Найти пользователя по username (без @)"""
#     if not db_pool:
#         logger.error("DB pool not available in get_user_by_username")
#         return None
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             # lower() для регистронезависимого поиска
#             cur.execute("SELECT * FROM users WHERE lower(username) = lower(%s)", (username,))
#             result = cur.fetchone()
#             return dict(result) if result else None
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in get_user_by_username: {e}")
#         return None
#     finally:
#         db_pool.putconn(conn)
#
#
# def save_post(user_id: int, channel_id: int, content_message_id: int,
#               scheduled_time: datetime, pin_duration: int = 0,
#               notify_subscribers: bool = False, auto_delete_hours: int = 0,
#               advertiser_user_id: Optional[int] = None) -> Optional[int]:  # Изменено
#     """Сохранить пост в БД"""
#     if not db_pool:
#         logger.error("DB pool not available in save_post")
#         return None
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor() as cur:
#             cur.execute("""
#                 INSERT INTO posts (user_id, channel_id, content_message_id, scheduled_time,
#                                  pin_duration, notify_subscribers, auto_delete_hours,
#                                  advertiser_user_id, status)
#                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')
#                 RETURNING id
#             """, (user_id, channel_id, content_message_id, scheduled_time,
#                   pin_duration, notify_subscribers, auto_delete_hours, advertiser_user_id))  # Изменено
#             post_id = cur.fetchone()[0]
#             conn.commit()
#             return post_id
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in save_post: {e}")
#         conn.rollback()
#         return None
#     finally:
#         db_pool.putconn(conn)
#
#
# def get_channel_by_id(channel_id: int) -> Optional[Dict]:
#     """Получить канал по ID"""
#     if not db_pool:
#         logger.error("DB pool not available in get_channel_by_id")
#         return None
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             cur.execute("SELECT * FROM channels WHERE channel_id = %s", (channel_id,))
#             result = cur.fetchone()
#             return dict(result) if result else None
#     except (Exception, psycopg2.Error) as e:
#         logger.error(f"DB error in get_channel_by_id: {e}")
#         return None
#     finally:
#         db_pool.putconn(conn)
#
#
# # --- Клавиатуры ---
# def main_menu_keyboard(user_id: int):
#     """Главное меню"""
#     keyboard = [
#         [InlineKeyboardButton("✍️ Создать пост", callback_data="create_post")],
#         [InlineKeyboardButton("📅 Запланированные", callback_data="scheduled_posts")],
#         [InlineKeyboardButton("📺 Мои площадки", callback_data="my_channels")],
#     ]
#     if user_id == OWNER_ID:
#         keyboard.append([InlineKeyboardButton("👑 Админ", callback_data="admin_panel")])
#     return InlineKeyboardMarkup(keyboard)
#
#
# def cancel_keyboard():
#     """Кнопка отмены"""
#     return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])
#
#
# def yes_no_keyboard(yes_data: str, no_data: str):
#     """Да/Нет"""
#     return InlineKeyboardMarkup([
#         [
#             InlineKeyboardButton("✅ Да", callback_data=yes_data),
#             InlineKeyboardButton("❌ Нет", callback_data=no_data)
#         ],
#         [InlineKeyboardButton("« Отмена", callback_data="cancel")]
#     ])
#
#
# def duration_keyboard(prefix: str):
#     """Выбор длительности"""
#     return InlineKeyboardMarkup([
#         [InlineKeyboardButton("24 часа", callback_data=f"{prefix}_24")],
#         [InlineKeyboardButton("48 часов", callback_data=f"{prefix}_48")],
#         [InlineKeyboardButton("72 часа", callback_data=f"{prefix}_72")],
#         [InlineKeyboardButton("7 дней (168ч)", callback_data=f"{prefix}_168")],
#         [InlineKeyboardButton("« Отмена", callback_data="cancel")]
#     ])
#
#
# def date_keyboard():
#     """Быстрый выбор даты"""
#     return InlineKeyboardMarkup([
#         [InlineKeyboardButton("📅 Сегодня", callback_data="date_today")],
#         [InlineKeyboardButton("📅 Завтра", callback_data="date_tomorrow")],
#         [InlineKeyboardButton("📅 Послезавтра", callback_data="date_aftertomorrow")],
#         [InlineKeyboardButton("« Отмена", callback_data="cancel")]
#     ])
#
#
# def skip_keyboard():
#     """Кнопка пропуска"""
#     return InlineKeyboardMarkup([
#         [InlineKeyboardButton("⏭ Пропустить", callback_data="skip")],
#         [InlineKeyboardButton("« Отмена", callback_data="cancel")]
#     ])
#
#
# # --- Обработчики команд ---
#
# async def send_error_message(update: Update, text: str = TEXTS['error_generic']):
#     """Отправка сообщения об ошибке пользователю"""
#     try:
#         if update.callback_query:
#             await update.callback_query.edit_message_text(text)
#         elif update.message:
#             await update.message.reply_text(text)
#     except Exception as e:
#         logger.error(f"Не удалось отправить сообщение об ошибке: {e}")
#
#
# # Команда /start
# async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Команда /start"""
#     try:
#         user = update.effective_user
#         if not user:
#             logger.warning("Не удалось получить effective_user в start_command")
#             return
#
#         create_user(user.id, user.username, user.first_name)
#
#         await update.message.reply_text(
#             TEXTS['welcome'],
#             reply_markup=main_menu_keyboard(user.id)
#         )
#     except Exception as e:
#         logger.error(f"Error in start_command: {e}", exc_info=True)
#         await update.message.reply_text(TEXTS['error_generic'])
#
#
# # Обработчик кнопок
# async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Обработчик всех кнопок"""
#     query = update.callback_query
#     if not query:
#         logger.warning("query is None in button_handler")
#         return
#
#     try:
#         await query.answer()
#     except TelegramError as e:
#         logger.warning(f"Не удалось ответить на callback query: {e}")
#
#     try:
#         data = query.data
#         user_id = query.from_user.id
#
#         logger.info(f"=== BUTTON HANDLER ===")
#         logger.info(f"Button: {data}")
#         logger.info(f"User ID: {user_id}")
#         logger.info(f"Current state: {context.user_data.get('state')}")
#         logger.info(f"User data: {context.user_data}")
#
#         # Отмена
#         if data == "cancel":
#             context.user_data.clear()
#             await query.edit_message_text(
#                 "❌ Действие отменено",
#                 reply_markup=main_menu_keyboard(user_id)
#             )
#             logger.info("Действие отменено")
#             return
#
#         # Главное меню
#         if data == "main_menu":
#             context.user_data.clear()
#             await query.edit_message_text(
#                 TEXTS['main_menu'],
#                 reply_markup=main_menu_keyboard(user_id)
#             )
#             logger.info("Возврат в главное меню")
#             return
#
#         # Начало создания поста
#         if data == "create_post":
#             context.user_data.clear()
#             context.user_data['state'] = PostState.CONTENT
#             context.user_data['post'] = {}
#             await query.edit_message_text(
#                 TEXTS['create_post'],
#                 reply_markup=cancel_keyboard()
#             )
#             logger.info("Начало создания поста, state: CONTENT")
#             return
#
#         # Выбор канала
#         if data.startswith("channel_"):
#             if context.user_data.get('state') != PostState.CHANNEL:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'channel_'")
#                 return
#
#             channel_id = int(data.replace("channel_", ""))
#             context.user_data['post']['channel_id'] = channel_id
#             context.user_data['state'] = PostState.DATE
#             await query.edit_message_text(
#                 TEXTS['schedule_date'],
#                 reply_markup=date_keyboard()
#             )
#             logger.info(f"Канал выбран: {channel_id}, state: DATE")
#             return
#
#         # Выбор даты
#         if data.startswith("date_"):
#             if context.user_data.get('state') != PostState.DATE:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'date_'")
#                 return
#
#             today = datetime.now()
#             if data == "date_today":
#                 date_str = today.strftime('%d.%m.%Y')
#             elif data == "date_tomorrow":
#                 date_str = (today + timedelta(days=1)).strftime('%d.%m.%Y')
#             elif data == "date_aftertomorrow":
#                 date_str = (today + timedelta(days=2)).strftime('%d.%m.%Y')
#             else:
#                 logger.warning(f"Неизвестная data 'date_': {data}")
#                 return
#
#             context.user_data['post']['date'] = date_str
#             context.user_data['state'] = PostState.TIME
#             await query.edit_message_text(
#                 TEXTS['schedule_time'],
#                 reply_markup=cancel_keyboard()
#             )
#             logger.info(f"Дата выбрана: {date_str}, state: TIME")
#             return
#
#         # Закрепление
#         if data == "pin_yes":
#             if context.user_data.get('state') != PostState.PIN:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'pin_yes'")
#                 return
#             context.user_data['state'] = PostState.PIN_DURATION
#             await query.edit_message_text(
#                 TEXTS['pin_duration'],
#                 reply_markup=duration_keyboard("pindur")
#             )
#             logger.info("Pin: YES, state: PIN_DURATION")
#             return
#
#         if data == "pin_no":
#             if context.user_data.get('state') != PostState.PIN:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'pin_no'")
#                 return
#             context.user_data['post']['pin_duration'] = 0
#             context.user_data['state'] = PostState.NOTIFY
#             await query.edit_message_text(
#                 TEXTS['notify_subscribers'],
#                 reply_markup=yes_no_keyboard("notify_yes", "notify_no")
#             )
#             logger.info("Pin: NO, state: NOTIFY")
#             return
#
#         # Длительность закрепа
#         if data.startswith("pindur_"):
#             if context.user_data.get('state') != PostState.PIN_DURATION:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'pindur_'")
#                 return
#             hours = int(data.replace("pindur_", ""))
#             context.user_data['post']['pin_duration'] = hours
#             context.user_data['state'] = PostState.NOTIFY
#             await query.edit_message_text(
#                 TEXTS['notify_subscribers'],
#                 reply_markup=yes_no_keyboard("notify_yes", "notify_no")
#             )
#             logger.info(f"Pin duration: {hours}h, state: NOTIFY")
#             return
#
#         # Уведомления
#         if data == "notify_yes":
#             if context.user_data.get('state') != PostState.NOTIFY:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'notify_yes'")
#                 return
#             context.user_data['post']['notify'] = True
#             context.user_data['state'] = PostState.DELETE
#             await query.edit_message_text(
#                 TEXTS['auto_delete'],
#                 reply_markup=yes_no_keyboard("delete_yes", "delete_no")
#             )
#             logger.info("Notify: YES, state: DELETE")
#             return
#
#         if data == "notify_no":
#             if context.user_data.get('state') != PostState.NOTIFY:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'notify_no'")
#                 return
#             context.user_data['post']['notify'] = False
#             context.user_data['state'] = PostState.DELETE
#             await query.edit_message_text(
#                 TEXTS['auto_delete'],
#                 reply_markup=yes_no_keyboard("delete_yes", "delete_no")
#             )
#             logger.info("Notify: NO, state: DELETE")
#             return
#
#         # Автоудаление
#         if data == "delete_yes":
#             if context.user_data.get('state') != PostState.DELETE:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'delete_yes'")
#                 return
#             context.user_data['state'] = PostState.DELETE_DURATION
#             await query.edit_message_text(
#                 TEXTS['delete_duration'],
#                 reply_markup=duration_keyboard("deldur")
#             )
#             logger.info("Delete: YES, state: DELETE_DURATION")
#             return
#
#         if data == "delete_no":
#             if context.user_data.get('state') != PostState.DELETE:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'delete_no'")
#                 return
#             context.user_data['post']['delete_hours'] = 0
#             context.user_data['state'] = PostState.ADVERTISER
#             await query.edit_message_text(
#                 TEXTS['advertiser_report'],
#                 reply_markup=yes_no_keyboard("adv_yes", "adv_no")
#             )
#             logger.info("Delete: NO, state: ADVERTISER")
#             return
#
#         # Длительность до удаления
#         if data.startswith("deldur_"):
#             if context.user_data.get('state') != PostState.DELETE_DURATION:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'deldur_'")
#                 return
#             hours = int(data.replace("deldur_", ""))
#             context.user_data['post']['delete_hours'] = hours
#             context.user_data['state'] = PostState.ADVERTISER
#             await query.edit_message_text(
#                 TEXTS['advertiser_report'],
#                 reply_markup=yes_no_keyboard("adv_yes", "adv_no")
#             )
#             logger.info(f"Delete duration: {hours}h, state: ADVERTISER")
#             return
#
#         # Рекламодатель
#         if data == "adv_yes":
#             if context.user_data.get('state') != PostState.ADVERTISER:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'adv_yes'")
#                 return
#             context.user_data['state'] = PostState.ADVERTISER_USERNAME
#             await query.edit_message_text(
#                 TEXTS['advertiser_username'],
#                 reply_markup=skip_keyboard()
#             )
#             logger.info("Advertiser: YES, state: ADVERTISER_USERNAME")
#             return
#
#         if data == "adv_no" or data == "skip":
#             if context.user_data.get('state') not in [PostState.ADVERTISER, PostState.ADVERTISER_USERNAME]:
#                 logger.warning(f"Неверный state ({context.user_data.get('state')}) для 'adv_no/skip'")
#                 return
#             # Сохраняем None (или имя, если оно было введено до 'skip')
#             context.user_data['post'].setdefault('advertiser_user_id', None)
#             context.user_data['post'].setdefault('advertiser_username', None)
#             logger.info("Advertiser: NO/SKIP, finalizing post")
#             await finalize_post(query, context)
#             return
#
#         # Мои площадки
#         if data == "my_channels":
#             channels = get_user_channels(user_id)
#             if not channels:
#                 await query.edit_message_text(
#                     TEXTS['no_channels'],
#                     reply_markup=main_menu_keyboard(user_id)
#                 )
#                 return
#
#             text = f"📺 Ваши площадки ({len(channels)}):\n\n"
#             for ch in channels:
#                 title = ch['channel_title'] or ch['channel_username'] or f"ID: {ch['channel_id']}"
#                 text += f"• {title}\n"
#
#             text += "\nЧтобы удалить площадку, просто удалите бота из администраторов канала."
#             keyboard = [[InlineKeyboardButton("« Назад", callback_data="main_menu")]]
#             await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
#             return
#
#         # Запланированные посты
#         if data == "scheduled_posts":
#             if not db_pool:
#                 await query.edit_message_text(
#                     TEXTS['error_db'],
#                     reply_markup=main_menu_keyboard(user_id)
#                 )
#                 return
#
#             conn = db_pool.getconn()
#             posts = []
#             try:
#                 with conn.cursor(cursor_factory=RealDictCursor) as cur:
#                     cur.execute("""
#                         SELECT p.*, c.channel_title
#                         FROM posts p
#                         LEFT JOIN channels c ON p.channel_id = c.channel_id
#                         WHERE p.user_id = %s AND p.status = 'scheduled'
#                         ORDER BY p.scheduled_time
#                         LIMIT 10
#                     """, (user_id,))
#                     posts = [dict(row) for row in cur.fetchall()]
#             except (Exception, psycopg2.Error) as e:
#                 logger.error(f"DB error in scheduled_posts: {e}")
#                 await query.edit_message_text(TEXTS['error_db'])
#                 return
#             finally:
#                 db_pool.putconn(conn)
#
#             if not posts:
#                 text = "📅 У вас нет запланированных постов"
#             else:
#                 text = f"📅 Ближайшие запланированные посты ({len(posts)}):\n\n"
#                 for post in posts:
#                     scheduled = post['scheduled_time'].strftime('%d.%m %H:%M')
#                     channel = post['channel_title'] or 'Канал'
#                     text += f"• {channel} - {scheduled}\n"
#
#             await query.edit_message_text(
#                 text,
#                 reply_markup=InlineKeyboardMarkup([[
#                     InlineKeyboardButton("« Назад", callback_data="main_menu")
#                 ]])
#             )
#             return
#
#     except Exception as e:
#         logger.error(f"Error in button_handler (data: {query.data}): {e}", exc_info=True)
#         await send_error_message(update)
#
#
# # Обработчик сообщений
# async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Обработчик текстовых и любых других сообщений"""
#     message = update.message
#     if not message:
#         logger.warning("message is None in message_handler")
#         return
#
#     user_id = message.from_user.id
#     state = context.user_data.get('state')
#
#     try:
#         logger.info(f"=== MESSAGE HANDLER ===")
#         logger.info(f"User ID: {user_id}")
#         logger.info(f"Current state: {state}")
#         logger.info(f"Message text: {message.text[:100] if message.text else 'NO TEXT'}")
#         logger.info(f"User data: {context.user_data}")
#
#         # Если нет активного состояния - игнорируем
#         if not state:
#             logger.warning(f"Нет активного state для user {user_id}, игнорируем")
#             return
#
#         # ЭТАП 1: Получение контента поста
#         if state == PostState.CONTENT:
#             logger.info("Processing CONTENT state")
#
#             # Сохраняем ID сообщения для 'copy_message'
#             context.user_data['post']['content_message_id'] = message.message_id
#
#             # Получаем каналы
#             channels = get_user_channels(user_id)
#             logger.info(f"Найдено {len(channels)} каналов для user {user_id}")
#
#             if not channels:
#                 await message.reply_text(
#                     TEXTS['no_channels'],
#                     reply_markup=main_menu_keyboard(user_id)
#                 )
#                 context.user_data.clear()
#                 return
#
#             # Показываем каналы
#             context.user_data['state'] = PostState.CHANNEL
#             keyboard = []
#             for ch in channels:
#                 title = ch['channel_title'] or ch['channel_username'] or f"ID: {ch['channel_id']}"
#                 keyboard.append([InlineKeyboardButton(
#                     f"📺 {title}",
#                     callback_data=f"channel_{ch['channel_id']}"
#                 )])
#             keyboard.append([InlineKeyboardButton("« Отмена", callback_data="cancel")])
#
#             await message.reply_text(
#                 TEXTS['select_channel'],
#                 reply_markup=InlineKeyboardMarkup(keyboard)
#             )
#             logger.info("Отправлена клавиатура выбора канала, state: CHANNEL")
#             return
#
#         # ЭТАП 2: Ручной ввод даты
#         if state == PostState.DATE:
#             logger.info(f"Processing DATE state, input: {message.text}")
#             date_str = message.text.strip()
#             try:
#                 # Валидация формата
#                 date_obj = datetime.strptime(date_str, '%d.%m.%Y').date()
#
#                 # Валидация, что дата не в прошлом
#                 if date_obj < datetime.now().date():
#                     await message.reply_text(
#                         TEXTS['error_date_past'],
#                         reply_markup=cancel_keyboard()
#                     )
#                     return
#
#                 context.user_data['post']['date'] = date_str
#                 context.user_data['state'] = PostState.TIME
#                 await message.reply_text(
#                     TEXTS['schedule_time'],
#                     reply_markup=cancel_keyboard()
#                 )
#                 logger.info(f"Дата установлена: {date_str}, state: TIME")
#             except ValueError:
#                 await message.reply_text(
#                     TEXTS['error_date_format'],
#                     reply_markup=cancel_keyboard()
#                 )
#             return
#
#         # ЭТАП 3: Ввод времени
#         if state == PostState.TIME:
#             logger.info(f"Processing TIME state, input: {message.text}")
#             time_str = message.text.strip()
#             time_pattern = r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$'
#
#             if not re.match(time_pattern, time_str):
#                 await message.reply_text(
#                     TEXTS['error_time_format'],
#                     reply_markup=cancel_keyboard()
#                 )
#                 return
#
#             # Валидация, что дата+время не в прошлом
#             try:
#                 date_str = context.user_data.get('post', {}).get('date')
#                 if not date_str:
#                     logger.error("Потеряна дата в state=TIME")
#                     raise ValueError("Date not found")
#
#                 scheduled_dt = datetime.strptime(f"{date_str} {time_str}", '%d.%m.%Y %H:%M')
#
#                 if scheduled_dt <= datetime.now():
#                     await message.reply_text(
#                         TEXTS['error_time_past'],
#                         reply_markup=cancel_keyboard()
#                     )
#                     return
#
#             except ValueError as e:
#                 logger.error(f"Ошибка валидации времени: {e}")
#                 await message.reply_text(
#                     TEXTS['error_generic'],
#                     reply_markup=cancel_keyboard()
#                 )
#                 context.user_data.clear()
#                 return
#
#             context.user_data['post']['time'] = time_str
#             context.user_data['state'] = PostState.PIN
#             await message.reply_text(
#                 TEXTS['pin_settings'],
#                 reply_markup=yes_no_keyboard("pin_yes", "pin_no")
#             )
#             logger.info(f"Время установлено: {time_str}, state: PIN")
#             return
#
#         # ЭТАП 4: Ввод username рекламодателя (ИЗМЕНЕНО)
#         if state == PostState.ADVERTISER_USERNAME:
#             logger.info(f"Processing ADVERTISER_USERNAME state")
#             username = message.text.strip().lstrip('@')
#
#             # Ищем пользователя в БД
#             advertiser = get_user_by_username(username)
#
#             if advertiser:
#                 logger.info(f"Рекламодатель {username} найден, ID: {advertiser['user_id']}")
#                 context.user_data['post']['advertiser_user_id'] = advertiser['user_id']
#                 context.user_data['post']['advertiser_username'] = advertiser['username']  # Для отчета
#                 await finalize_post(message, context)
#             else:
#                 # Пользователь не найден
#                 logger.warning(f"Рекламодатель @{username} не найден в БД")
#                 bot_username = (await context.bot.get_me()).username
#                 await message.reply_text(
#                     TEXTS['advertiser_not_found'].format(username, bot_username),
#                     reply_markup=skip_keyboard()  # Даем пропустить или ввести снова
#                 )
#             return
#
#     except Exception as e:
#         logger.error(f"Error in message_handler (state: {state}): {e}", exc_info=True)
#         await send_error_message(update)
#
#
# async def finalize_post(message_or_query, context: ContextTypes.DEFAULT_TYPE):
#     """Финализация и сохранение поста"""
#     user_id = message_or_query.from_user.id
#
#     try:
#         post = context.user_data.get('post', {})
#         if not post:
#             logger.error(f"User {user_id}: 'post' data not found in user_data for finalize_post")
#             await send_message(message_or_query, TEXTS['error_generic'])
#             return
#
#         logger.info(f"=== FINALIZING POST ===")
#         logger.info(f"Post data: {post}")
#
#         # Парсинг даты и времени
#         date_str = post.get('date')
#         time_str = post.get('time')
#
#         if not date_str or not time_str:
#             logger.error(f"User {user_id}: Date ({date_str}) or Time ({time_str}) missing")
#             await send_message(message_or_query, "❌ Ошибка: дата или время не установлены. Попробуйте снова.")
#             context.user_data.clear()
#             return
#
#         scheduled_dt = datetime.strptime(f"{date_str} {time_str}", '%d.%m.%Y %H:%M')
#
#         if scheduled_dt <= datetime.now():
#             await send_message(message_or_query, "❌ Дата и время должны быть в будущем. Пост не создан.")
#             context.user_data.clear()
#             return
#
#         # --- Сохранение в БД и Планирование ---
#         try:
#             channel_id = post.get('channel_id')
#             content_message_id = post.get('content_message_id')
#
#             if not channel_id or not content_message_id:
#                 logger.error(f"User {user_id}: Channel ID or Content ID missing")
#                 raise ValueError("Channel or Content ID missing")
#
#             post_id = save_post(
#                 user_id=user_id,
#                 channel_id=channel_id,
#                 content_message_id=content_message_id,
#                 scheduled_time=scheduled_dt,
#                 pin_duration=post.get('pin_duration', 0),
#                 notify_subscribers=post.get('notify', False),
#                 auto_delete_hours=post.get('delete_hours', 0),
#                 advertiser_user_id=post.get('advertiser_user_id')  # Изменено
#             )
#
#             if not post_id:
#                 raise Exception("Failed to save post to DB")
#
#             logger.info(f"Пост сохранен в БД, ID: {post_id}")
#
#             # Планирование публикации
#             job_id = f"post_{post_id}"
#             scheduler.add_job(
#                 publish_post,
#                 DateTrigger(run_date=scheduled_dt),
#                 args=[context.bot, post_id, user_id],
#                 id=job_id,
#                 replace_existing=True
#             )
#             logger.info(f"Запланирована задача {job_id} на {scheduled_dt}")
#
#         except Exception as e:
#             logger.error(f"Error saving or scheduling post: {e}", exc_info=True)
#             await send_message(message_or_query, "❌ Произошла ошибка при сохранении. Пост не создан.")
#             context.user_data.clear()
#             return
#
#         # --- Формирование ответа ---
#         channel = get_channel_by_id(post.get('channel_id'))
#         channel_name = channel['channel_title'] if channel else 'Канал'
#
#         pin_text = f"{post.get('pin_duration', 0)} ч" if post.get('pin_duration', 0) > 0 else "Нет"
#         notify_text = "Да" if post.get('notify', False) else "Нет"
#         delete_text = f"{post.get('delete_hours', 0)} ч" if post.get('delete_hours', 0) > 0 else "Нет"
#
#         # Изменено
#         adv_username = post.get('advertiser_username')
#         adv_text = f"Да (@{adv_username})" if adv_username else "Нет"
#
#         result_text = TEXTS['post_created'].format(
#             date_str,
#             time_str,
#             channel_name,
#             pin_text,
#             notify_text,
#             delete_text,
#             adv_text
#         )
#
#         await send_message(
#             message_or_query,
#             result_text,
#             reply_markup=main_menu_keyboard(user_id)
#         )
#
#         logger.info("Создание поста успешно завершено")
#
#     except Exception as e:
#         logger.error(f"Critical error in finalize_post: {e}", exc_info=True)
#         await send_message(message_or_query, TEXTS['error_generic'])
#
#     finally:
#         context.user_data.clear()
#
#
# async def send_message(message_or_query, text, reply_markup=None):
#     """Универсальная отправка/редактирование сообщения"""
#     try:
#         if hasattr(message_or_query, 'edit_message_text'):
#             await message_or_query.edit_message_text(text, reply_markup=reply_markup)
#         else:
#             await message_or_query.reply_text(text, reply_markup=reply_markup)
#     except TelegramError as e:
#         logger.warning(f"Error sending message: {e}")
#
#
# # --- Функции публикации (фоновые задачи) ---
# async def publish_post(bot: Bot, post_id: int, user_id: int):
#     """Опубликовать пост"""
#     logger.info(f"Запуск publish_post для post_id: {post_id}")
#
#     if not db_pool:
#         logger.error("DB pool not available in publish_post")
#         return
#
#     conn = db_pool.getconn()
#     try:
#         with conn.cursor(cursor_factory=RealDictCursor) as cur:
#             # Получаем данные о посте
#             cur.execute("""
#                 SELECT p.*, c.channel_title, c.channel_username
#                 FROM posts p
#                 LEFT JOIN channels c ON p.channel_id = c.channel_id
#                 WHERE p.id = %s AND p.status = 'scheduled'
#             """, (post_id,))
#             result = cur.fetchone()
#
#             if not result:
#                 logger.error(f"Пост {post_id} не найден или уже опубликован")
#                 return
#
#             post = dict(result)
#             channel_id = post['channel_id']
#             content_message_id = post['content_message_id']
#
#             if not content_message_id:
#                 logger.error(f"content_message_id is null for post {post_id}")
#                 return
#
#             # Отправка сообщения (копирование)
#             sent_message = await bot.copy_message(
#                 chat_id=channel_id,
#                 from_chat_id=user_id,  # user_id - это chat_id личного чата с ботом
#                 message_id=content_message_id,
#                 disable_notification=not post['notify_subscribers']
#             )
#
#             logger.info(f"Пост {post_id} опубликован в {channel_id}, msg_id: {sent_message.message_id}")
#
#             # Закрепление
#             if post['pin_duration'] > 0:
#                 try:
#                     await bot.pin_chat_message(
#                         chat_id=channel_id,
#                         message_id=sent_message.message_id,
#                         disable_notification=not post['notify_subscribers']
#                     )
#
#                     # Запланировать открепление
#                     unpin_time = datetime.now() + timedelta(hours=post['pin_duration'])
#                     scheduler.add_job(
#                         unpin_post,
#                         DateTrigger(run_date=unpin_time),
#                         args=[bot, channel_id, sent_message.message_id],
#                         id=f"unpin_{post_id}",
#                         replace_existing=True
#                     )
#                     logger.info(f"Пост {post_id} закреплен, открепление в {unpin_time}")
#
#                 except TelegramError as e:
#                     logger.error(f"Ошибка закрепления post {post_id}: {e}")
#                     # Оповестить пользователя об ошибке?
#                     await bot.send_message(
#                         chat_id=user_id,
#                         text=f"❗️ Ошибка при закреплении поста #{post_id} "
#                              f"в канале {post.get('channel_title') or channel_id}. "
#                              f"Убедитесь, что у бота есть права на закрепление."
#                     )
#
#             # Запланировать удаление
#             if post['auto_delete_hours'] > 0:
#                 delete_time = datetime.now() + timedelta(hours=post['auto_delete_hours'])
#                 scheduler.add_job(
#                     delete_post,
#                     DateTrigger(run_date=delete_time),
#                     args=[bot, channel_id, sent_message.message_id],
#                     id=f"delete_{post_id}",
#                     replace_existing=True
#                 )
#                 logger.info(f"Запланировано удаление поста {post_id} в {delete_time}")
#
#             # Обновление статуса
#             cur.execute("""
#                 UPDATE posts
#                 SET status = 'published', published_at = NOW(), posted_message_id = %s
#                 WHERE id = %s
#             """, (sent_message.message_id, post_id))
#             conn.commit()
#
#             # --- Уведомления (ИЗМЕНЕНО) ---
#
#             # Формируем ссылку на пост
#             post_link = ""
#             if post.get('channel_username'):
#                 post_link = f"https://t.me/{post['channel_username']}/{sent_message.message_id}"
#
#             channel_name = post.get('channel_title') or post.get('channel_username') or str(channel_id)
#
#             # 1. Уведомление владельца
#             owner_text = f"✅ Пост #{post_id} успешно опубликован в '{channel_name}'!"
#             if post_link:
#                 owner_text += f"\n\n🔗 {post_link}"
#
#             await bot.send_message(
#                 chat_id=post['user_id'],
#                 text=owner_text
#             )
#
#             # 2. Уведомление рекламодателя (НОВОЕ)
#             if post.get('advertiser_user_id'):
#                 adv_user_id = post['advertiser_user_id']
#                 logger.info(f"Отправка отчета рекламодателю {adv_user_id} для поста {post_id}")
#
#                 adv_text = f"📊 Ваш рекламный пост (ID: {post_id}) был успешно опубликован в канале '{channel_name}'."
#                 if post_link:
#                     adv_text += f"\n\n🔗 {post_link}"
#
#                 try:
#                     await bot.send_message(
#                         chat_id=adv_user_id,
#                         text=adv_text
#                     )
#                     logger.info(f"Отчет рекламодателю {adv_user_id} успешно отправлен.")
#                 except Forbidden:
#                     logger.warning(f"Не удалось отправить отчет: рекламодатель {adv_user_id} заблокировал бота.")
#                     # Оповестить владельца поста, что рекламодатель не получил отчет
#                     await bot.send_message(
#                         chat_id=post['user_id'],
#                         text=f"❗️Не удалось отправить отчет рекламодателю (ID: {adv_user_id}) "
#                              f"для поста #{post_id}. Похоже, он заблокировал бота."
#                     )
#                 except Exception as e:
#                     logger.error(f"Неизвестная ошибка при отправке отчета рекламодателю {adv_user_id}: {e}")
#
#
#     except Forbidden as e:
#         logger.error(f"Forbidden: Не удалось опубликовать пост {post_id} в {channel_id}: {e}")
#         channel_name = post.get('channel_title') or str(channel_id)
#         await bot.send_message(
#             chat_id=user_id,
#             text=f"❗️ ОШИБКА ПУБЛИКАЦИИ поста #{post_id} в {channel_name}.\n"
#                  f"Причина: {e.message}\n"
#                  f"Убедитесь, что бот является администратором и имеет права на отправку сообщений."
#         )
#         # Обновить статус, чтобы не пытаться снова
#         cur.execute("UPDATE posts SET status = 'failed' WHERE id = %s", (post_id,))
#         conn.commit()
#
#     except Exception as e:
#         logger.error(f"Критическая ошибка при публикации post {post_id}: {e}", exc_info=True)
#         conn.rollback()
#         # Попытаться оповестить
#         try:
#             await bot.send_message(
#                 chat_id=user_id,
#                 text=f"❗️ КРИТИЧЕСКАЯ ОШИБКА при публикации поста #{post_id}. Свяжитесь с поддержкой."
#             )
#         except:
#             pass
#     finally:
#         db_pool.putconn(conn)
#
#
# async def unpin_post(bot: Bot, channel_id: int, message_id: int):
#     """Открепить пост"""
#     try:
#         await bot.unpin_chat_message(chat_id=channel_id, message_id=message_id)
#         logger.info(f"Пост {message_id} откреплен из {channel_id}")
#     except TelegramError as e:
#         logger.error(f"Ошибка открепления: {e}")
#
#
# async def delete_post(bot: Bot, channel_id: int, message_id: int):
#     """Удалить пост"""
#     try:
#         await bot.delete_message(chat_id=channel_id, message_id=message_id)
#         logger.info(f"Пост {message_id} удален из {channel_id}")
#     except TelegramError as e:
#         logger.error(f"Ошибка удаления: {e}")
#
#
# async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     """Обработчик добавления/удаления бота в канал/чат"""
#     try:
#         member_update = update.my_chat_member
#         if not member_update:
#             return
#
#         chat = member_update.chat
#         new_status = member_update.new_chat_member.status
#         user = member_update.from_user
#
#         if new_status == "administrator":
#             # Бот добавлен как админ
#             add_channel(
#                 user_id=user.id,
#                 channel_id=chat.id,
#                 title=chat.title,
#                 username=chat.username
#             )
#
#             try:
#                 await context.bot.send_message(
#                     chat_id=user.id,
#                     text=f"✅ Канал '{chat.title}' успешно добавлен! "
#                          f"Убедитесь, что у бота есть права на публикацию, "
#                          f"удаление и закрепление сообщений."
#                 )
#             except (TelegramError, Forbidden):
#                 logger.warning(f"Не удалось отправить сообщение user {user.id} о добавлении канала")
#
#             logger.info(f"Бот добавлен в {chat.title} (ID: {chat.id}) пользователем {user.id}")
#
#         elif new_status in ["left", "kicked"]:
#             # Бот удален или разжалован
#             deactivate_channel(chat.id)
#
#             try:
#                 await context.bot.send_message(
#                     chat_id=user.id,
#                     text=f"❌ Бот был удален из канала '{chat.title}'. "
#                          f"Площадка деактивирована."
#                 )
#             except (TelegramError, Forbidden):
#                 logger.warning(f"Не удалось отправить сообщение user {user.id} об удалении канала")
#
#             logger.info(f"Бот удален из {chat.title} (ID: {chat.id})")
#
#     except Exception as e:
#         logger.error(f"Error in my_chat_member_handler: {e}", exc_info=True)
#
#
# # --- Основная функция ---
# def main():
#     """Запуск бота"""
#     if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN':
#         logger.critical("BOT_TOKEN не установлен! Бот не может запуститься.")
#         return
#
#     if not db_pool:
#         logger.critical("Бот не может запуститься без соединения с БД!")
#         return
#
#     # Инициализация БД
#     init_db()
#
#     # Создание приложения
#     application = Application.builder().token(BOT_TOKEN).build()
#
#     # Регистрация обработчиков
#     application.add_handler(CommandHandler("start", start_command))
#     application.add_handler(CallbackQueryHandler(button_handler))
#
#     # filters.TEXT -> filters.ANY
#     # Это ловит текст, фото, видео, файлы, вообще ВСЕ.
#     application.add_handler(MessageHandler(
#         filters.ALL & ~filters.COMMAND,
#         message_handler
#     ))
#
#     # Обработчик изменения статуса бота в чатах
#     application.add_handler(ChatMemberHandler(
#         my_chat_member_handler,
#         ChatMemberHandler.MY_CHAT_MEMBER
#     ))
#
#     # Запуск планировщика
#     try:
#         scheduler.start()
#         logger.info("Планировщик APScheduler запущен")
#     except Exception as e:
#         logger.error(f"Не удалось запустить планировщик: {e}")
#         return
#
#     # Запуск бота
#     logger.info("Бот запускается...")
#     logger.info(f"Owner ID: {OWNER_ID}")
#     application.run_polling(allowed_updates=Update.ALL_TYPES)
#
#
# if __name__ == "__main__":
#     main()