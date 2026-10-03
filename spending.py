from presentation import number
"""Independent TWD spending ledger. Money is stored as integer cents."""
import calendar
from contextlib import closing
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as Decimal
from db import get_conn
from ledger import transaction
from life_ledger_rules import (
    money as money, month_date as month_date, next_month as next_month,
    category_totals as _category_totals, comparison_period as _comparison_period,
    compare_expenses, budget_cents, validate_budget_totals, recurring_due_date,
)

TZ = timezone(timedelta(hours=8))
CATEGORIES = ('餐飲', '交通', '購物', '居住', '娛樂', '醫療', '其他')


class ExpenseUnavailableError(ValueError):
    """The owner's active consumption entry is unavailable."""


class ExpenseRevisionConflictError(ValueError):
    """The entry changed after the caller's snapshot."""


class RecurringUnavailableError(ValueError):
    """The owner's fixed rule is unavailable for this operation."""


class RecurringRevisionConflictError(ValueError):
    """The rule changed after the caller's snapshot."""


def today():
    return datetime.now(TZ).date()


def category_names(user_id, include_inactive=False):
    overrides = {r['name']:r['active'] for r in rows('SELECT name,active FROM spending_categories WHERE user_id=?',(user_id,))}
    names = list(dict.fromkeys((*CATEGORIES,*overrides)))
    return names if include_inactive else [n for n in names if overrides.get(n,1)]


def _category_names(conn, user_id, include_inactive=False):
    overrides = dict(conn.execute(
        'SELECT name,active FROM spending_categories WHERE user_id=?',
        (user_id,),
    ).fetchall())
    names = list(dict.fromkeys((*CATEGORIES, *overrides)))
    return names if include_inactive else [n for n in names if overrides.get(n, 1)]


def category(value, user_id):
    names = category_names(user_id)
    if value not in names:
        raise ValueError('分類請選：' + '、'.join(names))
    return value


def _category(conn, value, user_id):
    names = _category_names(conn, user_id)
    if value not in names:
        raise ValueError('分類請選：' + '、'.join(names))
    return value


def _category_name(name):
    name = name.strip()
    if not name or len(name)>20 or name=='總額' or any(c in name for c in '\n\r'):
        raise ValueError('分類名稱需1～20字，不能使用「總額」')
    return name


def set_category(user_id,name,active):
    name = _category_name(name)
    if not active and name not in category_names(user_id):
        raise ValueError('找不到啟用中的分類')
    with transaction() as conn:
        register(conn,user_id)
        conn.execute('INSERT INTO spending_categories VALUES(?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET active=excluded.active',(user_id,name,int(active)))


