import psycopg2

from database.connection import db_pool
from utils.logging import logger


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

                    media_group_data JSONB NULL,

                    pin_duration FLOAT DEFAULT 0,
                    pin_notify BOOLEAN DEFAULT FALSE,
                    auto_delete_hours FLOAT DEFAULT 0,
                    report_enabled BOOLEAN DEFAULT FALSE,
                    advertiser_user_id BIGINT NULL,
                    post_type VARCHAR(50) DEFAULT 'repost',
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
                    pin_duration FLOAT DEFAULT 0,
                    pin_notify BOOLEAN DEFAULT FALSE,
                    auto_delete_hours FLOAT DEFAULT 0,
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