import logging
from typing import Any, Dict, List, Optional

import mysql.connector
from mysql.connector import MySQLConnection, pooling
from config import DB_CONFIG

logger = logging.getLogger(__name__)

# Connection pool for web app to eliminate TCP handshake overhead on every request
_POOL: Optional[pooling.MySQLConnectionPool] = None

try:
    _POOL = pooling.MySQLConnectionPool(
        pool_name="stockpro_pool",
        pool_size=15,
        pool_reset_session=True,
        **DB_CONFIG
    )
    logger.info("Initialized MySQL connection pool with size 15.")
except Exception as exc:
    logger.warning("Failed to initialize MySQL pool, falling back to direct connections: %s", exc)
    _POOL = None


def get_connection() -> MySQLConnection:
    """
    Establish or acquire a MySQL connection from the connection pool.
    Logs sanitized connection information on failure without exposing passwords.
    """
    global _POOL
    if _POOL is not None:
        try:
            return _POOL.get_connection()
        except Exception as exc:
            logger.warning("Pool connection acquisition failed (%s), attempting direct connect.", exc)

    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Exception as exc:
        sanitized_config = {k: ("***" if k == "password" else v) for k, v in DB_CONFIG.items()}
        logger.error("Failed to connect to MySQL database %s: %s", sanitized_config, exc)
        raise




