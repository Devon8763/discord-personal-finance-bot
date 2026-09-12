from form_helpers import fill
"""Real Discord components, mocked transport, isolated ledger."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import db
import spending as sp
import discord
from dashboard import Dashboard, EntryModal, start_entry


def interaction(owner=42):
    return SimpleNamespace(user=SimpleNamespace(id=owner),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(),
            send_modal=AsyncMock(), defer=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())


def choose(view, value):
    select = next(c for c in view.children if isinstance(c, discord.ui.Select))
    index = next(i for i, (_, v) in enumerate(view.items[view.page*view.page_size:(view.page+1)*view.page_size]) if v == value)
    select._values = [str(index)]
    return select


class PhaseOneUI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(db, 'DB_NAME', str(Path(self.temp.name)/'ui.db'))
        patcher.start(); self.addCleanup(patcher.stop)
        clock = patch('spending.today', return_value=date(2026, 9, 10))
        clock.start(); self.addCleanup(clock.stop)
        db.init_db()
        self.sent = AsyncMock()
        self.cog = SimpleNamespace(notify=AsyncMock(),
            interaction_context=lambda i: SimpleNamespace(send=i.followup.send))
        self.view = Dashboard(self.cog, 42)

    async def test_quick_flow_add_missing_category_source_then_today_and_save_once(self):
        from selection_ui import NewCategory, NewPaymentSource
        for cat in sp.category_names('42'):
            sp.set_category('42', cat, False)
        i = interaction()
        await start_entry(self.view, i, 'expense')
        from form_ui import MORE
        entry=i.response.send_modal.await_args.args[0]
        fill(entry.fields['amount'],'80.25');fill(entry.fields['note'],'早餐')
        fill(entry.fields['category'],MORE);await entry.on_submit(i)
        picker=i.response.send_message.await_args.kwargs['view']
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        modal=NewCategory(picker);modal.name._value='早餐';await modal.on_submit(i)
        await choose(picker,'早餐').callback(i)
        entry=i.response.send_modal.await_args.args[0]
        fill(entry.fields['payment'],MORE);await entry.on_submit(i)
        sources=i.response.send_message.await_args.kwargs['view']
        self.assertIn('現金',[c.label for c in sources.children if isinstance(c,discord.ui.Button)])
        modal=NewPaymentSource(sources);modal.name._value='街口支付';await modal.on_submit(i)
        source=next(p['id'] for p in sp.payment_sources('42') if p['name']=='街口支付')
        await choose(sources,source).callback(i)
        entry=i.response.send_modal.await_args.args[0]
        self.assertEqual(entry.fields['amount'].value,'80.25')
        await entry.on_submit(i)
        await entry.on_submit(i)
        rows = sp.month_expenses('42', '2026-09')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['cents'], rows[0]['payment_source_name'], rows[0]['kind']), (8025, '街口支付', 'consumption'))
        self.assertTrue(i.followup.send.await_args.kwargs.get('ephemeral', True))

    async def test_entry_and_payment_modals_reject_other_owner(self):
        from selection_ui import PaymentPicker, NewPaymentSource
        picker = PaymentPicker(42, AsyncMock())
        other = interaction(43)
        self.assertFalse(await picker.interaction_check(other))
        modal = NewPaymentSource(picker); modal.name._value = '偷建'
        await modal.on_submit(other)
        modal = EntryModal(self.view, 'expense', {'date':'2026-09-10', 'category':'餐飲'})
        fill(modal.fields['amount'],'99'); fill(modal.fields['note'],'偷寫')
        await modal.on_submit(other)
        self.assertEqual(sp.rows('SELECT * FROM expenses'), [])
        self.assertNotIn('偷建', [p['name'] for p in sp.payment_sources('43')])
        self.assertTrue(other.response.send_message.await_args.kwargs['ephemeral'])

    async def test_manage_opens_single_record_modal_and_edits_all_fields(self):
        from dashboard import EditExpenseModal, open_accounts
        source = sp.add_payment_source('42', '玉山信用卡')
        key = sp.add('42', 100, '餐飲', '午餐')
        sp.add('43', 999, '餐飲', '他人用途')
        i = interaction()
        await open_accounts(self.view, i, mode="list")
        picker = i.response.send_message.await_args.kwargs['view']
        self.assertEqual([value for _, value in picker.items], [key])
        await choose(picker, key).callback(i)
        modal = i.response.send_modal.await_args.args[0]
        for name, value in dict(amount='250.50', category='交通', note='車票', date='2026-08-31', payment='玉山信用卡').items():
            fill(modal.fields[name],value)
        await modal.on_submit(i)
        row = sp.get_expense('42', key)
        self.assertEqual((row['cents'], row['category'], row['note'], row['spent_on'], row['payment_source_id']), (25050, '交通', '車票', '2026-08-31', source))
        self.assertEqual(sp.month_report('42')['total'], 0)
        stale = EditExpenseModal(self.view, row)
        sp.edit('42', key, 1, '交通', '其他表單', '2026-08-31')
        await stale.on_submit(i)
        self.assertEqual(sp.get_expense('42', key)['cents'], 100)
        self.assertIn('已變動', i.followup.send.await_args.args[0])
        before = sp.get_expense('42', key)
        await EditExpenseModal(self.view, before).on_submit(interaction(43))
        self.assertEqual(sp.get_expense('42', key), before)

    async def test_source_management_and_large_chart_pagination_private(self):
        from selection_ui import PaymentPicker, NewPaymentSource
        from dashboard import Charts
        picker = PaymentPicker(42, manage=True)
        for n in range(31):
            source = sp.add_payment_source('42', f'來源{n}')
            sp.add('42', n+1, '餐飲', '用途', payment_source_id=source)
        picker.reload()
        self.assertEqual(len(picker.children[0].options), 25)
        i = interaction()
        first = sp.payment_sources('42')[2]
        await choose(picker, first['id']).callback(i)
        actions = i.response.edit_message.await_args.kwargs['view']
        await next(b for b in actions.children if b.label == '修改名稱').callback(i)
        modal = i.response.send_modal.await_args.args[0]
        modal.name._value = '新名稱'; await modal.on_submit(i)
        self.assertIn('新名稱', [p['name'] for p in sp.payment_sources('42')])
        await choose(picker, first['id']).callback(i)
        actions = i.response.edit_message.await_args.kwargs['view']
        await next(b for b in actions.children if b.label == '停用').callback(i)
        self.assertNotIn(first['id'], [p['id'] for p in sp.payment_sources('42')])
        chart = Charts(42, '2026-09')
        chart.kind = 'payments'
        pages = []
        for n in range(4):
            chart.page = n
            embed = chart.render()
            self.assertLessEqual(len(embed), 6000)
            self.assertIn('僅依已記錄資料統計', embed.footer.text)
            pages.extend(f.name for f in embed.fields)
        self.assertEqual(len(pages), 31)
        self.assertIn('來源0', pages)  # Historical name, even after rename/disable.
        self.assertFalse(await chart.interaction_check(interaction(43)))
        empty = Charts(99, '2026-09').render()
        self.assertIn('尚無', empty.description)

    async def test_account_pagination_preserves_month_button_and_full_details(self):
        from dashboard import open_accounts
        for n in range(8):
            sp.add('42', n+1, '餐飲', '完整用途'*35 + str(n))
        i = interaction()
        await open_accounts(self.view, i, mode="list")
        picker = i.response.send_message.await_args.kwargs['view']
        embed = i.response.send_message.await_args.kwargs['embed']
        self.assertEqual(len(embed.fields), 6)
        self.assertIn('完整用途'*35 + '7', embed.fields[0].value)
        self.assertIn('未指定', embed.fields[0].value)
        await next(c for c in picker.children if isinstance(c, discord.ui.Button) and c.label=='下一頁').callback(i)
        embed = i.response.edit_message.await_args.kwargs['embed']
        self.assertEqual(len(embed.fields), 2)
        self.assertIn('切換月份', [c.label for c in picker.children if isinstance(c, discord.ui.Button)])
        await choose(picker, 1).callback(i)
        self.assertEqual(i.response.send_modal.await_args.args[0].expense['id'], 1)

    async def test_date_pagination_and_cash_unspecified_quick_choices(self):
        from selection_ui import choose_date, PaymentPicker
        i = interaction()
        await choose_date(i, AsyncMock())
        picker = i.response.send_message.await_args.kwargs['view']
        await next(c for c in picker.children if isinstance(c, discord.ui.Button) and c.label=='下一頁').callback(i)
        self.assertIn('今天', [c.label for c in picker.children if isinstance(c, discord.ui.Button)])
        self.assertIn('昨天', [c.label for c in picker.children if isinstance(c, discord.ui.Button)])
        for name in ('現金', '未指定'):
            await start_entry(self.view, i, 'expense')
            modal=i.response.send_modal.await_args.args[0]
            fill(modal.fields['category'],'餐飲');fill(modal.fields['payment'],name)
            fill(modal.fields['date'],'2026-09-09')
            fill(modal.fields['amount'],'1');fill(modal.fields['note'],name)
            await modal.on_submit(i)
        self.assertEqual({r['payment_source_name'] for r in sp.month_expenses('42','2026-09')}, {'現金','未指定'})
        self.assertTrue(all(r['spent_on']=='2026-09-09' for r in sp.month_expenses('42','2026-09')))

    async def test_dashboard_chart_entry_is_private_and_matches_totals(self):
        sp.add('42', 12, '交通', '車票')
        sp.add('43', 999, '交通', '其他人')
        i = interaction()
        await next(b for b in self.view.children if b.label=='更多').callback(i)
        await next(b for b in self.view.children if b.label=='洞察').callback(i)
        menu=i.response.send_message.await_args.kwargs['view']
        await choose(menu,'支出圖表').callback(i)
        self.assertTrue(i.response.defer.await_args.kwargs['ephemeral'])
        self.assertTrue(i.followup.send.await_args.kwargs['ephemeral'])
        embed=i.followup.send.await_args.kwargs['embed']
        self.assertIn('12 元', embed.fields[0].value)
        self.assertNotIn('999', str(embed.to_dict()))
        chart=i.followup.send.await_args.kwargs['view']
        await next(b for b in chart.children if b.label=='最近六個月支出趨勢').callback(i)
        self.assertEqual(len(i.response.edit_message.await_args.kwargs['embed'].fields),6)
