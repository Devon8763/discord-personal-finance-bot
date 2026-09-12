"""Versioned account-owned backup data; no identity or AI data is serialized."""
import csv
import hashlib
import io
import json
import math
import zipfile
from datetime import datetime
import spending as sp

FORMAT='discordbot-life-ledger'
VERSION=2
SETTINGS={
    'categories':('spending_categories',('name','active')),
    'payment_sources':('payment_sources',('name','active')),
    'shortcuts':('spending_shortcuts',('name','category','payment_source_name','note','cents','position','active')),
    'budgets':('budgets',('month','category','cents')),
}
INVESTMENTS={
    'assets':('symbol','buy_price','shares'),
    'fund_transactions':('fund_name','amount','price','units'),
    'watchlist':('symbol',),
    'fund_prices':('fund_name','price','updated_at'),
    'trade_history':('kind','name','price','quantity','profit','before_state','fund_ref','created_at','undone'),
}
REQUIRED={'manifest.json','README.txt','consumptions.json','settings.json','expenses.csv','categories.csv','payment_sources.csv','shortcuts.csv','budgets.csv'}


def fetch(conn,table,fields,user):
    order=' ORDER BY id' if table in ('assets','fund_transactions','watchlist','trade_history') else ''
    return [dict(zip(fields,row)) for row in conn.execute('SELECT '+','.join(fields)+f' FROM {table} WHERE user_id=?'+order,(str(user),))]


def snapshot(conn,user):
    from life_transfer import FIELDS
    result={'expenses':[r for r in fetch(conn,'expenses',FIELDS,user) if r['kind']=='consumption']}
    for name,(table,fields) in SETTINGS.items():
        if name=='shortcuts':
            result[name]=[dict(zip(fields,row)) for row in conn.execute('SELECT s.name,s.category,p.name,s.note,s.cents,s.position,s.active FROM spending_shortcuts s JOIN payment_sources p ON p.user_id=s.user_id AND p.id=s.payment_source_id WHERE s.user_id=? ORDER BY s.position,s.id',(str(user),))]
        else:result[name]=fetch(conn,table,fields,user)
    result['budgets']=[r for r in result['budgets'] if r['month']>=sp.today().strftime('%Y-%m')]
    inv={name:fetch(conn,name,fields,user) for name,fields in INVESTMENTS.items() if name!='trade_history'}
    fund_ids=[r[0] for r in conn.execute('SELECT id FROM fund_transactions WHERE user_id=? ORDER BY id',(str(user),))]
    history=fetch(conn,'trade_history',('kind','name','price','quantity','profit','before_state','fund_row_id','created_at','undone'),user)
    for r in history:
        key=r.pop('fund_row_id')
        r['fund_ref']=fund_ids.index(key) if key in fund_ids else None
        r['before_state']=json.loads(r['before_state'])
    inv['trade_history']=history
    if any(inv.values()):result['investments']=inv
    return result


