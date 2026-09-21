"""Private shortcuts, repeat confirmation and deterministic reviews."""
import discord
import spending as sp
from selection_ui import OwnedView, Picker
from presentation import number
from form_ui import InlineForm


class ConfirmExpense(InlineForm):
    def __init__(self,dashboard,original,shortcut_id=None,draft=None):
        super().__init__(dashboard.owner,title='確認新增消費（尚未記帳）',draft=draft)
        self.dashboard,self.original,self.shortcut_id=dashboard,dict(original),shortcut_id
        self.saved=False
        self.reopen=lambda draft:ConfirmExpense(dashboard,original,shortcut_id,draft=draft)
        self.text('amount','金額（元）',str(sp.Decimal(original['cents'])/100) if original.get('cents') is not None else '',limit=30)
        self.category(original['category'])
        self.payment(original=original)
        self.text('date','日期（YYYY-MM-DD）',sp.today().isoformat(),limit=10)
        self.text('note','用途',original['note'])

    async def on_submit(self,i):
        if i.user.id!=self.dashboard.owner or str(i.user.id)!=str(self.original['user_id']):
            await i.response.send_message('請開啟自己的消費表單。',ephemeral=True);return
        values=await self.read_form(i)
        if values is None: return
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            if self.saved:
                await i.followup.send('此表單已儲存，請重新開啟下一筆。',ephemeral=True);return
            if self.shortcut_id is not None:
                sp.shortcut(str(i.user.id),self.shortcut_id)
            key=sp.add(str(i.user.id),values['amount'],values['category'],values['note'],values['date'],values['payment'])
            self.saved=True
            await i.followup.send(f'✅ 已新增消費 #{key}；原紀錄與捷徑未改動。',ephemeral=True)
            await self.dashboard.cog.notify(self.dashboard.cog.interaction_context(i))
            await self.dashboard.refresh()
        except ValueError as error:
            await i.followup.send(str(error),ephemeral=True)


async def open_recent(dashboard,i):
    entries=sp.recent_expenses(str(dashboard.owner))
    async def chosen(event,key):
        # Re-read before opening: another action may have voided the record.
        row=sp.get_expense(str(dashboard.owner),key)
        if row['source']!='manual':
            await event.response.send_message('請重新開啟最近消費清單。',ephemeral=True);return
        await event.response.send_modal(ConfirmExpense(dashboard,row))
    picker=Picker(dashboard.owner,[(f"{r['spent_on']} · {number(r['cents']/100)}元 · {r['category']} · {r['note'][:35]}",r['id']) for r in entries],chosen,'選擇一筆，確認後才新增')
    await i.response.send_message('最近6筆有效手動消費：日期預填今天，確認後才新增。' if entries else '尚無手動消費，可從 !help → 生活記帳 → ＋記一筆消費建立第一筆。',view=picker,ephemeral=True)


class ShortcutForm(InlineForm):
    def __init__(self,picker,original=None,draft=None):
        super().__init__(picker.owner,title='修改常用捷徑' if original else '新增常用捷徑',draft=draft)
        self.picker,self.original=picker,original
        self.reopen=lambda draft:ShortcutForm(picker,original,draft=draft)
        row=original or dict(name='',category=None,note='',cents=None)
        self.text('name','捷徑名稱',row['name'],limit=30)
        self.category(row['category'])
        self.payment(original=original)
        self.text('note','用途',row['note'])
        self.text('amount','預設金額（選填）',str(sp.Decimal(row['cents'])/100) if row['cents'] is not None else '',limit=30,required=False)

    async def on_submit(self,i):
        if i.user.id!=self.picker.owner:
            await i.response.send_message('請使用自己的捷徑面板。',ephemeral=True);return
        v=await self.read_form(i)
        if v is None: return
        await i.response.defer()
        try:
            sp.save_shortcut(str(i.user.id),v['name'],v['category'],v['payment'],v['note'],v['amount'],key=self.original['id'] if self.original else None)
            self.picker.reload()
            await i.edit_original_response(content='✅ 捷徑已儲存；使用時仍須確認消費表單。',view=self.picker,embed=None)
        except ValueError as error:
            await i.followup.send(str(error),ephemeral=True)


