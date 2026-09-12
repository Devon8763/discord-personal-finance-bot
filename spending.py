from presentation import number
"""Independent TWD spending ledger. Money is stored as integer cents."""
import calendar
import json
import sqlite3
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
    CREATE TABLE IF NOT EXISTS spending_onboarding(user_id TEXT PRIMARY KEY);
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
    # Additive, repeatable upgrade. Old action JSON is handled by undo defaults.
    conn.execute('BEGIN IMMEDIATE')
    try:
        columns = {r[1] for r in conn.execute('PRAGMA table_info(expenses)')}
        for name, definition in (
            ('payment_source_id', 'INTEGER'),
            ('payment_source_name', "TEXT NOT NULL DEFAULT '未指定'"),
            ('kind', "TEXT NOT NULL DEFAULT 'consumption'"),
            ('revision', 'INTEGER NOT NULL DEFAULT 0'),
        ):
            if name not in columns:
                conn.execute(f'ALTER TABLE expenses ADD COLUMN {name} {definition}')
        conn.execute('''CREATE TABLE IF NOT EXISTS payment_sources(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(user_id,name))''')
        conn.execute('CREATE INDEX IF NOT EXISTS payment_sources_user_active ON payment_sources(user_id,active,id)')
        conn.execute('CREATE INDEX IF NOT EXISTS expenses_user_kind_date ON expenses(user_id,kind,voided,spent_on,id)')
        conn.execute('''CREATE TABLE IF NOT EXISTS spending_shortcuts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,name TEXT NOT NULL,
            category TEXT NOT NULL,payment_source_id INTEGER NOT NULL,note TEXT NOT NULL,
            cents INTEGER,position INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1)''')
        conn.execute('CREATE INDEX IF NOT EXISTS shortcuts_user_order ON spending_shortcuts(user_id,active,position,id)')
        for (user_id,) in conn.execute('SELECT user_id FROM spending_users UNION SELECT user_id FROM expenses').fetchall():
            ensure_payment_sources(conn, user_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def register(conn, user_id):
    conn.execute('INSERT OR IGNORE INTO spending_users VALUES(?,?)', (user_id, today().isoformat()))
    ensure_payment_sources(conn, user_id)


def ensure_payment_sources(conn, user_id):
    conn.executemany('INSERT OR IGNORE INTO payment_sources(user_id,name) VALUES(?,?)',
                     [(user_id, '未指定'), (user_id, '現金')])


def payment_sources(user_id, include_inactive=False):
    with transaction() as conn:
        ensure_payment_sources(conn, user_id)
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            'SELECT * FROM payment_sources WHERE user_id=?' + ('' if include_inactive else ' AND active=1') +
            " ORDER BY CASE name WHEN '未指定' THEN 0 WHEN '現金' THEN 1 ELSE 2 END,id", (user_id,))]


def payment_name(name):
    name = name.strip()
    if not name or len(name) > 30 or any(ord(c) < 32 for c in name):
        raise ValueError('付款來源名稱需 1～30 字，不可包含換行或控制字元')
    return name


