import unittest
from unittest.mock import AsyncMock, patch
from datetime import date
import db
import spending as sp
import dashboard as ui
import test_phase1_ui as fixtures
from test_phase1_ui import interaction, choose
from form_helpers import fill


class OnboardingClosingTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def test_new_user_open_and_persistent_dismissal(self):
        self.cog.prepare=AsyncMock()
        for label in ('先跳過','完成導覽'):
            i=interaction();await ui.open_dashboard(self.cog,i)
            guide=i.followup.send.await_args_list[0].kwargs['view']
            self.assertIsInstance(guide,ui.Onboarding)
            self.assertIsInstance(i.followup.send.await_args_list[-1].kwargs['view'],ui.Dashboard)
            self.assertTrue(all(c.kwargs['ephemeral'] for c in i.followup.send.await_args_list))
            again=interaction();await ui.open_dashboard(self.cog,again)
            self.assertIsInstance(again.followup.send.await_args_list[0].kwargs['view'],ui.Onboarding)
            stranger=interaction(43)
            await next(b for b in guide.children if b.label==label).callback(stranger)
            self.assertTrue(sp.onboarding_needed('42'))
            await next(b for b in guide.children if b.label==label).callback(interaction())
            db.init_db();self.assertFalse(sp.onboarding_needed('42'))
            i=interaction();await ui.open_dashboard(self.cog,i)
            self.assertEqual(i.followup.send.await_count,1)
            with sp.transaction() as conn:conn.execute('DELETE FROM spending_onboarding')

    async def test_existing_data_does_not_interrupt_and_defaults_do_not_count(self):
        sp.payment_sources('42');self.assertTrue(sp.onboarding_needed('42'))
        for user,write in [('1',lambda:sp.add('1','10','餐飲','早餐')),
                           ('2',lambda:sp.set_budget('2','2026-09','總額','100')),
                           ('3',lambda:sp.add_payment_source('3','信用卡')),
                           ('4',lambda:sp.set_category('4','自訂',True))]:
            write();self.assertFalse(sp.onboarding_needed(user))
        self.assertTrue(sp.onboarding_needed('42'))

    async def test_shortcuts_recurring_and_repeatable_upgrade_preserve_data(self):
        source=sp.payment_sources('5')[0]['id']
        sp.save_shortcut('5','早餐','餐飲',source,'早餐')
        sp.add_recurring('6','固定','房租','100','居住','2026-09')
        self.assertFalse(sp.onboarding_needed('5'))
        self.assertFalse(sp.onboarding_needed('6'))
        before=sp.shortcuts('5')
        with sp.transaction() as conn:conn.execute('DROP TABLE spending_onboarding')
        db.init_db();db.init_db()
        self.assertEqual(before,sp.shortcuts('5'))
        self.assertTrue(sp.onboarding_needed('42'))

    async def test_closing_effective_records_escaping_and_pagination(self):
        for index in range(10):
            source=sp.add_payment_source('42',f'*卡{index}*')
            sp.add('42','10','餐飲','餐','2026-09-10',source)
        voided=sp.add('42','900','餐飲','撤銷','2026-09-10')
        excluded=sp.add('42','800','餐飲','非消費','2026-09-10')
        with sp.transaction() as conn:
            conn.execute('UPDATE expenses SET voided=1 WHERE id=?',(voided,))
            conn.execute("UPDATE expenses SET kind='other' WHERE id=?",(excluded,))
            conn.execute("UPDATE expenses SET source='固定' WHERE id=(SELECT min(id) FROM expenses)")
        snapshot=sp.rows('SELECT * FROM expenses')
        data=sp.monthly_closing('42','2026-09')
        self.assertEqual(data['current']['total'],100)
        self.assertEqual(data['current']['fixed'],10)
        view=ui.MonthlyClosing(42,'2026-09');embed=view.render()
        self.assertIn('\\*卡',str(embed.to_dict()))
        self.assertLessEqual(len(embed.fields),8)
        other=interaction(43);await view.children[1].callback(other)
        self.assertEqual(view.page,0)
        await view.children[1].callback(interaction());self.assertEqual(view.page,1)
        self.assertEqual(snapshot,sp.rows('SELECT * FROM expenses'))

    async def test_manual_guide_reuses_settings_and_returns_same_card(self):
        sp.dismiss_onboarding('42')
        i=interaction();await ui.open_dashboard_tools(self.view,i,'設定')
        menu=i.response.send_message.await_args.kwargs['view']
        message=AsyncMock();i.original_response=AsyncMock(return_value=message)
        await choose(menu,'重新開啟新手導覽').callback(i)
        guide=i.response.send_message.await_args.kwargs['view']
        self.assertFalse(sp.onboarding_needed('42'))
        for button in guide.children:
            stranger=interaction(43);await button.callback(stranger)
            stranger.response.send_modal.assert_not_awaited()
            stranger.response.edit_message.assert_not_awaited()
            self.assertTrue(stranger.response.send_message.await_args.kwargs['ephemeral'])
        for label in ('設定付款來源','建立常用捷徑'):
            event=interaction();await next(b for b in guide.children if b.label==label).callback(event)
            child=event.response.edit_message.await_args.kwargs['view']
            self.assertLessEqual(len(child.children),25)
            await next(b for b in child.children if getattr(b,'label',None)=='返回新手導覽').callback(event)
            self.assertIs(event.response.edit_message.await_args.kwargs['view'],guide)
        event=interaction();await next(b for b in guide.children if b.label=='設定當月預算').callback(event)
        modal=event.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal,ui.EntryModal)
        fill(modal.fields['amount'],'1000');await modal.on_submit(event)
        self.assertIs(message.edit.await_args.kwargs['view'],guide)
        self.assertEqual(sp.rows('SELECT * FROM expenses'),[])
        self.assertEqual(sp.month_report('42')['budgets'][0]['budget'],1000)

    async def test_closing_current_history_unspecified_isolated_read_only(self):
        sp.add('42','100','餐飲','早餐','2026-09-10')
        sp.add('42','40','餐飲','早餐','2026-08-10')
        sp.add('42','60','餐飲','早餐','2026-08-31')
        sp.add('43','999','餐飲','其他','2026-09-10')
        sp.set_budget('42','2026-09','總額','80')
        before=sp.rows('SELECT * FROM expenses')
        data=sp.monthly_closing('42','2026-09')
        self.assertEqual(data['difference'],60)
        self.assertEqual(data['previous']['end'],'2026-08-10')
        self.assertEqual(data['unspecified'],{'count':1,'cents':10000})
        self.assertEqual(data['current']['budgets'][0]['remaining'],-20)
        historical=sp.monthly_closing('42','2026-08')
        self.assertEqual(historical['comparison']['end'],'2026-08-31')
        self.assertEqual(historical['previous']['end'],'2026-07-31')
        self.assertIsNone(historical['difference'])
        self.assertEqual(before,sp.rows('SELECT * FROM expenses'))

    async def test_short_month_empty_budget_and_private_menu(self):
        with patch('spending.today',return_value=date(2026,3,31)):
            data=sp.monthly_closing('42','2026-03')
            self.assertEqual(data['comparison']['end'],'2026-03-28')
            self.assertEqual(data['previous']['end'],'2026-02-28')
        self.view.month='2026-08'
        i=interaction();await ui.open_dashboard_tools(self.view,i,'洞察')
        menu=i.response.send_message.await_args.kwargs['view']
        other=interaction(43);await choose(menu,'本月結帳').callback(other)
        self.assertTrue(other.response.send_message.await_args.kwargs['ephemeral'])
        await choose(menu,'本月結帳').callback(i)
        result=i.response.send_message.await_args.kwargs
        self.assertTrue(result['ephemeral'])
        self.assertEqual(result['embed'].title,'2026-08 · 月結摘要')
        self.assertIn('尚未設定',str(result['embed'].to_dict()))
        self.assertIn('資料不足',str(result['embed'].to_dict()))
