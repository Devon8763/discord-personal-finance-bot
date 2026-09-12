from form_helpers import fill
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch
import test_phase1_ui as ui_tests
from test_phase1_ui import interaction
import test_spending_phase1 as data_tests
import spending as sp
import db
import discord
import sys
from types import SimpleNamespace, ModuleType


class RoundTwoData(unittest.TestCase):
    setUp = data_tests.PaymentTests.setUp
    # Reuse the isolated database setup, not the parent tests.
    def test_shortcuts_order_edit_disable_and_isolation(self):
        source=sp.add_payment_source('a','信用卡')
        first=sp.save_shortcut('a','早餐','餐飲',source,'蛋餅',None)
        second=sp.save_shortcut('a','午餐','餐飲',source,'便當','120')
        self.assertEqual(sp.recent_expenses('a'),[])
        sp.move_shortcut('a',second,-1)
        self.assertEqual([r['id'] for r in sp.shortcuts('a')],[second,first])
        sp.save_shortcut('a','午餐改','餐飲',source,'麵','130',key=second)
        self.assertEqual(sp.shortcut('a',second)['cents'],13000)
        for operation in (lambda:sp.shortcut('b',first),lambda:sp.move_shortcut('b',first,1),lambda:sp.disable_shortcut('b',first),lambda:sp.save_shortcut('b','偷改','餐飲',source,'x',1,key=first)):
            with self.assertRaises(ValueError): operation()
        sp.disable_shortcut('a',first)
        self.assertEqual([r['id'] for r in sp.shortcuts('a')],[second])
        self.assertEqual(len(sp.shortcuts('a',True)),2)
        sp.disable_payment_source('a',source)
        with self.assertRaises(ValueError): sp.save_shortcut('a','錯','餐飲',source,'用途',1)

    def test_recent_filters_and_review_ranges(self):
        for n in range(8): sp.add('a',n+1,'餐飲',str(n),'2026-09-10')
        sp.add('b',999,'餐飲','他人')
        sp.add('a',999,'餐飲','撤銷'); action,_=sp.undo('a');sp.undo('a',action)
        sp.add_recurring('a','固定','租金',100,'居住','2026-09');sp.sync_recurring('a')
        self.assertEqual([r['note'] for r in sp.recent_expenses('a')],['7','6','5','4','3','2'])
        sp.add('a',10,'餐飲','上週','2026-09-03')
        sp.add('a',999,'餐飲','上週週五排除','2026-09-04')
        review=sp.review('a','week')
        self.assertEqual((review['current']['start'],review['current']['end']),('2026-09-07','2026-09-10'))
        self.assertEqual((review['previous']['start'],review['previous']['end']),('2026-08-31','2026-09-03'))
        self.assertEqual(review['difference'],-74)
        self.assertIsNone(sp.review('empty','month')['difference'])
        sp.set_budget('a','2026-09','總額',2000)
        self.assertEqual(sp.review('a','month')['current']['budgets'][0]['budget'],2000)
        with patch('spending.today',return_value=date(2026,3,31)):
            report=sp.review('a','month')
        self.assertEqual(report['comparison']['end'],'2026-03-28')
        self.assertEqual(report['previous']['end'],'2026-02-28')

    def test_upgrade_clear_and_history_preserved(self):
        source=sp.add_payment_source('a','來源')
        key=sp.add('a',10,'餐飲','歷史',payment_source_id=source)
        shortcut=sp.save_shortcut('a','早餐','餐飲',source,'用途',None)
        with sp.transaction() as conn:
            conn.execute("INSERT INTO assets(user_id,symbol,buy_price,shares) VALUES('a','2330',100,2)")
        before=sp.get_expense('a',key)
        db.init_db();db.init_db()
        sp.rename_payment_source('a',source,'新名')
        sp.disable_shortcut('a',shortcut)
        self.assertEqual(sp.get_expense('a',key),before)
        self.assertEqual(sp.rows('SELECT shares FROM assets'),[{'shares':2.0}])
        sp.clear('a')
        self.assertEqual(sp.shortcuts('a',True),[])
        self.assertEqual(sp.rows('SELECT shares FROM assets'),[{'shares':2.0}])

    def test_reviews_empty_comparison_scope_and_no_mutations(self):
        sp.add('a',100,'餐飲','上月','2026-08-05')
        sp.add('a',999,'餐飲','上月範圍外','2026-08-11')
        source=sp.add_payment_source('a','付款卡')
        sp.add('a',150,'餐飲','本月',payment_source_id=source)
        sp.add('b',1000,'餐飲','他人')
        sp.set_budget('a','2026-09','總額',100)
        before=sp.rows('SELECT * FROM expenses')
        notices=sp.rows('SELECT * FROM spending_notices')
        data=sp.review('a','month')
        self.assertEqual(data['difference'],50)
        self.assertEqual(data['payments'],[{'payment_source_name':'付款卡','cents':15000}])
        self.assertEqual(data['current']['budgets'][0]['remaining'],-50)
        self.assertEqual(sp.rows('SELECT * FROM expenses'),before)
        self.assertEqual(sp.rows('SELECT * FROM spending_notices'),notices)
        self.assertIsNone(sp.review('b','month')['difference'])
        with patch('spending.today',return_value=date(2026,9,7)):
            data=sp.review('a','week')
        self.assertEqual(data['current']['start'],data['current']['end'])


