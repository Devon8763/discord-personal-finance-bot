import unittest
import discord
import spending as sp
import test_phase1_ui as fixtures
from test_phase1_ui import interaction
from dashboard import Dashboard,card


class SimpleDashboardTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    def labels(self,view):
        self.assertLessEqual(len(view.children),25)
        return [c.label for c in view.children if isinstance(c,discord.ui.Button)]

    async def click(self,view,label,i=None):
        i=i or interaction()
        await next(c for c in view.children if c.label==label).callback(i)
        return i

    async def test_today_current_month_summary_three_sorted_active_shortcuts(self):
        sp.set_budget('42','2026-09','總額',1000)
        sp.add('42',80,'餐飲','早餐')
        keys=[sp.save_shortcut('42',n,'餐飲',None,n) for n in ('早餐','午餐','晚餐','咖啡','停用')]
        sp.disable_shortcut('42',keys[-1]);sp.move_shortcut('42',keys[3],-1)
        sp.save_shortcut('43','他人','餐飲',None,'他人')
        view=Dashboard(self.cog,42,'2026-08')
        labels=self.labels(view)
        expected=[s['name'] for s in sp.shortcuts('42')[:3]]
        self.assertEqual(labels,['今天','帳目','更多','＋記一筆消費']+expected)
        embed,_,_=card('42','2026-08','今天')
        fields={f.name:f.value for f in embed.fields}
        self.assertEqual(set(fields),{'本月已支出','剩餘總預算','常用捷徑'})
        self.assertIn('80 元',fields['本月已支出']);self.assertIn('920 元',fields['剩餘總預算'])
        self.assertIn('2026-09',embed.title)

    async def test_today_empty_without_total_budget(self):
        fields={f.name:f.value for f in self.view.render().fields}
        self.assertEqual(fields['剩餘總預算'],'尚未設定')
        self.assertIn('尚無捷徑',fields['常用捷徑'])
        self.assertEqual(self.labels(self.view),['今天','帳目','更多','＋記一筆消費','＋建立捷徑'])
        i=await self.click(self.view,'＋建立捷徑')
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        picker=i.response.send_message.await_args.kwargs['view']
        await picker.new(i)
        self.assertIsNotNone(i.response.send_modal.await_args)

    async def test_shortcut_confirm_only_fresh_read_and_owner_checks(self):
        key=sp.save_shortcut('42','早餐','餐飲',None,'原用途')
        self.view.render()
        sp.save_shortcut('42','早餐','餐飲',None,'新用途',key=key)
        i=await self.click(self.view,'早餐')
        self.assertEqual(i.response.send_modal.await_args.args[0].fields['note'].value,'新用途')
        self.assertEqual(sp.month_expenses('42','2026-09'),[])
        stranger=await self.click(self.view,'早餐',interaction(43))
        stranger.response.send_modal.assert_not_awaited()
        sp.disable_shortcut('42',key)
        i=await self.click(self.view,'早餐')
        i.response.send_modal.assert_not_awaited()
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])

    async def test_accounts_actions_and_more_preserve_all_destinations(self):
        await self.click(self.view,'帳目')
        labels=self.labels(self.view)
        self.assertTrue({'帳目工具','切換月份'}<=set(labels))
        self.assertFalse({'付款來源','支出圖表','預算','固定負擔'}&set(labels))
        await self.click(self.view,'更多')
        self.assertEqual(self.labels(self.view),['今天','帳目','更多','設定','洞察','常用捷徑管理'])
        i=await self.click(self.view,'常用捷徑管理')
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        self.assertTrue(i.response.send_message.await_args.kwargs['view'].manage)

    async def test_every_dashboard_action_rejects_other_user(self):
        for tab in ('今天','帳目','更多','預算','固定負擔','AI'):
            self.view.tab=tab;self.view.render();self.labels(self.view)
            for button in self.view.children:
                i=interaction(43);await button.callback(i)
                i.response.send_modal.assert_not_awaited()
                self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