def add_payment_source(user_id, name):
    name = payment_name(name)
    with transaction() as conn:
        register(conn, user_id)
        try:
            return conn.execute('INSERT INTO payment_sources(user_id,name) VALUES(?,?)', (user_id, name)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('已有同名付款來源（含停用項目），請使用其他名稱') from None


def rename_payment_source(user_id, key, name):
    name = payment_name(name)
    with transaction() as conn:
        old = conn.execute('SELECT name FROM payment_sources WHERE id=? AND user_id=?', (key, user_id)).fetchone()
        if not old:
            raise ValueError('找不到自己的付款來源')
        if old[0] in ('現金', '未指定'):
            raise ValueError('「現金」與「未指定」為保留來源，不能改名或停用')
        try:
            conn.execute('UPDATE payment_sources SET name=? WHERE id=? AND user_id=?', (name, key, user_id))
        except sqlite3.IntegrityError:
            raise ValueError('已有同名付款來源（含停用項目），請使用其他名稱') from None


def disable_payment_source(user_id, key):
    with transaction() as conn:
        old = conn.execute('SELECT name FROM payment_sources WHERE id=? AND user_id=? AND active=1', (key, user_id)).fetchone()
        if not old:
            raise ValueError('找不到自己的啟用付款來源')
        if old[0] in ('現金', '未指定'):
            raise ValueError('「現金」與「未指定」為保留來源，不能改名或停用')
        conn.execute('UPDATE payment_sources SET active=0 WHERE id=? AND user_id=?', (key, user_id))


def resolve_payment(conn, user_id, key):
    if key is None:
        result = conn.execute("SELECT id,name FROM payment_sources WHERE user_id=? AND name='未指定' AND active=1", (user_id,)).fetchone()
    else:
        result = conn.execute('SELECT id,name FROM payment_sources WHERE user_id=? AND id=? AND active=1', (user_id, key)).fetchone()
    if not result:
        raise ValueError('付款來源已停用或不屬於你，請重新選擇；也可選「未指定」')
    return result[0], result[1]


def get_expense(user_id, key):
    found = rows("SELECT * FROM expenses WHERE user_id=? AND id=? AND voided=0 AND kind='consumption'", (user_id, key))
    if not found:
        raise ValueError('找不到自己的有效消費')
    return found[0]


def month_expenses(user_id, month):
    start = month_date(month)
    return rows("SELECT * FROM expenses WHERE user_id=? AND spent_on>=? AND spent_on<? AND voided=0 AND kind='consumption' ORDER BY spent_on DESC,id DESC",
                (user_id, start.isoformat(), next_month(start).isoformat()))


def alerts(conn, user_id, month):
    setting = conn.execute('SELECT levels FROM spending_settings WHERE user_id=?',(user_id,)).fetchone()
    levels = json.loads(setting[0]) if setting else [80,100]
    for cat, budget in conn.execute('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id, month)).fetchall():
        sql = "SELECT COALESCE(SUM(cents),0) FROM expenses WHERE user_id=? AND substr(spent_on,1,7)=? AND voided=0 AND kind='consumption'"
        args = [user_id, month]
        if cat != '總額':
            sql += ' AND category=?'
            args.append(cat)
        spent = conn.execute(sql, args).fetchone()[0]
        for level in levels:
            if spent * 100 >= budget * level:
                text = f'⚠️ {month} {cat}預算達 {level}%（目前使用率 {number(spent/budget*100)}%）：已用 {number(spent/100)}／預算 {number(budget/100)} 元，超支 {number(max(0,spent-budget)/100)} 元。'
                conn.execute('INSERT OR IGNORE INTO spending_notices(user_id,notice_key,body) VALUES(?,?,?)', (user_id, f'budget:{month}:{cat}:{level}', text))


def add(user_id, amount, cat, note, on=None, payment_source_id=None):
    cents, cat = money(amount), category(cat,user_id)
    day = date.fromisoformat(on) if on else today()
    if day > today():
        raise ValueError('日常支出不能填未來日期；固定負擔請用 !固定新增')
    if not note.strip() or len(note) > 200:
        raise ValueError('用途需為 1～200 字')
    with transaction() as conn:
        register(conn, user_id)
        source_id, source_name = resolve_payment(conn, user_id, payment_source_id)
        key = conn.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,payment_source_id,payment_source_name,kind) VALUES(?,?,?,?,?,?,?,'consumption')", (user_id, day.isoformat(), cents, cat, note, source_id, source_name)).lastrowid
        conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (user_id, key, 'null'))
        alerts(conn, user_id, day.strftime('%Y-%m'))
    return key


