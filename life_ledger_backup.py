"""Owner-scoped portable life-ledger JSON; restore into an empty ledger only."""
import json
import re
from datetime import date

from db import get_conn
from ledger import transaction

FORMAT = 'life-ledger-backup'
VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_DEPTH = 12
MAX_TEXT = 4096
MAX_INTEGER = 2**63-1
EXPENSE_FIELDS = ('id','spent_on','cents','category','note','source','recurring_id','period','voided',
                  'payment_source_id','payment_source_name','kind','revision')
# Only these fixed, code-owned table/column names are used in SQL.
SECTIONS = {
    'expenses': ('expenses', EXPENSE_FIELDS),
    'categories': ('spending_categories', ('name','active')),
    'payment_sources': ('payment_sources', ('id','name','active')),
    'budgets': ('budgets', ('month','category','cents')),
    'recurring_rules': ('recurring_expenses', ('id','name','cents','category','kind','start_month','periods','due_day','active','revision')),
    'recurring_versions': ('recurring_expense_versions', ('recurring_id','effective_month','name','cents','category','due_day')),
    'shortcuts': ('spending_shortcuts', ('id','name','category','payment_source_id','note','cents','position','active')),
    'actions': ('expense_actions', ('id','expense_id','before_json','undone')),
}
EMPTY_TABLES = tuple(table for table, _ in SECTIONS.values()) + (
    'spending_settings','spending_users','spending_onboarding','spending_notices',
)
PREFIXES = {'expenses':'e','payment_sources':'p','recurring_rules':'r','recurring_versions':'v','shortcuts':'s','actions':'a'}


class PortableBackupError(ValueError):
    """Malformed, unsupported or unsafe portable data; messages contain no row data."""


class NonemptyLedgerError(PortableBackupError):
    """A restore requires an entirely empty target life ledger."""


class PortableRestoreError(RuntimeError):
    """The write failed and the entire restore was rolled back."""


def _invalid(kind):
    raise PortableBackupError('生活帳本備份資料異常：' + kind)


def _shape(value, fields, kind):
    if type(value) is not dict or set(value) != set(fields):
        _invalid(kind + '欄位')


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid('整數')


def _text(value, *, empty=False):
    if type(value) is not str or len(value)>MAX_TEXT or (not empty and not value.strip()) or '\x00' in value:
        _invalid('文字')
    try:
        value.encode('utf-8')
    except UnicodeError:
        _invalid('文字編碼')


def _iso(value, month=False):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}' if month else r'\d{4}-\d{2}-\d{2}',value,flags=re.ASCII):
        _invalid('月份' if month else '日期')
    try:
        parsed = date.fromisoformat(value+'-01' if month else value)
    except ValueError:
        _invalid('月份' if month else '日期')
    if parsed.isoformat() != (value+'-01' if month else value):
        _invalid('日期')


def _cents(value, *, zero=False):
    if type(value) is not str or len(value)>19 or not re.fullmatch(r'0|[1-9][0-9]*',value):
        _invalid('cents')
    _integer(int(value), 0 if zero else 1)


def _unique(values, kind):
    try:
        distinct = set(values)
    except TypeError:
        _invalid(kind + '型別')
    if len(values)!=len(distinct):
        _invalid(kind + '重複')


def _load_json(payload):
    if type(payload) is not bytes or not payload or len(payload)>MAX_BYTES:
        _invalid('輸入大小／型別')
    try:
        text = payload.decode('utf-8')
    except UnicodeError:
        _invalid('UTF-8')
    # Bound nesting before the JSON decoder allocates recursive structures.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped: escaped = False
            elif char=='\\': escaped = True
            elif char=='"': quoted = False
        elif char=='"': quoted = True
        elif char in '[{':
            depth += 1
            if depth>MAX_DEPTH: _invalid('結構深度')
        elif char in ']}': depth -= 1
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result: _invalid('JSON重複欄位')
            result[key] = value
        return result
    def reject_number(value):
        _invalid('非整數／非標準數值')
    try:
        return json.loads(text,object_pairs_hook=unique,parse_float=reject_number,parse_constant=reject_number)
    except (ValueError,RecursionError) as error:
        if isinstance(error,PortableBackupError): raise
        _invalid('JSON')


