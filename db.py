import sqlite3
from pathlib import Path

from schema import initialize_schema

DB_NAME = str(Path(__file__).with_name("data.db"))


def get_conn():
    # All writers already use BEGIN IMMEDIATE; allow short competing writes to finish.
    return sqlite3.connect(DB_NAME, timeout=10)


def init_db():
    conn = get_conn()
    try:
        initialize_schema(conn)
    finally:
        conn.close()