def edit(user_id, key, amount, cat, note, on, payment_source_id=None, expected_revision=None):
    # Omitted source preserves the original snapshot, including inactive sources.
    cents, day = money(amount), date.fromisoformat(on)
    if day > today() or not note.strip() or len(note) > 200:
        raise ValueError('請確認日期與用途，日期不可在未來')
    with transaction() as conn:
        conn.row_factory = __import__('sqlite3').Row
        old = conn.execute("SELECT * FROM expenses WHERE id=? AND user_id=? AND voided=0 AND kind='consumption'", (key, user_id)).fetchone()
        if not old:
            raise ValueError('找不到自己的有效支出')
        if expected_revision is not None and old['revision'] != expected_revision:
            raise ValueError('此筆帳目已變動，請重新選取後修改')
        if cat != old['category']:
            category(cat, user_id)
        source_id, source_name = (old['payment_source_id'], old['payment_source_name']) if payment_source_id is None else resolve_payment(conn, user_id, payment_source_id)
        if old['source'] != 'manual' and on != old['spent_on']:
            raise ValueError('自動記帳的月份與日期不可移動，可調整金額、分类與用途')
        conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (user_id, key, json.dumps(dict(old))))
        conn.execute('UPDATE expenses SET spent_on=?,cents=?,category=?,note=?,payment_source_id=?,payment_source_name=?,revision=revision+1 WHERE id=? AND user_id=?', (day.isoformat(), cents, cat, note, source_id, source_name, key, user_id))
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
            conn.execute('UPDATE expenses SET voided=1,revision=revision+1 WHERE id=? AND user_id=?', (row[1], user_id))
        else:
            conn.execute('UPDATE expenses SET spent_on=?,cents=?,category=?,note=?,voided=?,payment_source_id=?,payment_source_name=?,revision=revision+1 WHERE id=? AND user_id=?', (old['spent_on'], old['cents'], old['category'], old['note'], old['voided'], old.get('payment_source_id'), old.get('payment_source_name','未指定'), row[1], user_id))
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
    entries = rows("SELECT * FROM expenses WHERE user_id=? AND spent_on>=? AND spent_on<=? AND voided=0 AND kind='consumption' ORDER BY spent_on,id", (user_id,start.isoformat(),end.isoformat()))
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


def onboarding_needed(user_id):
    if rows('SELECT 1 FROM spending_onboarding WHERE user_id=?',(user_id,)):return False
    for table in ('expenses','budgets','spending_shortcuts','recurring_expenses','spending_categories','spending_settings'):
        if rows(f'SELECT 1 FROM {table} WHERE user_id=? LIMIT 1',(user_id,)):return False
    return not rows("SELECT 1 FROM payment_sources WHERE user_id=? AND name NOT IN ('現金','未指定') LIMIT 1",(user_id,))


def dismiss_onboarding(user_id):
    with transaction() as conn:
        conn.execute('INSERT OR IGNORE INTO spending_onboarding(user_id) VALUES(?)',(user_id,))


def monthly_closing(user_id,month):
    current=month_report(user_id,month)
    start=month_date(month)
    prior_end=start-timedelta(days=1)
    comparison=current
    if start==today().replace(day=1):
        days=min(today().day,prior_end.day)
        comparison=report(user_id,start,start.replace(day=days))
        prior_end=prior_end.replace(day=days)
    previous=report(user_id,prior_end.replace(day=1),prior_end)
    payments=rows("SELECT payment_source_name,SUM(cents) AS cents,COUNT(*) AS count FROM expenses WHERE user_id=? AND kind='consumption' AND voided=0 AND spent_on>=? AND spent_on<=? GROUP BY payment_source_name ORDER BY cents DESC,payment_source_name",(user_id,current['start'],current['end']))
    unspecified=next((dict(count=p['count'],cents=p['cents']) for p in payments if p['payment_source_name']=='未指定'),dict(count=0,cents=0))
    difference=round(comparison['total']-previous['total'],2) if comparison['has_records'] and previous['has_records'] else None
    return dict(current=current,comparison=comparison,previous=previous,payments=payments,unspecified=unspecified,difference=difference)


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


def chart_data(user_id, month=None):
    start = month_date(month) if month else today().replace(day=1)
    if start > today():
        raise ValueError('尚未到此月份')
    end = min(next_month(start)-timedelta(days=1), today())
    months = [start]
    for _ in range(5):
        months.insert(0, (months[0]-timedelta(days=1)).replace(day=1))
    entries = rows("SELECT spent_on,cents,category,payment_source_name FROM expenses WHERE user_id=? AND spent_on>=? AND spent_on<=? AND voided=0 AND kind='consumption'", (user_id,months[0].isoformat(),end.isoformat()))
    categories, payments = {}, {}
    totals = {m.strftime('%Y-%m'): 0 for m in months}
    for row in entries:
        period = row['spent_on'][:7]
        totals[period] += row['cents']
        if period == start.strftime('%Y-%m'):
            for group, label in ((categories, row['category']), (payments, row['payment_source_name'])):
                group[label] = group.get(label, 0) + row['cents']
    return dict(start=start.isoformat(), end=end.isoformat(), trend_start=months[0].isoformat(),
                categories=sorted(categories.items(), key=lambda p: (-p[1], p[0])),
                payments=sorted(payments.items(), key=lambda p: (-p[1], p[0])), months=list(totals.items()))