def rename_category(user_id, old_name, new_name):
    old_name = old_name.strip()
    new_name = _category_name(new_name)
    if old_name == new_name:
        raise ValueError('新分類名稱必須與原名稱不同')

    with transaction() as conn:
        register(conn, user_id)
        if old_name not in _category_names(conn, user_id):
            raise ValueError('找不到自己的啟用分類')
        if new_name in _category_names(conn, user_id, True):
            raise ValueError('已有同名分類，不能合併分類')

        for table in ('expenses', 'budgets', 'recurring_expenses', 'recurring_expense_versions', 'spending_shortcuts'):
            if conn.execute(
                f'SELECT 1 FROM {table} WHERE user_id=? AND category=? LIMIT 1',
                (user_id, new_name),
            ).fetchone():
                raise ValueError('已有同名分類歷史，不能合併分類')

        action_updates = []
        for action_id, before_json in conn.execute(
            "SELECT id,before_json FROM expense_actions "
            "WHERE user_id=? AND before_json!='null'",
            (user_id,),
        ).fetchall():
            try:
                before = json.loads(before_json)
            except (TypeError, json.JSONDecodeError):
                raise ValueError('分類改名失敗，請檢查資料後重試') from None
            if not isinstance(before, dict):
                raise ValueError('分類改名失敗，請檢查資料後重試')
            if before.get('category') == new_name:
                raise ValueError('已有同名分類歷史，不能合併分類')
            if before.get('category') == old_name:
                before['category'] = new_name
                action_updates.append((json.dumps(before), user_id, action_id))

        if old_name in CATEGORIES:
            conn.execute(
                'INSERT INTO spending_categories VALUES(?,?,0) '
                'ON CONFLICT(user_id,name) DO UPDATE SET active=0',
                (user_id, old_name),
            )
            conn.execute(
                'INSERT INTO spending_categories VALUES(?,?,1)',
                (user_id, new_name),
            )
        else:
            changed = conn.execute(
                'UPDATE spending_categories SET name=? '
                'WHERE user_id=? AND name=? AND active=1',
                (new_name, user_id, old_name),
            )
            if changed.rowcount != 1:
                raise ValueError('找不到自己的啟用分類')

        conn.execute(
            'UPDATE recurring_expenses SET revision=revision+1 WHERE user_id=? AND '
            '(category=? OR id IN (SELECT recurring_id FROM recurring_expense_versions WHERE user_id=? AND category=?))',
            (user_id, old_name, user_id, old_name),
        )

        for table in ('expenses', 'budgets', 'recurring_expenses', 'recurring_expense_versions', 'spending_shortcuts'):
            revision_update = ',revision=revision+1' if table == 'expenses' else ''
            conn.execute(
                f'UPDATE {table} SET category=?{revision_update} WHERE user_id=? AND category=?',
                (new_name, user_id, old_name),
            )
        conn.executemany(
            'UPDATE expense_actions SET before_json=? WHERE user_id=? AND id=?',
            action_updates,
        )

        pending = conn.execute(
            "SELECT id,notice_key FROM spending_notices WHERE user_id=? "
            "AND delivered=0 AND notice_key LIKE 'budget:%'",
            (user_id,),
        ).fetchall()
        obsolete = [
            (notice_id, user_id)
            for notice_id, notice_key in pending
            if ':'.join(notice_key.split(':')[2:-1]) == old_name
        ]
        conn.executemany(
            'DELETE FROM spending_notices WHERE id=? AND user_id=?',
            obsolete,
        )


def reminder_levels(user_id):
    found = rows('SELECT levels FROM spending_settings WHERE user_id=?',(user_id,))
    return json.loads(found[0]['levels']) if found else [80,100]


def set_reminders(user_id,levels=None):
    if levels is not None:
        if not levels or len(levels)>10 or any(type(n) is not int or not 1<=n<=1000 for n in levels):
            raise ValueError('請設定1～10個整數百分比，範圍1～1000')
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


def rows(sql, args=()):
    conn = get_conn()
    conn.row_factory = __import__('sqlite3').Row
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def register(conn, user_id):
    conn.execute('INSERT OR IGNORE INTO spending_users VALUES(?,?)', (user_id, today().isoformat()))
    ensure_payment_sources(conn, user_id)


def ensure_payment_sources(conn, user_id):
    conn.executemany('INSERT OR IGNORE INTO payment_sources(user_id,name) VALUES(?,?)',
                     [(user_id, '未指定'), (user_id, '現金')])


def payment_sources(user_id, include_inactive=False, *, initialize_defaults=True):
    sql = ('SELECT * FROM payment_sources WHERE user_id=?' + ('' if include_inactive else ' AND active=1') +
           " ORDER BY CASE name WHEN '未指定' THEN 0 WHEN '現金' THEN 1 ELSE 2 END,id")
    if not initialize_defaults:
        return rows(sql, (user_id,))
    with transaction() as conn:
        ensure_payment_sources(conn, user_id)
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, (user_id,))]


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
        raise ExpenseUnavailableError('找不到自己的有效消費')
    return found[0]


def month_expenses(user_id, month):
    start = month_date(month)
    return rows("SELECT * FROM expenses WHERE user_id=? AND spent_on>=? AND spent_on<? AND voided=0 AND kind='consumption' ORDER BY spent_on DESC,id DESC",
                (user_id, start.isoformat(), next_month(start).isoformat()))


def list_expenses(user_id, month, include_voided=False, limit=None, offset=0):
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError('筆數需為非負整數')
    if type(offset) is not int or offset < 0:
        raise ValueError('起始位置需為非負整數')
    start = month_date(month)
    return _list_expenses_between(user_id, start, next_month(start), include_voided, limit, offset)


