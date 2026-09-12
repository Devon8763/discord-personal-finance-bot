"""Owner-bound dropdowns with pagination, shared by forms."""
import discord
import spending as sp
from privacy_rules import private_interaction


class SafeModal(discord.ui.Modal):
    async def interaction_check(self,interaction):
        return await private_interaction(interaction)

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
        if not await private_interaction(interaction):return False
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
    def __init__(self,owner,items,callback,title,add_category=False,page=0,page_size=25):
        super().__init__(owner)
        self.items,self.chosen,self.title,self.add_category=items,callback,title,add_category
        self.page_size = min(25, max(1, page_size))
        self.extra_buttons = []
        self.page=max(0,min(page,max(0,(len(items)-1)//self.page_size)))
        self.build()

    def page_content(self):
        return {}

    def build(self):
        self.clear_items()
        values=self.items[self.page*self.page_size:(self.page+1)*self.page_size]
        if values:
            select=discord.ui.Select(placeholder=self.title,options=[discord.SelectOption(label=label[:100],value=str(index)) for index,(label,value) in enumerate(values)])
            async def selected(i):
                value=values[int(select.values[0])][1]
                await self.chosen(i,value)
            select.callback=selected
            self.add_item(select)
        for label,delta,disabled in (('上一頁',-1,self.page==0),('下一頁',1,(self.page+1)*self.page_size>=len(self.items))):
            button=discord.ui.Button(label=label,row=1,disabled=disabled)
            async def turn(i,d=delta):
                self.page+=d
                self.build()
                await i.response.edit_message(view=self, **self.page_content())
            button.callback=turn
            self.add_item(button)
        for button in self.extra_buttons:
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


class PaymentPicker(Picker):
    def __init__(self, owner, callback=None, manage=False):
        self.manage = manage
        super().__init__(owner, [], callback or self.manage_source, '選擇付款來源')
        self.reload()

    def reload(self):
        self.sources = sp.payment_sources(str(self.owner), self.manage)
        self.items = [(p['name'] + ('（停用）' if not p['active'] else ''), p['id']) for p in self.sources]
        self.page = min(self.page, max(0, (len(self.items)-1)//25))
        self.build()

    def build(self):
        super().build()
        button = discord.ui.Button(label='＋新增付款來源', style=discord.ButtonStyle.success, row=1)
        async def add(i):
            await i.response.send_modal(NewPaymentSource(self))
        button.callback = add
        self.add_item(button)
        if not self.manage:
            for name, key in self.items:
                if name not in ('現金', '未指定'):
                    continue
                button = discord.ui.Button(label=name, row=2)
                async def quick(i, value=key):
                    await self.chosen(i, value)
                button.callback = quick
                self.add_item(button)

    async def manage_source(self, i, key):
        source = next((p for p in sp.payment_sources(str(self.owner), True) if p['id'] == key), None)
        if source is None:
            await i.response.send_message('付款來源已變動，請重新開啟。', ephemeral=True)
            return
        view = OwnedView(self.owner)
        async def rename(event):
            await event.response.send_modal(NewPaymentSource(self, source))
        async def disable(event):
            sp.disable_payment_source(str(self.owner), key)
            self.reload()
            await event.response.edit_message(content='✅ 已停用，既有消費保留歷史名稱。', view=self)
        async def back(event):
            self.reload()
            await event.response.edit_message(content='付款來源（含停用項目）：', view=self)
        builtin = source['name'] in ('現金', '未指定')
        for label, callback, disabled in (('修改名稱', rename, builtin), ('停用', disable, builtin or not source['active']), ('返回清單', back, False)):
            button = discord.ui.Button(label=label, disabled=disabled)
            button.callback = callback
            view.add_item(button)
        await i.response.edit_message(content=f"付款來源：{discord.utils.escape_markdown(source['name'])}｜" + ('啟用' if source['active'] else '停用') + '\n改名或停用不會更改歷史消費。現金與未指定為保留來源。', view=view, allowed_mentions=discord.AllowedMentions.none())


class NewPaymentSource(SafeModal):
    def __init__(self, picker, source=None):
        super().__init__(title='修改付款來源名稱' if source else '新增付款來源')
        self.picker, self.source = picker, source
        self.name = discord.ui.TextInput(label='付款來源名稱', max_length=30, default=source['name'] if source else None,
                                         placeholder='例如：街口支付、玉山信用卡、台新帳戶')
        self.add_item(self.name)

    async def on_submit(self, i):
        if i.user.id != self.picker.owner:
            await i.response.send_message('請使用自己的付款來源面板。', ephemeral=True)
            return
        try:
            if self.source:
                sp.rename_payment_source(str(self.picker.owner), self.source['id'], self.name.value)
            else:
                sp.add_payment_source(str(self.picker.owner), self.name.value)
            self.picker.reload()
            if not self.source:
                self.picker.page = (len(self.picker.items)-1)//25
                self.picker.build()
            await i.response.edit_message(content='✅ 付款來源已儲存，請從選單選擇。', view=self.picker)
        except ValueError as error:
            await i.response.send_message(str(error), ephemeral=True)


class DatePicker(Picker):
    def __init__(self, owner, callback):
        self.month = sp.today().replace(day=1)
        super().__init__(owner, [], callback, '直接選擇完整日期')

    def page_content(self):
        import calendar
        grid = '\n'.join(calendar.TextCalendar().formatmonth(self.month.year, self.month.month).splitlines()[2:])
        embed = discord.Embed(title=f'📅 {self.month:%Y-%m} · 選擇日期',
            description='```\n一 二 三 四 五 六 日\n' + grid + '\n```\n直接從下拉選單選擇完整日期；切月與翻頁會更新此卡片。', color=0x2ecc71)
        embed.set_footer(text=f'日期第 {self.page+1}／{max(1,(len(self.items)+24)//25)} 頁 · 未來日期不可選 · 僅本人可見')
        return {'embed': embed}

    def build(self):
        import calendar
        from datetime import timedelta
        count = min(calendar.monthrange(self.month.year,self.month.month)[1],
                    sp.today().day if self.month == sp.today().replace(day=1) else 31)
        self.items = [(f'{self.month:%Y-%m}-{d:02d}（週'+'一二三四五六日'[self.month.replace(day=d).weekday()]+'）', f'{self.month:%Y-%m}-{d:02d}') for d in range(count,0,-1)]
        self.page = min(self.page, (len(self.items)-1)//25)
        super().build()
        select = self.children[0]
        values = self.items[self.page*25:self.page*25+25]
        select.options = [discord.SelectOption(label=label,value=value) for label,value in values]
        async def selected(i):
            await self.chosen(i, select.values[0])
        select.callback = selected
        for label, target, disabled in (
            ('上個月', (self.month-timedelta(days=1)).replace(day=1), self.month.year==1 and self.month.month==1),
            ('本月', sp.today().replace(day=1), False),
            ('下個月', sp.next_month(self.month), self.month >= sp.today().replace(day=1)),
        ):
            button = discord.ui.Button(label=label,row=2,disabled=disabled)
            async def move(i, value=target):
                self.month, self.page = value, 0
                self.build()
                await i.response.edit_message(**self.page_content(),view=self)
            button.callback = move
            self.add_item(button)
        for label, day in (('今天',sp.today()),('昨天',sp.today()-timedelta(days=1))):
            button = discord.ui.Button(label=label,row=3,style=discord.ButtonStyle.primary)
            async def quick(i, value=day.isoformat()): await self.chosen(i,value)
            button.callback = quick
            self.add_item(button)


async def choose_date(i, callback):
    view = DatePicker(i.user.id,callback)
    await i.response.send_message(**view.page_content(),view=view,ephemeral=True)


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
