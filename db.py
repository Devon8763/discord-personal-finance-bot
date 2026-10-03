import os
import sqlite3
from pathlib import Path

from schema import initialize_schema


def configured_db_path(environment=None) -> str:
    environment = os.environ if environment is None else environment
    configured = environment.get("DISCORDBOT_DB_PATH", "").strip()
    if not configured:
        return str(Path(__file__).with_name("data.db"))
    if not Path(configured).is_absolute():
        raise ValueError("DISCORDBOT_DB_PATH 必須是絕對路徑")
    return configured


DB_NAME = configured_db_path()


def get_conn():
    # All writers already use BEGIN IMMEDIATE; allow short competing writes to finish.
    return sqlite3.connect(DB_NAME, timeout=10)


def init_db():
    conn = get_conn()
    try:
        initialize_schema(conn)
    finally:
        conn.close()
