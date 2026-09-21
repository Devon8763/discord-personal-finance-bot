"""Private, paged Discord spending dashboard."""
import discord
import calendar
import life_ledger_service as life_service
from selection_ui import OwnedView, Picker, PaymentPicker
from form_ui import InlineForm
import spending as sp
from presentation import number, NOTE
from privacy_rules import private_interaction

TABS = ('今天','帳目','更多')


def progress(percent):
    blocks = min(10,max(0,int(percent/10)))
    return '■'*blocks+'□'*(10-blocks)


def card(user_id,month,tab,page=0):
    if tab=='帳目': return calendar_embed(user_id,month),0,1
    if tab=='今天': month=sp.today().strftime('%Y-%m')
    report = life_service.get_month_summary(user_id,month)
    total = next((b for b in report['budgets'] if b['category']=='總額'),None)
    ratio = total['used_percent'] if total else 0
    color = 0xe74c3c if ratio>=100 else 0xf1c40f if ratio>=80 else 0x2ecc71
    embed = discord.Embed(title=f'💰 {month} · {tab}',color=color)
    pages = 1
    if tab=='今天':
        embed.add_field(name='本月已支出',value=f"**{number(report['total'])} 元**",inline=True)
        embed.add_field(name='剩餘總預算',value=f"**{number(total['remaining'])} 元**" if total else '尚未設定',inline=True)
        names=[discord.utils.escape_markdown(r['name']) for r in sp.shortcuts(user_id)[:3]]
        embed.add_field(name='常用捷徑',value='、'.join(names) if names else '尚無捷徑，按「＋建立捷徑」開始。',inline=False)
    elif tab=='更多':
        embed.description='需要調整資料請選「設定」；想看花費狀況請選「洞察」。'
        embed.add_field(name='設定',value='預算 · 固定負擔 · 付款來源 · 分類 · 提醒 · 重新開啟新手導覽 · 我的資料與隱私',inline=False)
        embed.add_field(name='洞察',value='支出圖表 · 本週回顧 · 本月回顧 · 生活 AI · 本月結帳',inline=False)
        embed.add_field(name='常用捷徑管理',value='新增、修改、排序與停用；使用捷徑仍須確認表單。',inline=False)
    elif tab=='清單':
        requested_page = max(0,page)
        listing = life_service.list_expenses(user_id,month,include_voided=True,limit=6,offset=requested_page*6)
        pages = max(1,(listing['total']+5)//6)
        page = min(requested_page,pages-1)
        if page != requested_page:
            listing = life_service.list_expenses(user_id,month,include_voided=True,limit=6,offset=page*6)
        entries = listing['items']
        embed.description=f"本月已記錄 **{number(report['total'])} 元**"
        for row in entries:
            embed.add_field(name=f"{row['spent_on'][5:]} · {row['category']} · {number(row['cents']/100)} 元"+(' · 已撤銷' if row['voided'] else ''),value=f"{row['note']}\n`#{row['id']}` · {row['payment_source_name']} · "+('日常支出' if row['source']=='manual' else row['source']),inline=False)
        if not entries:
            embed.description='本月尚無紀錄。可切換月份，或到「今天」記一筆消費。'
    elif tab=='預算':
        budgets = sorted(report['budgets'],key=lambda b:(b['category']!='總額',b['category']))
        pages = max(1,(len(budgets)+4)//5)
        page = min(max(0,page),pages-1)
        for b in budgets[page*5:page*5+5]:
            embed.add_field(name=f"{b['category']} · 使用率 {number(b['used_percent'])}%",value=f"{progress(b['used_percent'])}\n{number(b['spent'])}／{number(b['budget'])} 元\n"+('超支 ' if b['remaining']<0 else '剩餘 ')+f"{number(abs(b['remaining']))} 元",inline=False)
        if not budgets:
            embed.description='尚未設定預算。先設定「總額」，再設定各分類。'
        embed.add_field(name='提醒門檻',value='／'.join(f'{v}%' for v in sp.reminder_levels(user_id)),inline=False)
    elif tab=='固定負擔':
        rules=sp.rows('SELECT * FROM recurring_expenses WHERE user_id=? ORDER BY active DESC,id DESC',(user_id,))
        pages=max(1,(len(rules)+4)//5)
        page=min(max(0,page),pages-1)
        embed.description=f"本月已列入 **{number(report['fixed'])} 元**"
        for r in rules[page*5:page*5+5]:
            start=sp.month_date(r['start_month']); selected=sp.month_date(month)
            elapsed=max(0,(selected.year-start.year)*12+selected.month-start.month+1)
            status='停用' if not r['active'] else '尚未開始' if elapsed==0 else '已結束' if r['periods'] and elapsed>r['periods'] else '啟用'
            detail=f"`#{r['id']}` · {r['category']} · {status}\n開始月份 {r['start_month']}"
            if r['periods']:
                detail+=f"\n第 {min(elapsed,r['periods'])}／{r['periods']} 期 · 剩餘 {max(0,r['periods']-elapsed)} 期"
            embed.add_field(name=f"{r['name']} · {number(r['cents']/100)} 元／月",value=detail,inline=False)
        if not rules:
            embed.description='尚無固定項目。點「＋新增固定負擔」，選擇訂閱、固定支出或分期。'
    else:
        embed.description='**用短重點理解你的支出。**\n\n📅 月分析：近 3 月同天數比較\n📆 週分析：近 4 週同天數比較\n💬 問問題：例如「上個月餐飲花多少？」'
        embed.add_field(name='查詢範圍',value='分析以今天為基準；不會自動修改帳目或預算。',inline=False)
    embed.set_footer(text=f'第 {page+1}／{pages} 頁 · '+NOTE+' · 僅本人可見')
    return embed,page,pages


class EntryModal(InlineForm):
    def __init__(self,view,kind,selections=None,draft=None):
        super().__init__(view.owner,title={'expense':'記錄支出','budget':'設定預算','month':'切換月份','ask':'自然語言查詢'}[kind],draft=draft)
        self.view,self.kind=view,kind
        selected=selections or {}
        self.saved=False
        self.reopen=lambda draft:EntryModal(view,kind,draft=draft)
        if kind=='ask':
            self.text('question','你想查詢什麼？')
        elif kind=='month':
            self.month(view.month)
        elif kind=='budget':
            self.month(selected.get('month',view.month),future=True)
            self.category(selected.get('category','總額'),budget=True)
            self.text('amount','預算金額（元）',limit=30)
        else:
            self.text('date','日期（YYYY-MM-DD）',selected.get('date',sp.today().isoformat()),limit=10)
            self.text('amount','支出金額（元）',limit=30)
            if 'category' in selected: self.category(selected['category'])
            else: self.category()
            if 'payment_source_id' in selected: self.payment(selected['payment_source_id'])
            else: self.payment()
            self.text('note','用途（例如午餐）')

    async def on_submit(self,interaction):
        if interaction.user.id!=self.view.owner:
            await interaction.response.send_message('請開啟自己的生活看板。',ephemeral=True)
            return
        if self.saved:
            await interaction.response.send_message('此表單已儲存，請開啟新表單記錄下一筆。', ephemeral=True)
            return
        values=await self.read_form(interaction)
        if values is None: return
        await interaction.response.defer(ephemeral=True,thinking=True)
        ctx=self.view.cog.interaction_context(interaction)
        try:
            if self.saved:
                await ctx.send('此表單已儲存，請開啟新表單記錄下一筆。')
                return
            if self.kind=='ask':
                await self.view.cog.ask.callback(self.view.cog,ctx,question=values['question'])
                return
            if self.kind=='month':
                life_service.get_month_summary(str(self.view.owner),values['month'])
                self.view.month=values['month']
                self.view.page=0
                await ctx.send('已切換月份。')
            elif self.kind=='budget':
                sp.set_budget(str(self.view.owner),values['month'],values['category'],values['amount'])
                await ctx.send('✅ 預算已儲存。')
            else:
                key=life_service.add_expense(str(self.view.owner),values['amount'],values['category'],values['note'],values['date'],values['payment'])
                self.saved = True
                await ctx.send(f'✅ 支出 #{key} 已記錄。')
            await self.view.cog.notify(ctx)
            await self.view.refresh()
        except ValueError as error:
            await ctx.send(str(error))


class Dashboard(discord.ui.View):
    def __init__(self,cog,owner,month=None):
        super().__init__(timeout=300)
        self.cog,self.owner=cog,owner
        self.month=month or sp.today().strftime('%Y-%m')
        self.tab='今天'; self.page=0; self.message=None
        self.calendar_half='first'
        self.render()

    async def interaction_check(self,interaction):
        if not await private_interaction(interaction):return False
        if interaction.user.id==self.owner:
            return True
        await interaction.response.send_message('請用 !記帳說明 開啟自己的看板。',ephemeral=True)
        return False

    def button(self,label,callback,row=1,style=discord.ButtonStyle.secondary,disabled=False):
        button=discord.ui.Button(label=label,row=row,style=style,disabled=disabled)
        async def owned(interaction):
            if await self.interaction_check(interaction):
                await callback(interaction)
        button.callback=owned
        self.add_item(button)

    def render(self):
        embed,self.page,pages=card(str(self.owner),self.month,self.tab,self.page)
        self.clear_items()
        for tab in TABS:
            async def navigate(interaction,target=tab):
                await interaction.response.defer()
                self.tab,self.page=target,0
                await self.refresh(interaction)
            self.button(tab,navigate,row=0,style=discord.ButtonStyle.primary if tab==self.tab else discord.ButtonStyle.secondary)
        if self.tab=='今天':
            async def expense(i): await start_entry(self,i,'expense')
            self.button('＋記一筆消費',expense,style=discord.ButtonStyle.success)
            from lifestyle_ui import open_shortcut,open_shortcuts
            shortcuts=sp.shortcuts(str(self.owner))[:3]
            for shortcut in shortcuts:
                async def quick(i,key=shortcut['id']): await open_shortcut(self,i,key)
                self.button(shortcut['name'],quick,row=2)
            if not shortcuts:
                async def create(i): await open_shortcuts(self,i,manage=True)
                self.button('＋建立捷徑',create,row=2)
        elif self.tab=='帳目':
            async def tools(i): await open_dashboard_tools(self,i,'帳目工具')
            async def expense(i): await start_entry(self,i,'expense')
            self.button('帳目工具',tools)
            self.button('＋記一筆消費',expense)
            CalendarAccounts(self,self.month,self.calendar_half).add_dates(self,row=2)
        elif self.tab=='更多':
            for group in ('設定','洞察'):
                async def tools(i,value=group): await open_dashboard_tools(self,i,value)
                self.button(group,tools)
            from lifestyle_ui import open_shortcuts
            async def shortcuts(i): await open_shortcuts(self,i,manage=True)
            self.button('常用捷徑管理',shortcuts)
        elif self.tab=='預算':
            async def budget(i): await start_entry(self,i,'budget')
            self.button('設定預算',budget)
        elif self.tab=='固定負擔':
            async def recurring(i):
                from spending_commands import RecurringModal
                await i.response.send_modal(RecurringModal(self.cog,owner=self.owner))
            self.button('＋新增固定負擔',recurring,style=discord.ButtonStyle.success)
        elif self.tab=='AI':
            for label,unit,count in (('月分析','月',3),('週分析','週',4)):
                async def analyze(i,u=unit,n=count):
                    await i.response.defer(ephemeral=True,thinking=True)
                    await self.cog.analyze_spending(self.cog.interaction_context(i),u,n)
                self.button(label,analyze,style=discord.ButtonStyle.success)
            async def ask(i): await i.response.send_modal(EntryModal(self,'ask'))
            self.button('問問題',ask)
        async def select_month(i):
            from selection_ui import choose_month
            async def selected(event,month):
                await event.response.defer()
                self.month,self.page=month,0
                self.calendar_half='first'
                await self.refresh()
                await event.edit_original_response(content='✅ 已切換月份：'+month,view=None)
            await choose_month(i,selected)
        if self.tab in ('帳目','預算','固定負擔'):
            self.button('切換月份',select_month,row=4)
        if self.tab=='預算':
            async def reminders(i):
                from selection_ui import Reminders
                await i.response.send_message('選擇提醒門檻：',view=Reminders(self.cog,self.owner),ephemeral=True)
            self.button('提醒設定',reminders,row=1)
        async def refresh(i):
            await i.response.defer()
            sp.sync_recurring(str(self.owner))
            await self.refresh(i)
        if self.tab not in ('今天','更多'): self.button('重新整理',refresh,row=4)
        if self.tab in ('預算','固定負擔'):
            for label,delta,disabled in (('上一頁',-1,self.page==0),('下一頁',1,self.page>=pages-1)):
                async def turn(i,d=delta):
                    await i.response.defer()
                    self.page+=d
                    await self.refresh(i)
                self.button(label,turn,row=2,disabled=disabled)
        return embed

    async def refresh(self,interaction=None):
        embed=self.render()
        if interaction:
            await interaction.edit_original_response(embed=embed,view=self)
        elif self.message:
            try:
                await self.message.edit(embed=embed,view=self)
            except discord.HTTPException:
                pass

    async def on_error(self,interaction,error,item):
        text='看板暫時無法更新，請重新輸入 !記帳說明。'
        if interaction.response.is_done():
            await interaction.followup.send(text,ephemeral=True)
        else:
            await interaction.response.send_message(text,ephemeral=True)


async def open_dashboard(cog,interaction):
    if not await private_interaction(interaction):return
    await interaction.response.defer(ephemeral=True,thinking=True)
    ctx=cog.interaction_context(interaction)
    await cog.prepare(ctx)
    view=Dashboard(cog,interaction.user.id)
    if sp.onboarding_needed(str(view.owner)):
        guide=Onboarding(view.cog,view.owner)
        guide.message=await interaction.followup.send(embed=guide.render(),view=guide,ephemeral=True,wait=True)
    view.message=await interaction.followup.send(embed=view.render(),view=view,ephemeral=True,wait=True)


async def open_dashboard_tools(dashboard,i,group):
    if not await dashboard.interaction_check(i):return
    options={'帳目工具':('搜尋帳目','清單檢視','最近再記'),
             '設定':('預算','固定負擔','付款來源','分類','提醒','重新開啟新手導覽','我的資料與隱私'),
             '洞察':('支出圖表','本週回顧','本月回顧','生活 AI','本月結帳')}[group]
    title=group+'：'+'、'.join(options)
    async def selected(event,option):
        if not await dashboard.interaction_check(event):return
        if option not in options:return
        if option in ('預算','固定負擔','生活 AI'):
            await event.response.defer()
            dashboard.tab,dashboard.page=('AI' if option=='生活 AI' else option),0
            dashboard.message=getattr(event,'message',dashboard.message)
            await dashboard.refresh(event)
        elif option=='我的資料與隱私':
            from life_privacy import PrivacyView
            view=PrivacyView(dashboard)
            await event.response.send_message(embed=view.render(),view=view,ephemeral=True)
        elif option=='重新開啟新手導覽':
            guide=Onboarding(dashboard.cog,dashboard.owner)
            await event.response.send_message(embed=guide.render(),view=guide,ephemeral=True)
            guide.message=getattr(event,'original_response',None)
            if guide.message:guide.message=await guide.message()
        elif option=='本月結帳':
            view=MonthlyClosing(dashboard.owner,dashboard.month)
            await event.response.send_message('洞察 → 本月結帳；可回原洞察選單按「返回主看板」。',embed=view.render(),view=view,ephemeral=True)
        elif option=='搜尋帳目':await event.response.send_modal(SearchExpensesModal(dashboard))
        elif option=='清單檢視':await open_accounts(dashboard,event,mode='list')
        elif option=='最近再記':
            from lifestyle_ui import open_recent
            await open_recent(dashboard,event)
        elif option=='付款來源':
            await event.response.send_message('設定 → 付款來源：新增、改名或停用。返回主看板請用原「設定」選單的返回按鈕。',view=PaymentPicker(dashboard.owner,manage=True),ephemeral=True)
        elif option=='分類':
            await event.response.send_message('設定 → 分類：分類指令見下方；返回主看板請用原「設定」選單的返回按鈕。',embed=dashboard.cog.detail_embed(),ephemeral=True)
        elif option=='提醒':
            from selection_ui import Reminders
            await event.response.send_message('設定 → 提醒：選擇門檻；返回主看板請用原「設定」選單的返回按鈕。',view=Reminders(dashboard.cog,dashboard.owner),ephemeral=True)
        elif option=='支出圖表':
            await event.response.defer(ephemeral=True,thinking=True)
            view=Charts(dashboard.owner,dashboard.month)
            await event.followup.send('洞察 → 支出圖表；返回主看板請用原「洞察」選單的返回按鈕。',embed=view.render(),view=view,ephemeral=True)
        else:
            from lifestyle_ui import open_reviews
            await open_reviews(dashboard,event,'week' if option=='本週回顧' else 'month')
    picker=Picker(dashboard.owner,[(o,o) for o in options],selected,title)
    back=discord.ui.Button(label='返回主看板',row=2)
    async def return_dashboard(event):
        if not await dashboard.interaction_check(event):return
        dashboard.message=getattr(event,'message',dashboard.message)
        await event.response.edit_message(content=None,embed=dashboard.render(),view=dashboard)
    back.callback=return_dashboard
    picker.extra_buttons.append(back);picker.build()
    await i.response.send_message(title+'\n選擇功能會開啟私人表單或訊息；完成後可回到此選單按「返回主看板」。預算、固定負擔與生活 AI 頁可按「更多」返回。',view=picker,ephemeral=True)


class Onboarding(OwnedView):
    def __init__(self,cog,owner):
        super().__init__(owner)
        self.cog,self.month,self.message=cog,sp.today().strftime('%Y-%m'),None
        for label in ('設定付款來源','建立常用捷徑','設定當月預算','先跳過','完成導覽'):
            button=discord.ui.Button(label=label,row=0 if label.startswith(('設定','建立')) else 1)
            async def click(i,action=label):
                if not await self.interaction_check(i):return
                if action in ('先跳過','完成導覽'):
                    sp.dismiss_onboarding(str(self.owner))
                    await i.response.edit_message(content='已關閉自動導覽。可直接使用生活看板；需要時從「更多 → 設定」重新開啟。',embed=None,view=None)
                elif action=='設定當月預算':await i.response.send_modal(EntryModal(self,'budget'))
                else:
                    if action=='設定付款來源':view=PaymentPicker(self.owner,manage=True)
                    else:
                        from lifestyle_ui import Shortcuts
                        view=Shortcuts(self,manage=True)
                    back=discord.ui.Button(label='返回新手導覽',row=3)
                    async def return_guide(event):
                        if not await self.interaction_check(event):return
                        await event.response.edit_message(content=None,embed=self.render(),view=self)
                    back.callback=return_guide;view.extra_buttons.append(back);view.build()
                    await i.response.edit_message(content=action+'；完成後按「返回新手導覽」繼續。',embed=None,view=view)
            button.callback=click;self.add_item(button)

    def render(self):
        return discord.Embed(title='歡迎使用生活記帳',description='你可以直接記一筆消費，也可以先完成這三項常用設定：\n1. 付款來源：現金、電子支付、信用卡或帳戶\n2. 常用捷徑：快速記錄固定類型的消費\n3. 當月預算：查看剩餘可用金額\n\n三項都可稍後再設定。\n直接記帳請使用下方生活看板的「＋記一筆消費」。',color=0x3498db)

    async def refresh(self):
        if self.message:
            try:await self.message.edit(content=None,embed=self.render(),view=self)
            except discord.HTTPException:pass


class MonthlyClosing(OwnedView):
    def __init__(self,owner,month):
        super().__init__(owner)
        self.month,self.page=month,0

    def render(self):
        data=sp.monthly_closing(str(self.owner),self.month)
        current,previous,comparison=data['current'],data['previous'],data['comparison']
        safe=discord.utils.escape_markdown
        money=lambda value:safe(number(value))+' 元'
        embed=discord.Embed(title=self.month+' · 月結摘要',description=current['start']+' ～ '+current['end']+('\n本月尚無消費紀錄；未記錄不代表沒有消費。' if not current['has_records'] else ''),color=0x3498db)
        budget=next((b for b in current['budgets'] if b['category']=='總額'),None)
        budget_text='尚未設定' if budget is None else '已使用 '+money(budget['spent'])+'／預算 '+money(budget['budget'])+'\n'+('超支 ' if budget['remaining']<0 else '剩餘 ')+money(abs(budget['remaining']))
        categories=sorted(((k,v['amount']) for k,v in current['categories'].items() if v['amount']),key=lambda item:(-item[1],item[0]))[:3]
        fields=[('已記錄支出總額',money(current['total'])),('總預算狀態',budget_text),('前三大支出分類','\n'.join(safe(k)+'：'+money(v) for k,v in categories) or '尚無消費紀錄')]
        fields.extend(('付款來源：'+safe(p['payment_source_name']),money(p['cents']/100)) for p in data['payments'])
        if not data['payments']:fields.append(('付款來源分布','尚無消費紀錄'))
        fields.append(('未指定付款來源',str(data['unspecified']['count'])+' 筆 · '+money(data['unspecified']['cents']/100)))
        ranges=f"本期：{comparison['start']} ～ {comparison['end']}\n上期：{previous['start']} ～ {previous['end']}"
        fields.append(('與上月同期比較',ranges+'\n'+('任一期無紀錄，資料不足，不計算差額。' if data['difference'] is None else '已記錄金額差額：'+money(data['difference'])+'；不代表真實消費增減。')))
        pages=max(1,(len(fields)+7)//8);self.page=min(self.page,pages-1)
        for name,value in fields[self.page*8:self.page*8+8]:embed.add_field(name=name,value=value,inline=False)
        embed.set_footer(text=f'第 {self.page+1}／{pages} 頁 · 僅本人可見 · 僅依已記錄消費統計，不代表全部實際消費。')
        self.clear_items()
        for label,delta,disabled in (('上一頁',-1,self.page==0),('下一頁',1,self.page==pages-1)):
            button=discord.ui.Button(label=label,disabled=disabled)
            async def turn(i,d=delta):
                if not await self.interaction_check(i):return
                self.page+=d;await i.response.edit_message(embed=self.render(),view=self)
            button.callback=turn;self.add_item(button)
        return embed


async def start_entry(view,i,kind):
    if i.user.id!=view.owner:
        await i.response.send_message('請開啟自己的生活看板。',ephemeral=True);return
    await i.response.send_modal(EntryModal(view,kind))


class EditExpenseModal(InlineForm):
    def __init__(self, view, expense,draft=None):
        super().__init__(view.owner,title=f"修改消費 #{expense['id']}",draft=draft)
        self.view,self.expense=view,expense
        self.reopen=lambda draft:EditExpenseModal(view,expense,draft=draft)
        self.text('amount','金額（元）',str(sp.Decimal(expense['cents'])/100),limit=30)
        self.category(expense['category'],keep=True)
        self.text('note','用途',expense['note'])
        self.text('date','日期（YYYY-MM-DD；自動記帳不可移動）',expense['spent_on'],limit=10)
        self.payment(original=expense,keep=True)

    async def on_submit(self, i):
        if i.user.id != self.view.owner or str(i.user.id) != self.expense['user_id']:
            await i.response.send_message('請開啟自己的帳目管理。', ephemeral=True)
            return
        values=await self.read_form(i)
        if values is None: return
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            source=None if values['payment']=='keep' else values['payment']
            life_service.update_expense(str(self.view.owner), self.expense['id'], values['amount'], values['category'], values['note'], values['date'],
                                        payment_source_id=source, expected_revision=self.expense['revision'])
            await i.followup.send('✅ 消費已修改；可用 !記帳撤銷 還原最近操作。重新開啟帳目管理可查看最新清單。', ephemeral=True)
            await self.view.cog.notify(self.view.cog.interaction_context(i))
            await self.view.refresh()
        except ValueError as error:
            await i.followup.send(str(error), ephemeral=True)


class Accounts(Picker):
    def __init__(self, owner, month, entries, selected):
        self.entries, self.month = entries, month
        items = [(f"#{r['id']} · {r['spent_on']} · {number(r['cents']/100)}元 · {r['category']}", r['id']) for r in entries]
        super().__init__(owner, items, selected, '選取消費，開啟單筆修改表單', page_size=6)

    def page_content(self):
        embed = discord.Embed(title=f'🧾 {self.month} · 帳目管理', color=0x2ecc71)
        for row in self.entries[self.page*6:self.page*6+6]:
            embed.add_field(name=f"#{row['id']} · {row['spent_on']} · {number(row['cents']/100)} 元",
                value=f"分類：{discord.utils.escape_markdown(row['category'])}\n用途：{discord.utils.escape_markdown(row['note'])}\n付款來源：{discord.utils.escape_markdown(row['payment_source_name'])}", inline=False)
        if not self.entries:
            embed.description = '本月尚無有效消費，可切換月份或回看板記支出。'
        if getattr(self,'day',None):
            embed.title=f'🧾 {self.day} · 當日帳目'
            embed.description=f"當日已記錄總額 **{number(sum(r['cents'] for r in self.entries)/100)} 元**" if self.entries else '當日尚無已記錄消費。未記錄不代表沒有消費。'
        embed.set_footer(text=f'第 {self.page+1}／{max(1,(len(self.items)+5)//6)} 頁 · 僅本人可見 · 選取單筆後修改')
        return {'embed': embed}


class SearchExpensesModal(InlineForm):
    def __init__(self,dashboard):
        super().__init__(dashboard.owner,title='搜尋帳目（至少填一項）')
        self.dashboard=dashboard
        self.text('keyword','名稱／用途關鍵字（可空白）',required=False)
        self.text('start','開始日 YYYY-MM-DD／YYYY/MM/DD／YYYYMMDD（可空白）',limit=10,required=False)
        self.text('end','結束日 YYYY-MM-DD／YYYY/MM/DD／YYYYMMDD（可空白）',limit=10,required=False)

    async def on_submit(self,i):
        values=await self.read_form(i)
        if values is None:return
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            entries=life_service.search_expenses(str(self.owner),**values)
            view=SearchResults(self.dashboard,entries)
            await i.followup.send(**view.page_content(),view=view,ephemeral=True)
        except ValueError as error:
            await i.followup.send(str(error),ephemeral=True)


class SearchResults(Accounts):
    def __init__(self,dashboard,entries):
        self.dashboard=dashboard
        super().__init__(dashboard.owner,'搜尋結果',entries,self.edit)

    def page_content(self):
        content=super().page_content()
        content['embed'].title='🔎 搜尋帳目結果'
        content['embed'].description=f'共 {len(self.entries)} 筆有效手動消費，依日期由新到舊。' if self.entries else '沒有符合條件的帳目，請調整關鍵字或日期後再搜尋。'
        return content

    async def edit(self,i,key):
        if not await self.interaction_check(i):return
        try:
            entry=life_service.get_expense(str(self.owner),key)
            if entry['source']!='manual' or key not in [r['id'] for r in self.entries]:
                raise ValueError('搜尋結果已變動，請重新搜尋。')
            await i.response.send_modal(EditExpenseModal(self.dashboard,entry))
        except ValueError as error:
            await i.response.send_message(str(error),ephemeral=True)


def calendar_cell(text):
    return text.ljust(3)


def calendar_row(values):
    # Both weekday and date rows use three-character cells and one separator.
    return ' '.join(calendar_cell(value) for value in values)


def calendar_embed(owner,month):
    days=life_service.get_calendar_days(str(owner),month)
    start=sp.month_date(month)
    marks={int(r['date'][-2:]):r['level'] for r in days}
    weeks=calendar.Calendar().monthdayscalendar(start.year,start.month)
    grid=calendar_row(('Mon','Tue','Wed','Thu','Fri','Sat','Sun'))+'\n'+'\n'.join(
        calendar_row(f'{d:02}{marks[d]}' if d else '' for d in week) for week in weeks)
    embed=discord.Embed(title=f'📅 {month} · 帳目月曆',description='```\n'+grid+'\n```\n— 無已記錄消費\n以當月最高每日金額為基準：░ ≤1/3 · ▒ >1/3 且 ≤2/3 · ▓ >2/3\n選日期後查看金額與帳目。搜尋、完整清單或重記最近消費，請用主看板「帳目工具」。')
    if not any(r['cents'] for r in days):embed.description+='\n\n尚無已記錄消費，可切換月份或記一筆消費。'
    embed.set_footer(text='未記錄不代表沒有消費 · 僅依已記錄資料統計 · 僅本人可見')
    return embed


class CalendarAccounts(OwnedView):
    def __init__(self,dashboard,month,half='first'):
        super().__init__(dashboard.owner)
        self.dashboard,self.month,self.half=dashboard,month,half

    def add_dates(self,target,row=0):
        days=life_service.get_calendar_days(str(self.owner),self.month)
        selected=days[:15] if self.half=='first' else days[15:]
        select=discord.ui.Select(placeholder='選擇日期查看帳目',row=row+1,options=[
            discord.SelectOption(label=f"{d['date']}（週{'一二三四五六日'[sp.date.fromisoformat(d['date']).weekday()]}）",value=d['date']) for d in selected])
        async def choose(i):
            if not await self.interaction_check(i):return
            on=select.values[0]
            if on not in [d['date'] for d in selected]:
                await i.response.send_message('日期選項已失效，請重新開啟月曆。',ephemeral=True);return
            await open_accounts(self.dashboard,i,self.month,mode='list',on=on)
        select.callback=choose;target.add_item(select)
        for label,half in (('前半月（1～15 日）','first'),('後半月（16 日～月底）','second')):
            button=discord.ui.Button(label=label,row=row,disabled=half==self.half)
            async def turn(i,value=half):
                if not await self.interaction_check(i):return
                self.half=value
                if target is not self:target.calendar_half=value
                await i.response.edit_message(embed=target.render(),view=target)
            button.callback=turn;target.add_item(button)

    def render(self):
        self.clear_items();self.add_dates(self)
        for label,callback in (('清單檢視',self.listing),('切換月份',self.switch),('＋記一筆消費',self.expense),('返回生活看板',self.back)):
            button=discord.ui.Button(label=label,row=2)
            button.callback=callback;self.add_item(button)
        return calendar_embed(self.owner,self.month)

    async def listing(self,i):
        if await self.interaction_check(i):await open_accounts(self.dashboard,i,self.month,mode='list')

    async def expense(self,i):
        if await self.interaction_check(i):await start_entry(self.dashboard,i,'expense')

    async def back(self,i):
        if not await self.interaction_check(i):return
        self.dashboard.month=self.month
        await i.response.edit_message(embed=self.dashboard.render(),view=self.dashboard)

    async def switch(self,i):
        if not await self.interaction_check(i):return
        from selection_ui import choose_month
        async def selected(event,month):
            if not await self.interaction_check(event):return
            life_service.get_calendar_days(str(self.owner),month)
            self.month,self.half=month,'first'
            await event.response.edit_message(content=None,embed=self.render(),view=self)
        await choose_month(i,selected)


async def open_accounts(view, i, month=None,mode='calendar',on=None):
    if i.user.id!=view.owner:
        await i.response.send_message('請開啟自己的帳目管理。',ephemeral=True);return
    month = month or view.month
    life_service.get_calendar_days(str(view.owner),month)
    if mode=='calendar':
        calendar_view=CalendarAccounts(view,month)
        await i.response.send_message(embed=calendar_view.render(),view=calendar_view,ephemeral=True)
        return
    entries = life_service.list_expenses(str(view.owner), month)['items']
    if on is not None:entries=[r for r in entries if r['spent_on']==on]
    async def selected(event, key):
        if event.user.id!=view.owner:
            await event.response.send_message('請開啟自己的帳目管理。',ephemeral=True);return
        try:
            entry = life_service.get_expense(str(view.owner), key)
            await event.response.send_modal(EditExpenseModal(view, entry))
        except ValueError as error:
            await event.response.send_message(str(error), ephemeral=True)
    picker = Accounts(view.owner, month, entries, selected)
    picker.day=on
    button = discord.ui.Button(label='切換月份', row=2)
    async def switch(event):
        from selection_ui import choose_month
        if not await picker.interaction_check(event):return
        async def chosen(event, value): await open_accounts(view, event, value,mode='list')
        await choose_month(event, chosen)
    button.callback = switch
    picker.extra_buttons.append(button)
    back=discord.ui.Button(label='回月曆',row=2)
    async def calendar_back(event):await open_accounts(view,event,month)
    back.callback=calendar_back;picker.extra_buttons.append(back)
    picker.build()
    await i.response.send_message(**picker.page_content(), view=picker, ephemeral=True)


class Charts(OwnedView):
    def __init__(self, owner, month):
        super().__init__(owner)
        self.month, self.kind, self.page = month, 'categories', 0

    def render(self):
        data = life_service.get_chart_data(str(self.owner), self.month)
        names = {'categories':'支出分類分布', 'months':'最近六個月支出趨勢', 'payments':'付款來源支出分布'}
        items = data[self.kind]
        pages = max(1, (len(items)+7)//8)
        self.page = min(max(0, self.page), pages-1)
        trend = self.kind == 'months'
        start = data['trend_start'] if trend else data['start']
        embed = discord.Embed(title=f'📊 {self.month} · {names[self.kind]}', color=0x3498db,
            description=f"{start} ～ {data['end']} · TWD\n" + ('各歷史月為整月，本月截至今天；未記錄不等於沒有消費。' if trend else '依當筆保存的名稱統計有效消費。'))
        maximum = max((v for _, v in items), default=0)
        total = sum(v for _, v in items)
        if not maximum:
            embed.description += '\n\n此期間尚無已記錄消費，記下第一筆後即可查看圖表。'
        for label, cents in items[self.page*8:self.page*8+8]:
            width = max(1, round(cents/maximum*12)) if cents and maximum else 0
            bar = '█'*width + '░'*(12-width)
            share = f' · {number(cents/total*100)}%' if total and not trend else ''
            embed.add_field(name=label, value=f"`{bar}` **{number(cents/100)} 元**{share}", inline=False)
        embed.set_footer(text=f'第 {self.page+1}／{pages} 頁 · 長條依本圖最大值縮放 · 僅依已記錄資料統計 · 僅本人可見')
        self.clear_items()
        for kind, label in names.items():
            button = discord.ui.Button(label=label, row=0, style=discord.ButtonStyle.primary if kind == self.kind else discord.ButtonStyle.secondary)
            async def switch(i, value=kind):
                self.kind, self.page = value, 0
                await i.response.edit_message(embed=self.render(), view=self)
            button.callback = switch
            self.add_item(button)
        for label, delta, disabled in (('上一頁', -1, self.page == 0), ('下一頁', 1, self.page == pages-1)):
            button = discord.ui.Button(label=label, row=1, disabled=disabled)
            async def turn(i, value=delta):
                self.page += value
                await i.response.edit_message(embed=self.render(), view=self)
            button.callback = turn
            self.add_item(button)
        return embed
