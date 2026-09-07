"""Atomic trade recording and reversal, including pre-upgrade holdings."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from db import get_conn
from portfolio import normalize_symbol, positive


@contextmanager
def transaction():
    conn = get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def trade(user_id, kind, name, price=0, quantity=0):
    if kind not in ('buy', 'sell', 'fundbuy', 'fundsell', 'remove'):
        raise ValueError('未知交易類型')
    if kind != 'remove' and not all(positive(v) for v in (price, quantity)):
        raise ValueError('價格、金額與數量必須是有效正數')
    fund = kind.startswith('fund')
    if not fund:
        name = normalize_symbol(name)
    if not name.strip():
        raise ValueError('名稱不可空白')
    with transaction() as conn:
        before = []
        row_id = None
        profit = None
        if fund:
            cost, held = conn.execute('SELECT COALESCE(SUM(amount),0), COALESCE(SUM(units),0) FROM fund_transactions WHERE user_id=? AND fund_name=?', (user_id, name)).fetchone()
            if kind == 'fundbuy':
                amount, units = quantity, quantity / price
                quantity = units
            else:
                if held <= 0 or quantity > held:
                    raise ValueError('贖回單位超過持有，或沒有這檔基金')
                amount, units = -cost / held * quantity, -quantity
                profit = price * quantity + amount
            row_id = conn.execute('INSERT INTO fund_transactions(user_id,fund_name,amount,price,units) VALUES(?,?,?,?,?)', (user_id, name, amount, price, units)).lastrowid
            remaining = held + units
            average = (cost + amount) / remaining if remaining else 0
        else:
            before = conn.execute('SELECT buy_price,shares FROM assets WHERE user_id=? AND symbol=?', (user_id, name)).fetchall()
            held = sum(r[1] or 0 for r in before)
            cost = sum((r[0] or 0) * (r[1] or 0) for r in before)
            average = cost / held if held else 0
            if kind == 'buy':
                remaining = held + quantity
                average = (cost + price * quantity) / remaining
            elif kind == 'sell':
                if held <= 0 or quantity > held:
                    raise ValueError('賣出股數超過持有，或沒有這檔股票')
                remaining = held - quantity
                profit = (price - average) * quantity
            else:
                if not before:
                    raise ValueError('沒有這檔股票')
                remaining, quantity = 0, held
            conn.execute('DELETE FROM assets WHERE user_id=? AND symbol=?', (user_id, name))
            if remaining:
                conn.execute('INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES(?,?,?,?)', (user_id, name, average, remaining))
        if not all(positive(x) or x == 0 for x in (remaining, average, quantity)):
            raise ValueError('數值超出有效範圍')
        trade_id = conn.execute('INSERT INTO trade_history(user_id,kind,name,price,quantity,profit,before_state,fund_row_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (user_id, kind, name, price, quantity, profit, json.dumps(before), row_id, now())).lastrowid
        return dict(id=trade_id, name=name, quantity=quantity, remaining=remaining, average=average, profit=profit)


def history(user_id, limit=20, active_only=False):
    conn = get_conn()
    conn.row_factory = __import__('sqlite3').Row
    try:
        clause = ' AND undone=0' if active_only else ''
        return [dict(r) for r in conn.execute('SELECT * FROM trade_history WHERE user_id=?' + clause + ' ORDER BY id DESC LIMIT ?', (user_id, limit))]
    finally:
        conn.close()


def undo(user_id, trade_id):
    with transaction() as conn:
        conn.row_factory = __import__('sqlite3').Row
        row = conn.execute('SELECT * FROM trade_history WHERE user_id=? AND undone=0 ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
        if not row or row['id'] != trade_id:
            raise ValueError('只能撤銷自己最新一筆有效交易，請重新輸入 !undo 查看')
        if row['kind'].startswith('fund'):
            cursor = conn.execute('DELETE FROM fund_transactions WHERE id=? AND user_id=?', (row['fund_row_id'], user_id))
            if cursor.rowcount != 1:
                raise ValueError('原始基金交易不存在，無法撤銷')
        else:
            conn.execute('DELETE FROM assets WHERE user_id=? AND symbol=?', (user_id, row['name']))
            for price, shares in json.loads(row['before_state']):
                conn.execute('INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES(?,?,?,?)', (user_id, row['name'], price, shares))
        conn.execute('UPDATE trade_history SET undone=1 WHERE id=?', (trade_id,))
        return row['name']


def save_fund_price(user_id, name, price):
    if not positive(price):
        raise ValueError('淨值必須是有效正數')
    timestamp = now()
    with transaction() as conn:
        if not conn.execute('SELECT 1 FROM fund_transactions WHERE user_id=? AND fund_name=?', (user_id, name)).fetchone():
            raise ValueError('找不到這檔基金，請先確認名稱或使用 !fundbuy 記帳')
        conn.execute('INSERT INTO fund_prices VALUES(?,?,?,?) ON CONFLICT(user_id,fund_name) DO UPDATE SET price=excluded.price,updated_at=excluded.updated_at', (user_id, name, price, timestamp))
    return timestamp
