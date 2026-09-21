import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
import db
import spending as sp


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.object(db,'DB_NAME',str(Path(self.temp.name)/'test.db'))
        p.start();self.addCleanup(p.stop)
        db.init_db()
        self.now=datetime(2026,9,15,12,tzinfo=timezone.utc)
        from service_safety import Safety
        self.safety=Safety(db.DB_NAME,clock=lambda:self.now)

    async def test_daily_restart_cross_day_and_retention(self):
        first=self.safety.backup('daily')
        self.assertEqual(first,self.safety.backup('daily'))
        from service_safety import Safety
        self.assertEqual(first,Safety(db.DB_NAME,clock=lambda:self.now).backup('daily'))
        self.now+=timedelta(days=1)
        second=self.safety.backup('daily');self.assertNotEqual(first,second)
        stranger=self.safety.backups/'unrelated.txt';stranger.write_text('keep')
        self.now+=timedelta(days=7)
        self.safety.prune()
        self.assertFalse(first.exists());self.assertFalse(second.exists())
        self.assertTrue(stranger.exists())

    async def test_restore_validates_and_protects_current_database(self):
        sp.add('42',10,'餐飲','old')
        saved=self.safety.backup('pre-update')
        sp.add('42',20,'餐飲','new')
        self.safety.restore(saved.name,confirm=True)
        self.assertEqual(len(sp.month_expenses('42',sp.today().strftime('%Y-%m'))),1)
        self.assertTrue(any(r['kind']=='emergency' for r in self.safety.backup_records()))
        saved.write_bytes(b'broken')
        before=Path(db.DB_NAME).read_bytes()
        with self.assertRaises(ValueError):self.safety.restore(saved.name,confirm=True)
        self.assertEqual(before,Path(db.DB_NAME).read_bytes())

    async def test_deleted_data_stays_deleted_but_later_backup_survives(self):
        sp.add('42',10,'餐飲','deleted');sp.add('43',5,'餐飲','other')
        with sp.transaction() as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('42','2330',1,1)")
        old=self.safety.backup('pre-update')
        self.now+=timedelta(hours=1)
        with patch('service_safety.utc_now',return_value=self.now):sp.clear('42')
        self.now+=timedelta(hours=1)
        sp.add('42',20,'餐飲','after-delete')
        new=self.safety.backup('pre-update')
        self.safety.restore(old.name,confirm=True)
        self.assertEqual(sp.month_expenses('42',sp.today().strftime('%Y-%m')),[])
        self.assertEqual(len(sp.rows("SELECT * FROM assets WHERE user_id='42'")),1)
        self.assertEqual(len(sp.month_expenses('43',sp.today().strftime('%Y-%m'))),1)
        self.safety.restore(new.name,confirm=True)
        self.assertEqual(sp.month_expenses('42',sp.today().strftime('%Y-%m'))[0]['note'],'after-delete')

    async def test_failure_pending_notice_and_recovery_are_once_and_private(self):
        from service_safety import Safety
        with patch.object(self.safety,'backup',side_effect=OSError('SECRET amount 999')):
            self.safety.check();self.safety.check()
        other=Safety(db.DB_NAME,clock=lambda:self.now)
        send=AsyncMock(side_effect=RuntimeError('offline'))
        await other.deliver(send)
        send=AsyncMock();await other.deliver(send);await other.deliver(send)
        self.assertEqual(send.await_count,1)
        self.assertNotIn('SECRET',send.await_args.args[0])
        self.assertNotIn('999',send.await_args.args[0])
        other.check();await other.deliver(send);other.check();await other.deliver(send)
        self.assertEqual(send.await_count,2)
        self.assertIn('恢復',send.await_args.args[0])

    async def test_restore_requires_confirmation_and_stopped_bot(self):
        saved=self.safety.backup('daily')
        with self.assertRaises(ValueError):self.safety.restore(saved.name)
        from service_safety import maintenance_lock
        with maintenance_lock(db.DB_NAME):
            with self.assertRaises(RuntimeError):self.safety.restore(saved.name,confirm=True)

    async def test_failed_delete_rolls_back_marker_and_ledger(self):
        sp.add('42',10,'餐飲','keep');sp.set_budget('42',sp.today().strftime('%Y-%m'),'總額',100)
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON budgets BEGIN SELECT RAISE(ABORT,'fail'); END")
        with self.assertRaises(sqlite3.IntegrityError):sp.clear('42')
        self.assertEqual(self.safety.deletions(),[])
        self.assertEqual(len(sp.month_expenses('42',sp.today().strftime('%Y-%m'))),1)

    async def test_retention_runs_even_when_current_database_is_broken(self):
        old=self.safety.backup('daily')
        self.now+=timedelta(hours=1)
        with patch('service_safety.utc_now',return_value=self.now):sp.clear('42')
        self.now+=timedelta(days=8)
        Path(db.DB_NAME).write_bytes(b'broken')
        self.safety.check()
        self.assertFalse(old.exists())
        self.assertEqual(self.safety.deletions(),[])

    async def test_update_failure_is_queued_and_candidate_failure_preserves_current(self):
        with patch.object(self.safety,'backup',side_effect=OSError('SECRET')):
            for _ in range(2):
                with self.assertRaises(OSError):self.safety.pre_update()
        send=AsyncMock();await self.safety.deliver(send)
        self.assertEqual(send.await_count,1)
        saved=self.safety.pre_update();await self.safety.deliver(send)
        self.assertEqual(send.await_count,2)
        before=Path(db.DB_NAME).read_bytes()
        verify=self.safety.verify
        def failed_candidate(path,writable=False):
            if Path(path).name.startswith('.restore-'):raise OSError('write failed')
            return verify(path,writable)
        with patch.object(self.safety,'verify',side_effect=failed_candidate):
            with self.assertRaises(OSError):self.safety.restore(saved.name,confirm=True)
        self.assertEqual(before,Path(db.DB_NAME).read_bytes())

    async def test_unknown_expired_and_path_escape_backups_refused(self):
        for name in ('../data.db','discordbot-unknown.sqlite3'):
            with self.assertRaises(ValueError):self.safety.restore(name,confirm=True)
        saved=self.safety.backup('daily')
        self.now+=timedelta(days=7)
        with self.assertRaises(ValueError):self.safety.restore(saved.name,confirm=True)

    async def test_monitor_sends_only_to_configured_admin(self):
        from service_safety import monitor
        from types import SimpleNamespace
        self.safety.record('daily',True)
        admin=SimpleNamespace(send=AsyncMock())
        bot=SimpleNamespace(wait_until_ready=AsyncMock(),is_closed=lambda:False,
                            get_user=lambda user:None,fetch_user=AsyncMock(return_value=admin))
        import asyncio
        with patch.object(self.safety,'check'),patch('service_safety.asyncio.sleep',side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):await monitor(bot,self.safety,123)
        bot.fetch_user.assert_awaited_once_with(123)
        self.assertTrue(admin.send.await_args.kwargs['allowed_mentions'].everyone is False)

    async def test_admin_setting_accepts_only_one_id(self):
        from config import load_admin_id
        for value in ('a','123,456','-1','0','1 2'):
            with patch.dict('os.environ',{'ADMIN_DISCORD_ID':value}):
                with self.assertRaises(ValueError):load_admin_id()
        with patch.dict('os.environ',{'ADMIN_DISCORD_ID':'123'}):self.assertEqual(load_admin_id(),123)

    async def test_old_interrupted_backup_files_are_cleaned_but_unrelated_files_stay(self):
        import os
        orphan=self.safety.backups/('discordbot-daily-'+'a'*32+'.partial')
        orphan.write_bytes(b'partial test data')
        timestamp=(self.now-timedelta(days=8)).timestamp()
        os.utime(orphan,(timestamp,timestamp))
        unrelated=self.safety.backups/'manual-copy.partial';unrelated.write_bytes(b'keep')
        os.utime(unrelated,(timestamp,timestamp))
        self.safety.prune()
        self.assertFalse(orphan.exists());self.assertTrue(unrelated.exists())

    async def test_startup_failure_is_persisted_before_discord_connection(self):
        from service_safety import run_protected
        from unittest.mock import Mock
        bot=Mock()
        with patch.dict('os.environ',{'ADMIN_DISCORD_ID':'123'}):
            with self.assertRaises(SystemExit):run_protected(bot,Mock(side_effect=OSError('secret')), 'test-only')
        bot.run.assert_not_called()
        send=AsyncMock();await self.safety.deliver(send)
        self.assertEqual(send.await_count,2)
        self.assertNotIn('secret',str(send.await_args_list))

    async def test_online_backup_excludes_uncommitted_changes(self):
        sp.add('42',10,'餐飲','committed')
        writer=db.get_conn()
        try:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE expenses SET note='uncommitted' WHERE user_id='42'")
            saved=self.safety.backup('daily')
        finally:writer.rollback();writer.close()
        with closing(sqlite3.connect(saved)) as conn:
            self.assertEqual(conn.execute('SELECT note FROM expenses').fetchone()[0],'committed')
