# --- Хелпер i18n ---
from telegram.ext import ContextTypes

from localization.texts import TEXTS


def get_text(key: str, context: ContextTypes.DEFAULT_TYPE, lang: str = None) -> str:
    """Получает текст на нужном языке из user_data или по умолчанию (en)."""
    if not lang:
        lang = context.user_data.get('language_code', 'en')

    if lang not in TEXTS:
        lang = 'en'

    return TEXTS.get(lang, {}).get(key) or TEXTS['en'].get(key, f"_{key}_")