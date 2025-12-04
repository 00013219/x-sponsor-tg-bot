from typing import Dict

from database.connection import db_query

def get_user_settings(user_id: int) -> Dict:
    return db_query("SELECT language_code, timezone, tariff FROM users WHERE user_id = %s", (user_id,),
                    fetchone=True) or {}