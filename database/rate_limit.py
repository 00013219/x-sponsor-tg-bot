"""
Rate limiting for task creation.
Max 10 tasks per 10 minutes per user.
"""

from datetime import datetime, timedelta
from database.connection import db_query, db_pool
from utils.logging import logger


def init_rate_limit_table():
    """Initialize rate limit tracking table"""
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            # Set schema context
            cur.execute("SET search_path TO public")
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.task_creation_rate_limit (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Index for efficient querying
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_limit_user_time 
                ON public.task_creation_rate_limit(user_id, created_at)
            """)
            
            conn.commit()
            logger.info("Rate limit table initialized")
    except Exception as e:
        logger.error(f"Error initializing rate limit table: {e}")
        conn.rollback()
    finally:
        db_pool.putconn(conn)


def check_task_creation_rate_limit(user_id: int, max_tasks: int = 10, time_window_minutes: int = 10) -> dict:
    """
    Check if user has exceeded task creation rate limit.
    
    Args:
        user_id: Telegram user ID
        max_tasks: Maximum tasks allowed (default: 10)
        time_window_minutes: Time window in minutes (default: 10)
    
    Returns:
        {
            'allowed': bool,
            'current_count': int,
            'remaining': int,
            'reset_at': datetime or None
        }
    """
    now = datetime.utcnow()
    time_window = now - timedelta(minutes=time_window_minutes)
    
    # Get count of recent task creations
    result = db_query("""
        SELECT COUNT(*) as count, MAX(created_at) as latest
        FROM public.task_creation_rate_limit
        WHERE user_id = %s AND created_at > %s
    """, (user_id, time_window), fetchone=True)
    
    current_count = result['count'] if result else 0
    latest_time = result['latest'] if result else None
    
    allowed = current_count < max_tasks
    remaining = max(0, max_tasks - current_count)
    
    # Calculate reset time (when oldest request becomes older than window)
    reset_at = None
    if latest_time and current_count >= max_tasks:
        # Find the oldest request in the window
        oldest = db_query("""
            SELECT created_at
            FROM public.task_creation_rate_limit
            WHERE user_id = %s AND created_at > %s
            ORDER BY created_at ASC
            LIMIT 1
        """, (user_id, time_window), fetchone=True)
        
        if oldest:
            reset_at = oldest['created_at'] + timedelta(minutes=time_window_minutes)
    
    return {
        'allowed': allowed,
        'current_count': current_count,
        'remaining': remaining,
        'reset_at': reset_at
    }


def record_task_creation(user_id: int):
    """Record a new task creation for rate limiting"""
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public")
            cur.execute("""
                INSERT INTO public.task_creation_rate_limit (user_id, created_at)
                VALUES (%s, CURRENT_TIMESTAMP)
            """, (user_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Error recording task creation: {e}")
        conn.rollback()
    finally:
        db_pool.putconn(conn)


def cleanup_old_rate_limit_records(days: int = 1):
    """Clean up old rate limit records (older than specified days)"""
    cutoff_time = datetime.utcnow() - timedelta(days=days)
    
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public")
            cur.execute("""
                DELETE FROM public.task_creation_rate_limit
                WHERE created_at < %s
            """, (cutoff_time,))
            deleted = cur.rowcount
            conn.commit()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old rate limit records")
    except Exception as e:
        logger.error(f"Error cleaning up rate limit records: {e}")
        conn.rollback()
    finally:
        db_pool.putconn(conn)
