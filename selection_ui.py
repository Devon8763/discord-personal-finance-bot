"""Owner-bound dropdowns with pagination, shared by forms."""
import discord
import spending as sp


class SafeModal(discord.ui.Modal):
    async def on_error(self,interaction,error):
        text='操作暫時失敗，請重新開啟表單。'
        if interaction.response.is_done():
            await interaction.followup.send(text,ephemeral=True)
        else:
            await interaction.response.send_message(text,ephemeral=True)


class OwnedView(discord.ui.View):
    def __init__(self,owner,**kwargs):
        super().__init__(timeout=300,**kwargs)
        self.owner=owner

    async def interaction_check(self,interaction):
        if interaction.user.id==self.owner:
            return True
        await interaction.response.send_message('請開啟自己的操作面板。',ephemeral=True)
        return False

    async def on_error(self,interaction,error,item):
        text='操作暫時失敗，請重新開啟面板。'
        if interaction.response.is_done():
            await interaction.followup.send(text,ephemeral=True)
        else:
            await interaction.response.send_message(text,ephemeral=True)


class Picker(OwnedView):
    def __init__(self,owner,items,callback,title,add_category=False,page=0):
        super().__init__(owner)
        self.items,self.chosen,self.title,self.add_category=items,callback,title,add_category
        self.page=max(0,min(page,max(0,(len(items)-1)//25)))
        self.build()

    def build(self):
        self.clear_items()
        values=self.items[self.page*25:self.page*25+25]
        if values:
            select=discord.ui.Select(placeholder=self.title,options=[discord.SelectOption(label=label[:100],value=str(index)) for index,(label,value) in enumerate(values)])
            async def selected(i):
                value=values[int(select.values[0])][1]
                await self.chosen(i,value)
            select.callback=selected
            self.add_item(select)
        for label,delta,disabled in (('上一頁',-1,self.page==0),('下一頁',1,(self.page+1)*25>=len(self.items))):
            button=discord.ui.Button(label=label,row=1,disabled=disabled)
            async def turn(i,d=delta):
                self.page+=d
                self.build()
                await i.response.edit_message(view=self)
            button.callback=turn
            self.add_item(button)
        if self.add_category:
            button=discord.ui.Button(label='＋新增分類',style=discord.ButtonStyle.success,row=1)
            async def add(i): await i.response.send_modal(NewCategory(self))
            button.callback=add
            self.add_item(button)


class NewCategory(SafeModal):
    def __init__(self,picker):
        super().__init__(title='新增分類')
        self.picker=picker
        self.name=discord.ui.TextInput(label='分類名稱',max_length=20)
        self.add_item(self.name)

    async def on_submit(self,i):
        if i.user.id!=self.picker.owner:
            await i.response.send_message('請開啟自己的面板。',ephemeral=True)
            return
        try:
            name=self.name.value.strip()
            sp.set_category(str(i.user.id),name,True)
            total=[('整體預算（總額）','總額')] if any(v=='總額' for _,v in self.picker.items) else []
            self.picker.items=total+[(n,n) for n in sp.category_names(str(i.user.id))]
            self.picker.page=(len(self.picker.items)-1)//25
            self.picker.build()
            await i.response.edit_message(content=f'✅ 已新增 {name}，請從下拉選單選擇。',view=self.picker)
        except ValueError as error:
            await i.response.send_message(str(error),ephemeral=True)


def month_items(user_id,future=False,recurring=False):
    current=sp.today().replace(day=1)
    if recurring:
        return [('本月 · '+current.strftime('%Y-%m'),current.strftime('%Y-%m')),('下月 · '+sp.next_month(current).strftime('%Y-%m'),sp.next_month(current).strftime('%Y-%m'))]
    months={current.strftime('%Y-%m')}
    cursor=current
    from datetime import timedelta
    for _ in range(36):
        months.add(cursor.strftime('%Y-%m'))
        cursor=(cursor-timedelta(days=1)).replace(day=1)
    for row in sp.rows('SELECT DISTINCT substr(spent_on,1,7) AS month FROM expenses WHERE user_id=? UNION SELECT month FROM budgets WHERE user_id=?',(str(user_id),str(user_id))):
        if future or row['month']<=current.strftime('%Y-%m'):
            months.add(row['month'])
    if future:
        cursor=current
        for _ in range(12):
            cursor=sp.next_month(cursor);months.add(cursor.strftime('%Y-%m'))
    return [(m,m) for m in sorted(months,reverse=True)]


async def choose_category(i,callback,budget=False):
    names=sp.category_names(str(i.user.id))
    items=[(n,n) for n in names]
    if budget:
        items.insert(0,('整體預算（總額）','總額'))
    await i.response.send_message('選擇分類，或直接新增：',view=Picker(i.user.id,items,callback,'選擇分類',True),ephemeral=True)


async def choose_month(i,callback,future=False,recurring=False):
    await i.response.send_message('選擇月份：',view=Picker(i.user.id,month_items(i.user.id,future,recurring),callback,'選擇月份'),ephemeral=True)


class Reminders(OwnedView):
    def __init__(self,cog,owner):
        super().__init__(owner)
        self.cog=cog
        for label,levels in (('預設 80%／100%',None),('50%／80%／100%',[50,80,100]),('90%／100%',[90,100])):
            button=discord.ui.Button(label=label)
            async def use(i,values=levels):
                sp.set_reminders(str(i.user.id),values)
                await i.response.send_message('✅ 提醒門檻：'+'／'.join(f'{v}%' for v in sp.reminder_levels(str(i.user.id))),ephemeral=True)
                await self.cog.notify(self.cog.interaction_context(i))
            button.callback=use
            self.add_item(button)
        select=discord.ui.Select(placeholder='或勾選常用百分比（可多選）',row=1,min_values=1,max_values=8,options=[discord.SelectOption(label=f'{v}%',value=str(v)) for v in (25,50,60,70,80,90,100,120)])
        async def pick(i):
            sp.set_reminders(str(i.user.id),[int(v) for v in select.values])
            await i.response.send_message('✅ 提醒已更新：'+'／'.join(f'{v}%' for v in sp.reminder_levels(str(i.user.id))),ephemeral=True)
            await self.cog.notify(self.cog.interaction_context(i))
        select.callback=pick
        self.add_item(select)
        custom=discord.ui.Button(label='自訂其他百分比',row=2)
        async def customize(i): await i.response.send_modal(ReminderModal(self))
        custom.callback=customize
        self.add_item(custom)


class ReminderModal(SafeModal):
    def __init__(self,view):
        super().__init__(title='自訂提醒門檻')
        self.view=view
        self.levels=discord.ui.TextInput(label='百分比（以空格分開）',placeholder='例如：40 75 100',max_length=60)
        self.add_item(self.levels)

    async def on_submit(self,i):
        if i.user.id!=self.view.owner:
            await i.response.send_message('請使用自己的面板。',ephemeral=True);return
        try:
            levels=[int(v.rstrip('%')) for v in self.levels.value.split()]
            sp.set_reminders(str(i.user.id),levels)
            await i.response.send_message('✅ 提醒已更新。',ephemeral=True)
            await self.view.cog.notify(self.view.cog.interaction_context(i))
        except ValueError:
            await i.response.send_message('請輸入1～10個整數百分比，範圍1～1000。',ephemeral=True)
