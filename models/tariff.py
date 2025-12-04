from enum import Enum

from utils.logging import logger


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
