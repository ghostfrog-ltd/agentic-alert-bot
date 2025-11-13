# infrastructure/utils/db_connection.py
from __future__ import annotations

import os
import psycopg2
from psycopg2.extensions import connection as PGConnection
from dotenv import load_dotenv

from infrastructure.utils.logger import get_logger

logger = get_logger(__name__)

load_dotenv()

database = os.getenv("DB_NAME")
user = os.getenv("DB_USER")
password = os.getenv("DB_PASS")
host = os.getenv("DB_HOST")
port = os.getenv("DB_PORT")

# ------------------------------
# Core connection constructors
# ------------------------------


def create_connection() -> PGConnection:
    """
    Always create a brand new psycopg2 connection.
    """
    logger.debug("[DB] Creating new psycopg2 connection")
    return psycopg2.connect(
        database=database,
        user=user,
        password=password,
        host=host,
        port=port,
    )


# Shared connection (managed via get_connection)
_shared_connection: PGConnection | None = None


def get_connection() -> PGConnection:
    """
    Return a shared connection. If it's None or closed, recreate it.
    """
    global _shared_connection
    if _shared_connection is None or _shared_connection.closed:
        logger.debug("[DB] (Re)initialising shared shared_connection")
        _shared_connection = create_connection()
    return _shared_connection


# -------------------------------------------------------
# Lazy proxy so `connection` auto-heals (for legacy code)
# -------------------------------------------------------
class ConnectionProxy:
    """
    A thin proxy around get_connection() so that existing code that
    imports `connection` still works, but always talks to a live
    psycopg2 connection.

    Supports:
    - attribute access (cursor, commit, etc.)
    - context manager: `with connection: ...`
    """

    def _conn(self) -> PGConnection:
        return get_connection()

    def __getattr__(self, name):
        return getattr(self._conn(), name)

    def __enter__(self):
        return self._conn().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn().__exit__(exc_type, exc_val, exc_tb)


# This is what old code will use:
connection: PGConnection = ConnectionProxy()
