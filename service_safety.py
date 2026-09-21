"""Local whole-database maintenance. Never serialize ledger content to logs or DMs."""
import argparse
import asyncio
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
from pathlib import Path
import sqlite3
import uuid

RETENTION=timedelta(days=7)
LIFE_TABLES=('expenses','expense_actions','budgets','recurring_expenses','spending_notices',
             'spending_users','spending_categories','spending_settings','payment_sources',
             'spending_shortcuts','spending_onboarding')


def utc_now():
    return datetime.now(timezone.utc)


def private_directory(path):
    path=Path(path).absolute()
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (path,*path.parents)):
        raise ValueError('維護目錄不可使用符號連結或目錄接合點。')
    if path.drive.startswith('\\\\') or any(any(word in part.lower() for word in ('onedrive','dropbox','google drive','icloud')) or part.lower()=='public' for part in path.parts):
        raise ValueError('維護資料只能放在非公開、非同步的主機本機目錄。')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path.resolve()


def state_file(database):
    return private_directory(Path(database).absolute().parent/'.safety')/'state.sqlite3'


@contextmanager
def maintenance_lock(database):
    """Held for the Bot lifetime, or exclusively for a local restore."""
    path=state_file(database).with_name('maintenance.lock')
    with path.open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Bot或其他維護操作仍在執行，請先停止。') from None
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


