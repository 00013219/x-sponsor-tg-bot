from typing import Optional, Any

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from config.settings import DATABASE_URL
from utils.logging import logger

try:
    if not DATABASE_URL:
        logger.critical("DATABASE_URL не установлен! Бот не может работать без БД.")
        db_pool = None
    else:
        db_pool = SimpleConnectionPool(1, 20, DATABASE_URL)
        logger.info("Пул соединений с БД успешно создан")
except Exception as e:
    logger.error(f"Не удалось создать пул соединений с БД: {e}")
    db_pool = None


def db_query(sql: str, params: tuple = None, fetchone=False, fetchall=False, commit=False) -> Optional[Any]:
    """Универсальный хелпер для запросов к БД с улучшенной обработкой ошибок"""
    if not db_pool:
        logger.error("DB pool not available in db_query")
        return None

    conn = None
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            conn = db_pool.getconn()

            # Test if connection is alive
            with conn.cursor() as test_cur:
                test_cur.execute("SELECT 1")

            # Connection is good, proceed with actual query
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params or ())

                if commit:
                    conn.commit()
                    if fetchone:
                        return dict(cur.fetchone()) if cur.rowcount else None
                    if "RETURNING" in sql.upper() and cur.rowcount:
                        row = cur.fetchone()
                        return dict(row) if row else None
                    return None

                if fetchone:
                    row = cur.fetchone()
                    return dict(row) if row else None
                if fetchall:
                    return [dict(row) for row in cur.fetchall()]

                # Для INSERT ... RETURNING id
                if "RETURNING" in sql.upper() and cur.rowcount:
                    row = cur.fetchone()
                    return dict(row) if row else None

            # Success - return connection to pool
            if conn and db_pool:
                db_pool.putconn(conn)
            return None

        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # Connection error - close bad connection and retry
            logger.warning(f"DB connection error (attempt {retry_count + 1}/{max_retries}): {e}")

            if conn:
                try:
                    conn.close()
                except:
                    pass
                # Remove bad connection from pool
                try:
                    db_pool.putconn(conn, close=True)
                except:
                    pass
                conn = None

            retry_count += 1

            if retry_count >= max_retries:
                logger.error(f"DB query failed after {max_retries} attempts (SQL: {sql[:100]}...): {e}")
                return None

            # Wait a bit before retrying
            import time
            time.sleep(0.5 * retry_count)

        except (Exception, psycopg2.Error) as e:
            logger.error(f"DB error in db_query (SQL: {sql[:100]}...): {e}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            return None

        finally:
            # Always return connection to pool if we still have it
            if conn and db_pool:
                try:
                    db_pool.putconn(conn)
                except:
                    pass

    return None