def export_bundle(user):
    conn=sp.get_conn()
    try:
        conn.execute('BEGIN')
        bundle=snapshot(conn,user)
    finally:conn.rollback();conn.close()
    files={
        'consumptions.json':json.dumps(bundle['expenses'],ensure_ascii=False).encode(),
        'settings.json':json.dumps({k:bundle[k] for k in SETTINGS},ensure_ascii=False).encode(),
        'README.txt':('DiscordBOT 備份格式2\n匯出日期：'+sp.today().isoformat()+'''\n金額cents為新臺幣分；CSV金額為元。投資金額沿用原記錄單位，不換匯、不跨幣別合計。
CSV UTF-8 BOM；expenses含日期、金額、分類、用途、付款來源、來源類型、撤銷與種類。
settings包含分類(name/active)、付款方式(name/active)、捷徑(name/category/payment_source_name/note/cents/position/active)、本月及未來預算(month/category/cents)的最新設定。
有投資資料時另有investments.json：股票持倉、基金交易、觀察清單、基金淨值與交易歷史。fund_ref是本備份內基金交易索引，不是舊資料庫或Discord ID；before_state是撤銷需要的價格數量。
沒有舊Discord ID、AI對話、AI紀錄、AI偏好、Token或管理者設定。不要轉傳給不信任的人。
舊帳號自行匯出，新帳號私訊Bot上傳預覽後確認。同名設定或衝突保留目標帳號；目標已有預算即略過全部備份預算，過期月份不套用。同一投資標的已有持倉或歷史時整組略過，不混合持倉。
CSV公式開頭加單引號，原始匯入內容以JSON為準。manifest僅驗證格式、版本與完整性，不證明來源真偽。
無法登入舊帳號且無相容備份時無法找回。Bot不提供管理者代查看、代轉移或還原權限；主機管理者仍可能直接讀取資料庫。
匯入後AI關閉，使用前需重新同意。無每日備份或背景同步。
''').encode('utf-8-sig'),
    }
    from life_transfer import FIELDS
    headers={'spent_on':'日期','cents':'金額','category':'分類','note':'用途','payment_source_name':'付款來源','source':'來源類型','voided':'是否撤銷','kind':'記錄種類'}
    for name,rows in [('expenses',bundle['expenses'])]+[(k,bundle[k]) for k in SETTINGS]:
        columns=FIELDS if name=='expenses' else SETTINGS[name][1]
        with io.StringIO(newline='') as text:
            writer=csv.writer(text);writer.writerow([headers.get(c,c) for c in columns])
            for row in rows:
                values=[]
                for column in columns:
                    value=row[column]
                    if column=='cents' and value is not None:value=format(sp.Decimal(value)/100,'.2f')
                    elif isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):value="'"+value
                    values.append(value)
                writer.writerow(values)
            files[name+'.csv']=text.getvalue().encode('utf-8-sig')
    if 'investments' in bundle:files['investments.json']=json.dumps(bundle['investments'],ensure_ascii=False).encode()
    files['manifest.json']=json.dumps(dict(format=FORMAT,version=VERSION,sha256={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})).encode()
    with io.BytesIO() as output:
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in files.items():archive.writestr(name,data)
        return output.getvalue()


def read_bundle(payload):
    from life_transfer import strict_json,MAX_UPLOAD
    if len(payload)>MAX_UPLOAD:raise ValueError('備份超過8 MiB限制。')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos=archive.infolist();names=[r.filename for r in infos]
            if len(names)!=len(set(names)) or set(names) not in (REQUIRED,REQUIRED|{'investments.json'}):
                raise ValueError('必要檔案不符；請由舊帳號重新匯出格式2備份。')
            if sum(r.file_size for r in infos)>32*1024*1024 or any(r.flag_bits&1 for r in infos):raise ValueError('不支援加密或解壓超過32 MiB的備份。')
            manifest=strict_json(archive.read('manifest.json'))
            if not isinstance(manifest,dict) or set(manifest)!={'format','version','sha256'} or manifest['format']!=FORMAT:raise ValueError('不是支援的Bot備份格式。')
            if type(manifest['version']) is not int or manifest['version']!=VERSION:raise ValueError('不支援此版本；請由舊帳號重新匯出。')
            hashes=manifest['sha256']
            if not isinstance(hashes,dict) or set(hashes)!=set(names)-{'manifest.json'}:raise ValueError('完整性欄位不符。')
            files={name:archive.read(name) for name in hashes}
            if any(hashlib.sha256(data).hexdigest()!=hashes[name] for name,data in files.items()):raise ValueError('完整性檢查失敗，備份可能損毀或已修改。')
            from life_transfer import FIELDS
            labels={'spent_on':'日期','cents':'金額','category':'分類','note':'用途','payment_source_name':'付款來源','source':'來源類型','voided':'是否撤銷','kind':'記錄種類'}
            for name,columns in [('expenses',FIELDS)]+[(k,v[1]) for k,v in SETTINGS.items()]:
                rows=csv.reader(io.StringIO(files[name+'.csv'].decode('utf-8-sig')))
                if next(rows,None)!=[labels.get(c,c) for c in columns] or any(len(row)!=len(columns) for row in rows):raise ValueError('CSV欄位或列結構不符，未寫入資料。')
            settings=strict_json(files['settings.json'])
            if not isinstance(settings,dict) or set(settings)!=set(SETTINGS):raise ValueError('設定資料欄位不符。')
            bundle=dict(settings,expenses=strict_json(files['consumptions.json']))
            if 'investments.json' in files:bundle['investments']=strict_json(files['investments.json'])
            return validate_bundle(bundle)
    except (zipfile.BadZipFile,UnicodeError,json.JSONDecodeError,csv.Error,RuntimeError,NotImplementedError,EOFError):
        raise ValueError('備份已損毀或無法解讀，未寫入任何資料。') from None


