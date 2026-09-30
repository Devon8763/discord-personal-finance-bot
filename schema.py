"""SQLite schema creation and legacy-compatible initialization helpers."""


def create_core_tables(conn):
    for statement in (
        "CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY)",
        "CREATE TABLE IF NOT EXISTS assets (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,symbol TEXT,buy_price REAL,shares REAL)",
        "CREATE TABLE IF NOT EXISTS fund_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,fund_name TEXT,amount REAL,price REAL,units REAL)",
        "CREATE TABLE IF NOT EXISTS watchlist (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,symbol TEXT)",
        "CREATE TABLE IF NOT EXISTS fund_prices (user_id TEXT NOT NULL,fund_name TEXT NOT NULL,price REAL NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(user_id,fund_name))",
        "CREATE TABLE IF NOT EXISTS trade_history (id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,price REAL,quantity REAL,profit REAL,before_state TEXT NOT NULL,fund_row_id INTEGER,created_at TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0)",
    ):
        conn.execute(statement)


def create_life_tables(conn):
    for statement in (
        "CREATE TABLE IF NOT EXISTS spending_categories(user_id TEXT NOT NULL,name TEXT NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(user_id,name))",
        "CREATE TABLE IF NOT EXISTS spending_settings(user_id TEXT PRIMARY KEY,levels TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS spending_onboarding(user_id TEXT PRIMARY KEY)",
        "CREATE TABLE IF NOT EXISTS spending_users(user_id TEXT PRIMARY KEY,started TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,spent_on TEXT NOT NULL,cents INTEGER NOT NULL,category TEXT NOT NULL,note TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'manual',recurring_id INTEGER,period TEXT,voided INTEGER NOT NULL DEFAULT 0,UNIQUE(recurring_id,period))",
        "CREATE TABLE IF NOT EXISTS expense_actions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,expense_id INTEGER NOT NULL,before_json TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS budgets(user_id TEXT NOT NULL,month TEXT NOT NULL,category TEXT NOT NULL,cents INTEGER NOT NULL,PRIMARY KEY(user_id,month,category))",
        "CREATE TABLE IF NOT EXISTS recurring_expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,name TEXT NOT NULL,cents INTEGER NOT NULL,category TEXT NOT NULL,kind TEXT NOT NULL,start_month TEXT NOT NULL,periods INTEGER NOT NULL,due_day INTEGER,active INTEGER NOT NULL DEFAULT 1)",
        "CREATE TABLE IF NOT EXISTS recurring_expense_versions(user_id TEXT NOT NULL,recurring_id INTEGER NOT NULL,effective_month TEXT NOT NULL,name TEXT NOT NULL,cents INTEGER NOT NULL,category TEXT NOT NULL,due_day INTEGER NOT NULL CHECK(due_day BETWEEN 1 AND 31),PRIMARY KEY(recurring_id,effective_month))",
        "CREATE TABLE IF NOT EXISTS spending_notices(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,notice_key TEXT NOT NULL,body TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,UNIQUE(user_id,notice_key))",
        "CREATE TABLE IF NOT EXISTS payment_sources(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,UNIQUE(user_id,name))",
        "CREATE TABLE IF NOT EXISTS spending_shortcuts(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,name TEXT NOT NULL,category TEXT NOT NULL,payment_source_id INTEGER NOT NULL,note TEXT NOT NULL,cents INTEGER,position INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1)",
    ):
        conn.execute(statement)


def create_life_indexes(conn):
    for statement in (
        "CREATE INDEX IF NOT EXISTS expenses_user_date ON expenses(user_id,spent_on)",
        "CREATE INDEX IF NOT EXISTS payment_sources_user_active ON payment_sources(user_id,active,id)",
        "CREATE INDEX IF NOT EXISTS expenses_user_kind_date ON expenses(user_id,kind,voided,spent_on,id)",
        "CREATE INDEX IF NOT EXISTS shortcuts_user_order ON spending_shortcuts(user_id,active,position,id)",
    ):
        conn.execute(statement)


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply_legacy_compatibility(conn):
    if "shares" not in _column_names(conn, "assets"):
        conn.execute("ALTER TABLE assets ADD COLUMN shares REAL")
    conn.execute("UPDATE assets SET shares=0 WHERE shares IS NULL")

    expense_columns = _column_names(conn, "expenses")
    for name, definition in (
        ("payment_source_id", "INTEGER"),
        ("payment_source_name", "TEXT NOT NULL DEFAULT '未指定'"),
        ("kind", "TEXT NOT NULL DEFAULT 'consumption'"),
        ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in expense_columns:
            conn.execute(f"ALTER TABLE expenses ADD COLUMN {name} {definition}")

    if "revision" not in _column_names(conn, "recurring_expenses"):
        conn.execute("ALTER TABLE recurring_expenses ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "INSERT OR IGNORE INTO recurring_expense_versions "
        "SELECT user_id,id,start_month,name,cents,category,COALESCE(due_day,1) "
        "FROM recurring_expenses WHERE kind='固定'"
    )

    users = conn.execute("SELECT user_id FROM spending_users UNION SELECT user_id FROM expenses").fetchall()
    for (user_id,) in users:
        conn.executemany(
            "INSERT OR IGNORE INTO payment_sources(user_id,name) VALUES(?,?)",
            ((user_id, "未指定"), (user_id, "現金")),
        )


def initialize_schema(conn):
    try:
        conn.execute("BEGIN IMMEDIATE")
        create_core_tables(conn)
        create_life_tables(conn)
        apply_legacy_compatibility(conn)
        create_life_indexes(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