def _list_expenses_between(user_id, start, until, include_voided=False, limit=None, offset=0, *, keyword='', category='', conn=None):
    where = 'user_id=?'
    args = [str(user_id)]
    if start is not None:
        where += ' AND spent_on>=?'
        args.append(start.isoformat())
    where += " AND spent_on<? AND kind='consumption'"
    args.append(until.isoformat())
    if not include_voided:
        where += ' AND voided=0'
    if keyword:
        where += ' AND instr(lower(note),lower(?))>0'
        args.append(keyword)
    if category:
        where += ' AND category=?'
        args.append(category)
    read = rows if conn is None else lambda sql, values: [dict(row) for row in conn.execute(sql, values)]
    total = read(f'SELECT COUNT(*) AS n FROM expenses WHERE {where}', args)[0]['n']
    sql = f'SELECT * FROM expenses WHERE {where} ORDER BY spent_on DESC,id DESC'
    page_args = list(args)
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'
        page_args.extend((limit, offset))
    elif offset:
        sql += ' LIMIT -1 OFFSET ?'
        page_args.append(offset)
    return {'items': read(sql, page_args), 'total': total}


def list_expenses_in_range(user_id, start=None, end=None, *, keyword=''):
    start_text = parse_search_date(start, '開始日期') if start is not None else None
    end_text = parse_search_date(end, '結束日期') if end is not None else today().isoformat()
    if (start is not None and start_text != start) or (end is not None and end_text != end):
        raise ValueError('日期請使用 YYYY-MM-DD')
    first = date.fromisoformat(start_text) if start is not None else None
    last = date.fromisoformat(end_text)
    if first is not None and first > last:
        raise ValueError('開始日期不可晚於結束日期。')
    return _list_expenses_between(user_id, first, last+timedelta(days=1), keyword=keyword.strip())


def _comparison_categories(conn, user_id, as_of):
    known = _category_names(conn, user_id, include_inactive=True)
    active = _category_names(conn, user_id)
    historical = [row[0] for row in conn.execute(
        "SELECT DISTINCT category FROM expenses WHERE user_id=? AND kind='consumption' "
        "AND voided=0 AND spent_on<=? ORDER BY category", (user_id, as_of.isoformat()),
    )]
    return [{'name': name, 'state': 'active' if name in active else 'inactive' if name in known else 'historical'}
            for name in dict.fromkeys((*known, *historical))]


def expense_comparison_categories(user_id, *, as_of=None):
    as_of = as_of if as_of is not None else today()
    with closing(get_conn()) as conn:
        conn.execute('BEGIN')
        return _comparison_categories(conn, str(user_id), as_of)


def expense_comparison(user_id, a_start, a_end, b_start, b_end, *, category='', keyword='', as_of=None):
    as_of = as_of if as_of is not None else today()
    a, b = (_comparison_period(start, end, as_of) for start, end in ((a_start,a_end),(b_start,b_end)))
    if not isinstance(category, str) or not isinstance(keyword, str) or len(keyword)>200:
        raise ValueError('請確認分類與消費項目關鍵字')
    user_id = str(user_id)
    # One deferred read transaction keeps options, A/B and their category totals on the same snapshot.
    with closing(get_conn()) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        options = _comparison_categories(conn, user_id, as_of)
        if category and category not in {item['name'] for item in options}:
            raise ValueError('請選自己的分類或歷史分類')
        entries = []
        for period in (a, b):
            if not period['started']:
                entries.append([])
                continue
            items = _list_expenses_between(
                user_id, date.fromisoformat(period['actual_start']),
                date.fromisoformat(period['actual_end'])+timedelta(days=1),
                keyword=keyword.strip(), category=category, conn=conn,
            )['items']
            entries.append(items)
    return compare_expenses(a_start, a_end, b_start, b_end, *entries, as_of=as_of, category_options=options)


def recurring_expenses(user_id):
    with closing(get_conn()) as conn:
        conn.row_factory = sqlite3.Row
        month = today().strftime('%Y-%m')
        rules = conn.execute('SELECT * FROM recurring_expenses WHERE user_id=? ORDER BY active DESC,id DESC', (user_id,)).fetchall()
        result = []
        for rule in rules:
            if rule['kind'] == '固定':
                result.append(_fixed_view(conn, rule, month))
            else:
                current = _recurring_version(conn, rule, month)
                result.append(dict(rule, cents=current['cents'], category=current['category']))
        return result


def list_fixed_recurring(user_id):
    return [rule for rule in recurring_expenses(user_id) if rule['kind'] == '固定']


