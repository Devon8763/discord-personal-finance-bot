import unittest
from datetime import date
from unittest.mock import patch
import discord
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction
from form_helpers import fill


class SmartCalendarData(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    def fixed(self):
        return [sp.save_shortcut('42',name,'餐飲',None,name,50) for name in ('早餐','咖啡','晚餐')]

    def spend(self,n,note='午餐',on='2026-09-10',user='42',source=None,cat='餐飲'):
        return [sp.add(user,30,cat,note,on,source) for _ in range(n)]

    async def test_recommendation_boundaries_thresholds_and_tie_target(self):
        keys=self.fixed()
        self.spend(2)
        self.assertIsNone(sp.shortcut_recommendation('42'))
        self.spend(1,on='2026-08-12') # Inclusive day 30.
        self.spend(5,on='2026-08-11') # Excluded day 31.
        result=sp.shortcut_recommendation('42')
        self.assertEqual(result['candidate']['count'],3)
        self.assertEqual(result['target']['id'],keys[-1])
        self.assertEqual((result['period_start'],result['period_end']),('2026-08-12','2026-09-10'))
        for note in ('早餐','咖啡','晚餐'):self.spend(2,note)
        self.assertIsNone(sp.shortcut_recommendation('42'))
        self.spend(1)
        result=sp.shortcut_recommendation('42')
        self.assertEqual((result['candidate']['count'],result['lowest_count']),(4,2))
        self.assertEqual(sp.shortcut_recommendation('42',date(2026,9,9))['candidate']['count'],6)

    async def test_recommendation_excludes_invalid_data_and_pinned_combinations(self):
        self.fixed();self.spend(3)
        self.spend(10,'早餐') # Pinned, not a candidate.
        self.spend(20,'他人',user='43')
        source=sp.add_payment_source('42','舊卡');self.spend(12,'停用來源',source=source)
        sp.disable_payment_source('42',source)
        self.spend(12,'停用分類',cat='交通');sp.set_category('42','交通',False)
        for field,value,note in (('voided',1,'撤銷'),('source','recurring','固定'),('kind','other','非消費')):
            keys=self.spend(12,note)
            with sp.transaction() as conn:
                conn.executemany(f'UPDATE expenses SET {field}=? WHERE id=?',[(value,k) for k in keys])
        before=sp.shortcuts('42',True)
        self.assertEqual(sp.shortcut_recommendation('42')['candidate']['note'],'午餐')
        self.assertEqual(sp.shortcuts('42',True),before)
        self.assertIsNone(sp.shortcut_recommendation('43'))
        sp.disable_shortcut('42',before[-1]['id'])
        self.assertIsNone(sp.shortcut_recommendation('42'))

    async def test_recommendation_exact_combination_and_read_only(self):
        self.fixed()
        source=sp.add_payment_source('42','現代卡')
        self.spend(2,'午餐');self.spend(2,'午餐',source=source)
        self.spend(2,'午餐',cat='購物')
        self.assertIsNone(sp.shortcut_recommendation('42'))
        self.spend(1,'午餐',source=source)
        connect=sp.get_conn
        def readonly():
            conn=connect();conn.execute('PRAGMA query_only=ON');return conn
        with patch.object(sp,'get_conn',side_effect=readonly):
            self.assertEqual(sp.shortcut_recommendation('42')['candidate']['payment_source_id'],source)
            sp.calendar_days('42','2026-09')

    async def test_calendar_levels_boundaries_effective_records_and_empty_month(self):
        for day,amount in ((1,10),(2,20),(3,30)):
            sp.add('42',amount,'餐飲','已記錄',f'2026-08-{day:02}')
        sp.add('43',900,'餐飲','他人','2026-08-01')
        for field,value in (('voided',1),('kind','other')):
            key=sp.add('42',900,'餐飲','忽略','2026-08-01')
            with sp.transaction() as conn:conn.execute(f'UPDATE expenses SET {field}=? WHERE id=?',(value,key))
        key=sp.add('42',5,'餐飲','有效固定支出','2026-08-04')
        with sp.transaction() as conn:conn.execute("UPDATE expenses SET source='recurring' WHERE id=?",(key,))
        days=sp.calendar_days('42','2026-08')
        self.assertEqual(len(days),31)
        self.assertEqual([r['level'] for r in days[:5]],['░','▒','▓','░','—'])
        self.assertEqual(days[0]['cents'],1000)
        self.assertTrue(all(r['cents']==0 and r['level']=='—' for r in sp.calendar_days('empty','2026-08')))
        self.assertEqual(len(sp.calendar_days('42','2024-02')),29)
        with self.assertRaises(ValueError):sp.calendar_days('42','2026-10')


class SmartCalendarUI(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp
    fixed=SmartCalendarData.fixed
    spend=SmartCalendarData.spend

    async def test_recommendation_management_only_and_confirmation(self):
        from lifestyle_ui import Shortcuts
        self.fixed();self.spend(3)
        before=sp.shortcuts('42');expenses=sp.month_expenses('42','2026-09')
        self.assertNotIn('查看推薦',[b.label for b in Shortcuts(self.view).children if isinstance(b,discord.ui.Button)])
        picker=Shortcuts(self.view,manage=True)
        i=interaction();await next(b for b in picker.children if getattr(b,'label',None)=='查看推薦').callback(i)
        rec=i.response.send_message.await_args.kwargs['view']
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        embed=i.response.send_message.await_args.kwargs['embed']
        self.assertIn('2026-08-12',embed.description)
        self.assertIn('3 次',str(embed.to_dict()))
        adopt=next(b for b in rec.children if b.label=='採用建議')
        stranger=interaction(43);await adopt.callback(stranger)
        stranger.response.send_modal.assert_not_awaited()
        await adopt.callback(i)
        form=i.response.send_modal.await_args.args[0]
        self.assertEqual(form.fields['name'].value,'午餐')
        self.assertEqual(form.fields['amount'].value,'50')
        self.assertEqual(sp.shortcuts('42'),before)
        await form.on_submit(i)
        after=sp.shortcuts('42')
        self.assertEqual(after[-1]['note'],'午餐')
        self.assertEqual([(r['id'],r['position'],r['cents']) for r in before],[(r['id'],r['position'],r['cents']) for r in after])
        self.assertEqual(sp.month_expenses('42','2026-09'),expenses)

    async def test_calendar_halves_day_details_edit_and_month_isolation(self):
        from dashboard import open_accounts,CalendarAccounts
        key=sp.add('42',120,'餐飲','完整用途','2026-08-31')
        sp.add('43',999,'餐飲','別人的用途','2026-08-31')
        i=interaction();await open_accounts(self.view,i,'2026-08')
        view=i.response.send_message.await_args.kwargs['view']
        self.assertIsInstance(view,CalendarAccounts)
        self.assertIn('月曆',view.render().title)
        self.assertNotIn('120',str(view.render().to_dict()))
        self.assertIn('未記錄不代表沒有消費',str(view.render().to_dict()))
        await next(b for b in view.children if getattr(b,'label',None)=='後半月（16 日～月底）').callback(i)
        select=next(b for b in view.children if isinstance(b,discord.ui.Select))
        self.assertEqual(len(select.options),16)
        self.assertEqual(select.options[-1].label,'2026-08-31（週一）')
        select._values=['2026-08-31']
        stranger=interaction(43);await select.callback(stranger)
        stranger.response.send_message.assert_awaited_once()
        await select.callback(i)
        detail=i.response.send_message.await_args.kwargs['view']
        body=str(detail.page_content()['embed'].to_dict())
        self.assertIn('120 元',body);self.assertIn('完整用途',body);self.assertNotIn('999',body)
        pick=next(b for b in detail.children if isinstance(b,discord.ui.Select));pick._values=['0']
        await pick.callback(i)
        self.assertEqual(i.response.send_modal.await_args.args[0].expense['id'],key)
        await next(b for b in view.children if getattr(b,'label',None)=='切換月份').callback(i)
        months=i.response.send_message.await_args.kwargs['view']
        self.assertTrue(all(m<='2026-09' for _,m in months.items))
        await months.chosen(i,'2026-07')
        empty=i.response.edit_message.await_args.kwargs['view']
        self.assertIn('尚無已記錄消費',str(empty.render().to_dict()))
        self.assertLessEqual(len(empty.children),25)

    async def test_dashboard_accounts_starts_with_calendar_and_quick_entry(self):
        i=interaction();await next(b for b in self.view.children if b.label=='帳目').callback(i)
        self.assertIn('月曆',self.view.render().title)
        self.assertIn('帳目工具',[getattr(b,'label',None) for b in self.view.children])
        self.assertTrue(any(isinstance(b,discord.ui.Select) for b in self.view.children))
        self.assertLessEqual(len(self.view.children),25)
        await next(b for b in self.view.children if getattr(b,'label',None)=='後半月（16 日～月底）').callback(i)
        self.assertIs(i.response.edit_message.await_args.kwargs['view'],self.view)
        self.assertEqual([b.label for b in self.view.children[:3]],['今天','帳目','更多'])
        self.assertEqual(next(b for b in self.view.children if isinstance(b,discord.ui.Select)).options[0].value,'2026-09-16')

    async def test_calendar_ascii_heading_has_same_column_stride(self):
        from dashboard import calendar_embed
        lines=calendar_embed('42','2026-09').description.split('```')[1].strip('\n').splitlines()
        self.assertEqual(lines[0],'Mon Tue Wed Thu Fri Sat Sun')
        self.assertEqual([lines[0].index(c) for c in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')],list(range(0,28,4)))
        self.assertEqual(lines[1].index('01'),4)
        self.assertEqual(lines[1].index('06'),24)
        self.assertEqual(lines[1][:4],'    ')

    async def test_calendar_fixed_cells_use_three_characters(self):
        from dashboard import calendar_cell
        for text in ('','Mon','Tue','Sun','01░','02▒','03▓','12—'):
            self.assertEqual(calendar_cell(text),text if text else '   ')
            self.assertEqual(len(calendar_cell(text)),3)

    async def test_calendar_month_boundaries_and_february_display_cells(self):
        import calendar
        from dashboard import calendar_embed
        for month in ('2026-09','2026-06','2026-02','2024-02'):
            lines=calendar_embed('42',month).description.split('```')[1].strip('\n').splitlines()
            cells=[]
            for line in lines:
                self.assertEqual(len(line),27)
                self.assertEqual([line[n] for n in range(3,27,4)],[' ']*6)
                cells.append([line[n:n+3] for n in range(0,27,4)])
            self.assertEqual(cells[0],['Mon','Tue','Wed','Thu','Fri','Sat','Sun'])
            start=sp.month_date(month)
            weeks=calendar.Calendar().monthdayscalendar(start.year,start.month)
            self.assertEqual(len(cells)-1,len(weeks))
            for row,week in zip(cells[1:],weeks):
                self.assertEqual(row,[f'{d:02}—' if d else '   ' for d in week])

    async def test_calendar_render_keeps_all_shading_levels_and_no_amounts(self):
        from dashboard import calendar_embed
        for day,amount in ((1,100),(2,200),(3,300)):
            sp.add('42',amount,'餐飲','用途',f'2026-08-{day:02}')
        grid=calendar_embed('42','2026-08').description.split('```')[1]
        for mark in ('01░','02▒','03▓','04—'):self.assertIn(mark,grid)
        for amount in ('100','200','300'):self.assertNotIn(amount,grid)

    async def test_recommendation_return_stale_and_disabled_source_never_write(self):
        from lifestyle_ui import Shortcuts,RecommendationView
        self.fixed();source=sp.add_payment_source('42','支付')
        self.spend(3,source=source)
        picker=Shortcuts(self.view,manage=True)
        rec=RecommendationView(picker,sp.shortcut_recommendation('42'))
        before=sp.shortcuts('42')
        await rec.back(interaction())
        self.assertEqual(sp.shortcuts('42'),before)
        i=interaction();await rec.adopt(i)
        form=i.response.send_modal.await_args.args[0]
        sp.disable_payment_source('42',source)
        await form.on_submit(interaction())
        self.assertEqual(sp.shortcuts('42'),before)
        i=interaction();await rec.adopt(i)
        i.response.send_modal.assert_not_awaited()
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])

    async def test_calendar_all_controls_private_and_daily_pagination(self):
        from dashboard import CalendarAccounts,open_accounts
        self.spend(8,on='2026-08-20')
        view=CalendarAccounts(self.view,'2026-08');view.render()
        for item in view.children:
            i=interaction(43);await item.callback(i)
            self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
            i.response.edit_message.assert_not_awaited()
        i=interaction();await open_accounts(self.view,i,'2026-08',mode='list',on='2026-08-20')
        picker=i.response.send_message.await_args.kwargs['view']
        self.assertEqual(len(picker.page_content()['embed'].fields),6)
        await next(b for b in picker.children if getattr(b,'label',None)=='下一頁').callback(i)
        self.assertEqual(len(picker.page_content()['embed'].fields),2)
        self.assertIn('240 元',picker.page_content()['embed'].description)
        self.assertIn('回月曆',[getattr(b,'label',None) for b in picker.children])
        i=interaction();await view.expense(i)
        self.assertIn('amount',i.response.send_modal.await_args.args[0].fields)
