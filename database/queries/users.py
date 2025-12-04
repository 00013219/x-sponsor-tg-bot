from typing import Optional, Dict, List

from database.connection import db_query


def create_user(user_id: int, username: str, first_name: str):
    """
    Modified: Explicitly inserts NULL for language_code and timezone.
    This overrides the SQL DEFAULT 'en'/'Moscow', allowing us to detect
    new unconfigured users in start_command.
    """
    db_query("""
        INSERT INTO users (user_id, username, first_name, language_code, timezone)
        VALUES (%s, %s, %s, NULL, NULL)
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            is_active = TRUE
    """, (user_id, username, first_name), commit=True)


def get_user_by_username(username: str) -> Optional[Dict]:
    return db_query("SELECT * FROM users WHERE lower(username) = lower(%s)", (username,), fetchone=True)

def set_user_lang_tz(user_id: int, lang: str = None, tz: str = None):
    if lang:
        db_query("UPDATE users SET language_code = %s WHERE user_id = %s", (lang, user_id), commit=True)
    if tz:
        db_query("UPDATE users SET timezone = %s WHERE user_id = %s", (tz, user_id), commit=True)



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