def _fixed_rule(conn, user_id, key, expected_revision=None, *, active=False):
    conn.row_factory = sqlite3.Row
    rule = conn.execute("SELECT * FROM recurring_expenses WHERE user_id=? AND id=? AND kind='固定'", (user_id, key)).fetchone()
    if rule is None or (active and not rule['active']):
        raise RecurringUnavailableError('找不到自己的固定支出')
    if active or expected_revision is not None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('版本格式錯誤')
        if rule['revision'] != expected_revision:
            raise RecurringRevisionConflictError('此固定支出已變動，請重新載入')
    return rule


def _recurring_version(conn, rule, month):
    version = conn.execute(
        'SELECT * FROM recurring_expense_versions WHERE recurring_id=? AND user_id=? '
        'AND effective_month<=? ORDER BY effective_month DESC LIMIT 1',
        (rule['id'], rule['user_id'], month),
    ).fetchone()
    if version is not None:
        return dict(version)
    return dict(rule)


def _fixed_view(conn, rule, month):
    current = _recurring_version(conn, rule, month)
    result = dict(rule)
    for name in ('name', 'cents', 'category', 'due_day'):
        result[name] = current[name]
    result['due_day'] = result['due_day'] or 1
    pending = conn.execute(
        'SELECT * FROM recurring_expense_versions WHERE recurring_id=? AND user_id=? '
        'AND effective_month>? ORDER BY effective_month LIMIT 1',
        (rule['id'], rule['user_id'], month),
    ).fetchone()
    result['pending'] = dict(pending) if pending is not None and any(
        pending[name] != result[name] for name in ('name', 'cents', 'category', 'due_day')
    ) else None
    return result


def get_fixed_recurring(user_id, key):
    with closing(get_conn()) as conn:
        return _fixed_view(conn, _fixed_rule(conn, user_id, key), today().strftime('%Y-%m'))


def recorded_months(user_id):
    return [row['month'] for row in rows(
        'SELECT DISTINCT substr(spent_on,1,7) AS month FROM expenses WHERE user_id=? '
        'UNION SELECT month FROM budgets WHERE user_id=? ORDER BY month',
        (user_id, user_id),
    )]


def alerts(conn, user_id, month):
    setting = conn.execute('SELECT levels FROM spending_settings WHERE user_id=?',(user_id,)).fetchone()
    levels = json.loads(setting[0]) if setting else [80,100]
    for cat, budget in conn.execute('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id, month)).fetchall():
        if type(budget) is not int or budget < 0:
            raise ValueError('預算金額資料無效')
        if budget == 0:
            continue
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
    cents = money(amount)
    day = date.fromisoformat(on) if on else today()
    if day > today():
        raise ValueError('日常支出不能填未來日期')
    if not note.strip() or len(note) > 200:
        raise ValueError('用途需為 1～200 字')
    with transaction() as conn:
        category(cat,user_id)
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
            raise ExpenseUnavailableError('找不到自己的有效支出')
        if expected_revision is not None and old['revision'] != expected_revision:
            raise ExpenseRevisionConflictError('此筆帳目已變動，請重新選取後修改')
        if cat != old['category']:
            category(cat, user_id)
        source_id, source_name = (old['payment_source_id'], old['payment_source_name']) if payment_source_id is None else resolve_payment(conn, user_id, payment_source_id)
        if old['source'] != 'manual' and on != old['spent_on']:
            raise ValueError('自動記帳的月份與日期不可移動，可調整金額、分类與用途')
        conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (user_id, key, json.dumps(dict(old))))
        conn.execute('UPDATE expenses SET spent_on=?,cents=?,category=?,note=?,payment_source_id=?,payment_source_name=?,revision=revision+1 WHERE id=? AND user_id=?', (day.isoformat(), cents, cat, note, source_id, source_name, key, user_id))
        alerts(conn, user_id, on[:7])


