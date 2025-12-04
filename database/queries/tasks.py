from typing import Optional, Dict, List

from database.connection import db_query
from utils.logging import logger


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

def create_new_task(user_id: int) -> Optional[int]:
    """
    Создает новую задачу (черновик).
    (Based on existing INSERT logic)
    """
    result = db_query(""" 
        INSERT INTO tasks (user_id, status) VALUES (%s, 'inactive') 
        RETURNING id 
    """, (user_id,), commit=True)
    if result and 'id' in result:
        logger.info(f"Создана новая задача ID: {result['id']} для user {user_id}")
        return result['id']
    return None

def get_task_details(task_id: int) -> Optional[Dict]:
    """Получает все данные о задаче для конструктора"""
    return db_query("SELECT * FROM tasks WHERE id = %s", (task_id,), fetchone=True)


def get_user_tasks(user_id: int) -> List[Dict]:
    """Получает список задач для экрана 'Мои задачи'"""
    return db_query("""
        SELECT id, task_name, status, created_at
        FROM tasks 
        WHERE user_id = %s 
        ORDER BY created_at DESC
    """, (user_id,), fetchall=True) or []


def get_user_task_count(user_id: int) -> int:
    """Returns the total number of tasks (active or inactive) created by the user."""
    result = db_query("SELECT COUNT(*) as count FROM tasks WHERE user_id = %s", (user_id,), fetchone=True)
    return result['count'] if result else 0