def _expense(record, payments, rules):
    _shape(record, EXPENSE_FIELDS, '消費')
    _iso(record['spent_on'])
    _cents(record['cents'])
    for key in ('category','payment_source_name'): _text(record[key])
    _text(record['note'],empty=True)
    _integer(record['voided'],0,1)
    _integer(record['revision'])
    if record['kind']!='consumption' or record['source'] not in ('manual','固定','訂閱','分期'):
        _invalid('消費種類／來源')
    if record['payment_source_id'] is not None and (type(record['payment_source_id']) is not str or record['payment_source_id'] not in payments):
        _invalid('付款引用')
    ref,period = record['recurring_id'],record['period']
    if ref is None:
        if period is not None: _invalid('規則月份引用')
        # Legacy ZIP imports can retain an automatic source without a rule link.
        return
    if type(ref) is not str or ref not in rules or record['source']!=rules[ref]['kind']:
        _invalid('規則引用')
    _iso(period,month=True)
    rule = rules[ref]
    if record['spent_on'][:7]!=period or period<rule['start_month']:
        _invalid('已入帳月份')
    if rule['periods']:
        offset=(int(period[:4])-int(rule['start_month'][:4]))*12+int(period[5:])-int(rule['start_month'][5:])
        if offset>=rule['periods']: _invalid('分期月份')


def _validate(bundle):
    _shape(bundle, ('format','version','data'), '格式')
    if bundle['format']!=FORMAT or type(bundle['version']) is not int or bundle['version']!=VERSION:
        _invalid('格式／版本')
    data = bundle['data']
    _shape(data, (*SECTIONS,'settings'), '生活帳本')
    total = 0
    all_ids = []
    for section,(_,fields) in SECTIONS.items():
        records = data[section]
        if type(records) is not list: _invalid(section + '結構')
        total += len(records)
        if total>MAX_RECORDS: _invalid('資料筆數上限')
        portable_fields = (*fields,'id') if section=='recurring_versions' else fields
        if section=='actions': portable_fields = ('id','expense_id','before','undone')
        for row in records:
            _shape(row,portable_fields,section)
            if 'id' in row:
                if type(row['id']) is not str or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',row['id']): _invalid('識別')
                all_ids.append(row['id'])
    _unique(all_ids,'識別')
    for section in ('categories','payment_sources'):
        _unique([r['name'] for r in data[section]],section)
        for row in data[section]:
            _text(row['name']);_integer(row['active'],0,1)
        if section=='categories' and any(r['name']=='總額' for r in data[section]): _invalid('分類')
        if section=='payment_sources' and any(r['name'] in ('現金','未指定') and not r['active'] for r in data[section]): _invalid('保留付款方式')
    payments = {r['id'] for r in data['payment_sources']}
    rules = {r['id']:r for r in data['recurring_rules']}
    for row in rules.values():
        for key in ('name','category'): _text(row[key])
        _cents(row['cents']);_iso(row['start_month'],month=True)
        _integer(row['periods']);_integer(row['active'],0,1);_integer(row['revision'])
        if row['kind'] not in ('固定','訂閱','分期') or (row['kind']=='分期') != bool(row['periods']): _invalid('規則種類／期數')
        if row['due_day'] is not None: _integer(row['due_day'],1,31)
    _unique([(r['recurring_id'],r['effective_month']) for r in data['recurring_versions']],'生效版本')
    for row in data['recurring_versions']:
        if type(row['recurring_id']) is not str or row['recurring_id'] not in rules: _invalid('版本規則引用')
        rule = rules[row['recurring_id']]
        _iso(row['effective_month'],month=True)
        if row['effective_month']<rules[row['recurring_id']]['start_month']: _invalid('生效月份')
        for key in ('name','category'): _text(row[key])
        _cents(row['cents']);_integer(row['due_day'],1,31)
        if rule['kind']!='固定' and (row['name']!=rule['name'] or row['due_day']!=(rule['due_day'] or 1)): _invalid('版本不可更改欄位')
    _unique([(r['month'],r['category']) for r in data['budgets']],'預算')
    for row in data['budgets']:
        _iso(row['month'],month=True);_text(row['category']);_cents(row['cents'],zero=True)
    for row in data['expenses']: _expense(row,payments,rules)
    _unique([(r['recurring_id'],r['period']) for r in data['expenses'] if r['recurring_id'] is not None],'規則月份')
    for row in data['shortcuts']:
        for key in ('name','category'): _text(row[key])
        _text(row['note'],empty=True)
        if type(row['payment_source_id']) is not str or row['payment_source_id'] not in payments: _invalid('捷徑付款引用')
        if row['cents'] is not None: _cents(row['cents'])
        _integer(row['position']);_integer(row['active'],0,1)
    expenses = {r['id']:r for r in data['expenses']}
    for row in data['actions']:
        if type(row['expense_id']) is not str or row['expense_id'] not in expenses: _invalid('操作消費引用')
        _integer(row['undone'],0,1)
        before = row['before']
        if before is not None:
            _expense(before,payments,rules)
            if before['id']!=row['expense_id']: _invalid('操作快照引用')
            current = expenses[row['expense_id']]
            if any(before[field]!=current[field] for field in ('source','recurring_id','period','kind')):
                _invalid('操作快照不可變欄位')
    settings = data['settings']
    _shape(settings,('reminder_levels','recording_started_on'),'設定')
    levels = settings['reminder_levels']
    if levels is not None:
        if type(levels) is not list or len(levels)>10: _invalid('提醒門檻')
        for level in levels: _integer(level,1,1000)
        _unique(levels,'提醒門檻')
    if settings['recording_started_on'] is not None: _iso(settings['recording_started_on'])
    return bundle


