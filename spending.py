from presentation import number
"""Independent TWD spending ledger. Money is stored as integer cents."""
import calendar
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from db import get_conn
from ledger import transaction

TZ = timezone(timedelta(hours=8))
CATEGORIES = ('餐飲', '交通', '購物', '居住', '娛樂', '醫療', '其他')


def today():
    return datetime.now(TZ).date()


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal('1000000000'):
            raise ValueError()
        cents = number * 100
        if cents != cents.to_integral_value():
            raise ValueError()
        return int(cents)
    except (InvalidOperation, ValueError):
        raise ValueError('金額需為正數，最多兩位小數且不超過十億元')


def category_names(user_id, include_inactive=False):
    overrides = {r['name']:r['active'] for r in rows('SELECT name,active FROM spending_categories WHERE user_id=?',(user_id,))}
    names = list(dict.fromkeys((*CATEGORIES,*overrides)))
    return names if include_inactive else [n for n in names if overrides.get(n,1)]


def category(value, user_id):
    names = category_names(user_id)
    if value not in names:
        raise ValueError('分類請選：' + '、'.join(names) + '；可用 !分類新增 建立')
    return value


def set_category(user_id,name,active):
    name = name.strip()
    if not name or len(name)>20 or name=='總額' or any(c in name for c in '\n\r'):
        raise ValueError('分類名稱需1～20字，不能使用「總額」')
    if not active and name not in category_names(user_id):
        raise ValueError('找不到啟用中的分類')
    with transaction() as conn:
        register(conn,user_id)
        conn.execute('INSERT INTO spending_categories VALUES(?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET active=excluded.active',(user_id,name,int(active)))


def reminder_levels(user_id):
    found = rows('SELECT levels FROM spending_settings WHERE user_id=?',(user_id,))
    return json.loads(found[0]['levels']) if found else [80,100]


def set_reminders(user_id,levels=None):
    if levels is not None:
        if not levels or len(levels)>10 or any(type(n) is not int or not 1<=n<=1000 for n in levels):
            raise ValueError('請設定1～10個整數百分比，範圍1～1000，例如 !提醒設定 50 80 100')
        levels = sorted(set(levels))
    with transaction() as conn:
        register(conn,user_id)
        if levels is None:
            conn.execute('DELETE FROM spending_settings WHERE user_id=?',(user_id,))
        else:
            conn.execute('INSERT INTO spending_settings VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET levels=excluded.levels',(user_id,json.dumps(levels)))
        # Remove obsolete, undelivered threshold notices; retain delivered history.
        enabled = levels if levels is not None else [80,100]
        for key,notice_key in conn.execute("SELECT id,notice_key FROM spending_notices WHERE user_id=? AND delivered=0 AND notice_key LIKE 'budget:%'",(user_id,)).fetchall():
            if int(notice_key.rsplit(':',1)[1]) not in enabled:
                conn.execute('DELETE FROM spending_notices WHERE id=?',(key,))
        alerts(conn,user_id,today().strftime('%Y-%m'))


def month_date(value):
    try:
        result = date.fromisoformat(value + '-01')
        if result.strftime('%Y-%m') != value:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('月份格式：YYYY-MM')