def void_expense(user_id, key, expected_revision=None):
    with transaction() as conn:
        conn.row_factory = sqlite3.Row
        old = conn.execute(
            "SELECT * FROM expenses "
            "WHERE user_id=? AND id=? AND voided=0 AND kind='consumption'",
            (user_id, key),
        ).fetchone()
        if not old:
            raise ExpenseUnavailableError('找不到自己的有效消費')
        if expected_revision is not None and old['revision'] != expected_revision:
            raise ExpenseRevisionConflictError('此筆帳目已變動，請重新選取後修改')
        conn.execute(
            'INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)',
            (user_id, key, json.dumps(dict(old))),
        )
        changed = conn.execute(
            "UPDATE expenses SET voided=1,revision=revision+1 "
            "WHERE user_id=? AND id=? AND voided=0 AND kind='consumption'",
            (user_id, key),
        )
        if changed.rowcount != 1:
            raise ExpenseUnavailableError('找不到自己的有效消費')


def fixed_burdens(user_id):
    rules = sorted(recurring_expenses(user_id), key=lambda rule: (not rule['active'], rule['id']))
    month = today().strftime('%Y-%m')
    entries = rows(
        "SELECT id,note,cents,source,voided FROM expenses "
        "WHERE user_id=? AND period=? AND kind='consumption' ORDER BY id",
        (user_id, month),
    )
    for rule in rules:
        rule.pop('user_id', None)
        if rule.get('pending'):
            rule['pending'].pop('user_id', None)
        rule['monthly_amount'] = rule.pop('cents') / 100
        if rule['periods']:
            start = month_date(rule['start_month'])
            elapsed = max(0, (today().year - start.year) * 12 + today().month - start.month + 1)
            rule['current_period'] = min(elapsed, rule['periods'])
            rule['remaining_periods'] = max(0, rule['periods'] - elapsed)
            rule['remaining_scheduled_amount'] = rule['remaining_periods'] * rule['monthly_amount']
    for entry in entries:
        entry['amount'] = entry.pop('cents') / 100
    return dict(month=month, rules=rules, entries=entries, total=sum(e['amount'] for e in entries if not e['voided']))


