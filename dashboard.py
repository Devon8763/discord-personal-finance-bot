"""Private, paged Discord spending dashboard."""
import discord
from selection_ui import SafeModal
import spending as sp
from presentation import number, NOTE

TABS = ('總覽','支出','預算','固定負擔','AI')


def progress(percent):
    blocks = min(10,max(0,int(percent/10)))
    return '■'*blocks+'□'*(10-blocks)


def card(user_id,month,tab,page=0):
    report = sp.month_report(user_id,month)
    total = next((b for b in report['budgets'] if b['category']=='總額'),None)
    ratio = total['used_percent'] if total else 0
    color = 0xe74c3c if ratio>=100 else 0xf1c40f if ratio>=80 else 0x2ecc71
    embed = discord.Embed(title=f'💰 {month} · {tab}',color=color)
    pages = 1
    if tab=='總覽':
        embed.add_field(name='本月支出',value=f"**{number(report['total'])} 元**",inline=True)
        embed.add_field(name='剩餘預算',value=f"**{number(total['remaining'])} 元**" if total else '尚未設定',inline=True)
        embed.add_field(name='預算使用率',value=f"**{number(ratio)}%**\n{progress(ratio)}" if total else '點「設定預算」開始',inline=False)
        embed.add_field(name='固定負擔',value=f"{number(report['fixed'])} 元",inline=True)
        embed.add_field(name='日常支出',value=f"{number(report['total']-report['fixed'])} 元",inline=True)
        top = sorted(report['categories'].items(),key=lambda pair:pair[1]['amount'],reverse=True)[:3]
        embed.add_field(name='主要支出分類',value='\n'.join(f"{cat}　{number(item['amount'])} 元 · {number(item['share'])}%" for cat,item in top if item['amount']) or '尚無支出，點「＋記支出」建立第一筆',inline=False)
    elif tab=='支出':
        count = sp.rows('SELECT COUNT(*) AS n FROM expenses WHERE user_id=? AND substr(spent_on,1,7)=?',(user_id,month))[0]['n']
        pages = max(1,(count+5)//6)
        page = min(max(0,page),pages-1)
        entries = sp.rows('SELECT * FROM expenses WHERE user_id=? AND substr(spent_on,1,7)=? ORDER BY spent_on DESC,id DESC LIMIT 6 OFFSET ?',(user_id,month,page*6))
        embed.description=f"本月已記錄 **{number(report['total'])} 元**"
        for row in entries:
            embed.add_field(name=f"{row['spent_on'][5:]} · {row['category']} · {number(row['cents']/100)} 元"+(' · 已撤銷' if row['voided'] else ''),value=f"{row['note']}\n`#{row['id']}` · "+('日常支出' if row['source']=='manual' else row['source']),inline=False)
        if not entries:
            embed.description='本月尚無紀錄。點「＋記支出」開始。'
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


class EntryModal(SafeModal):
    def __init__(self,view,kind,selections=None):
        super().__init__(title={'expense':'記錄支出','budget':'設定預算','month':'切換月份','ask':'自然語言查詢'}[kind])
        self.view,self.kind=view,kind
        self.selections=selections or {}
        if kind=='month':
            fields=[('month','月份（YYYY-MM）',view.month)]
        elif kind=='ask':
            fields=[('question','你想查詢什麼？',None)]
        elif kind=='budget':
            fields=[('month','月份（YYYY-MM）',view.month),('category','總額 或 分類名稱','總額'),('amount','預算金額（元）',None)]
        else:
            fields=[('date','日期（YYYY-MM-DD）',sp.today().isoformat()),('amount','支出金額（元）',None),('category','分類（例如餐飲）',None),('note','用途（例如午餐）',None)]
        self.fields={}
        for key,label,default in fields:
            if key in self.selections:
                continue
            item=discord.ui.TextInput(label=label,default=default,max_length=200 if key in ('note','question') else 30)
            self.fields[key]=item
            self.add_item(item)

    async def on_submit(self,interaction):
        if interaction.user.id!=self.view.owner:
            await interaction.response.send_message('請開啟自己的生活看板。',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        ctx=self.view.cog.interaction_context(interaction)
        values={**self.selections,**{k:v.value.strip() for k,v in self.fields.items()}}
        try:
            if self.kind=='ask':
                await self.view.cog.ask.callback(self.view.cog,ctx,question=values['question'])
                return
            if self.kind=='month':
                sp.month_report(str(self.view.owner),values['month'])
                self.view.month=values['month']
                self.view.page=0
                await ctx.send('已切換月份。')
            elif self.kind=='budget':
                sp.set_budget(str(self.view.owner),values['month'],values['category'],values['amount'])
                await ctx.send('✅ 預算已儲存。')
            else:
                key=sp.add(str(self.view.owner),values['amount'],values['category'],values['note'],values['date'])
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
        self.tab='總覽'; self.page=0; self.message=None
        self.render()

    async def interaction_check(self,interaction):
        if interaction.user.id==self.owner:
            return True
        await interaction.response.send_message('請用 !記帳說明 開啟自己的看板。',ephemeral=True)
        return False

    def button(self,label,callback,row=1,style=discord.ButtonStyle.secondary,disabled=False):
        button=discord.ui.Button(label=label,row=row,style=style,disabled=disabled)
        button.callback=callback
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
        if self.tab in ('總覽','支出'):
            async def expense(i): await start_entry(self,i,'expense')
            self.button('＋記支出',expense,style=discord.ButtonStyle.success)
        if self.tab in ('總覽','預算'):
            async def budget(i): await start_entry(self,i,'budget')
            self.button('設定預算',budget)
        if self.tab=='固定負擔':
            async def recurring(i):
                from spending_commands import RecurringKindView
                await i.response.send_message('選擇種類：',view=RecurringKindView(self.cog),ephemeral=True)
            self.button('＋新增固定負擔',recurring,style=discord.ButtonStyle.success)
        if self.tab=='AI':
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
                await self.refresh()
                await event.edit_original_response(content='✅ 已切換月份：'+month,view=None)
            await choose_month(i,selected)
        self.button('切換月份',select_month,row=2)
        if self.tab=='預算':
            async def reminders(i):
                from selection_ui import Reminders
                await i.response.send_message('選擇提醒門檻：',view=Reminders(self.cog,self.owner),ephemeral=True)
            self.button('提醒設定',reminders,row=1)
        async def refresh(i):
            await i.response.defer()
            sp.sync_recurring(str(self.owner))
            await self.refresh(i)
        self.button('重新整理',refresh,row=2)
        if self.tab in ('支出','預算','固定負擔'):
            for label,delta,disabled in (('上一頁',-1,self.page==0),('下一頁',1,self.page>=pages-1)):
                async def turn(i,d=delta):
                    await i.response.defer()
                    self.page+=d
                    await self.refresh(i)
                self.button(label,turn,row=2,disabled=disabled)
        async def help(i): await i.response.send_message(embed=self.cog.detail_embed(),ephemeral=True)
        self.button('指令／分類／提醒',help,row=3)
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
    await interaction.response.defer(ephemeral=True,thinking=True)
    ctx=cog.interaction_context(interaction)
    await cog.prepare(ctx)
    view=Dashboard(cog,interaction.user.id)
    view.message=await interaction.followup.send(embed=view.render(),view=view,ephemeral=True,wait=True)


async def start_entry(view,i,kind):
    from selection_ui import choose_category,choose_month,Picker
    import calendar
    async def category_selected(event,category):
        async def month_selected(event,month):
            selections={'category':category,'month':month}
            if kind=='budget':
                await event.response.send_modal(EntryModal(view,kind,selections))
                return
            year,month_num=map(int,month.split('-'))
            count=sp.today().day if month==sp.today().strftime('%Y-%m') else calendar.monthrange(year,month_num)[1]
            async def day_selected(event,day):
                await event.response.send_modal(EntryModal(view,kind,{'category':category,'date':f'{month}-{day:02d}'}))
            await event.response.send_message('選擇日期：',view=Picker(view.owner,[(f'{month}-{d:02d}',d) for d in range(count,0,-1)],day_selected,'選擇日期'),ephemeral=True)
        await choose_month(event,month_selected,future=kind=='budget')
    await choose_category(i,category_selected,budget=kind=='budget')