def delivered(key, user_id):
    with transaction() as conn:
        conn.execute('UPDATE spending_notices SET delivered=1 WHERE id=? AND user_id=?', (key,user_id))


def clear(user_id):
    with transaction() as conn:
        for table in ('expenses','expense_actions','budgets','recurring_expenses','spending_notices','spending_users','spending_categories','spending_settings','payment_sources','spending_shortcuts','spending_onboarding'):
            conn.execute(f'DELETE FROM {table} WHERE user_id=?', (user_id,))


def shortcuts(user_id, include_inactive=False):
    return rows('SELECT s.*,p.name AS payment_source_name,p.active AS payment_active FROM spending_shortcuts s LEFT JOIN payment_sources p ON p.id=s.payment_source_id AND p.user_id=s.user_id WHERE s.user_id=?' +
                ('' if include_inactive else ' AND s.active=1') + ' ORDER BY s.position,s.id', (user_id,))


def shortcut(user_id, key):
    found = next((r for r in shortcuts(user_id) if r['id']==key), None)
    if not found:
        raise ValueError('找不到自己的啟用捷徑')
    return found


def save_shortcut(user_id, name, cat, payment_source_id, note, amount=None, key=None):
    name=payment_name(name)
    category(cat,user_id)
    if not note.strip() or len(note)>200:
        raise ValueError('用途需為1～200字')
    cents=money(amount) if amount is not None and str(amount).strip() else None
    with transaction() as conn:
        register(conn,user_id)
        source_id,_=resolve_payment(conn,user_id,payment_source_id)
        if key is None:
            position=conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM spending_shortcuts WHERE user_id=?',(user_id,)).fetchone()[0]
            return conn.execute('INSERT INTO spending_shortcuts(user_id,name,category,payment_source_id,note,cents,position) VALUES(?,?,?,?,?,?,?)',(user_id,name,cat,source_id,note,cents,position)).lastrowid
        changed=conn.execute('UPDATE spending_shortcuts SET name=?,category=?,payment_source_id=?,note=?,cents=? WHERE user_id=? AND id=? AND active=1',(name,cat,source_id,note,cents,user_id,key)).rowcount
        if not changed:
            raise ValueError('找不到自己的啟用捷徑')
        return key


def disable_shortcut(user_id, key):
    with transaction() as conn:
        if not conn.execute('UPDATE spending_shortcuts SET active=0 WHERE user_id=? AND id=? AND active=1',(user_id,key)).rowcount:
            raise ValueError('找不到自己的啟用捷徑')


def move_shortcut(user_id, key, direction):
    if direction not in (-1,1):
        raise ValueError('排序方向需為上一項或下一項')
    with transaction() as conn:
        items=conn.execute('SELECT id,position FROM spending_shortcuts WHERE user_id=? AND active=1 ORDER BY position,id',(user_id,)).fetchall()
        index=next((i for i,r in enumerate(items) if r[0]==key),None)
        if index is None:
            raise ValueError('找不到自己的啟用捷徑')
        target=index+direction
        if 0<=target<len(items):
            conn.executemany('UPDATE spending_shortcuts SET position=? WHERE user_id=? AND id=?',[(items[target][1],user_id,key),(items[index][1],user_id,items[target][0])])


def recent_expenses(user_id):
    return rows("SELECT * FROM expenses WHERE user_id=? AND kind='consumption' AND voided=0 AND source='manual' ORDER BY spent_on DESC,id DESC LIMIT 6",(user_id,))


def parse_search_date(value,label):
    if not value:return ''
    for fmt in ('%Y-%m-%d','%Y/%m/%d','%Y%m%d'):
        try:
            parsed=datetime.strptime(value,fmt).date()
        except ValueError:
            continue
        if parsed.strftime(fmt)!=value:continue
        if parsed>today():raise ValueError(label+'不可是未來日期。')
        return parsed.isoformat()
    raise ValueError(label+'格式錯誤，請使用 YYYY-MM-DD、YYYY/MM/DD 或 YYYYMMDD，日期須存在且月份、日期補零。')


