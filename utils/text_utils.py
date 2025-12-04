import re

from telegram.ext import ContextTypes

from localization.loader import get_text


def generate_smart_name(text: str, context: ContextTypes.DEFAULT_TYPE, limit: int = 3) -> str:
    """
    Возвращает первые N слов текста.
    Если слов меньше — берёт сколько есть.
    """
    if not text:
        return get_text('name_not_set', context)

    # Убираем пунктуацию, оставляем буквы/цифры/пробелы
    clean_text = re.sub(r"[^\w\s]", "", text)
    words = clean_text.split()

    return " ".join(words[:limit]) + ("..." if len(words) > limit else "")