def validate_bundle(bundle):
    from life_transfer import validate_records
    def invalid():raise ValueError('備份資料欄位或內容不符，未寫入任何資料。')
    def text(value,limit=200):return isinstance(value,str) and bool(value.strip()) and len(value)<=limit and '\x00' not in value
    def number(value):return type(value) in (int,float) and math.isfinite(value) and abs(value)<=1e15
    def flag(value):return type(value) is int and value in (0,1)
    if not isinstance(bundle,dict) or set(bundle) not in (set(SETTINGS)|{'expenses'},set(SETTINGS)|{'expenses','investments'}):invalid()
    validate_records(bundle['expenses'])
    for name,(_,fields) in SETTINGS.items():
        rows=bundle[name]
        if not isinstance(rows,list) or len(rows)>10000:invalid()
        for r in rows:
            if not isinstance(r,dict) or set(r)!=set(fields):invalid()
            for key in fields:
                value=r[key]
                if key=='active':
                    if not flag(value):invalid()
                elif key=='cents':
                    if value is None and name=='shortcuts':continue
                    if type(value) is not int or not 0<value<=100000000000:invalid()
                elif key=='position':
                    if type(value) is not int or not 0<=value<=1000000000:invalid()
                elif not text(value,200 if key=='note' else 30):invalid()
            if name=='categories' and (len(r['name'])>20 or r['name']=='總額'):invalid()
            if name=='payment_sources' and r['name'] in ('現金','未指定') and r['active']!=1:invalid()
            if name=='budgets':sp.month_date(r['month'])
    cats=set(sp.CATEGORIES)|{r['name'] for r in bundle['categories']}
    payments={r['name'] for r in bundle['payment_sources']}
    if any(r['category'] not in cats or r['payment_source_name'] not in payments for r in bundle['shortcuts']):invalid()
    if any(r['category'] not in cats|{'總額'} for r in bundle['budgets']):invalid()
    if 'investments' in bundle:
        inv=bundle['investments']
        if not isinstance(inv,dict) or set(inv)!=set(INVESTMENTS):invalid()
        for name,fields in INVESTMENTS.items():
            if not isinstance(inv[name],list) or len(inv[name])>10000:invalid()
            for r in inv[name]:
                if not isinstance(r,dict) or set(r)!=set(fields):invalid()
                for key in ('symbol','fund_name','name'):
                    if key in r and not text(r[key],100):invalid()
                for key in ('price','buy_price','shares','amount','units','quantity','profit'):
                    if key in r and not (key=='profit' and r[key] is None):
                        if not number(r[key]) or (key in ('price','buy_price','shares','quantity') and r[key]<0):invalid()
                for key in ('updated_at','created_at'):
                    if key in r:
                        if not text(r[key],50):invalid()
                        try:datetime.fromisoformat(r[key])
                        except ValueError:invalid()
        for r in inv['trade_history']:
            if r['kind'] not in ('buy','sell','remove','fundbuy','fundsell') or not flag(r['undone']):invalid()
            before=r['before_state']
            if not isinstance(before,list) or len(before)>10000 or any(not isinstance(p,list) or len(p)!=2 or any(not number(v) or v<0 for v in p) for p in before):invalid()
            ref=r['fund_ref']
            if ref is not None:
                if type(ref) is not int or not 0<=ref<len(inv['fund_transactions']) or not r['kind'].startswith('fund') or inv['fund_transactions'][ref]['fund_name']!=r['name']:invalid()
            elif r['kind'].startswith('fund') and not r['undone']:invalid()
    return bundle


