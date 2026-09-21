import unittest
from unittest.mock import patch
import discord
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction,choose
from form_helpers import fill


class SearchTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def test_partial_literal_keywords_ranges_and_order(self):
        keys=[sp.add('42',20,'餐飲',note,on) for note,on in (
            ('早餐店','2026-08-01'),('早餐','2026-09-01'),('早餐晚一筆','2026-09-01'),('午餐','2026-09-10'))]
        self.assertEqual([r['id'] for r in sp.search_expenses('42','早餐')],keys[2::-1])
        self.assertEqual([r['id'] for r in sp.search_expenses('42',start='2026-09-01')],keys[:0:-1])
        self.assertEqual([r['id'] for r in sp.search_expenses('42',end='2026-08-01')],[keys[0]])
        self.assertEqual([r['id'] for r in sp.search_expenses('42','早餐','2026-08-01','2026-09-01')],keys[2::-1])
        self.assertEqual(sp.search_expenses('42','%'),[])
        self.assertEqual(sp.search_expenses('42',"' OR 1=1 --"),[])

    async def test_invalid_conditions_rejected_before_query(self):
        for args in ({},{'keyword':'  '},{'start':'2026.09.01'},{'end':'2026-02-30'},
                     {'start':'2026/09-01'},{'start':'2026/9/01'},{'end':'2026091'},
                     {'start':'2026-09-11'},{'end':'2026-09-11'},
                     {'start':'2026-09-02','end':'2026-09-01'}):
            with self.subTest(args=args),patch.object(sp,'rows') as query:
                with self.assertRaises(ValueError):sp.search_expenses('42',**args)
                query.assert_not_called()

    async def test_all_three_date_formats_normalize_for_both_bounds(self):
        key=sp.add('42',20,'餐飲','早餐','2026-09-01')
        for value in ('2026-09-01','2026/09/01','20260901'):
            self.assertEqual([r['id'] for r in sp.search_expenses('42',start=value,end=value)],[key])
            self.assertEqual([r['id'] for r in sp.search_expenses('42',start=value)],[key])
            self.assertEqual([r['id'] for r in sp.search_expenses('42',end=value)],[key])
        for args in ({'start':'20260911'},{'end':'2026/09/11'},{'start':'20260902','end':'2026/09/01'},
                     {'start':'20260230'},{'end':'2026/02/30'},{'start':'2026-9-01'}):
            with self.subTest(args=args),self.assertRaises(ValueError):sp.search_expenses('42',**args)

    async def test_only_own_valid_manual_consumption_and_read_only(self):
        key=sp.add('42',20,'餐飲','早餐','2026-09-01')
        sp.add('43',20,'餐飲','早餐','2026-09-01')
        for field,value in (('voided',1),('kind','other'),('source','固定'),('source','訂閱'),('source','分期')):
            other=sp.add('42',20,'餐飲','早餐','2026-09-01')
            with sp.transaction() as conn:conn.execute(f'UPDATE expenses SET {field}=? WHERE id=?',(value,other))
        connect=sp.get_conn
        def readonly():
            conn=connect();conn.execute('PRAGMA query_only=ON');return conn
        with patch.object(sp,'get_conn',side_effect=readonly):
            self.assertEqual([r['id'] for r in sp.search_expenses('42','早餐')],[key])

    async def test_private_search_three_fields_pagination_and_existing_edit(self):
        from dashboard import EditExpenseModal
        keys=[sp.add('42',20,'餐飲',f'早餐店{n}','2026-09-01') for n in range(8)]
        self.view.tab='帳目';self.view.render()
        i=interaction();await next(b for b in self.view.children if getattr(b,'label',None)=='帳目工具').callback(i)
        menu=i.response.send_message.await_args.kwargs['view']
        await choose(menu,'搜尋帳目').callback(i)
        modal=i.response.send_modal.await_args.args[0]
        self.assertEqual(set(modal.fields),{'keyword','start','end'})
        self.assertTrue(all(not f.required for f in modal.fields.values()))
        fill(modal.fields['keyword'],'早餐')
        await modal.on_submit(interaction(43))
        await modal.on_submit(i)
        self.assertTrue(i.followup.send.await_args.kwargs['ephemeral'])
        result=i.followup.send.await_args.kwargs['view']
        self.assertEqual(len(result.page_content()['embed'].fields),6)
        self.assertFalse(await result.interaction_check(interaction(43)))
        select=next(c for c in result.children if isinstance(c,discord.ui.Select));select._values=['0']
        stranger=interaction(43);await select.callback(stranger)
        stranger.response.send_modal.assert_not_awaited()
        await select.callback(i)
        edit=i.response.send_modal.await_args.args[0]
        self.assertIsInstance(edit,EditExpenseModal)
        self.assertEqual(edit.expense['id'],keys[-1])
        fill(edit.fields['note'],'修改用途');await edit.on_submit(i)
        self.assertEqual(sp.get_expense('42',keys[-1])['note'],'修改用途')
        await next(b for b in result.children if getattr(b,'label',None)=='下一頁').callback(i)
        self.assertEqual(len(result.page_content()['embed'].fields),2)

    async def test_empty_results_and_stale_record_cannot_open_editor(self):
        from dashboard import SearchExpensesModal
        modal=SearchExpensesModal(self.view);i=interaction()
        with patch.object(sp,'rows') as query:
            await modal.on_submit(i);query.assert_not_called()
        fill(modal.fields['keyword'],'不存在');await modal.on_submit(i)
        self.assertIn('沒有符合',i.followup.send.await_args.kwargs['embed'].description)
        key=sp.add('42',20,'餐飲','不存在','2026-09-01')
        await modal.on_submit(i)
        view=i.followup.send.await_args.kwargs['view']
        with sp.transaction() as conn:conn.execute('UPDATE expenses SET voided=1 WHERE id=?',(key,))
        select=next(c for c in view.children if isinstance(c,discord.ui.Select));select._values=['0']
        fresh=interaction();await select.callback(fresh)
        fresh.response.send_modal.assert_not_awaited()
        self.assertTrue(fresh.response.send_message.await_args.kwargs['ephemeral'])
