from typing import List, Dict
from database.connection import db_query

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