def undo(user_id, confirm=None):
    with transaction() as conn:
        row = conn.execute('SELECT id,expense_id,before_json FROM expense_actions WHERE user_id=? AND undone=0 ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
        if not row:
            raise ValueError('沒有可撤銷的生活記帳操作')
        if confirm is None:
            return row[0], row[1]
        if confirm != row[0]:
            raise ValueError('操作已變動，請重新確認')
        old = json.loads(row[2])
        if old is None:
            conn.execute('UPDATE expenses SET voided=1,revision=revision+1 WHERE id=? AND user_id=?', (row[1], user_id))
        else:
            conn.execute('UPDATE expenses SET spent_on=?,cents=?,category=?,note=?,voided=?,payment_source_id=?,payment_source_name=?,revision=revision+1 WHERE id=? AND user_id=?', (old['spent_on'], old['cents'], old['category'], old['note'], old['voided'], old.get('payment_source_id'), old.get('payment_source_name','未指定'), row[1], user_id))
        conn.execute('UPDATE expense_actions SET undone=1 WHERE id=?', (row[0],))
        return row[0], row[1]


def set_budget(user_id, month, cat, amount):
    month_date(month)
    cents = budget_cents(amount)
    with transaction() as conn:
        register(conn, user_id)
        if cat != '總額':
            _category(conn, cat, user_id)
        existing = dict(conn.execute('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id, month)).fetchall())
        existing[cat] = cents
        validate_budget_totals(existing)
        conn.execute('INSERT INTO budgets VALUES(?,?,?,?) ON CONFLICT(user_id,month,category) DO UPDATE SET cents=excluded.cents', (user_id, month, cat, cents))
        alerts(conn, user_id, month)


def _remove_pending_budget_notices(conn, user_id, month, cat):
    prefix = f'budget:{month}:{cat}:'
    conn.execute(
        'DELETE FROM spending_notices WHERE user_id=? AND delivered=0 '
        'AND substr(notice_key,1,?)=?',
        (user_id, len(prefix), prefix),
    )


def clear_budget(user_id, month, cat):
    month_date(month)
    with transaction() as conn:
        changed = conn.execute(
            'DELETE FROM budgets WHERE user_id=? AND month=? AND category=?',
            (user_id, month, cat),
        )
        if changed.rowcount != 1:
            raise ValueError('找不到自己的本月預算')
        _remove_pending_budget_notices(conn, user_id, month, cat)


def set_total_budget_to_category_sum(user_id, month):
    month_date(month)
    with transaction() as conn:
        register(conn, user_id)
        count, cents = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(cents),0) FROM budgets "
            "WHERE user_id=? AND month=? AND category!='總額'",
            (user_id, month),
        ).fetchone()
        if not count:
            raise ValueError('目前沒有分類預算可合計')
        if cents <= 0:
            raise ValueError('預算金額需為正整數台幣')
        conn.execute(
            'INSERT INTO budgets VALUES(?,?,?,?) '
            'ON CONFLICT(user_id,month,category) DO UPDATE SET cents=excluded.cents',
            (user_id, month, '總額', cents),
        )
        alerts(conn, user_id, month)


def _recurring_fields(name, amount, due_day):
    if not name.strip() or len(name) > 100:
        raise ValueError('項目名稱需 1～100 字')
    if due_day is not None and (type(due_day) is not int or not 1 <= due_day <= 31):
        raise ValueError('付款日需 1～31，短月以月底為準')
    return money(amount)


def recurring_start_months():
    current = today().replace(day=1)
    return [current.strftime('%Y-%m'), next_month(current).strftime('%Y-%m')]


def add_recurring(user_id, kind, name, amount, cat, start, periods=0, due_day=None):
    if kind not in ('分期', '訂閱', '固定'):
        raise ValueError('種類請選：分期／訂閱／固定')
    beginning = month_date(start)
    if beginning.strftime('%Y-%m') not in recurring_start_months():
        raise ValueError('開始月份請選本月或下月')
    if (kind == '分期' and not 1 <= periods <= 600) or (kind != '分期' and periods != 0):
        raise ValueError('分期期數需 1～600；固定／訂閱請填 0')
    cents = _recurring_fields(name, amount, due_day)
    with transaction() as conn:
        _category(conn, cat, user_id)
        register(conn, user_id)
        key = conn.execute('INSERT INTO recurring_expenses(user_id,name,cents,category,kind,start_month,periods,due_day) VALUES(?,?,?,?,?,?,?,?)', (user_id,name,cents,cat,kind,start,periods,due_day)).lastrowid
        if kind == '固定':
            conn.execute('INSERT INTO recurring_expense_versions VALUES(?,?,?,?,?,?,?)',
                         (user_id, key, start, name, cents, cat, due_day or 1))
        return key


def update_fixed_recurring(user_id, key, name, amount, cat, due_day, expected_revision):
    cents = _recurring_fields(name, amount, due_day)
    if due_day is None:
        raise ValueError('請選每月預定扣款日')
    with transaction() as conn:
        rule = _fixed_rule(conn, user_id, key, expected_revision, active=True)
        effective = next_month(today().replace(day=1)).strftime('%Y-%m')
        target = _recurring_version(conn, rule, effective)
        if cat != target['category']:
            _category(conn, cat, user_id)
        conn.execute(
            'INSERT INTO recurring_expense_versions VALUES(?,?,?,?,?,?,?) '
            'ON CONFLICT(recurring_id,effective_month) DO UPDATE SET '
            'name=excluded.name,cents=excluded.cents,category=excluded.category,due_day=excluded.due_day',
            (user_id, key, effective, name, cents, cat, due_day),
        )
        conn.execute('UPDATE recurring_expenses SET revision=revision+1 WHERE user_id=? AND id=?', (user_id, key))
    return effective


def _post_recurring(conn, rules, day):
    count = 0
    for rule in rules:
        month = month_date(rule['start_month'])
        index = 1
        while month <= day.replace(day=1) and (rule['periods'] == 0 or index <= rule['periods']):
            period = month.strftime('%Y-%m')
            settings = _recurring_version(conn, rule, period)
            scheduled = recurring_due_date(month, settings['due_day'])
            if scheduled <= day:
                note = settings['name'] + (f"（第 {index}/{rule['periods']} 期）" if rule['periods'] else '')
                cursor = conn.execute('INSERT OR IGNORE INTO expenses(user_id,spent_on,cents,category,note,source,recurring_id,period) VALUES(?,?,?,?,?,?,?,?)', (rule['user_id'],scheduled.isoformat(),settings['cents'],settings['category'],note,rule['kind'],rule['id'],period))
                if cursor.rowcount:
                    count += 1
                    conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)', (rule['user_id'],cursor.lastrowid,'null'))
                    conn.execute('INSERT OR IGNORE INTO spending_notices(user_id,notice_key,body) VALUES(?,?,?)', (rule['user_id'],f"auto:{rule['id']}:{period}",f"📅 自動記帳／補記 {period}：{note} {number(settings['cents']/100)} 元（非銀行扣款）"))
                    alerts(conn, rule['user_id'], period)
            month = next_month(month)
            index += 1
    return count


