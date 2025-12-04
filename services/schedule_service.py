from datetime import datetime, timedelta


def get_next_weekday(base: datetime, target_weekday: int) -> datetime:
    """
    base: datetime in UTC
    target_weekday: Monday=0 ... Sunday=6
    Returns next occurrence of that weekday (excluding today).
    """
    days_ahead = (target_weekday - base.weekday() + 7) % 7
    if days_ahead == 0:
        days_ahead = 7
    return base + timedelta(days=days_ahead)

def get_next_weekday_including_today(base: datetime, target_weekday: int) -> datetime:
    """
    base: datetime in UTC
    target_weekday: Monday=0 ... Sunday=6

    If the target weekday is today → return today.
    Otherwise → find the next occurrence of the weekday.
    """
    days_ahead = (target_weekday - base.weekday()) % 7
    return base + timedelta(days=days_ahead)