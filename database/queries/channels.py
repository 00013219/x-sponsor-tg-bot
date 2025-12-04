from typing import List, Dict

from database.connection import db_query
from utils.logging import logger


def get_user_channels(user_id: int) -> List[Dict]:
    return db_query("""
        SELECT * FROM channels
        WHERE user_id = %s AND is_active = TRUE
        ORDER BY added_at DESC
    """, (user_id,), fetchall=True) or []

def add_channel(user_id: int, channel_id: int, title: str, username: str = None) -> tuple[bool, str]:
    """
    Добавляет канал.
    Возвращает (True, msg) если успешно или (False, msg) если канал занят другим юзером.
    """
    # 1. Check if channel exists and belongs to another user
    existing = db_query("SELECT user_id FROM channels WHERE channel_id = %s", (channel_id,), fetchone=True)

    if existing and existing['user_id'] != user_id:
        return False, "occupied"

    # 2. Insert or Update (Only if owner is same or new)
    db_query("""
        INSERT INTO channels (user_id, channel_id, channel_title, channel_username, is_active)
        VALUES (%s, %s, %s, %s, TRUE)
        ON CONFLICT (channel_id) DO UPDATE
        SET channel_title = EXCLUDED.channel_title,
            channel_username = EXCLUDED.channel_username,
            is_active = TRUE
            -- We do NOT update user_id here because we checked ownership above
    """, (user_id, channel_id, title, username), commit=True)

    logger.info(f"Канал {title} (ID: {channel_id}) добавлен/обновлен для user {user_id}")
    return True, "success"


def deactivate_channel(channel_id: int):
    db_query("UPDATE channels SET is_active = FALSE WHERE channel_id = %s", (channel_id,), commit=True)
    logger.info(f"Канал {channel_id} деактивирован")