class Shortcuts(Picker):
    def __init__(self,dashboard,manage=False):
        self.dashboard,self.manage=dashboard,manage
        super().__init__(dashboard.owner,[],self.chosen_shortcut,'管理捷徑' if manage else '使用捷徑（確認後才記帳）')
        self.reload()

    def reload(self):
        self.records=sp.shortcuts(str(self.owner),self.manage)
        self.items=[(r['name']+('（停用）' if not r['active'] else '')+f" · {r['category']} · "+(f"{number(r['cents']/100)}元" if r['cents'] is not None else '金額未填'),r['id']) for r in self.records]
        self.page=min(self.page,max(0,(len(self.items)-1)//25))
        self.build()

    def build(self):
        super().build()
        for label,callback in (('＋新增捷徑',self.new),('使用捷徑' if self.manage else '管理／排序／停用',self.toggle)):
            b=discord.ui.Button(label=label,row=2);b.callback=callback;self.add_item(b)
        if self.manage:
            b=discord.ui.Button(label='查看推薦',row=2)
            b.callback=self.recommend;self.add_item(b)

    async def recommend(self,i):
        if not await self.interaction_check(i):return
        result=sp.shortcut_recommendation(str(self.owner))
        if result is None:
            await i.response.send_message('近 30 天尚無明顯高於首頁捷徑的消費習慣。需先有三個首頁捷徑。',ephemeral=True)
            return
        view=RecommendationView(self,result)
        await i.response.send_message(embed=view.render(),view=view,ephemeral=True)

    async def new(self,i):
        await i.response.send_modal(ShortcutForm(self))

    async def toggle(self,i):
        self.manage=not self.manage
        self.title='管理捷徑' if self.manage else '使用捷徑（確認後才記帳）'
        self.reload()
        await i.response.edit_message(content='選擇捷徑進行管理（含停用項目）。' if self.manage else '選擇捷徑，確認表單後才新增消費。',view=self,embed=None)

    async def chosen_shortcut(self,i,key):
        row=next((r for r in sp.shortcuts(str(self.owner),True) if r['id']==key),None)
        if row is None:
            await i.response.send_message('捷徑已變動，請重新開啟。',ephemeral=True);return
        if not self.manage:
            row=sp.shortcut(str(self.owner),key)
            await i.response.send_modal(ConfirmExpense(self.dashboard,row,key));return
        view=OwnedView(self.owner)
        async def edit(event): await event.response.send_modal(ShortcutForm(self,row))
        async def change(event,action):
            if action=='停用': sp.disable_shortcut(str(self.owner),key)
            else: sp.move_shortcut(str(self.owner),key,-1 if action=='往上移' else 1)
            self.reload()
            await event.response.edit_message(content='✅ 捷徑已更新，歷史消費不受影響。',view=self,embed=None)
        for label in ('修改','往上移','往下移','停用'):
            b=discord.ui.Button(label=label,disabled=not row['active'])
            async def click(event,action=label):
                if action=='修改': await edit(event)
                else: await change(event,action)
            b.callback=click;view.add_item(b)
        b=discord.ui.Button(label='返回清單')
        async def back(event):
            self.reload();await event.response.edit_message(content='常用捷徑清單',view=self,embed=None)
        b.callback=back;view.add_item(b)
        embed=discord.Embed(title=row['name'],description=f"分類：{discord.utils.escape_markdown(row['category'])}\n來源：{discord.utils.escape_markdown(row['payment_source_name'] or '來源不存在')}\n用途：{discord.utils.escape_markdown(row['note'])}\n金額："+(number(row['cents']/100)+'元' if row['cents'] is not None else '每次填寫'))
        await i.response.edit_message(content='管理捷徑；不會寫入消費。',embed=embed,view=view)


class RecommendationView(OwnedView):
    def __init__(self,picker,result):
        super().__init__(picker.owner)
        self.picker,self.result=picker,result
        for label,callback in (('採用建議',self.adopt),('返回',self.back)):
            button=discord.ui.Button(label=label)
            button.callback=callback;self.add_item(button)

    def render(self):
        r=self.result;c=r['candidate'];t=r['target']
        embed=discord.Embed(title='30 天智慧捷徑推薦',description=f"{r['period_start']} ～ {r['period_end']}\n僅依已記錄資料統計；不會自動取代首頁捷徑。")
        for title,item,count in (('候選',c,c['count']),('建議取代',t,r['lowest_count'])):
            embed.add_field(name=f'{title} · {count} 次',value=discord.utils.escape_markdown(
                f"{item.get('name',item['note'])}\n分類：{item['category']}\n付款來源：{item['payment_source_name']}\n用途：{item['note']}"),inline=False)
        embed.set_footer(text='採用只開修改表單；確認後才更新。預設金額保留原值。僅本人可見')
        return embed

    async def adopt(self,i):
        if not await self.interaction_check(i):return
        current=sp.shortcut_recommendation(str(self.owner))
        if current is None or current['target']['id']!=self.result['target']['id'] or any(
                current['candidate'][key]!=self.result['candidate'][key] for key in ('category','payment_source_id','note')):
            await i.response.send_message('推薦資料已變動，請返回管理頁重新查看。',ephemeral=True);return
        c=current['candidate']
        draft=dict(name=c['note'][:30],category=c['category'],payment=c['payment_source_id'],note=c['note'])
        await i.response.send_modal(ShortcutForm(self.picker,current['target'],draft=draft))

    async def back(self,i):
        if not await self.interaction_check(i):return
        self.picker.reload()
        await i.response.edit_message(content='常用捷徑管理；未採用推薦。',embed=None,view=self.picker)


async def open_shortcut(dashboard,i,shortcut_id):
    if i.user.id!=dashboard.owner:
        await i.response.send_message('請開啟自己的生活看板。',ephemeral=True);return
    try:
        row=sp.shortcut(str(dashboard.owner),shortcut_id)
        await i.response.send_modal(ConfirmExpense(dashboard,row,shortcut_id))
    except ValueError as error:
        await i.response.send_message(str(error),ephemeral=True)


async def open_shortcuts(dashboard,i,manage=False):
    view=Shortcuts(dashboard,manage=manage)
    await i.response.send_message('常用捷徑：選取後先確認，不會直接記帳。' if view.items else '尚無捷徑，按「＋新增捷徑」建立常用消費。',view=view,ephemeral=True)


class Reviews(OwnedView):
    def __init__(self,owner):
        super().__init__(owner)
        self.unit,self.page='week',0

    def render(self):
        data=sp.review(str(self.owner),self.unit)
        current,previous,comparison=data['current'],data['previous'],data['comparison']
        embed=discord.Embed(title='本週回顧' if self.unit=='week' else '本月回顧',color=0x3498db,
            description=f"{current['start']} ～ {current['end']} · TWD\n"+(f"已記錄 **{number(current['total'])} 元**（{current['record_count']}筆）" if current['has_records'] else '此期間尚無消費紀錄，資料不足，不能視為沒有消費。'))
        ranges=f"本期比較：{comparison['start']} ～ {comparison['end']}\n上期比較：{previous['start']} ～ {previous['end']}"
        difference='任一期無紀錄，資料不足，不計算差額。' if data['difference'] is None else f"已記錄金額差額：{number(data['difference'],True)} 元；不代表真實消費增減。"
        fields=[('同天數比較',ranges+'\n'+difference)]
        if comparison['end']!=current['end']:
            fields.append(('短月比較','上月較短，差額只比較雙方共同天數；上方本月總額仍截至今天。'))
        for name,item in sorted(current['categories'].items(),key=lambda p:p[1]['amount'],reverse=True)[:3]:
            if item['amount']: fields.append(('主要分類：'+name,f"{number(item['amount'])} 元 · {number(item['share'])}%"))
        if self.unit=='month':
            fields.extend(('付款來源：'+r['payment_source_name'],f"{number(r['cents']/100)} 元") for r in data['payments'])
            budgets=current['budgets']
            for b in budgets:
                value=(f"已記錄 {number(b['spent'])}／{number(b['budget'])} 元 · 使用率 {number(b['used_percent'])}%\n"+('超支 ' if b['remaining']<0 else '剩餘 ')+number(abs(b['remaining']))+' 元') if current['has_records'] else f"預算額度 {number(b['budget'])} 元；尚無消費紀錄，資料不足，暫不計算使用率。"
                fields.append(('預算：'+b['category'],value))
            if not budgets: fields.append(('預算','尚未設定本月預算。'))
        pages=max(1,(len(fields)+7)//8);self.page=min(self.page,pages-1)
        for name,value in fields[self.page*8:self.page*8+8]: embed.add_field(name=name,value=value,inline=False)
        embed.set_footer(text=f'第 {self.page+1}／{pages} 頁 · 僅依已記錄資料統計 · 未記錄不代表零 · 僅本人可見')
        self.clear_items()
        for label,unit in (('本週回顧','week'),('本月回顧','month')):
            b=discord.ui.Button(label=label,row=0)
            async def change(i,value=unit):
                self.unit,self.page=value,0;await i.response.edit_message(embed=self.render(),view=self)
            b.callback=change;self.add_item(b)
        for label,d,disabled in (('上一頁',-1,self.page==0),('下一頁',1,self.page==pages-1)):
            b=discord.ui.Button(label=label,row=1,disabled=disabled)
            async def turn(i,value=d):
                self.page+=value;await i.response.edit_message(embed=self.render(),view=self)
            b.callback=turn;self.add_item(b)
        return embed


async def open_reviews(dashboard,i,unit='week'):
    await i.response.defer(ephemeral=True,thinking=True)
    view=Reviews(dashboard.owner)
    view.unit=unit
    await i.followup.send(embed=view.render(),view=view,ephemeral=True)