def next_month(value):
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def rows(sql, args=()):
    conn = get_conn()
    conn.row_factory = __import__('sqlite3').Row
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def init_schema(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS spending_categories(user_id TEXT NOT NULL,name TEXT NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(user_id,name));
    CREATE TABLE IF NOT EXISTS spending_settings(user_id TEXT PRIMARY KEY,levels TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS spending_users(user_id TEXT PRIMARY KEY, started TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS expenses(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, spent_on TEXT NOT NULL,
      cents INTEGER NOT NULL, category TEXT NOT NULL, note TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'manual', recurring_id INTEGER, period TEXT,
      voided INTEGER NOT NULL DEFAULT 0, UNIQUE(recurring_id,period));
    CREATE INDEX IF NOT EXISTS expenses_user_date ON expenses(user_id,spent_on);
    CREATE TABLE IF NOT EXISTS expense_actions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,expense_id INTEGER NOT NULL,
      before_json TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS budgets(
      user_id TEXT NOT NULL,month TEXT NOT NULL,category TEXT NOT NULL,cents INTEGER NOT NULL,
      PRIMARY KEY(user_id,month,category));
    CREATE TABLE IF NOT EXISTS recurring_expenses(
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,name TEXT NOT NULL,
      cents INTEGER NOT NULL,category TEXT NOT NULL,kind TEXT NOT NULL,
      start_month TEXT NOT NULL,periods INTEGER NOT NULL,due_day INTEGER,
      active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS spending_notices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,notice_key TEXT NOT NULL,
      body TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,UNIQUE(user_id,notice_key));
    ''')


def register(conn, user_id):
    conn.execute('INSERT OR IGNORE INTO spending_users VALUES(?,?)', (user_id, today().isoformat()))


def alerts(conn, user_id, month):
    setting = conn.execute('SELECT levels FROM spending_settings WHERE user_id=?',(user_id,)).fetchone()
    levels = json.loads(setting[0]) if setting else [80,100]
    for cat, budget in conn.execute('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id, month)).fetchall():
        sql = 'SELECT COALESCE(SUM(cents),0) FROM expenses WHERE user_id=? AND substr(spent_on,1,7)=? AND voided=0'
        args = [user_id, month]
        if cat != '總額':
            sql += ' AND category=?'
            args.append(cat)
        spent = conn.execute(sql, args).fetchone()[0]
        for level in levels:
            if spent * 100 >= budget * level:
                text = f'⚠️ {month} {cat}預算達 {level}%（目前使用率 {number(spent/budget*100)}%）：已用 {number(spent/100)}／預算 {number(budget/100)} 元，超支 {number(max(0,spent-budget)/100)} 元。'
                conn.execute('INSERT OR IGNORE INTO spending_notices(user_id,notice_key,body) VALUES(?,?,?)', (user_id, f'budget:{month}:{cat}:{level}', text))


def add(user_id, amount, cat, note, on=None):
    cents, cat = money(amount), category(cat,user_id)
    day = date.fromisoformat(on) if on else today()
    if day > today():
        raise ValueError('日常支出不能填未來日期；固定負擔請用 !固定新增')
    if not note.strip() or len(note) > 200:
        raise ValueError('用途需為 1～200 字')
    with transaction() as conn:
        register(conn, user_id)
        key = conn.execute('INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES(?,?,?,?,?)', (user_id, day.isoformat(), cents, cat, note)).lastrowid
        conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (user_id, key, 'null'))
        alerts(conn, user_id, day.strftime('%Y-%m'))
    return key


def edit(user_id, key, amount, cat, note, on):
    cents, cat, day = money(amount), category(cat,user_id), date.fromisoformat(on)
    if day > today() or not note.strip() or len(note) > 200:
        raise ValueError('請確認日期與用途，日期不可在未來')
    with transaction() as conn:
        conn.row_factory = __import__('sqlite3').Row
        old = conn.execute('SELECT * FROM expenses WHERE id=? AND user_id=? AND voided=0', (key, user_id)).fetchone()
        if not old:
            raise ValueError('找不到自己的有效支出')
        if old['source'] != 'manual' and on != old['spent_on']:
            raise ValueError('自動記帳的月份與日期不可移動，可調整金額、分类與用途')
        conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (user_id, key, json.dumps(dict(old))))
        conn.execute('UPDATE expenses SET spent_on=?,cents=?,category=?,note=? WHERE id=?', (on, cents, cat, note, key))
        alerts(conn, user_id, on[:7])


def undo(user_id, confirm=None):
    with transaction() as conn:
        row = conn.execute('SELECT id,expense_id,before_json FROM expense_actions WHERE user_id=? AND undone=0 ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
        if not row:
            raise ValueError('沒有可撤銷的生活記帳操作')
        if confirm is None:
            return row[0], row[1]
        if confirm != row[0]:
            raise ValueError('操作已變動，請重新輸入 !記帳撤銷')
        old = json.loads(row[2])
        if old is None:
            conn.execute('UPDATE expenses SET voided=1 WHERE id=? AND user_id=?', (row[1], user_id))
        else:
            conn.execute('UPDATE expenses SET spent_on=?,cents=?,category=?,note=?,voided=? WHERE id=? AND user_id=?', (old['spent_on'], old['cents'], old['category'], old['note'], old['voided'], row[1], user_id))
        conn.execute('UPDATE expense_actions SET undone=1 WHERE id=?', (row[0],))
        return row[0], row[1]


def set_budget(user_id, month, cat, amount):
    month_date(month)
    cents = money(amount)
    if cat != '總額':
        category(cat,user_id)
    with transaction() as conn:
        register(conn, user_id)
        existing = dict(conn.execute('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id, month)).fetchall())
        existing[cat] = cents
        if '總額' not in existing:
            raise ValueError('請先設定該月「總額」預算')
        if sum(v for k,v in existing.items() if k != '總額') > existing['總額']:
            raise ValueError('分類預算合計不可超過總預算')
        conn.execute('INSERT INTO budgets VALUES(?,?,?,?) ON CONFLICT(user_id,month,category) DO UPDATE SET cents=excluded.cents', (user_id, month, cat, cents))
        alerts(conn, user_id, month)


def add_recurring(user_id, kind, name, amount, cat, start, periods=0, due_day=None):
    if kind not in ('分期', '訂閱', '固定'):
        raise ValueError('種類請選：分期／訂閱／固定')
    beginning = month_date(start)
    current = today().replace(day=1)
    if beginning not in (current, next_month(current)):
        raise ValueError('開始月份請選本月或下月')
    if (kind == '分期' and not 1 <= periods <= 600) or (kind != '分期' and periods != 0):
        raise ValueError('分期期數需 1～600；固定／訂閱請填 0')
    if due_day is not None and not 1 <= due_day <= 31:
        raise ValueError('付款日需 1～31，短月以月底為準；僅作備註')
    if not name.strip() or len(name) > 100:
        raise ValueError('項目名稱需 1～100 字')
    cents, cat = money(amount), category(cat,user_id)
    with transaction() as conn:
        register(conn, user_id)
        return conn.execute('INSERT INTO recurring_expenses(user_id,name,cents,category,kind,start_month,periods,due_day) VALUES(?,?,?,?,?,?,?,?)', (user_id,name,cents,cat,kind,start,periods,due_day)).lastrowid


def sync_recurring(user_id=None, as_of=None):
    day = as_of or today()
    count = 0
    with transaction() as conn:
        conn.row_factory = __import__('sqlite3').Row
        query = 'SELECT * FROM recurring_expenses WHERE active=1'
        rules = conn.execute(query + (' AND user_id=?' if user_id is not None else ''), (user_id,) if user_id is not None else ()).fetchall()
        for rule in rules:
            month = month_date(rule['start_month'])
            index = 1
            while month <= day.replace(day=1) and (rule['periods'] == 0 or index <= rule['periods']):
                period = month.strftime('%Y-%m')
                note = rule['name'] + (f"（第 {index}/{rule['periods']} 期）" if rule['periods'] else '')
                cursor = conn.execute('INSERT OR IGNORE INTO expenses(user_id,spent_on,cents,category,note,source,recurring_id,period) VALUES(?,?,?,?,?,?,?,?)', (rule['user_id'],month.isoformat(),rule['cents'],rule['category'],note,rule['kind'],rule['id'],period))
                if cursor.rowcount:
                    count += 1
                    conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (rule['user_id'],cursor.lastrowid,'null'))
                    conn.execute('INSERT OR IGNORE INTO spending_notices(user_id,notice_key,body) VALUES(?,?,?)', (rule['user_id'],f"auto:{rule['id']}:{period}",f"📅 自動記帳／補記 {period}：{note} {number(rule['cents']/100)} 元（非銀行扣款）"))
                    alerts(conn, rule['user_id'], period)
                month = next_month(month)
                index += 1
    return count


def stop_recurring(user_id, key):
    sync_recurring(user_id)
    with transaction() as conn:
        if not conn.execute('UPDATE recurring_expenses SET active=0 WHERE id=? AND user_id=? AND active=1', (key,user_id)).rowcount:
            raise ValueError('找不到自己的啟用項目')


def report(user_id, start, end):
    entries = rows('SELECT * FROM expenses WHERE user_id=? AND spent_on>=? AND spent_on<=? AND voided=0 ORDER BY spent_on,id', (user_id,start.isoformat(),end.isoformat()))
    total = sum(r['cents'] for r in entries)
    cats = {}
    for cat in dict.fromkeys((*category_names(user_id,True),*(r['category'] for r in entries))):
        amount = sum(r['cents'] for r in entries if r['category']==cat)
        fixed = sum(r['cents'] for r in entries if r['category']==cat and r['source']!='manual')
        cats[cat] = dict(amount=amount/100,fixed=fixed/100,daily=(amount-fixed)/100,
                         share=round(amount/total*100,2) if total else None)
    return dict(start=start.isoformat(),end=end.isoformat(),record_count=len(entries),total=total/100,
                fixed=sum(r['cents'] for r in entries if r['source']!='manual')/100,categories=cats,
                coverage='僅代表已記錄資料；未記錄不代表沒有消費',has_records=bool(entries))


def month_report(user_id, month=None):
    start = month_date(month) if month else today().replace(day=1)
    if start > today():
        raise ValueError('尚未到此月份；未來固定負擔請查看 !固定清單')
    end = min(next_month(start)-timedelta(days=1), today())
    result = report(user_id,start,end)
    result['budgets'] = []
    for row in rows('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id,start.strftime('%Y-%m'))):
        spent = result['total'] if row['category']=='總額' else result['categories'][row['category']]['amount']
        budget = row['cents']/100
        result['budgets'].append(dict(category=row['category'],budget=budget,spent=spent,remaining=round(budget-spent,2),used_percent=round(spent/budget*100,2)))
    return result


def trends(user_id, unit='月', count=3):
    if unit not in ('月','週') or not 2 <= count <= 12:
        raise ValueError('期間請選 月／週，比較期數 2～12')
    now = today()
    periods = []
    if unit == '月':
        current = now.replace(day=1)
        starts = []
        for _ in range(count):
            starts.append(current)
            current = (current-timedelta(days=1)).replace(day=1)
        days = min(now.day, *(calendar.monthrange(s.year,s.month)[1] for s in starts))
        periods = [report(user_id,s,s+timedelta(days=days-1)) for s in starts]
    else:
        current = now-timedelta(days=now.weekday())
        periods = [report(user_id,current-timedelta(weeks=i),current-timedelta(weeks=i)+timedelta(days=now.weekday())) for i in range(count)]
    changes = []
    a,b = periods[:2]
    for cat in a['categories']:
        x,y = a['categories'][cat],b['categories'][cat]
        changes.append(dict(category=cat,amount_change=round(x['amount']-y['amount'],2) if a['has_records'] and b['has_records'] else None,
                            fixed_change=round(x['fixed']-y['fixed'],2) if a['has_records'] and b['has_records'] else None,
                            daily_change=round(x['daily']-y['daily'],2) if a['has_records'] and b['has_records'] else None,
                            share_percentage_point_change=round(x['share']-y['share'],2) if x['share'] is not None and y['share'] is not None else None))
    return dict(unit=unit,periods=periods,latest_changes=changes,current_month=month_report(user_id),
                caveats=['每期比較相同天數，資料不完整時不可推論真實消費減少','固定負擔月初一次列入，週比較不可解讀為消費突然增加','占比上升不一定代表金額增加','不提供收入或現金餘額，不自動調整預算'])


def notices(user_id):
    return rows('SELECT * FROM spending_notices WHERE user_id=? AND delivered=0 ORDER BY id', (user_id,))


def delivered(key, user_id):
    with transaction() as conn:
        conn.execute('UPDATE spending_notices SET delivered=1 WHERE id=? AND user_id=?', (key,user_id))


def clear(user_id):
    with transaction() as conn:
        for table in ('expenses','expense_actions','budgets','recurring_expenses','spending_notices','spending_users','spending_categories','spending_settings'):
            conn.execute(f'DELETE FROM {table} WHERE user_id=?', (user_id,))