def sync_recurring(user_id=None, as_of=None):
    with transaction() as conn:
        conn.row_factory = sqlite3.Row
        query = 'SELECT * FROM recurring_expenses WHERE active=1'
        rules = conn.execute(query + (' AND user_id=?' if user_id is not None else ''), (user_id,) if user_id is not None else ()).fetchall()
        return _post_recurring(conn, rules, as_of or today())


def sync_fixed_recurring(user_id):
    with transaction() as conn:
        conn.row_factory = sqlite3.Row
        rules = conn.execute("SELECT * FROM recurring_expenses WHERE active=1 AND user_id=? AND kind='固定'", (user_id,)).fetchall()
        return _post_recurring(conn, rules, today())


def _stop_recurring(conn, rule, day):
    _post_recurring(conn, [rule], day)
    conn.execute('UPDATE recurring_expenses SET active=0,revision=revision+1 WHERE id=? AND user_id=?', (rule['id'],rule['user_id']))


def stop_recurring(user_id, key):
    with transaction() as conn:
        conn.row_factory = sqlite3.Row
        rule = conn.execute('SELECT * FROM recurring_expenses WHERE id=? AND user_id=? AND active=1', (key,user_id)).fetchone()
        if rule is None:
            raise ValueError('找不到自己的啟用項目')
        _stop_recurring(conn, rule, today())


def stop_fixed_recurring(user_id, key, expected_revision):
    with transaction() as conn:
        rule = _fixed_rule(conn, user_id, key, expected_revision, active=True)
        _stop_recurring(conn, rule, today())


def report(user_id, start, end):
    entries = rows("SELECT * FROM expenses WHERE user_id=? AND spent_on>=? AND spent_on<=? AND voided=0 AND kind='consumption' ORDER BY spent_on,id", (user_id,start.isoformat(),end.isoformat()))
    total = sum(r['cents'] for r in entries)
    cats = {}
    category_cents = _category_totals(entries)
    for cat in dict.fromkeys((*category_names(user_id,True),*category_cents)):
        amount = category_cents.get(cat,0)
        fixed = sum(r['cents'] for r in entries if r['category']==cat and r['source']!='manual')
        cats[cat] = dict(amount=amount/100,amount_cents=amount,fixed=fixed/100,daily=(amount-fixed)/100,
                         share=round(amount/total*100,2) if total else None)
    return dict(start=start.isoformat(),end=end.isoformat(),record_count=len(entries),total=total/100,total_cents=total,
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
        raise ValueError('尚未到此月份；請查看未來固定負擔')
    end = min(next_month(start)-timedelta(days=1), today())
    result = report(user_id,start,end)
    result['budgets'] = []
    for row in rows('SELECT category,cents FROM budgets WHERE user_id=? AND month=?', (user_id,start.strftime('%Y-%m'))):
        if type(row['cents']) is not int or row['cents'] < 0:
            raise ValueError('預算金額資料無效')
        spent = result['total'] if row['category']=='總額' else result['categories'][row['category']]['amount']
        spent_cents = result['total_cents'] if row['category']=='總額' else result['categories'][row['category']]['amount_cents']
        budget = row['cents']/100
        result['budgets'].append(dict(category=row['category'],budget=budget,spent=spent,remaining=(row['cents']-spent_cents)/100,used_percent=round(spent/budget*100,2) if row['cents'] else None,
                                      budget_cents=row['cents'],spent_cents=spent_cents,remaining_cents=row['cents']-spent_cents))
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
    from service_safety import record_deletion
    import db
    with transaction() as conn:
        record_deletion(conn,user_id,db.DB_NAME)
        for table in ('expenses','expense_actions','budgets','recurring_expense_versions','recurring_expenses','spending_notices','spending_users','spending_categories','spending_settings','payment_sources','spending_shortcuts','spending_onboarding'):
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
    if not note.strip() or len(note)>200:
        raise ValueError('用途需為1～200字')
    cents=money(amount) if amount is not None and str(amount).strip() else None
    with transaction() as conn:
        category(cat,user_id)
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
