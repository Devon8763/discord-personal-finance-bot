import unittest
import io
import json
import zipfile
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction


class TransferTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    def records(self):
        return [dict(spent_on='2026-09-01',cents=1234,category='舊分類',note="'=原用途",payment_source_name='舊信用卡',source='manual',voided=0,kind='consumption')]

    async def test_preview_import_dedup_current_owner_and_no_settings(self):
        from life_transfer import preview_records,import_records
        records=self.records()
        self.assertEqual(preview_records('42',records),dict(total=1,added=1,skipped=0))
        self.assertEqual(sp.rows('SELECT * FROM expenses'),[])
        self.assertEqual(import_records('42',records),dict(total=1,added=1,skipped=0))
        self.assertEqual(import_records('42',records),dict(total=1,added=0,skipped=1))
        self.assertEqual(import_records('43',records),dict(total=1,added=1,skipped=0))
        for table in ('payment_sources','spending_categories','budgets','spending_shortcuts','recurring_expenses','assets'):
            self.assertEqual(sp.rows(f'SELECT * FROM {table}'),[])
        own=sp.month_expenses('42','2026-09')[0]
        self.assertEqual(own['payment_source_name'],'舊信用卡')
        self.assertIsNone(own['payment_source_id'])
        self.assertEqual(own['note'],records[0]['note'])

    async def test_meaningful_content_duplicate_backup_and_atomic_rollback(self):
        from life_transfer import import_records,preview_records
        records=self.records();records.append(records[0].copy())
        self.assertEqual(import_records('42',records),dict(total=2,added=1,skipped=1))
        changes=[]
        for key,value in [('spent_on','2026-09-02'),('cents',1235),('category','另一分類'),('note','另一用途'),('payment_source_name','現金'),('source','固定'),('voided',1)]:
            changes.append(dict(records[0],**{key:value}))
        self.assertEqual(preview_records('42',changes)['added'],7)
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER transfer_failure BEFORE INSERT ON expense_actions BEGIN SELECT RAISE(ABORT,'private error'); END")
        with self.assertRaises(Exception):import_records('42',changes)
        self.assertEqual(len(sp.rows('SELECT * FROM expenses')),1)

    async def test_cancel_timeout_owner_guild_and_recheck_on_confirm(self):
        from life_transfer import ImportPreview,import_records
        records=self.records();view=ImportPreview(self.view,records)
        before=sp.rows('SELECT * FROM expenses')
        other=interaction(43);await view.children[0].callback(other)
        guild=interaction();guild.guild=SimpleNamespace(id=1)
        await view.children[0].callback(guild)
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        await view.children[1].callback(interaction())
        await view.children[0].callback(interaction())
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        timed=ImportPreview(self.view,records);await timed.on_timeout()
        await timed.children[0].callback(interaction())
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        confirmed=ImportPreview(self.view,records)
        import_records('42',records)
        event=interaction();await confirmed.children[0].callback(event)
        self.assertIn('新增 0',event.edit_original_response.await_args.kwargs['content'])
        self.assertIn('略過 1',event.edit_original_response.await_args.kwargs['content'])

    async def test_invalid_fields_rejected_before_any_write(self):
        from life_transfer import import_records
        for changes in ({'user_id':'43'},{'cents':True},{'voided':2},{'kind':'investment'},
                        {'spent_on':'2099-01-01'},{'spent_on':'2026-02-30'},{'note':''}):
            records=[*self.records(),dict(self.records()[0],**changes)]
            with self.assertRaises(ValueError):import_records('42',records)
            self.assertEqual(sp.rows('SELECT * FROM expenses'),[])

    async def test_export_roundtrip_format_integrity_and_unsupported_files(self):
        from life_privacy import export_life
        from life_transfer import read_backup,import_records
        source=sp.add_payment_source('42','舊支付')
        sp.add('42','50','餐飲',"'=原始單引號",'2026-09-01',source)
        sp.add('43','99','餐飲','他人秘密','2026-09-01')
        backup=export_life('42');records=read_backup(backup)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['note'],"'=原始單引號")
        self.assertNotIn('user_id',records[0])
        import_records('44',records)
        self.assertEqual(sp.month_expenses('44','2026-09')[0]['payment_source_name'],'舊支付')
        self.assertEqual(sp.rows('SELECT * FROM payment_sources WHERE user_id=?',('44',)),[])
        with zipfile.ZipFile(io.BytesIO(backup)) as z:files={n:z.read(n) for n in z.namelist()}
        for alteration in ('legacy','version','checksum','unknown','corrupt'):
            changed=files.copy()
            if alteration=='legacy':del changed['manifest.json']
            elif alteration=='version':
                manifest=json.loads(changed['manifest.json']);manifest['version']=999
                changed['manifest.json']=json.dumps(manifest).encode()
            elif alteration=='checksum':changed['consumptions.json']=b'[]'
            elif alteration=='unknown':changed['random.csv']=b'random'
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as z:
                for name,data in changed.items():z.writestr(name,data)
            with self.assertRaises(ValueError):read_backup(b'not a zip' if alteration=='corrupt' else output.getvalue())

    async def test_upload_private_preview_and_guild_rejected_before_read(self):
        from life_privacy import PrivacyView,export_life
        page=PrivacyView(self.view);i=interaction()
        await next(b for b in page.children if b.label=='匯入我的備份').callback(i)
        modal=i.response.send_modal.await_args.args[0]
        data=export_life('42')
        attachment=SimpleNamespace(filename='backup.zip',size=len(data),read=AsyncMock(return_value=data))
        modal.upload._values=[attachment]
        guild=interaction();guild.guild=SimpleNamespace(id=1)
        await modal.on_submit(guild);attachment.read.assert_not_awaited()
        other=interaction(43);await modal.on_submit(other);attachment.read.assert_not_awaited()
        await modal.on_submit(i)
        self.assertTrue(i.followup.send.await_args.kwargs['ephemeral'])
        self.assertIn('預計新增：0',i.followup.send.await_args.kwargs['embed'].description)
        self.assertEqual(sp.rows('SELECT * FROM expenses'),[])
        with patch('life_privacy.export_life') as export:
            await next(b for b in page.children if b.label=='匯出我的生活資料').callback(guild)
            export.assert_not_called()

    async def test_upload_errors_do_not_expose_attachment_exception(self):
        from life_transfer import ImportBackup
        modal=ImportBackup(self.view)
        modal.upload._values=[SimpleNamespace(filename='backup.zip',size=20,read=AsyncMock(side_effect=ValueError('SECRET host path')))]
        i=interaction();await modal.on_submit(i)
        self.assertNotIn('SECRET',str(i.followup.send.await_args))
        self.assertEqual(sp.rows('SELECT * FROM expenses'),[])

    async def test_backup_limits_and_fixed_voided_records_remain_consumption_only(self):
        from life_privacy import export_life
        from life_transfer import read_backup,import_records,MAX_UPLOAD
        sp.add_recurring('42','固定','固定支出','100','餐飲','2026-09')
        sp.sync_recurring('42')
        key=sp.add('42','20','餐飲','撤銷','2026-09-01')
        with sp.transaction() as conn:conn.execute('UPDATE expenses SET voided=1 WHERE id=?',(key,))
        records=read_backup(export_life('42'))
        self.assertEqual(len(records),2)
        result=import_records('43',records);self.assertEqual(result['added'],2)
        self.assertEqual(sp.month_report('43','2026-09')['total'],100)
        self.assertEqual(sp.rows('SELECT * FROM recurring_expenses WHERE user_id=?',('43',)),[])
        self.assertTrue(all(r['recurring_id'] is None for r in sp.rows('SELECT * FROM expenses WHERE user_id=?',('43',))))
        with self.assertRaises(ValueError):read_backup(b'x'*(MAX_UPLOAD+1))

    async def test_failed_confirmation_counts_and_imported_edit(self):
        from life_transfer import import_records,ImportPreview
        records=self.records();import_records('42',records)
        row=sp.month_expenses('42','2026-09')[0]
        sp.edit('42',row['id'],'15',row['category'],'已修改','2026-09-01',expected_revision=row['revision'])
        edited=sp.get_expense('42',row['id'])
        self.assertEqual(edited['payment_source_name'],'舊信用卡')
        records=[{k:edited[k] for k in records[0]},dict(records[0],note='新增')]
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER transfer_fail_ui BEFORE INSERT ON expenses BEGIN SELECT RAISE(ABORT,'SECRET'); END")
        view=ImportPreview(self.view,records);event=interaction()
        await view.children[0].callback(event)
        text=event.edit_original_response.await_args.kwargs['content']
        self.assertIn('新增 0 筆、略過 1 筆、失敗 1 筆',text)
        self.assertNotIn('SECRET',text)
        self.assertEqual(len(sp.month_expenses('42','2026-09')),1)
        await view.children[0].callback(interaction())
        self.assertEqual(len(sp.month_expenses('42','2026-09')),1)