class Safety:
    def __init__(self,database,clock=utc_now):
        self.database=Path(database).absolute()
        self.clock=clock
        self.backups=private_directory(self.database.parent/'backups')
        self.state=state_file(self.database)
        if self.state.is_symlink():raise ValueError('維護狀態檔不可使用符號連結。')
        with closing(self.connect()) as conn,conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS backups(name TEXT PRIMARY KEY,kind TEXT NOT NULL,created TEXT NOT NULL,digest TEXT NOT NULL,daily TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS deletions(user_id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS health(kind TEXT PRIMARY KEY,failed INTEGER NOT NULL,checked TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0);
            ''')

    def connect(self):
        conn=sqlite3.connect(self.state,timeout=10)
        conn.row_factory=sqlite3.Row
        return conn

    def backup_records(self):
        with closing(self.connect()) as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM backups ORDER BY created')]

    def deletions(self):
        with closing(self.connect()) as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM deletions')]

    def path(self,name):
        if Path(name).name!=name or not name.startswith('discordbot-') or not name.endswith('.sqlite3'):
            raise ValueError('只能選擇Bot登錄的本機備份。')
        path=self.backups/name
        if path.is_symlink() or path.resolve().parent!=self.backups:
            raise ValueError('備份位置不安全。')
        return path

    @staticmethod
    def verify(path,writable=False):
        mode='rw' if writable else 'ro'
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode='+mode,uri=True,timeout=10)) as conn:
            if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise ValueError('資料庫完整性檢查失敗。')
            names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'expenses','assets','budgets'}<=names:raise ValueError('不支援的資料庫結構。')
            if writable:
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('CREATE TABLE __service_write_probe(value INTEGER)')
                    conn.execute('INSERT INTO __service_write_probe VALUES(1)')
                finally:conn.rollback()

    def validate_backup(self,name):
        with closing(self.connect()) as conn:
            row=conn.execute('SELECT * FROM backups WHERE name=?',(name,)).fetchone()
        if not row:raise ValueError('不是Bot登錄的備份。')
        created=datetime.fromisoformat(row['created'])
        if not self.clock()-RETENTION<created<=self.clock():raise ValueError('備份已超過7天或時間無效，不可復原。')
        path=self.path(name)
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=row['digest']:
            raise ValueError('備份完整性校驗失敗。')
        self.verify(path)
        return dict(row)

    def record(self,kind,failed):
        # Only fixed technical categories/statuses are persisted and sent.
        if kind not in ('database','daily','pre-update','emergency','restore','startup'):
            raise ValueError('未知健康檢查項目。')
        now=self.clock().isoformat()
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT failed FROM health WHERE kind=?',(kind,)).fetchone()
            if (row is None and failed) or (row is not None and bool(row[0])!=failed):
                conn.execute('INSERT INTO events(kind,status,created) VALUES(?,?,?)',(kind,'異常' if failed else '恢復',now))
            conn.execute('INSERT INTO health VALUES(?,?,?) ON CONFLICT(kind) DO UPDATE SET failed=excluded.failed,checked=excluded.checked',(kind,int(failed),now))

    def backup(self,kind):
        if kind not in ('daily','pre-update','emergency'):raise ValueError('不支援的備份用途。')
        # Serializes competing backup/cleanup processes without locking ledger writes.
        with closing(self.connect()) as registry:
            registry.execute('BEGIN IMMEDIATE')
            day=self.clock().astimezone(timezone(timedelta(hours=8))).date().isoformat()
            if kind=='daily':
                existing=registry.execute('SELECT name FROM backups WHERE daily=?',(day,)).fetchone()
                if existing:
                    self.validate_backup(existing[0]);return self.path(existing[0])
            name=f'discordbot-{kind}-{uuid.uuid4().hex}.sqlite3'
            destination=self.path(name);partial=destination.with_suffix('.partial')
            try:
                with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as source:
                    source.execute('BEGIN')
                    source.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
                    # Snapshot is pinned before its time is recorded; concurrent deletes wait.
                    created=self.clock().isoformat()
                    with closing(sqlite3.connect(partial)) as target:source.backup(target)
                    source.rollback()
                self.verify(partial)
                with partial.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                os.replace(partial,destination)
                registry.execute('INSERT INTO backups VALUES(?,?,?,?,?)',(name,kind,created,digest,day if kind=='daily' else None))
                registry.commit()
                return destination
            except Exception:
                registry.rollback()
                partial.unlink(missing_ok=True);destination.unlink(missing_ok=True)
                raise

    def prune(self):
        cutoff=(self.clock()-RETENTION).isoformat()
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute('SELECT name FROM backups WHERE created<=?',(cutoff,)).fetchall():
                self.path(row[0]).unlink(missing_ok=True)
                conn.execute('DELETE FROM backups WHERE name=?',(row[0],))
            registered={r[0] for r in conn.execute('SELECT name FROM backups')}
            for path in self.backups.iterdir():
                if (re.fullmatch(r'discordbot-(daily|pre-update|emergency)-[0-9a-f]{32}\.(sqlite3|partial)',path.name)
                        and path.name not in registered):
                    if path.is_symlink() or path.resolve().parent!=self.backups:
                        raise ValueError('清理發現不安全的備份位置。')
                    if path.is_file() and path.stat().st_mtime<=(self.clock()-RETENTION).timestamp():path.unlink()
            # Never forget deletion markers before all eligible old backups are removed.
            conn.execute('DELETE FROM deletions WHERE deleted_at<=?',(cutoff,))
            conn.execute('DELETE FROM events WHERE sent=1 AND created<=?',(cutoff,))

    def check(self):
        try:self.verify(self.database,writable=True)
        except Exception:self.record('database',True)
        else:self.record('database',False)
        failed=False
        try:self.prune()
        except Exception:failed=True
        try:self.backup('daily')
        except Exception:failed=True
        self.record('daily',failed)

    def pre_update(self):
        try:
            self.prune()
            result=self.backup('pre-update')
        except Exception:
            self.record('pre-update',True)
            raise
        else:self.record('pre-update',False);return result

    async def deliver(self,send):
        with closing(self.connect()) as conn:
            events=[dict(r) for r in conn.execute('SELECT * FROM events WHERE sent=0 ORDER BY id')]
        for event in events:
            message=f"服務健康｜{event['status']}｜{event['kind']}\n時間：{event['created']}\n請在主機檢查維護狀態、可用空間與檔案權限；勿傳送帳目或備份附件。"
            try:await send(message)
            except Exception:return
            with closing(self.connect()) as conn,conn:
                conn.execute('UPDATE events SET sent=1 WHERE id=?',(event['id'],))

    def status(self):
        with closing(self.connect()) as conn:
            latest=conn.execute('SELECT MAX(created) FROM backups').fetchone()[0]
            states=[f"{r['kind']}：{'異常' if r['failed'] else '正常'}（{r['checked']}）" for r in conn.execute('SELECT * FROM health ORDER BY kind')]
        return '最近成功備份：'+(latest or '尚無')+'\n'+'\n'.join(states)

    def restore(self,name,confirm=False):
        if not confirm:raise ValueError('整體復原不是取消刪除；請停止Bot，確認退回備份時間並明確確認。')
        with maintenance_lock(self.database):
            metadata=self.validate_backup(name)
            self.backup('emergency')
            candidate=self.database.with_name('.restore-'+uuid.uuid4().hex+'.sqlite3')
            try:
                with closing(sqlite3.connect(self.path(name).as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(candidate)) as target:
                    source.backup(target)
                    for mark in self.deletions():
                        if mark['deleted_at']>=metadata['created']:
                            for table in LIFE_TABLES:target.execute(f'DELETE FROM {table} WHERE user_id=?',(mark['user_id'],))
                    target.commit()
                self.verify(candidate,writable=True)
                # Checkpoint only the current DB, then close all connections before replacement.
                with closing(sqlite3.connect(self.database)) as current:
                    if current.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:raise RuntimeError('資料庫仍有其他連線。')
                if any(Path(str(self.database)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
                    raise RuntimeError('資料庫仍有旁檔，請確認所有程序已停止。')
                os.replace(candidate,self.database)
            finally:candidate.unlink(missing_ok=True)


def record_deletion(conn,user_id,database):
    safety=Safety(database)
    conn.execute('ATTACH DATABASE ? AS safety',(str(safety.state),))
    # SQLite multi-file rollback journal commits ledger deletion and marker together.
    if any(conn.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0]!='delete' for schema in ('main','safety')):
        raise RuntimeError('刪除標記需要SQLite DELETE journal模式。')
    conn.execute('INSERT INTO safety.deletions VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET deleted_at=excluded.deleted_at',(str(user_id),utc_now().isoformat()))


async def monitor(bot,safety,admin_id):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await asyncio.to_thread(safety.check)
            if admin_id:
                user=bot.get_user(admin_id) or await bot.fetch_user(admin_id)
                async def send(message):
                    import discord
                    await user.send(message,allowed_mentions=discord.AllowedMentions.none())
                await safety.deliver(send)
        except Exception:
            print(utc_now().isoformat(),'Service safety check unavailable')
        await asyncio.sleep(60)


def run_protected(bot,initialize,token):
    import db
    from config import load_admin_id
    try:safety=Safety(db.DB_NAME)
    except Exception:
        raise SystemExit('本機維護狀態無法開啟，未啟動Bot；請檢查目錄與權限。') from None
    try:admin_id=load_admin_id()
    except ValueError:
        safety.record('startup',True)
        raise SystemExit('管理者設定無效，請檢查ADMIN_DISCORD_ID。') from None
    with maintenance_lock(db.DB_NAME):
        try:safety.prune()
        except Exception:safety.record('daily',True)
        try:
            initialize()
            safety.verify(safety.database,writable=True)
        except Exception:
            safety.record('database',True);safety.record('startup',True)
            raise SystemExit('資料庫啟動檢查失敗，已保存待送異常；請在主機檢查維護狀態。') from None
        safety.record('database',False);safety.record('startup',False)
        safety.check()
        bot.safety=safety;bot.admin_id=admin_id
        if admin_id is None:print('尚未設定ADMIN_DISCORD_ID；本機保留異常，設定後重啟才可私訊通知。')
        bot.run(token)


def main():
    import db
    parser=argparse.ArgumentParser(description='主機本機維護：整體復原不是取消刪除，沒有個別使用者還原功能。')
    parser.add_argument('action',choices=('pre-update','status','restore'))
    parser.add_argument('--backup',help='本機維護備份檔名（不是使用者匯出ZIP）')
    parser.add_argument('--confirm-whole-database',action='store_true',help='確認已停止Bot，接受全體資料退回備份時間；已刪除資料仍不得復活')
    args=parser.parse_args()
    try:safety=Safety(db.DB_NAME)
    except Exception:raise SystemExit('本機維護狀態無法開啟；請檢查目錄與權限。') from None
    if args.action=='status':
        print(safety.status())
        for row in safety.backup_records():print(row['name'],row['kind'],row['created'])
        return
    kind='pre-update' if args.action=='pre-update' else 'restore'
    try:
        if kind=='pre-update':
            path=safety.pre_update();print('更新前備份完成：'+path.name)
        else:
            if not args.backup:raise ValueError('必須指定Bot備份檔名。')
            safety.restore(args.backup,args.confirm_whole_database)
            print('整體復原完成。已套用生活資料刪除時間標記；請重新啟動Bot。')
    except Exception as error:
        safety.record(kind,True)
        print('維護未完成：'+type(error).__name__+'；請確認備份有效、Bot已停止及主機權限。')
        raise SystemExit(1)
    safety.record(kind,False)


if __name__=='__main__':main()
