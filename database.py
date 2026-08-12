import logging
from typing import Any, Dict, List, Optional

import mysql.connector
from mysql.connector import MySQLConnection
from config import DB_CONFIG


logger = logging.getLogger(__name__)


def get_connection() -> MySQLConnection:
    return mysql.connector.connect(**DB_CONFIG)


