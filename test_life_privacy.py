import csv
import io
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import spending as sp
import dashboard as ui
import test_phase1_ui as fixtures
from test_phase1_ui import interaction, choose


class LifePrivacyTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    def seed(self):
        sp.add('42','12.34','餐飲','=中文用途','2026-09-10')
        sp.add('43','999','餐飲','其他人的秘密','2026-09-10')
        sp.dismiss_onboarding('42')
        with sp.transaction() as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES(?,?,?,?)",('42','投資秘密',1,1))

    async def page(self):
        i=interaction();await ui.open_dashboard_tools(self.view,i,'設定')
        menu=i.response.send_message.await_args.kwargs['view']
        await choose(menu,'我的資料與隱私').callback(i)
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        return i.response.send_message.await_args.kwargs['view']

    async def press(self,view,label,event=None):
        event=event or interaction()
        await next(b for b in view.children if b.label==label).callback(event)
        return event

    async def test_export_allowlist_bom_chinese_empty_and_no_files(self):
        from life_privacy import export_life
        before=set(Path('.').glob('life-ledger-export*'))
        self.seed()
        payload=export_life('42')
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertTrue({'expenses.csv','budgets.csv','payment_sources.csv','categories.csv','shortcuts.csv','settings.json','README.txt','investments.json'}<=set(archive.namelist()))
            text='\n'.join(archive.read(n).decode('utf-8-sig') for n in archive.namelist())
            self.assertIn('投資秘密',text)
            for forbidden in ('其他人的秘密','user_id','data.db','token.txt',str(Path.cwd())):self.assertNotIn(forbidden,text)
            for name in archive.namelist():
                if name.endswith('.csv'):self.assertTrue(archive.read(name).startswith(b'\xef\xbb\xbf'))
            rows=list(csv.DictReader(io.StringIO(archive.read('expenses.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[0]['金額'],'12.34')
            self.assertEqual(rows[0]['用途'],"'=中文用途")
        with zipfile.ZipFile(io.BytesIO(export_life('99'))) as archive:
            self.assertIn('README.txt',archive.namelist())
            self.assertEqual(len(list(csv.reader(io.StringIO(archive.read('expenses.csv').decode('utf-8-sig'))))),1)
        self.assertEqual(before,set(Path('.').glob('life-ledger-export*')))

    async def test_owner_checks_and_private_export_cleanup(self):
        page=await self.page()
        for label in ('匯出我的生活資料','刪除我的生活資料','關閉'):
            event=await self.press(page,label,interaction(43))
            self.assertTrue(event.response.send_message.await_args.kwargs['ephemeral'])
            event.response.edit_message.assert_not_awaited()
        captured=[]
        event=interaction()
        async def delivered(*args,**kwargs):
            if 'file' in kwargs:
                file=kwargs['file'];captured.append((file,file.fp.read()))
            self.assertTrue(kwargs['ephemeral'])
        event.followup.send.side_effect=delivered
        await self.press(page,'匯出我的生活資料',event)
        self.assertIn('請勿轉傳',event.response.send_message.await_args.args[0])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(captured[0][1])))
        self.assertTrue(captured[0][0].fp.closed)

    async def test_cancel_export_first_and_confirm_share_clear_scope(self):
        from spending_commands import Spending
        self.seed();page=await self.page()
        event=await self.press(page,'刪除我的生活資料')
        confirm=event.response.send_message.await_args.kwargs['view']
        before=sp.rows('SELECT * FROM expenses')
        await self.press(confirm,'匯出後再刪除')
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        await self.press(confirm,'確認永久刪除',interaction(43))
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        await self.press(confirm,'取消')
        await self.press(confirm,'確認永久刪除')
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))
        event=await self.press(page,'刪除我的生活資料')
        confirm=event.response.send_message.await_args.kwargs['view']
        with patch.object(self.view,'refresh',AsyncMock()) as refreshed,patch.object(sp,'clear',wraps=sp.clear) as cleared:
            result=await self.press(confirm,'確認永久刪除');cleared.assert_called_once_with('42');refreshed.assert_awaited_once()
            self.assertFalse(result.response.defer.await_args.kwargs.get('thinking',False))
        self.assertEqual(sp.rows('SELECT * FROM expenses WHERE user_id=?',('42',)),[])
        self.assertEqual(len(sp.rows('SELECT * FROM expenses WHERE user_id=?',('43',))),1)
        self.assertEqual(len(sp.rows('SELECT * FROM assets WHERE user_id=?',('42',))),1)
        self.assertEqual(sp.rows('SELECT * FROM spending_onboarding'),[])
        ctx=SimpleNamespace(author=SimpleNamespace(id=43),send=AsyncMock())
        with patch.object(sp,'clear',wraps=sp.clear) as cleared:
            await Spending.clear.callback(self.cog,ctx,'yes');cleared.assert_called_once_with('43')

    async def test_timeout_and_failures_are_safe(self):
        self.seed();page=await self.page()
        event=await self.press(page,'刪除我的生活資料')
        confirm=event.response.send_message.await_args.kwargs['view']
        await confirm.on_timeout()
        await self.press(confirm,'確認永久刪除')
        self.assertEqual(len(sp.month_expenses('42','2026-09')),1)
        with patch('life_privacy.export_life',side_effect=RuntimeError('SECRET C:/private/data.db')):
            event=await self.press(page,'匯出我的生活資料')
            self.assertNotIn('SECRET',str(event.followup.send.await_args))
        event=await self.press(page,'刪除我的生活資料')
        confirm=event.response.send_message.await_args.kwargs['view']
        with patch.object(sp,'clear',side_effect=RuntimeError('SECRET')):
            event=await self.press(confirm,'確認永久刪除')
            self.assertNotIn('SECRET',str(event.edit_original_response.await_args))
            self.assertIsNone(event.edit_original_response.await_args.kwargs['view'])

    async def test_full_life_scope_and_atomic_failure(self):
        self.seed()
        source=sp.add_payment_source('42','測試支付')
        sp.set_category('42','測試分類',True)
        sp.set_budget('42','2026-09','總額','500')
        sp.set_reminders('42',[80,100])
        sp.save_shortcut('42','測試捷徑','測試分類',source,'用途','10')
        sp.add_recurring('42','固定','測試固定','5','餐飲','2026-09')
        with sp.transaction() as conn:
            conn.execute("CREATE TRIGGER prevent_privacy_test_delete BEFORE DELETE ON budgets BEGIN SELECT RAISE(ABORT,'private failure'); END")
        with self.assertRaises(Exception):sp.clear('42')
        self.assertEqual(len(sp.month_expenses('42','2026-09')),1)
        self.assertEqual(len(sp.rows('SELECT * FROM expense_actions WHERE user_id=?',('42',))),1)
        with sp.transaction() as conn:conn.execute('DROP TRIGGER prevent_privacy_test_delete')
        sp.clear('42')
        for table in ('expenses','expense_actions','budgets','recurring_expenses','spending_notices','spending_users','spending_categories','spending_settings','payment_sources','spending_shortcuts','spending_onboarding'):
            self.assertEqual(sp.rows(f'SELECT * FROM {table} WHERE user_id=?',('42',)),[],table)
        self.assertEqual(len(sp.month_expenses('43','2026-09')),1)
        self.assertEqual(len(sp.rows('SELECT * FROM assets')),1)

    async def test_private_menu_rejects_other_owner_and_transport_failure_closes_file(self):
        i=interaction();await ui.open_dashboard_tools(self.view,i,'設定')
        menu=i.response.send_message.await_args.kwargs['view']
        other=interaction(43);await choose(menu,'我的資料與隱私').callback(other)
        self.assertTrue(other.response.send_message.await_args.kwargs['ephemeral'])
        self.assertNotIn('embed',other.response.send_message.await_args.kwargs)
        page=await self.page();event=interaction();files=[]
        async def fail(*args,**kwargs):
            if 'file' in kwargs:
                files.append(kwargs['file']);raise RuntimeError('SECRET')
        event.followup.send.side_effect=fail
        await self.press(page,'匯出我的生活資料',event)
        self.assertTrue(files[0].fp.closed)
        self.assertNotIn('SECRET',str(event.followup.send.await_args))