def plan_merge(conn,user,bundle):
    from life_transfer import FIELDS,pending_records
    plan=[];counts={}
    def counted(name,rows,accepted):
        counts[name]=dict(total=len(rows),added=len(accepted),skipped=len(rows)-len(accepted))
        plan.extend((name,r) for r in accepted)
    for name in ('categories','payment_sources'):
        existing={r[0] for r in conn.execute(f'SELECT name FROM {SETTINGS[name][0]} WHERE user_id=?',(str(user),))}
        if name=='categories':existing.update(sp.CATEGORIES)
        accepted=[]
        for r in bundle[name]:
            if r['name'] not in existing:accepted.append(r);existing.add(r['name'])
        counted(name,bundle[name],accepted)
    existing_names={r[0] for r in conn.execute('SELECT name FROM spending_shortcuts WHERE user_id=?',(str(user),))}
    accepted=[]
    for r in sorted(bundle['shortcuts'],key=lambda r:r['position']):
        if r['name'] not in existing_names:accepted.append(r);existing_names.add(r['name'])
    counted('shortcuts',bundle['shortcuts'],accepted)
    budgets=[];seen=set()
    if not conn.execute('SELECT 1 FROM budgets WHERE user_id=? LIMIT 1',(str(user),)).fetchone():
        for r in bundle['budgets']:
            key=(r['month'],r['category'])
            if r['month']>=sp.today().strftime('%Y-%m') and key not in seen:budgets.append(r);seen.add(key)
    counted('budgets',bundle['budgets'],budgets)
    counted('expenses',bundle['expenses'],[dict(zip(FIELDS,values)) for values in pending_records(conn,user,bundle['expenses'])])
    if 'investments' in bundle:
        inv=bundle['investments']
        stocks={r[0] for r in conn.execute("SELECT symbol FROM assets WHERE user_id=? UNION SELECT name FROM trade_history WHERE user_id=? AND kind IN ('buy','sell','remove')",(str(user),str(user)))}
        funds={r[0] for r in conn.execute("SELECT fund_name FROM fund_transactions WHERE user_id=? UNION SELECT fund_name FROM fund_prices WHERE user_id=? UNION SELECT name FROM trade_history WHERE user_id=? AND kind IN ('fundbuy','fundsell')",(str(user),str(user),str(user)))}
        watched={r[0] for r in conn.execute('SELECT symbol FROM watchlist WHERE user_id=?',(str(user),))}
        for name,rows in inv.items():
            accepted=[]
            for index,r in enumerate(rows):
                if name=='watchlist':
                    if r['symbol'] in watched:continue
                    watched.add(r['symbol'])
                elif name=='assets':
                    if r['symbol'] in stocks:continue
                elif name in ('fund_transactions','fund_prices'):
                    if r['fund_name'] in funds:continue
                elif r['name'] in (funds if r['kind'].startswith('fund') else stocks):continue
                accepted.append(dict(r,_index=index) if name=='fund_transactions' else r)
            counted(name,rows,accepted)
    return counts,plan


def merge_bundle(user,bundle,apply=False):
    validate_bundle(bundle)
    if not apply:
        conn=sp.get_conn()
        try:
            conn.execute('BEGIN')
            return plan_merge(conn,user,bundle)[0]
        finally:conn.rollback();conn.close()
    counts={name:dict(total=len(rows),added=len(rows),skipped=0) for name,rows in bundle.items() if name!='investments'}
    if 'investments' in bundle:counts.update({name:dict(total=len(rows),added=len(rows),skipped=0) for name,rows in bundle['investments'].items()})
    try:
        with sp.transaction() as conn:
            counts,plan=plan_merge(conn,user,bundle)
            fund_ids={}
            position=conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM spending_shortcuts WHERE user_id=?',(str(user),)).fetchone()[0]
            # Fund transactions must precede history regardless of JSON object order.
            plan.sort(key=lambda item: item[0]=='trade_history')
            for name,original in plan:
                row=original.copy();table=SETTINGS[name][0] if name in SETTINGS else name
                if name=='shortcuts':
                    source=conn.execute('SELECT id FROM payment_sources WHERE user_id=? AND name=?',(str(user),row.pop('payment_source_name'))).fetchone()
                    if not source:raise ValueError('捷徑來源無法對應，匯入已取消。')
                    row['payment_source_id']=source[0];row['position']=position;position+=1
                elif name=='expenses':
                    source=conn.execute('SELECT id FROM payment_sources WHERE user_id=? AND name=?',(str(user),row['payment_source_name'])).fetchone()
                    row['payment_source_id']=source[0] if source else None
                elif name=='trade_history':
                    ref=row.pop('fund_ref');row['fund_row_id']=fund_ids.get(ref)
                    row['before_state']=json.dumps(row['before_state'])
                index=row.pop('_index',None)
                columns=list(row)
                key=conn.execute(f"INSERT INTO {table}(user_id,"+','.join(columns)+') VALUES('+','.join('?' for _ in range(len(columns)+1))+')',(str(user),*(row[c] for c in columns))).lastrowid
                if name=='fund_transactions':fund_ids[index]=key
                if name=='expenses' and not row['voided']:
                    conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)',(str(user),key,'null'))
            conn.execute('DELETE FROM ai_preferences WHERE user_id=?',(str(user),))
    except Exception:
        raise BundleImportFailed(counts) from None
    return counts


class BundleImportFailed(Exception):
    def __init__(self,counts):
        self.counts={name:dict(total=r['total'],added=0,skipped=r['skipped'],failed=r['added']) for name,r in counts.items()}
        super().__init__('匯入交易失敗，所有新增與設定變更已回滾。')
