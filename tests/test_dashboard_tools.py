import unittest
import discord
import test_phase1_ui as fixtures
from test_phase1_ui import interaction,choose


class DashboardToolsTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.PhaseOneUI.setUp

    async def menu(self,group):
        self.view.tab='帳目' if group=='帳目工具' else '更多';self.view.render()
        i=interaction()
        await next(b for b in self.view.children if getattr(b,'label',None)==group).callback(i)
        self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
        menu=i.response.send_message.await_args.kwargs['view']
        return menu,i

    async def test_main_pages_only_show_grouped_actions(self):
        self.view.tab='更多'
        embed=self.view.render()
        self.assertEqual([b.label for b in self.view.children],['今天','帳目','更多','設定','洞察','常用捷徑管理'])
        self.assertIn('需要調整資料請選「設定」；想看花費狀況請選「洞察」。',embed.description)
        self.view.tab='帳目';embed=self.view.render()
        labels=[getattr(b,'label',None) for b in self.view.children]
        self.assertIn('帳目工具',labels)
        self.assertFalse({'搜尋帳目','清單檢視','最近再記'}&set(labels))
        self.assertIn('帳目工具',embed.description)

    async def test_menus_explicit_complete_private_and_return(self):
        for group,options in (('帳目工具',['搜尋帳目','清單檢視','最近再記']),
                              ('設定',['預算','固定負擔','付款來源','分類','提醒','重新開啟新手導覽','我的資料與隱私']),
                              ('洞察',['支出圖表','本週回顧','本月回顧','生活 AI','本月結帳'])):
            menu,i=await self.menu(group)
            title=group+'：'+'、'.join(options)
            self.assertIn(title,i.response.send_message.await_args.args[0])
            select=next(c for c in menu.children if isinstance(c,discord.ui.Select))
            self.assertEqual(select.placeholder,title)
            self.assertEqual([o.label for o in select.options],options)
            stranger=interaction(43);await choose(menu,options[0]).callback(stranger)
            stranger.response.send_modal.assert_not_awaited()
            self.assertTrue(stranger.response.send_message.await_args.kwargs['ephemeral'])
            back=next(c for c in menu.children if getattr(c,'label',None)=='返回主看板')
            stranger=interaction(43);await back.callback(stranger)
            stranger.response.edit_message.assert_not_awaited()
            await back.callback(i)
            self.assertIs(i.response.edit_message.await_args.kwargs['view'],self.view)

    async def test_all_grouped_destinations_remain_reachable(self):
        self.cog.detail_embed=lambda:discord.Embed(title='分類與指令說明')
        for group,option in (('帳目工具','搜尋帳目'),('帳目工具','清單檢視'),('帳目工具','最近再記'),
                             ('設定','預算'),('設定','固定負擔'),('設定','付款來源'),('設定','分類'),('設定','提醒'),
                             ('洞察','支出圖表'),('洞察','本週回顧'),('洞察','本月回顧'),('洞察','生活 AI')):
            menu,_=await self.menu(group);i=interaction()
            await choose(menu,option).callback(i)
            if option=='搜尋帳目':self.assertEqual(len(i.response.send_modal.await_args.args[0].fields),3)
            elif option in ('預算','固定負擔','生活 AI'):
                self.assertEqual(self.view.tab,'AI' if option=='生活 AI' else option)
                self.assertIn('更多',[b.label for b in self.view.children])
            else:
                result=i.response.send_message.await_args or i.followup.send.await_args
                self.assertTrue(result.kwargs['ephemeral'],option)