class RoundTwoUI(unittest.IsolatedAsyncioTestCase):
    setUp = ui_tests.PhaseOneUI.setUp
    async def test_prefilled_confirmation_and_disabled_source(self):
        from lifestyle_ui import ConfirmExpense
        source=sp.add_payment_source('42','卡')
        key=sp.add('42',15,'餐飲','早餐','2026-09-01',source)
        original=sp.get_expense('42',key)
        form=ConfirmExpense(self.view,original)
        self.assertEqual(form.fields['date'].value,'2026-09-10')
        self.assertEqual(len(sp.recent_expenses('42')),1)
        await form.on_submit(interaction(43))
        self.assertEqual(len(sp.recent_expenses('42')),1)
        sp.disable_payment_source('42',source)
        await form.on_submit(interaction())
        self.assertEqual(len(sp.recent_expenses('42')),1)
        fill(form.fields['payment'],'現金')
        await form.on_submit(interaction())
        self.assertEqual(len(sp.recent_expenses('42')),2)
        await form.on_submit(interaction())
        self.assertEqual(len(sp.recent_expenses('42')),2)

    async def test_shortcut_forms_manage_and_confirm_only(self):
        from lifestyle_ui import Shortcuts,ShortcutForm
        from test_shortcut_dropdowns import selections
        picker=Shortcuts(self.view)
        cash=next(p['id'] for p in sp.payment_sources('42') if p['name']=='現金')
        form=ShortcutForm(picker)
        fill(form.fields['category'],'餐飲');fill(form.fields['payment'],'現金')
        for name,value in dict(name='早餐',note='蛋餅',amount='').items():
            fill(form.fields[name],value)
        await form.on_submit(interaction(43))
        self.assertEqual(sp.shortcuts('42'),[])
        i=interaction();await form.on_submit(i)
        row=sp.shortcuts('42')[0]
        self.assertIsNone(row['cents'])
        await picker.chosen_shortcut(i,row['id'])
        confirm=await selections(i)
        self.assertEqual(sp.recent_expenses('42'),[])
        self.assertEqual(confirm.fields['date'].value,'2026-09-10')
        fill(confirm.fields['amount'],'40')
        await confirm.on_submit(i)
        self.assertEqual(sp.month_report('42')['total'],40)
        await picker.toggle(i)
        await picker.chosen_shortcut(i,row['id'])
        actions=i.response.edit_message.await_args.kwargs['view']
        self.assertFalse(await actions.interaction_check(interaction(43)))
        await next(c for c in actions.children if c.label=='修改').callback(i)
        edit=await selections(i)
        fill(edit.fields['name'],'早餐二');fill(edit.fields['amount'],'55')
        await edit.on_submit(i)
        self.assertEqual(sp.shortcut('42',row['id'])['cents'],5500)
        self.assertEqual(sp.month_report('42')['total'],40)
        await picker.chosen_shortcut(i,row['id'])
        actions=i.response.edit_message.await_args.kwargs['view']
        await next(c for c in actions.children if c.label=='停用').callback(i)
        self.assertEqual(sp.shortcuts('42'),[])

    async def test_recent_and_reviews_private_without_ai(self):
        from lifestyle_ui import open_recent,open_reviews
        sp.add('42',10,'餐飲','自己')
        sp.add('43',999,'餐飲','他人')
        i=interaction()
        await open_recent(self.view,i)
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        picker=i.response.send_message.await_args.kwargs['view']
        self.assertEqual(len(picker.items),1)
        self.assertFalse(await picker.interaction_check(interaction(43)))
        await picker.chosen(i,picker.items[0][1])
        self.assertEqual(i.response.send_modal.await_args.args[0].fields['note'].value,'自己')
        with patch('spending_commands.complete',AsyncMock(side_effect=AssertionError('不得呼叫AI'))):
            await open_reviews(self.view,i)
            self.assertTrue(i.followup.send.await_args.kwargs['ephemeral'])
            view=i.followup.send.await_args.kwargs['view']
            self.assertFalse(await view.interaction_check(interaction(43)))
            await next(c for c in view.children if c.label=='本月回顧').callback(i)
            embed=i.response.edit_message.await_args.kwargs['embed']
            self.assertIn('10 元',embed.description)
            self.assertIn('僅依已記錄資料統計',embed.footer.text)
            self.assertNotIn('999',str(embed.to_dict()))

    async def test_slash_registration_sync_private_paths_and_investment_ai(self):
        fake=ModuleType('scraper');fake.get_price=lambda symbol:None
        with patch.dict(sys.modules,{'scraper':fake}):
            import bot as app
        async with app.bot as client:
            with patch.object(client.tree,'sync',AsyncMock(return_value=[])) as sync:
                await client.setup_hook()
                sync.assert_awaited_once()
            try:
                cog=client.get_cog('Spending')
                self.assertEqual(client.tree.get_commands(),[])
                i=interaction()
                from dashboard import Dashboard,start_entry
                await start_entry(Dashboard(cog,42),i,'expense')
                view=i.response.send_modal.await_args.args[0]
                self.assertEqual(view.owner,42)
                self.assertIsInstance(view.fields['category'],discord.ui.Select)
                await view.on_submit(interaction(43))
                self.assertEqual(sp.recent_expenses('42'),[])
                menu=app.MainHelpView()
                await next(c for c in menu.children if c.label=='💰 生活記帳').callback(i)
                self.assertIsInstance(i.followup.send.await_args.kwargs['view'],Dashboard)
                self.assertTrue(i.followup.send.await_args.kwargs['ephemeral'])
                self.assertEqual({c.label for c in app.MainHelpView().children},{'💰 生活記帳','📈 投資紀錄'})
                self.assertNotIn('🤖 AI 摘要',[c.label for c in app.HelpView().children])
                from investment_ui import InvestmentPanel
                panel=InvestmentPanel(app,42);panel.tab='AI';panel.render()
                self.assertFalse(await panel.interaction_check(interaction(43)))
                with patch.object(app,'build_analysis',AsyncMock()) as build:
                    await next(c for c in panel.children if c.label=='AI 投資摘要').callback(i)
                    self.assertEqual(build.await_args.args[0].author.id,42)
                    self.assertTrue(i.response.defer.await_args.kwargs['ephemeral'])
                await cog.cog_app_command_error(i,RuntimeError('private-secret'))
                self.assertNotIn('private-secret',str(i.response.send_message.await_args))
            finally:
                await client.remove_cog('Spending')

    async def test_sync_failure_log_is_safe_and_text_commands_survive(self):
        fake=ModuleType('scraper');fake.get_price=lambda symbol:None
        with patch.dict(sys.modules,{'scraper':fake}):
            import bot as app
        async with app.bot as client:
            with patch.object(client.tree,'sync',AsyncMock(side_effect=OSError('private-secret'))),patch('builtins.print') as log:
                await client.setup_hook()
                text=' '.join(str(c) for c in log.call_args_list)
                self.assertNotIn('private-secret',text)
                self.assertIn('!help',text)
            self.assertIsNotNone(client.get_command('支出'))
            self.assertIsNotNone(client.get_command('analyze'))
            await client.remove_cog('Spending')

    async def test_disabled_shortcut_source_and_category_revalidated_at_save(self):
        from lifestyle_ui import Shortcuts
        from test_shortcut_dropdowns import selections
        source=sp.add_payment_source('42','卡')
        key=sp.save_shortcut('42','早餐','餐飲',source,'蛋餅',10)
        picker=Shortcuts(self.view);i=interaction()
        await picker.chosen_shortcut(i,key)
        modal=await selections(i,payment='卡')
        sp.disable_payment_source('42',source)
        await modal.on_submit(i)
        self.assertEqual(sp.recent_expenses('42'),[])
        self.assertIn('已停用',i.followup.send.await_args.args[0])
        await picker.chosen_shortcut(i,key)
        modal=await selections(i)
        sp.set_category('42','餐飲',False)
        await modal.on_submit(i)
        self.assertEqual(sp.recent_expenses('42'),[])
        sp.disable_shortcut('42',key)
        await modal.on_submit(i)
        self.assertEqual(sp.recent_expenses('42'),[])

    async def test_shortcut_and_review_pagination_preserve_actions_and_all_sources(self):
        from lifestyle_ui import Shortcuts,Reviews
        for n in range(27):
            source=sp.add_payment_source('42',f'來源{n}')
            sp.save_shortcut('42',f'捷徑{n}','餐飲',source,'用途',1)
            sp.add('42',1,'餐飲','用途',payment_source_id=source)
        picker=Shortcuts(self.view);i=interaction()
        await next(c for c in picker.children if isinstance(c,discord.ui.Button) and c.label=='下一頁').callback(i)
        self.assertEqual(len(picker.children[0].options),2)
        self.assertIn('＋新增捷徑',[c.label for c in picker.children if isinstance(c,discord.ui.Button)])
        self.assertIn('管理／排序／停用',[c.label for c in picker.children if isinstance(c,discord.ui.Button)])
        view=Reviews(42);view.unit='month'
        names=[]
        for page in range(4):
            view.page=page;embed=view.render()
            names.extend(f.name for f in embed.fields)
            self.assertLessEqual(len(embed),6000)
        self.assertEqual(len([n for n in names if n.startswith('付款來源：')]),27)
        empty=Reviews(43).render()
        self.assertIn('尚無',empty.description)

    async def test_empty_month_budget_does_not_infer_zero_usage(self):
        from lifestyle_ui import Reviews
        sp.set_budget('42','2026-09','總額',1000)
        view=Reviews(42);view.unit='month'
        budget=next(f for f in view.render().fields if f.name=='預算：總額')
        self.assertIn('1,000',budget.value)
        self.assertIn('資料不足',budget.value)
        self.assertNotIn('0%',budget.value)
        self.assertNotIn('剩餘',budget.value)
