import io
import unittest
import zipfile
from unittest.mock import patch
import spending as sp
from test_phase1_ui import interaction
import test_phase1_ui as fixtures


class BundleTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def test_private_export_entry_uses_full_data_name(self):
        from life_privacy import PrivacyView
        from unittest.mock import AsyncMock
        view=PrivacyView(self.view)
        button=next(b for b in view.children if b.label=='匯出我的資料')
        with patch('life_privacy.send_export',new_callable=AsyncMock) as send:
            await button.callback(interaction(43))
            event=interaction();event.guild=object()
            await button.callback(event)
            send.assert_not_awaited()
            event=interaction();await button.callback(event)
            send.assert_awaited_once_with(event)

    async def test_export_rejects_data_that_cannot_be_imported(self):
        from backup_bundle import export_bundle,snapshot
        self.seed()
        conn=sp.get_conn()
        try:bundle=snapshot(conn,'42')
        finally:conn.close()
        bundle['expenses']=bundle['expenses']*10001
        with patch('backup_bundle.snapshot',return_value=bundle):
            with self.assertRaises(ValueError):export_bundle('42')

    async def test_export_validation_failure_sends_safe_private_error_without_attachment(self):
        from life_privacy import send_export
        event=interaction()
        with patch('life_privacy.export_life',side_effect=ValueError('SECRET purpose amount')):
            await send_export(event)
        self.assertTrue(event.followup.send.await_args.kwargs['ephemeral'])
        self.assertNotIn('file',event.followup.send.await_args.kwargs)
        self.assertNotIn('SECRET',event.followup.send.await_args.args[0])

    def seed(self,user='42'):
        source=sp.add_payment_source(user,'卡片')
        sp.set_category(user,'自訂',True)
        sp.add(user,50,'自訂','用途','2026-09-01',source)
        sp.save_shortcut(user,'捷徑','自訂',source,'用途',50)
        sp.set_budget(user,'2026-09','總額',1000)
        sp.set_budget(user,'2026-08','總額',900)

    async def test_settings_roundtrip_and_conflicts_no_old_identity(self):
        from backup_bundle import export_bundle,read_bundle,merge_bundle
        self.seed()
        data=export_bundle('42')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertNotIn('investments.json',z.namelist())
            self.assertNotIn('profile.csv',z.namelist())
            self.assertNotIn('user_id',z.read('settings.json').decode())
        bundle=read_bundle(data)
        self.assertEqual(len(bundle['budgets']),1)
        before=sp.rows('SELECT * FROM expenses')
        preview=merge_bundle('43',bundle,False)
        self.assertEqual(preview['shortcuts']['added'],1)
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        merge_bundle('43',bundle,True)
        self.assertEqual(sp.month_report('43','2026-09')['total'],50)
        again=merge_bundle('43',bundle,True)
        self.assertTrue(all(x['added']==0 for x in again.values()))
        sp.set_budget('43','2026-09','總額',2000)
        sp.rename_payment_source('43',sp.shortcuts('43')[0]['payment_source_id'],'另卡')
        merge_bundle('43',bundle,True)
        self.assertEqual(sp.month_report('43','2026-09')['budgets'][0]['budget'],2000)
        self.assertEqual(sp.shortcuts('43')[0]['payment_source_name'],'另卡')

    async def test_investment_optional_links_undo_and_repeat(self):
        from backup_bundle import export_bundle,read_bundle,merge_bundle
        from ledger import trade,undo,history,save_fund_price
        trade('42','buy','2330',100,2)
        trade('42','fundbuy','基金',10,100)
        trade('42','fundsell','基金',12,2)
        save_fund_price('42','基金',13)
        bundle=read_bundle(export_bundle('42'))
        self.assertIn('investments',bundle)
        result=merge_bundle('43',bundle,True)
        self.assertGreater(result['trade_history']['added'],0)
        self.assertTrue(all(x['added']==0 for x in merge_bundle('43',bundle,True).values()))
        undo('43',history('43')[0]['id'])
        self.assertEqual(sum(r['units'] for r in sp.rows('SELECT * FROM fund_transactions WHERE user_id=?',('43',))),10)
        self.assertEqual(sum(r['units'] for r in sp.rows('SELECT * FROM fund_transactions WHERE user_id=?',('42',))),8)

    async def test_reject_unknown_fields_and_whole_import_rolls_back(self):
        from backup_bundle import export_bundle,read_bundle,merge_bundle,validate_bundle,BundleImportFailed
        self.seed();bundle=read_bundle(export_bundle('42'))
        bundle['categories'][0]['user_id']='999'
        with self.assertRaises(ValueError):validate_bundle(bundle)
        del bundle['categories'][0]['user_id']
        with sp.transaction() as conn:
            conn.execute('CREATE TABLE ai_preferences(user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')
            conn.execute("INSERT INTO ai_preferences(user_id,enabled) VALUES('43',1)")
            conn.execute("CREATE TRIGGER bundle_fail BEFORE INSERT ON expenses BEGIN SELECT RAISE(ABORT,'SECRET'); END")
        with self.assertRaises(BundleImportFailed) as error:merge_bundle('43',bundle,True)
        self.assertNotIn('SECRET',str(error.exception))
        self.assertEqual(error.exception.counts['expenses']['failed'],1)
        self.assertEqual(sp.rows("SELECT enabled FROM ai_preferences WHERE user_id='43'")[0]['enabled'],1)
        for table in ('spending_categories','payment_sources','budgets','spending_shortcuts','expenses'):
            self.assertEqual(sp.rows(f'SELECT * FROM {table} WHERE user_id=?',('43',)),[])

    async def test_legacy_ai_preferences_are_not_exported_or_changed_by_import(self):
        from backup_bundle import export_bundle,read_bundle
        from life_transfer import ImportPreview
        self.seed('43')
        with sp.transaction() as conn:
            conn.execute('CREATE TABLE ai_preferences(user_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL)')
            conn.executemany('INSERT INTO ai_preferences(user_id,enabled) VALUES(?,1)',[('42',),('43',)])
        payload=export_bundle('43')
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            for name in ('settings.json','consumptions.json'):
                self.assertNotIn('ai_preferences',z.read(name).decode())
                self.assertNotIn('user_id',z.read(name).decode())
        bundle=read_bundle(payload)
        view=ImportPreview(self.view,bundle)
        await view.children[1].callback(interaction())
        self.assertEqual(sp.rows('SELECT user_id,enabled FROM ai_preferences ORDER BY user_id'),[{'user_id':'42','enabled':1},{'user_id':'43','enabled':1}])
        view=ImportPreview(self.view,bundle);event=interaction()
        await view.children[0].callback(event)
        self.assertEqual(sp.rows('SELECT user_id,enabled FROM ai_preferences ORDER BY user_id'),[{'user_id':'42','enabled':1},{'user_id':'43','enabled':1}])
        self.assertIn('匯入完成',event.edit_original_response.await_args.kwargs['content'])

    async def test_investment_existing_symbol_conflict_keeps_target(self):
        from backup_bundle import export_bundle,read_bundle,merge_bundle
        from ledger import trade
        trade('43','buy','2330',30,4)
        trade('42','buy','2330',50,2)
        trade('42','buy','0050',60,3)
        result=merge_bundle('43',read_bundle(export_bundle('42')),True)
        self.assertEqual(result['assets']['skipped'],1)
        self.assertEqual(result['assets']['added'],1)
        own=sp.rows('SELECT symbol,shares FROM assets WHERE user_id=?',('43',))
        self.assertEqual({r['symbol']:r['shares'] for r in own},{'2330':4,'0050':3})
