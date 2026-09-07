import sqlite3
from pathlib import Path

DB_NAME = str(Path(__file__).with_name("data.db"))


def get_conn():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # ========================
    # 使用者
    # ========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY
    )
    """)

    # ========================
    # 股票 / ETF（含 shares🔥）
    # ========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        symbol TEXT,
        buy_price REAL,
        shares REAL
    )
    """)

    # ========================
    # 基金交易
    # ========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS fund_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        fund_name TEXT,
        amount REAL,
        price REAL,
        units REAL
    )
    """)

    # ========================
    # Watchlist
    # ========================
    c.execute("""
    CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        symbol TEXT
    )
    """)

    # ========================
    # 🔥 升級舊資料（關鍵）
    # ========================

    # 1️⃣ 如果舊 DB 沒有 shares → 加欄位
    try:
        c.execute("ALTER TABLE assets ADD COLUMN shares REAL")
    except:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS fund_prices (
        user_id TEXT NOT NULL, fund_name TEXT NOT NULL, price REAL NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(user_id, fund_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
        kind TEXT NOT NULL, name TEXT NOT NULL, price REAL, quantity REAL,
        profit REAL, before_state TEXT NOT NULL, fund_row_id INTEGER,
        created_at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)''')

    # 2️⃣ 把舊資料補 0（避免 None 爆炸）
    try:
        c.execute("UPDATE assets SET shares = 0 WHERE shares IS NULL")
    except:
        pass

    conn.commit()
    from spending import init_schema
    init_schema(conn)
    conn.commit()
    conn.close()
