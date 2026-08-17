import logging
from typing import Any, Dict, List, Optional

import mysql.connector
from mysql.connector import MySQLConnection
from config import DB_CONFIG

logger = logging.getLogger(__name__)


def get_connection() -> MySQLConnection:
    """
    Establish a MySQL connection using configured DB_CONFIG.
    Logs sanitized connection information without exposing passwords.
    """
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Exception as exc:
        sanitized_config = {k: ("***" if k == "password" else v) for k, v in DB_CONFIG.items()}
        logger.error("Failed to connect to MySQL database %s: %s", sanitized_config, exc)
        raise