def read_backup(payload):
    """Parse and validate bounded UTF-8 JSON without touching storage."""
    return _validate(_load_json(payload))


def _storage_json(value):
    if type(value) is not str: _invalid('操作／設定JSON型別')
    try:
        payload = value.encode('utf-8')
    except UnicodeError:
        _invalid('操作／設定JSON編碼')
    return _load_json(payload)


def _storage_cents(value):
    _integer(value,1)
    return str(value)


def _map_reference(value, mapping, kind):
    if value is None: return None
    if type(value) is not int or value not in mapping: _invalid(kind + '引用')
    return mapping[value]


def _portable_expense(row, maps):
    result = dict(row)
    result['id'] = _map_reference(result['id'],maps['expenses'],'消費')
    result['cents'] = _storage_cents(result['cents'])
    for field,section in (('recurring_id','recurring_rules'),('payment_source_id','payment_sources')):
        result[field] = _map_reference(result[field],maps[section],field)
    return result


def export_backup(user_id):
    """Export only the caller's ledger from one read snapshot; no file or writes."""
    user_id = str(user_id)
    conn = get_conn()
    try:
        conn.execute('BEGIN')
        raw = {}
        for section,(table,fields) in SECTIONS.items():
            clause = " AND kind='consumption'" if section=='expenses' else ''
            order = 'recurring_id,effective_month' if section=='recurring_versions' else 'id' if 'id' in fields else ','.join(fields[:2])
            records = conn.execute('SELECT '+','.join(fields)+' FROM '+table+' WHERE user_id=?'+clause+' ORDER BY '+order+' LIMIT ?', (user_id,MAX_RECORDS+1)).fetchall()
            if len(records)>MAX_RECORDS: _invalid('資料筆數上限')
            raw[section] = [dict(zip(fields,row)) for row in records]
        settings = conn.execute('SELECT levels FROM spending_settings WHERE user_id=?',(user_id,)).fetchone()
        started = conn.execute('SELECT started FROM spending_users WHERE user_id=?',(user_id,)).fetchone()
        # Actions for other stored entry kinds are outside this life-consumption format.
        other_entries = {r[0] for r in conn.execute("SELECT id FROM expenses WHERE user_id=? AND kind!='consumption'",(user_id,))}
    finally:
        conn.rollback();conn.close()
    raw['actions'] = [row for row in raw['actions'] if row['expense_id'] not in other_entries]
    maps = {section:{row['id']:f'{prefix}{i+1}' for i,row in enumerate(raw[section])}
            for section,prefix in PREFIXES.items() if section!='recurring_versions'}
    data = {}
    for section,records in raw.items():
        result = []
        for i,record in enumerate(records):
            row = dict(record)
            if section=='expenses': row = _portable_expense(row,maps)
            else:
                if 'id' in row: row['id'] = maps[section][row['id']]
                if section=='recurring_versions': row['id'] = f'v{i+1}'
                if 'cents' in row and row['cents'] is not None:
                    _integer(row['cents'],0 if section=='budgets' else 1)
                    row['cents'] = str(row['cents'])
                for field,target in (('recurring_id','recurring_rules'),('payment_source_id','payment_sources'),('expense_id','expenses')):
                    if field in row: row[field] = _map_reference(row[field],maps[target],field)
                if section=='actions':
                    before = _storage_json(row.pop('before_json'))
                    if before is not None:
                        legacy = set(EXPENSE_FIELDS)-{'payment_source_id','payment_source_name','kind','revision'}
                        if type(before) is not dict or not legacy|{'user_id'} <= set(before) or not set(before) <= set(EXPENSE_FIELDS)|{'user_id'}:
                            _invalid('操作快照欄位')
                        if before.pop('user_id')!=user_id or before['id']!=record['expense_id']: _invalid('操作快照擁有者／引用')
                        # Only documented defaults for columns added by old schema upgrades.
                        before = {**dict(payment_source_id=None,payment_source_name='未指定',kind='consumption',revision=0),**before}
                        before = _portable_expense(before,maps)
                    row['before'] = before
            result.append(row)
        data[section] = result
    data['settings'] = dict(reminder_levels=_storage_json(settings[0]) if settings else None,
                            recording_started_on=started[0] if started else None)
    bundle = _validate(dict(format=FORMAT,version=VERSION,data=data))
    payload = json.dumps(bundle,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(payload)>MAX_BYTES: _invalid('輸入大小上限')
    return payload


def restore_backup(user_id, payload):
    """Validate first, then check emptiness and remap all data in one transaction."""
    data = read_backup(payload)['data']
    user_id = str(user_id)
    maps = {section:{} for section in PREFIXES}
    try:
        with transaction() as conn:
            for table in EMPTY_TABLES:
                if conn.execute('SELECT 1 FROM '+table+' WHERE user_id=? LIMIT 1',(user_id,)).fetchone():
                    raise NonemptyLedgerError('目標生活帳本不是空白，未還原任何資料')
            for section in ('categories','payment_sources','budgets','recurring_rules','recurring_versions','expenses','shortcuts','actions'):
                table,fields = SECTIONS[section]
                fields = tuple(field for field in fields if field!='id')
                for record in data[section]:
                    row = dict(record)
                    for field,target in (('recurring_id','recurring_rules'),('payment_source_id','payment_sources'),('expense_id','expenses')):
                        if field in row and row[field] is not None: row[field] = maps[target][row[field]]
                    if 'cents' in row and row['cents'] is not None: row['cents'] = int(row['cents'])
                    if section=='actions':
                        before = row.pop('before')
                        if before is not None:
                            before = dict(before,user_id=user_id,id=maps['expenses'][before['id']],cents=int(before['cents']))
                            for field,target in (('recurring_id','recurring_rules'),('payment_source_id','payment_sources')):
                                if before[field] is not None: before[field] = maps[target][before[field]]
                        row['before_json'] = json.dumps(before,ensure_ascii=False,separators=(',',':'))
                    columns = ('user_id',*fields)
                    cursor = conn.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',
                                          (user_id,*(row[field] for field in fields)))
                    if 'id' in record:
                        maps[section][record['id']] = cursor.lastrowid if section!='recurring_versions' else (row['recurring_id'],row['effective_month'])
            settings = data['settings']
            if settings['reminder_levels'] is not None:
                conn.execute('INSERT INTO spending_settings(user_id,levels) VALUES(?,?)',(user_id,json.dumps(settings['reminder_levels'])))
            if settings['recording_started_on'] is not None:
                conn.execute('INSERT INTO spending_users(user_id,started) VALUES(?,?)',(user_id,settings['recording_started_on']))
    except NonemptyLedgerError:
        raise
    except Exception:
        raise PortableRestoreError('生活帳本還原失敗，所有還原寫入已回滾') from None
    return {section:len(records) for section,records in data.items() if section!='settings'}
