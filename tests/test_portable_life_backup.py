"""Portable restore tests use synthetic owners and disposable SQLite only."""
import importlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

import db
import life_ledger_service as service
import spending as sp


class PortableLifeBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = patch.object(db, 'DB_NAME', str(Path(self.temp.name)/'ledger.db'))
        self.database.start()
        self.addCleanup(self.database.stop)
        self.clock = patch.object(sp, 'today', return_value=date(2025, 2, 28))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        db.init_db()
        self.assertIsNotNone(importlib.util.find_spec('life_ledger_backup'), 'Missing portable backup core')
        self.bk = importlib.import_module('life_ledger_backup')

    def snapshot(self):
        return {r['name']: sp.rows(f"SELECT * FROM {r['name']} ORDER BY rowid")
                for r in sp.rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

    def seed(self):
        user = 'source-discord-identity'
        with sp.transaction() as c:
            c.executemany('INSERT INTO spending_categories VALUES(?,?,?)', [(user,'交通',0),(user,'居住',1),(user,'自訂停用',0)])
            ids = [c.execute('INSERT INTO payment_sources(user_id,name,active) VALUES(?,?,?)',(user,n,a)).lastrowid
                   for n,a in [('未指定',1),('現金',1),('改名後信用卡',0)]]
            rule_ids = []
            for kind,periods,due in [('固定',0,31),('訂閱',0,None),('分期',2,29)]:
                key = c.execute('INSERT INTO recurring_expenses(user_id,name,cents,category,kind,start_month,periods,due_day,active,revision) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                (user,kind+'項目',1029,'居住',kind,'2025-01',periods,due,1,3)).lastrowid
                rule_ids.append(key)
            c.executemany('INSERT INTO recurring_expense_versions VALUES(?,?,?,?,?,?,?)',
                          [(user,rule_ids[0],'2025-01','原設定',1029,'居住',31),
                           (user,rule_ids[0],'2025-03','新設定',2034,'自訂停用',28)])
            records = [('2024-12-31',1234,'交通','精確小數','manual',None,None,0,ids[2],'改名前信用卡',2),
                       ('2025-01-31',1029,'居住','原固定','固定',rule_ids[0],'2025-01',1,None,'未指定',1),
                       ('2025-01-01',1029,'居住','訂閱','訂閱',rule_ids[1],'2025-01',0,None,'未指定',0),
                       ('2025-01-29',1029,'居住','分期','分期',rule_ids[2],'2025-01',0,None,'未指定',0),
                       ('2029-01-01',29,'自訂停用','已儲存未來帳目','manual',None,None,0,ids[2],'舊卡名',0)]
            for values in records:
                key = c.execute('INSERT INTO expenses(user_id,spent_on,cents,category,note,source,recurring_id,period,voided,payment_source_id,payment_source_name,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(user,*values)).lastrowid
                c.execute('INSERT INTO expense_actions(user_id,expense_id,before_json,undone) VALUES(?,?,?,?)',(user,key,'null',int(values[7])))
            c.row_factory = sqlite3.Row
            first = dict(c.execute('SELECT * FROM expenses WHERE user_id=? ORDER BY id',(user,)).fetchone())
            first['note'] = '更改前用途'
            first['revision'] = 1
            c.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)',(user,first['id'],json.dumps(first)))
            c.executemany('INSERT INTO budgets VALUES(?,?,?,?)',[(user,'2024-12','總額',999999),(user,'2024-12','交通',150000),(user,'2025-02','自訂停用',123456)])
            c.execute('INSERT INTO spending_shortcuts(user_id,name,category,payment_source_id,note,cents,position,active) VALUES(?,?,?,?,?,?,?,?)',
                      (user,'舊捷徑','自訂停用',ids[2],'原用途',29,7,0))
            c.execute('INSERT INTO spending_users VALUES(?,?)',(user,'2024-12-01'))
            c.execute('INSERT INTO spending_settings VALUES(?,?)',(user,'[80,100,120]'))
            c.execute('INSERT INTO spending_onboarding VALUES(?)',(user,))
            c.execute('INSERT INTO spending_notices(user_id,notice_key,body,delivered) VALUES(?,?,?,1)',(user,'old','排除通知'))
            c.execute('CREATE TABLE ai_preferences(user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')
            c.executemany('INSERT INTO ai_preferences VALUES(?,1)',[(user,),('target',)])
            c.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('target','2330',100,2)")
            c.execute("INSERT INTO users VALUES('target')")
            c.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('someone-else','2025-01-01',999,'餐飲','他人資料')")
        return user

    def payload(self):
        return self.bk.export_backup(self.seed())

    def encode(self, data):
        return json.dumps(data, ensure_ascii=False, separators=(',',':')).encode('utf-8')

    def test_full_roundtrip_preserves_meaning_settings_and_history(self):
        payload = self.payload()
        data = self.bk.read_backup(payload)
        self.assertEqual((data['format'],data['version']),('life-ledger-backup',1))
        self.assertEqual(len(data['data']['expenses']),5)
        self.assertEqual(len(data['data']['budgets']),3)
        self.assertEqual({r['source'] for r in data['data']['expenses']},{'manual','固定','訂閱','分期'})
        self.assertTrue(all(type(r['cents']) is str for r in data['data']['expenses']))
        before = self.snapshot()
        counts = self.bk.restore_backup('target',payload)
        self.assertEqual(counts['expenses'],5)
        self.assertEqual(self.bk.read_backup(self.bk.export_backup('target')),data)
        after = self.snapshot()
        for table in ('assets','ai_preferences','users'):
            self.assertEqual(before[table],after[table])
        self.assertEqual([r for r in before['expenses'] if r['user_id']!='target'],[r for r in after['expenses'] if r['user_id']!='target'])
        self.assertEqual([r for r in after['spending_notices'] if r['user_id']=='target'],[])
        self.assertEqual([r for r in after['spending_onboarding'] if r['user_id']=='target'],[])
        self.assertEqual(sp.reminder_levels('target'),[80,100,120])
        self.assertNotEqual(sp.rows("SELECT id FROM payment_sources WHERE user_id='target'")[0]['id'],sp.rows("SELECT id FROM payment_sources WHERE user_id='source-discord-identity'")[0]['id'])

    def test_export_is_private_read_only_and_does_not_create_defaults(self):
        user = self.seed()
        before = self.snapshot()
        payload = self.bk.export_backup(user)
        self.assertEqual(before,self.snapshot())
        text = payload.decode()
        for secret in ('source-discord-identity','someone-else','他人資料','排除通知','user_id','ai_preferences','assets','session','Token'):
            self.assertNotIn(secret,text)
        empty = self.bk.read_backup(self.bk.export_backup('never-used'))
        self.assertEqual(empty['data']['payment_sources'],[])
        self.assertEqual(before,self.snapshot())

    def test_statistics_equal_at_same_as_of_and_future_storage_not_lost(self):
        payload = self.payload()
        self.bk.restore_backup('target',payload)
        a='source-discord-identity'
        for month in ('2024-12','2025-01','2025-02'):
            self.assertEqual(sp.month_report(a,month),sp.month_report('target',month))
            self.assertEqual(sp.chart_data(a,month),sp.chart_data('target',month))
        for user in (a,'target'):
            result=sp.expense_comparison(user,'2024-12-01','2025-01-31','2025-02-01','2025-03-31',as_of=date(2025,2,28))
            if user==a: old=result
            else: self.assertEqual(old,result)
        self.assertEqual(sp.rows("SELECT spent_on FROM expenses WHERE user_id='target' ORDER BY id")[-1]['spent_on'],'2029-01-01')

    def test_actions_map_owner_references_order_and_undo_revision(self):
        payload=self.payload()
        self.bk.restore_backup('target',payload)
        key,expense=sp.undo('target')
        row=sp.get_expense('target',expense)
        old_revision=row['revision']
        sp.undo('target',key)
        new=sp.get_expense('target',expense)
        self.assertEqual((new['note'],new['revision']),('更改前用途',old_revision+1))
        for row in sp.rows("SELECT before_json FROM expense_actions WHERE user_id='target'"):
            before=json.loads(row['before_json'])
            if before is not None:
                self.assertEqual(before['user_id'],'target')
                self.assertIsNotNone(sp.rows('SELECT id FROM expenses WHERE user_id=? AND id=?',('target',before['id'])))
                self.assertIsNotNone(sp.rows('SELECT id FROM payment_sources WHERE user_id=? AND id=?',('target',before['payment_source_id'])))

    def test_sync_keeps_voided_marks_short_month_and_rule_versions(self):
        payload=self.payload();self.bk.restore_backup('target',payload)
        self.assertEqual(sp.sync_recurring('target',date(2025,1,31)),0)
        self.assertEqual(sp.sync_recurring('target',date(2025,2,28)),3)
        self.assertEqual(sp.sync_recurring('target',date(2025,2,28)),0)
        self.assertEqual(sp.sync_recurring('target',date(2025,3,31)),2)
        rows=sp.rows("SELECT * FROM expenses WHERE user_id='target' AND source='固定' ORDER BY spent_on")
        self.assertEqual([(r['spent_on'],r['cents'],r['voided']) for r in rows],[('2025-01-31',1029,1),('2025-02-28',1029,0),('2025-03-28',2034,0)])
        self.assertEqual(sp.rows("SELECT COUNT(*) AS n FROM expenses WHERE user_id='target' AND source='分期'")[0]['n'],2)

    def test_nonempty_target_rejected_without_cleanup_for_each_life_table(self):
        payload=self.payload()
        self.bk.restore_backup('target',payload)
        before=self.snapshot()
        with self.assertRaises(self.bk.NonemptyLedgerError): self.bk.restore_backup('target',payload)
        self.assertEqual(before,self.snapshot())
        statements=["INSERT INTO spending_categories VALUES('blank','餐飲',1)","INSERT INTO spending_settings VALUES('blank','[]')", "INSERT INTO spending_onboarding VALUES('blank')", "INSERT INTO spending_users VALUES('blank','2025-01-01')", "INSERT INTO payment_sources(user_id,name) VALUES('blank','未指定')", "INSERT INTO spending_notices(user_id,notice_key,body) VALUES('blank','x','通知')", "INSERT INTO expenses(user_id,spent_on,cents,category,note,kind) VALUES('blank','2025-01-01',100,'其他','非消費','income')", "INSERT INTO budgets VALUES('blank','2025-01','總額',1)", "INSERT INTO recurring_expenses(user_id,name,cents,category,kind,start_month,periods) VALUES('blank','規則',1,'居住','固定','2025-01',0)", "INSERT INTO recurring_expense_versions VALUES('blank',999,'2025-01','版本',1,'其他',1)", "INSERT INTO spending_shortcuts(user_id,name,category,payment_source_id,note,position) VALUES('blank','捷徑','其他',999,'用途',1)", "INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES('blank',999,'null')"]
        for statement in statements:
            with self.subTest(statement=statement):
                with sp.transaction() as c: c.execute(statement)
                before=self.snapshot()
                with self.assertRaises(self.bk.NonemptyLedgerError):self.bk.restore_backup('blank',payload)
                self.assertEqual(before,self.snapshot())
                with sp.transaction() as c:
                    for table in self.bk.EMPTY_TABLES:c.execute(f"DELETE FROM {table} WHERE user_id='blank'")

    def test_fault_mid_restore_rolls_back_every_table_and_sequence(self):
        payload=self.payload()
        with sp.transaction() as c:c.execute("CREATE TRIGGER portable_fault BEFORE INSERT ON expense_actions BEGIN SELECT RAISE(ABORT,'SECRET SQL'); END")
        before=self.snapshot()
        with self.assertRaises(self.bk.PortableRestoreError) as e:self.bk.restore_backup('target',payload)
        self.assertNotIn('SECRET',str(e.exception))
        self.assertEqual(before,self.snapshot())

    def test_empty_and_settings_only_roundtrip_no_registration(self):
        empty=self.bk.export_backup('empty')
        before=self.snapshot()
        self.bk.restore_backup('target',empty)
        self.assertEqual(before,self.snapshot())
        with sp.transaction() as c:c.execute("INSERT INTO spending_settings VALUES('only-settings','[]')")
        data=self.bk.export_backup('only-settings')
        self.bk.restore_backup('target',data)
        self.assertEqual(sp.reminder_levels('target'),[])
        self.assertEqual(self.bk.read_backup(data),self.bk.read_backup(self.bk.export_backup('target')))

    def test_large_exact_cents_and_historical_labels_are_not_form_revalidated(self):
        with sp.transaction() as c:
            c.executemany('INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES(?,?,?,?,?)', [('large','2024-12-31',5000000000000001,'歷史未建分類','舊用途')]*2)
            c.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,source) VALUES('large','2024-12-01',29,'其他','舊ZIP無規則引用','固定')")
        data=self.bk.export_backup('large');self.bk.restore_backup('target',data)
        result=sp.expense_comparison('target','2024-12-01','2024-12-31','2025-01-01','2025-01-01',as_of=date(2025,2,28))
        self.assertEqual(result['a']['total_cents'],10000000000000031)
        self.assertEqual(self.bk.read_backup(data),self.bk.read_backup(self.bk.export_backup('target')))

    def test_invalid_structure_relations_types_and_unique_keys_reject_before_write(self):
        original=self.bk.read_backup(self.payload())
        mutations=[lambda x:x.update(version=2),lambda x:x.update(version=True),lambda x:x.update(user_id='intruder'),lambda x:x.pop('format'),
                   lambda x:x['data']['expenses'][0].update(cents=1234),lambda x:x['data']['expenses'][0].update(cents='01'),lambda x:x['data']['expenses'][0].update(cents='1e2'),
                   lambda x:x['data']['expenses'][0].update(revision=True),lambda x:x['data']['expenses'][0].update(spent_on='2025-02-30'),lambda x:x['data']['expenses'][0].update(kind='income'),
                   lambda x:x['data']['expenses'][0].update(payment_source_id='missing'),lambda x:x['data']['expenses'][1].update(period=None),lambda x:x['data']['expenses'][1].update(recurring_id='missing'),
                   lambda x:x['data']['expenses'].append(dict(x['data']['expenses'][0])),lambda x:x['data']['expenses'].append(dict(x['data']['expenses'][1],id='e-new')),
                   lambda x:x['data']['payment_sources'].append(dict(x['data']['payment_sources'][0],id='p-new')),lambda x:x['data']['budgets'].append(dict(x['data']['budgets'][0])),
                   lambda x:x['data']['recurring_versions'].append(dict(x['data']['recurring_versions'][0],id='v-new')),lambda x:x['data']['recurring_versions'][0].update(recurring_id='missing'),
                   lambda x:x['data']['shortcuts'][0].update(payment_source_id='missing'),lambda x:x['data']['actions'][0].update(expense_id='missing'),lambda x:x['data']['actions'][-1]['before'].update(user_id='intruder'),
                   lambda x:x['data']['actions'][-1]['before'].update(id=x['data']['expenses'][1]['id']),lambda x:x['data']['settings'].update(oauth='secret'),
                   lambda x:x['data']['settings'].update(reminder_levels=[True]),lambda x:x['data']['recurring_rules'][0].update(due_day=0)]
        before=self.snapshot()
        for mutate in mutations:
            value=json.loads(json.dumps(original));mutate(value)
            with self.subTest(mutate=mutate):
                with self.assertRaises(ValueError):self.bk.restore_backup('target',self.encode(value))
                self.assertEqual(before,self.snapshot())

    def test_strict_json_utf8_size_depth_and_nonstandard_numbers(self):
        bad=[b'{',b'\xff',b'{"format":"x","format":"y"}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1.5}',b'{"x":1e3}',b'['*20+b']'*20]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):self.bk.read_backup(payload)
        with self.assertRaises(ValueError):self.bk.read_backup(b' '* (self.bk.MAX_BYTES+1))
        data=self.bk.read_backup(self.payload())
        data['data']['actions']=data['data']['actions']*20000
        with self.assertRaises(ValueError):self.bk.read_backup(self.encode(data))

    def test_source_anomalies_rejected_not_silently_removed_or_fixed(self):
        user=self.seed()
        with sp.transaction() as c:c.execute("UPDATE expense_actions SET before_json=? WHERE user_id=? AND before_json!='null'", ('{"user_id":"someone-else"}',user))
        before=self.snapshot()
        with self.assertRaises(ValueError) as e:self.bk.export_backup(user)
        self.assertNotIn('someone-else',str(e.exception))
        self.assertEqual(before,self.snapshot())

    def test_legacy_snapshot_defaults_only_known_schema_upgrade_fields(self):
        user=self.seed()
        row=sp.rows("SELECT * FROM expense_actions WHERE before_json!='null'")[0]
        old=json.loads(row['before_json'])
        for key in ('payment_source_id','payment_source_name','kind','revision'):old.pop(key)
        with sp.transaction() as c:c.execute('UPDATE expense_actions SET before_json=? WHERE id=?',(json.dumps(old),row['id']))
        data=self.bk.export_backup(user);self.bk.restore_backup('target',data)
        key,expense=sp.undo('target');sp.undo('target',key)
        self.assertEqual(sp.get_expense('target',expense)['payment_source_name'],'未指定')

    def test_export_uses_one_snapshot_when_writer_commits_between_tables(self):
        user=self.seed()
        with sp.transaction() as c:c.execute('PRAGMA user_version=0')
        c=sp.get_conn();c.execute('PRAGMA journal_mode=WAL');c.close()
        original=self.bk.get_conn;done=[]
        class Connection:
            def __init__(self):self.conn=original()
            def execute(inner,sql,args=()):
                result=inner.conn.execute(sql,args)
                if 'FROM expenses' in sql and not done:
                    done.append(True)
                    with sp.transaction() as c:c.execute("UPDATE budgets SET cents=777 WHERE user_id=?",(user,))
                return result
            def rollback(inner):inner.conn.rollback()
            def close(inner):inner.conn.close()
        with patch.object(self.bk,'get_conn',side_effect=Connection):data=self.bk.read_backup(self.bk.export_backup(user))
        self.assertNotIn('777',[r['cents'] for r in data['data']['budgets']])
        self.assertTrue(done)

    def test_parallel_restores_allow_one_empty_target_writer(self):
        payload=self.payload()
        def restore(_):
            try:self.bk.restore_backup('target',payload);return 'restored'
            except self.bk.NonemptyLedgerError:return 'nonempty'
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(restore,range(2)))
        self.assertCountEqual(result,['restored','nonempty'])
        self.assertEqual(len(sp.rows("SELECT * FROM expenses WHERE user_id='target'")),5)

    def test_service_normalizes_owner_and_has_no_auth_or_ui_side_effects(self):
        with patch.object(self.bk,'export_backup',return_value=b'portable') as export:
            self.assertEqual(service.export_portable_backup(42),b'portable');export.assert_called_once_with('42')
        with patch.object(self.bk,'restore_backup',return_value={'expenses':0}) as restore:
            self.assertEqual(service.restore_portable_backup(43,b'portable'),{'expenses':0});restore.assert_called_once_with('43',b'portable')

    def test_complete_export_is_not_search_or_today_limited(self):
        with sp.transaction() as c:
            c.executemany("INSERT INTO expenses(user_id,spent_on,cents,category,note) VALUES('many','2029-01-01',29,'其他','未來已存')",[()]*10001)
        payload=self.bk.export_backup('many')
        self.assertEqual(len(self.bk.read_backup(payload)['data']['expenses']),10001)
        self.bk.restore_backup('target',payload)
        self.assertEqual(sp.rows("SELECT COUNT(*) AS n FROM expenses WHERE user_id='target'")[0]['n'],10001)

    def test_malformed_field_types_return_only_classified_safe_errors(self):
        data=self.bk.read_backup(self.payload())['data']
        mutations=[lambda x:x['expenses'][0].update(payment_source_id=[]),lambda x:x['categories'][0].update(name=[]),
                   lambda x:x['budgets'][0].update(category={}),lambda x:x['recurring_versions'][0].update(recurring_id=[]),
                   lambda x:x['actions'][-1]['before'].update(payment_source_id={}),lambda x:x['shortcuts'][0].update(payment_source_id=[]),
                   lambda x:x['expenses'][0].update(note=chr(0xd800)),lambda x:x['recurring_rules'][0].update(cents='9223372036854775808')]
        before=self.snapshot()
        for mutate in mutations:
            value=json.loads(json.dumps(data));mutate(value)
            with self.subTest(mutate=mutate):
                payload=json.dumps(dict(format='life-ledger-backup',version=1,data=value)).encode()
                with self.assertRaises(self.bk.PortableBackupError):self.bk.restore_backup('target',payload)
                self.assertEqual(before,self.snapshot())

    def test_import_and_export_never_call_registration_sync_alerts_or_notifications(self):
        user=self.seed()
        with patch.object(sp,'register',side_effect=AssertionError('registration')), patch.object(sp,'sync_recurring',side_effect=AssertionError('sync')), patch.object(sp,'alerts',side_effect=AssertionError('alerts')), patch.object(sp,'payment_sources',side_effect=AssertionError('defaults')):
            payload=self.bk.export_backup(user)
            self.bk.restore_backup('target',payload)
        self.assertEqual([r for r in self.snapshot()['spending_notices'] if r['user_id']=='target'],[])

    def test_zero_budget_and_unicode_text_roundtrip_without_new_form_restrictions(self):
        user=self.seed()
        text='中文、逗號, "引號"\n<script>只是用途文字</script>'
        with sp.transaction() as c:
            c.execute('INSERT INTO budgets VALUES(?,?,?,0)',(user,'2020-01','總額'))
            c.execute('UPDATE expenses SET note=? WHERE user_id=? AND source=?',(text,user,'manual'))
            c.execute('UPDATE recurring_expenses SET active=0 WHERE user_id=?',(user,))
        payload=self.bk.export_backup(user);self.bk.restore_backup('target',payload)
        self.assertEqual(self.bk.read_backup(payload),self.bk.read_backup(self.bk.export_backup('target')))
        self.assertEqual(sp.rows("SELECT cents FROM budgets WHERE user_id='target' AND month='2020-01'")[0]['cents'],0)
        self.assertEqual(sp.rows("SELECT note FROM expenses WHERE user_id='target' AND source='manual'")[0]['note'],text)

    def test_source_blob_snapshot_is_rejected_as_safe_data_error(self):
        user=self.seed()
        with sp.transaction() as c:c.execute('UPDATE expense_actions SET before_json=? WHERE user_id=?',(b'not-text',user))
        before=self.snapshot()
        with self.assertRaises(self.bk.PortableBackupError):self.bk.export_backup(user)
        self.assertEqual(before,self.snapshot())

    def test_blank_check_occurs_again_after_parsing_before_any_restore_write(self):
        payload=self.payload();read=self.bk.read_backup
        def concurrent_writer(data):
            result=read(data)
            with sp.transaction() as c:c.execute("INSERT INTO spending_settings VALUES('target','[]')")
            return result
        with patch.object(self.bk,'read_backup',side_effect=concurrent_writer):
            with self.assertRaises(self.bk.NonemptyLedgerError):self.bk.restore_backup('target',payload)
        self.assertEqual(sp.rows("SELECT * FROM expenses WHERE user_id='target'"),[])
        self.assertEqual(sp.reminder_levels('target'),[])

    def test_action_snapshot_cannot_change_immutable_automatic_origin_or_period(self):
        original=self.bk.read_backup(self.payload())
        tests=[]
        data=json.loads(json.dumps(original))
        data['data']['expenses'][0].update(source='固定',recurring_id=data['data']['recurring_rules'][0]['id'],period='2025-02',spent_on='2025-02-28')
        tests.append(data)
        data=json.loads(json.dumps(original))
        auto=dict(data['data']['expenses'][1]);auto.update(spent_on='2025-02-28',period='2025-02')
        data['data']['actions'].append(dict(id='a-extra',expense_id=auto['id'],before=auto,undone=0))
        tests.append(data)
        before=self.snapshot()
        for data in tests:
            with self.subTest(case=tests.index(data)):
                with self.assertRaises(self.bk.PortableBackupError):self.bk.restore_backup('target',self.encode(data))
                self.assertEqual(before,self.snapshot())

    def test_restored_leap_year_and_finite_installments_keep_original_markers(self):
        with sp.transaction() as c:
            key=c.execute("INSERT INTO recurring_expenses(user_id,name,cents,category,kind,start_month,periods,due_day,revision) VALUES('leap','分期',29,'其他','分期','2027-12',3,31,7)").lastrowid
            c.execute("INSERT INTO expenses(user_id,spent_on,cents,category,note,source,recurring_id,period,voided) VALUES('leap','2027-12-31',29,'其他','撤銷原資料','分期',?,'2027-12',1)",(key,))
        payload=self.bk.export_backup('leap');self.bk.restore_backup('target',payload)
        self.assertEqual(sp.sync_recurring('target',date(2028,2,29)),2)
        self.assertEqual(sp.sync_recurring('target',date(2028,3,31)),0)
        self.assertEqual([(r['spent_on'],r['voided']) for r in sp.rows("SELECT * FROM expenses WHERE user_id='target' ORDER BY spent_on")], [('2027-12-31',1),('2028-01-31',0),('2028-02-29',0)])
        self.assertEqual(sp.rows("SELECT revision FROM recurring_expenses WHERE user_id='target'")[0]['revision'],7)

    def test_nonconsumption_is_excluded_but_foreign_references_are_not_ignored(self):
        user=self.seed()
        with sp.transaction() as c:
            key=c.execute('INSERT INTO expenses(user_id,spent_on,cents,category,note,kind) VALUES(?,?,?,?,?,?)',(user,'2025-01-01',99,'其他','排除收入','income')).lastrowid
            c.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)',(user,key,'null'))
        data=self.bk.export_backup(user)
        self.bk.restore_backup('target',data)
        self.assertEqual(data,self.bk.export_backup('target'))
        with sp.transaction() as c:
            foreign=c.execute("INSERT INTO payment_sources(user_id,name) VALUES('someone-else','他人卡')").lastrowid
            c.execute('UPDATE expenses SET payment_source_id=? WHERE user_id=? AND kind=?',(foreign,user,'consumption'))
        before=self.snapshot()
        with self.assertRaises(self.bk.PortableBackupError):self.bk.export_backup(user)
        self.assertEqual(before,self.snapshot())

    def test_historical_automatic_snapshot_date_in_same_month_is_preserved(self):
        data=self.bk.read_backup(self.payload())
        auto=dict(data['data']['expenses'][1]);auto['spent_on']='2025-01-01'
        data['data']['actions'].append(dict(id='a-extra',expense_id=auto['id'],before=auto,undone=0))
        self.bk.restore_backup('target',self.encode(data))
        key,expense=sp.undo('target');sp.undo('target',key)
        self.assertEqual(sp.rows('SELECT spent_on FROM expenses WHERE user_id=? AND id=?',('target',expense))[0]['spent_on'],'2025-01-01')
        self.bk.read_backup(self.bk.export_backup('target'))
