from form_helpers import fill
"""Offline integration: real discord.py parsing, no login or real database."""
import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, patch
import db
import spending as sp

AVAILABLE = importlib.util.find_spec('discord') is not None


@unittest.skipUnless(AVAILABLE, '需要 discord.py 套件才能執行離線整合測試')
class DiscordSpendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_command_parser_and_read_only_query(self):
        fake = ModuleType('scraper')
        fake.get_price = lambda symbol: None
        original_scraper = sys.modules.get('scraper')
        sys.modules['scraper'] = fake
        try:
            import bot as app
        finally:
            if original_scraper is None:
                sys.modules.pop('scraper',None)
            else:
                sys.modules['scraper'] = original_scraper
        import spending_commands as ui
        with tempfile.TemporaryDirectory() as directory, patch.object(db,'DB_NAME',str(Path(directory)/'test.db')):
            db.init_db()
            with sp.transaction() as conn:
                conn.execute('INSERT INTO ai_preferences(user_id,enabled) VALUES(?,1)',('42',))
            async with app.bot as client:
                with patch.object(client.tree, 'sync', AsyncMock(return_value=[])):
                    await client.setup_hook()
                client._connection.user = SimpleNamespace(id=999)
                month = sp.today().strftime('%Y-%m')
                async def command(text):
                    message = SimpleNamespace(content=text,author=SimpleNamespace(id=42,bot=False),
                        attachments=[],_state=client._connection,id=123,channel=SimpleNamespace(id=456),guild=None)
                    ctx = await client.get_context(message)
                    ctx.send = AsyncMock()
                    self.assertIsNotNone(ctx.command,text)
                    await ctx.command.invoke(ctx)
                    return ctx
                await command(f'!預算 {month} 總額 20000')
                await command(f'!預算 {month} 餐飲 6000')
                await command('!支出 150 餐飲 午餐 加飲料')
                await command(f'!固定新增 分期 "工作 筆電" 3000 購物 {month} 10 5')
                self.assertEqual(sp.month_report('42')['total'],3150)
                ctx = await command('!月報')
                self.assertIn('3,150',ctx.send.await_args.args[0])
                help_view = app.HelpView()
                self.assertIn('💰 生活記帳',[b.label for b in help_view.children])
                self.assertEqual(len(app.MainHelpView().children),2)
                cog = client.get_cog('Spending')
                from selection_ui import Picker,month_items,Reminders
                picker=Picker(42,[(str(n),n) for n in range(30)],AsyncMock(),'分類',True)
                self.assertEqual(len(picker.children[0].options),25)
                picker.page=1;picker.build()
                self.assertEqual(len(picker.children[0].options),5)
                self.assertEqual(len(month_items(42,recurring=True)),2)
                self.assertEqual(len(Reminders(cog,42).children),5)
                from investment_ui import InvestmentPanel,InvestmentModal
                panel=InvestmentPanel(app,42)
                modal=InvestmentModal(panel,'buy')
                for key,value in {'symbol':'AAPL','price':'200','shares':'2'}.items():
                    fill(modal.fields[key],value)
                fake_i=SimpleNamespace(user=SimpleNamespace(id=42),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
                await modal.on_submit(fake_i)
                self.assertEqual(sp.rows('SELECT shares FROM assets WHERE user_id=?',('42',)),[{'shares':2.0}])
                for tab in ('股票','基金','觀察','歷史','AI'):
                    panel.tab=tab
                    self.assertLessEqual(len(panel.render()),6000)
                view = ui.SpendingView(cog)
                self.assertEqual(len(ui.RecurringModal(cog,'訂閱',owner=42).children),5)
                self.assertEqual(len(ui.RecurringModal(cog,'分期',owner=42).children),5)
                self.assertIn('項目名稱',[f.text for f in ui.RecurringModal(cog,'訂閱',owner=42).children])
                interaction = SimpleNamespace(user=SimpleNamespace(id=42),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
                await view.month.callback(interaction)
                self.assertTrue(interaction.followup.send.await_args.kwargs['ephemeral'])
                from dashboard import Dashboard,EntryModal,progress,card
                dashboard=Dashboard(cog,42)
                selected=EntryModal(dashboard,'expense',{'category':'餐飲','date':sp.today().isoformat()})
                self.assertEqual(set(selected.fields),{'amount','note','category','payment','date'})
                selected=EntryModal(dashboard,'budget',{'category':'總額','month':month})
                self.assertEqual(set(selected.fields),{'amount','category','month'})
                self.assertEqual(len(ui.RecurringModal(cog,'訂閱',{'category':'娛樂','month':month},42).children),5)
                self.assertEqual(progress(150),'■■■■■■■■■■')
                self.assertEqual(progress(30),'■■■□□□□□□□')
                self.assertTrue(await dashboard.interaction_check(interaction))
                stranger=SimpleNamespace(user=SimpleNamespace(id=43),response=SimpleNamespace(send_message=AsyncMock()))
                self.assertFalse(await dashboard.interaction_check(stranger))
                for tab in ('今天','帳目','更多','預算','固定負擔','AI'):
                    dashboard.tab=tab
                    embed=dashboard.render()
                    self.assertLessEqual(len(embed),6000)
                    self.assertEqual([b.label for b in dashboard.children if b.row==0],['今天','帳目','更多'])
                    self.assertLessEqual(len(dashboard.children),25)
                for i in range(7):
                    sp.add('page-test',1,'餐飲',str(i))
                embed,page,pages=card('page-test',month,'清單',1)
                self.assertEqual((page,pages),(1,2))
                self.assertEqual(len(embed.fields),1)
                modal=EntryModal(dashboard,'expense')
                for key,value in dict(date=sp.today().isoformat(),amount='25',category='餐飲',note='表單測試').items():
                    fill(modal.fields[key],value)
                await modal.on_submit(interaction)
                self.assertEqual(sp.month_report('42')['total'],3175)
                # Restore test account amount for the existing query assertions.
                action,_=sp.undo('42');sp.undo('42',action)
                before = sp.rows('SELECT * FROM expenses')
                plan = json.dumps(dict(intent='spending',month=month,category='餐飲',unit='月',count=3))
                with patch.object(ui,'complete',AsyncMock(side_effect=[plan,'餐飲已記錄150元'])):
                    answer = await command('!問 這個月餐飲花了多少？')
                    body = answer.send.await_args.args[0]
                    self.assertIn('150 元',body)
                    self.assertNotIn('.00',body)
                    self.assertNotIn('當週',body)
                    self.assertEqual(body.count(ui.NOTE),1)
                with patch.object(ui,'complete',AsyncMock(return_value='')):
                    answer = await command('!問 這個月花多少？')
                    self.assertIn('AI 回覆未完成',answer.send.await_args.args[0])
                self.assertEqual(sp.rows('SELECT * FROM expenses'),before)
                plan = json.dumps(dict(intent='unsupported',month='',category='',unit='月',count=3))
                with patch.object(ui,'complete',AsyncMock(return_value=plan)):
                    await command('!問 幫我刪除所有支出')
                self.assertEqual(sp.rows('SELECT * FROM expenses'),before)
                modal = ui.RecurringModal(cog,'訂閱',owner=42)
                for field,value in ((modal.item_name,'影音平台'),(modal.amount,'390'),(modal.category,'娛樂'),(modal.plan,('訂閱',month))):
                    fill(field,value)
                await modal.on_submit(interaction)
                self.assertEqual(sp.rows("SELECT name,periods FROM recurring_expenses WHERE name='影音平台'"),[{'name':'影音平台','periods':0}])
                sp.set_budget('42',month,'餐飲','2000')
                sp.set_budget('42',month,'總額','4000')
                sp.add('42',1000,'其他','觸發提醒')
                recipient = SimpleNamespace(send=AsyncMock())
                with patch.object(client,'get_user',return_value=recipient):
                    await cog.monthly()
                    delivered_count = recipient.send.await_count
                    self.assertGreater(delivered_count,0)
                    await cog.monthly()
                    self.assertEqual(recipient.send.await_count,delivered_count)
                await client.remove_cog('Spending')