def search_expenses(user_id, keyword='', start=None, end=None):
    keyword=keyword.strip()
    start=(start or '').strip()
    end=(end or '').strip()
    if not any((keyword,start,end)):
        raise ValueError('請至少填寫關鍵字、開始日期或結束日期其中一項。')
    start=parse_search_date(start,'開始日期')
    end=parse_search_date(end,'結束日期') or today().isoformat()
    if start and start>end:
        raise ValueError('開始日期不可晚於結束日期。')
    sql="SELECT * FROM expenses WHERE user_id=? AND kind='consumption' AND voided=0 AND source='manual' AND spent_on<=?"
    args=[user_id,end]
    if start:
        sql+=' AND spent_on>=?';args.append(start)
    if keyword:
        sql+=' AND instr(lower(note),lower(?))>0';args.append(keyword)
    return rows(sql+' ORDER BY spent_on DESC,id DESC',args)


def shortcut_recommendation(user_id, as_of=None):
    end=as_of or today()
    start=end-timedelta(days=29)
    fixed=shortcuts(user_id)[:3]
    if len(fixed)!=3:
        return None
    active=set(category_names(user_id))
    groups=rows("""SELECT e.category,e.payment_source_id,e.note,p.name AS payment_source_name,COUNT(*) AS count
        FROM expenses e JOIN payment_sources p ON p.id=e.payment_source_id AND p.user_id=e.user_id AND p.active=1
        WHERE e.user_id=? AND e.voided=0 AND e.kind='consumption' AND e.source='manual'
        AND e.spent_on>=? AND e.spent_on<=?
        GROUP BY e.category,e.payment_source_id,e.note
        ORDER BY count DESC,e.category,e.payment_source_id,e.note""",(user_id,start.isoformat(),end.isoformat()))
    def key(row): return row['category'],row['payment_source_id'],row['note']
    counts={key(r):r['count'] for r in groups if r['category'] in active}
    lowest=min(range(3),key=lambda n:(counts.get(key(fixed[n]),0),-n))
    target=fixed[lowest]
    lowest_count=counts.get(key(target),0)
    pinned={key(r) for r in fixed}
    candidate=next((r for r in groups if r['category'] in active and key(r) not in pinned),None)
    if candidate is None or candidate['count']<max(3,lowest_count*2):
        return None
    return dict(candidate=candidate,target=target,lowest_count=lowest_count,
                period_start=start.isoformat(),period_end=end.isoformat())


def calendar_days(user_id, month):
    start=month_date(month)
    if start>today():
        raise ValueError('尚未到此月份')
    end=next_month(start)-timedelta(days=1)
    totals={r['spent_on']:r['cents'] for r in rows("""SELECT spent_on,SUM(cents) AS cents FROM expenses
        WHERE user_id=? AND voided=0 AND kind='consumption' AND spent_on>=? AND spent_on<=?
        GROUP BY spent_on""",(user_id,start.isoformat(),min(end,today()).isoformat()))}
    maximum=max(totals.values(),default=0)
    result=[]
    for day in range(1,end.day+1):
        on=start.replace(day=day).isoformat()
        cents=totals.get(on,0)
        level='—' if not cents else '░' if cents*3<=maximum else '▒' if cents*3<=maximum*2 else '▓'
        result.append(dict(date=on,cents=cents,level=level))
    return result


def review(user_id, unit):
    now=today()
    if unit=='week':
        start=now-timedelta(days=now.weekday())
        current=report(user_id,start,now)
        comparison=current
        previous=report(user_id,start-timedelta(days=7),now-timedelta(days=7))
    elif unit=='month':
        start=now.replace(day=1)
        prior_end=start-timedelta(days=1)
        days=min(now.day,prior_end.day)
        current=month_report(user_id)
        comparison=report(user_id,start,start.replace(day=days))
        previous=report(user_id,prior_end.replace(day=1),prior_end.replace(day=days))
    else:
        raise ValueError('回顧請選本週或本月')
    difference=round(comparison['total']-previous['total'],2) if comparison['has_records'] and previous['has_records'] else None
    payments=rows("SELECT payment_source_name,SUM(cents) AS cents FROM expenses WHERE user_id=? AND kind='consumption' AND voided=0 AND spent_on>=? AND spent_on<=? GROUP BY payment_source_name ORDER BY cents DESC,payment_source_name",(user_id,current['start'],current['end']))
    return dict(unit=unit,current=current,comparison=comparison,previous=previous,difference=difference,payments=payments)
