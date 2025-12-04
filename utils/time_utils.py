import re
from typing import Optional

from telegram.ext import ContextTypes


def parse_human_duration(text: str) -> Optional[float]:
    """
    Parses '30m', '12h', '1.5h', '1d' into FLOAT hours.
    Allows minute-level precision (e.g. 5m = 0.0833 hours).
    """
    text = text.lower().strip().replace(" ", "").replace(",", ".")

    # Regex to capture number (integer or decimal) and unit
    match = re.match(r"^(\d+(\.\d+)?)([mhd])?.*$", text)

    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(3)

    if unit == 'd':
        return value * 24.0
    elif unit == 'm':
        # Return exact fraction of hours (e.g. 30m -> 0.5h)
        return value / 60.0
    else:
        # Default to hours
        return value

def format_hours_to_dhms(hours: float, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Converts float hours (e.g., 0.08333) into localized string (e.g., '5m', '5м', '1h 30m').
    """
    # Use absolute value
    hours = abs(hours)
    if hours == 0:
        return ""

    # Get user language
    lang = context.user_data.get('language_code', 'en')

    # Define localized units (You can move these to your TEXTS dictionary if preferred)
    # This map ensures it works immediately in bot.py
    unit_map = {
        'ru': {'d': 'д', 'h': 'ч', 'm': 'м'},
        'en': {'d': 'd', 'h': 'h', 'm': 'm'},
        'es': {'d': 'd', 'h': 'h', 'm': 'm'},
        'fr': {'d': 'j', 'h': 'h', 'm': 'm'},
        'ua': {'d': 'д', 'h': 'г', 'm': 'хв'},
        'de': {'d': 'T', 'h': 'h', 'm': 'm'},
    }

    # Fallback to English if lang.py not found
    u = unit_map.get(lang, unit_map['en'])

    # Round to nearest second to avoid floating point issues
    total_seconds = int(round(hours * 3600))

    days = total_seconds // 86400
    total_seconds %= 86400

    _hours = total_seconds // 3600
    total_seconds %= 3600

    minutes = total_seconds // 60

    parts = []
    if days > 0:
        parts.append(f"{days}{u['d']}")
    if _hours > 0:
        parts.append(f"{_hours}{u['h']}")
    if minutes > 0:
        parts.append(f"{minutes}{u['m']}")

    # Edge case: If duration is > 0 but < 1 min (e.g. 30s), show 1m
    if not parts and hours > 0:
        return f"1{u['m']}"

    return " ".join(parts)