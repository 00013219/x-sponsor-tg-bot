from typing import List

from database.connection import db_query


def add_task_channel(task_id: int, channel_id: int):
    """Добавляет канал к задаче"""
    db_query("""
        INSERT INTO task_channels (task_id, channel_id)
        VALUES (%s, %s)
        ON CONFLICT (task_id, channel_id) DO NOTHING
    """, (task_id, channel_id), commit=True)

def get_task_channels(task_id: int) -> List[int]:
    """Получает список channel_id для задачи"""
    result = db_query("""
        SELECT channel_id FROM task_channels WHERE task_id = %s
    """, (task_id,), fetchall=True)
    return [row['channel_id'] for row in result] if result else []


def remove_task_channel(task_id: int, channel_id: int):
    """Удаляет канал из задачи"""
    db_query("""
        DELETE FROM task_channels WHERE task_id = %s AND channel_id = %s
    """, (task_id, channel_id), commit=True)
