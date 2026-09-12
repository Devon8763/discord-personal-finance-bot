"""Discord modal inputs with inline selects and draft-preserving overflow."""
import discord
import spending as sp
from selection_ui import SafeModal, OwnedView, Picker, PaymentPicker, month_items

MORE = object()
NEW = object()
UNSET = object()


class FormSelect(discord.ui.Select):
    def __init__(self,items,default=UNSET,extend=None):
        self.all_items=list(items)
        self.extend=extend
        can_add=extend in ('category','payment')
        limit=23 if can_add else 24 if extend or len(items)>25 else 25
        self.choices=list(items[:limit])
        if default is not UNSET and not any(v==default for _,v in self.choices):
            selected=next((pair for pair in items if pair[1]==default),None)
            if selected:
                self.choices=self.choices[:limit-1]+[selected]
        if extend or len(items)>25:
            self.choices.append(('其他選項…（保留已填內容）' if can_add else '其他月份…（保留已填內容）',MORE))
        if can_add:
            self.choices.append(('＋新增分類' if extend=='category' else '＋新增付款來源',NEW))
        super().__init__(options=[discord.SelectOption(label=label[:100],value=str(n),default=value==default) for n,(label,value) in enumerate(self.choices)])

    @property
    def value(self):
        if self.values:
            try:
                index=int(self.values[0])
                if 0<=index<len(self.choices): return self.choices[index][1]
            except (ValueError,TypeError):
                pass
            raise ValueError('選項已失效，請重新開啟表單。')
        for option,(_,value) in zip(self.options,self.choices):
            if option.default: return value
        raise ValueError('請在表單內選擇分類、來源或月份。')


class InlineForm(SafeModal):
    def __init__(self,owner,*,title,draft=None):
        super().__init__(title=title)
        self.owner=owner
        self.draft=draft or {}
        self.fields={}

    def text(self,key,label,default=None,limit=200,required=True):
        field=discord.ui.TextInput(default=self.draft.get(key,default),max_length=limit,required=required)
        self.fields[key]=field
        self.add_item(discord.ui.Label(text=label,component=field))
        return field

    def select(self,key,label,items,default=UNSET,extend=None):
        field=FormSelect(items,self.draft.get(key,default),extend)
        self.fields[key]=field
        self.add_item(discord.ui.Label(text=label,component=field))
        return field

    def category(self,default=UNSET,budget=False,keep=False):
        items=[(n,n) for n in sp.category_names(str(self.owner))]
        if budget: items.insert(0,('整體預算（總額）','總額'))
        if keep and default is not UNSET and default not in [v for _,v in items]:
            items.insert(0,('保留原分類：'+default,default))
        return self.select('category','分類',items,default,'category')

    def payment(self,default=UNSET,original=None,keep=False):
        items=[(p['name'],p['id']) for p in sp.payment_sources(str(self.owner))]
        if default is UNSET and original is None:
            default=next(v for n,v in items if n=='未指定')
        if original is not None:
            default=original.get('payment_source_id')
            if default is None: default=next(v for n,v in items if n=='未指定')
        if keep:
            items.insert(0,('保留原來源：'+original['payment_source_name'],'keep'))
            default='keep'
        return self.select('payment','付款來源',items,default,'payment')

    def month(self,default,future=False):
        return self.select('month','月份',month_items(self.owner,future=future),default,'month')

    async def read_form(self,i):
        if i.user.id!=self.owner:
            await i.response.send_message('請開啟自己的表單。',ephemeral=True)
            return None
        try:
            values={k:(f.value if isinstance(f,FormSelect) else f.value.strip()) for k,f in self.fields.items()}
        except ValueError as error:
            await i.response.send_message(str(error),ephemeral=True);return None
        for key,value in values.items():
            if value is NEW:
                view=OwnedView(self.owner)
                label='＋新增分類' if key=='category' else '＋新增付款來源'
                button=discord.ui.Button(label=label,style=discord.ButtonStyle.success)
                async def add(event,target=key):
                    if await view.interaction_check(event):
                        await event.response.send_modal(NewFormOption(self,values,target))
                button.callback=add;view.add_item(button)
                await i.response.send_message('已保留表單內容，尚未記帳。按下方按鈕填寫新名稱。',view=view,ephemeral=True)
                return None
            if value is not MORE: continue
            field=self.fields[key]
            async def selected(event,choice,target=key):
                draft={**values,target:choice}
                await event.response.send_modal(self.reopen(draft))
            if field.extend=='payment':
                view=PaymentPicker(self.owner,selected)
            else:
                view=Picker(self.owner,field.all_items,selected,'選擇後返回原表單',add_category=field.extend=='category')
            await i.response.send_message('已保留表單內容，尚未儲存。選取或新增項目後返回原表單。',view=view,ephemeral=True)
            return None
        return values


class NewFormOption(SafeModal):
    def __init__(self,form,values,key):
        super().__init__(title='新增分類' if key=='category' else '新增付款來源')
        self.form,self.values,self.key=form,dict(values),key
        self.choice=UNSET
        self.name=discord.ui.TextInput(max_length=20 if key=='category' else 30)
        self.add_item(discord.ui.Label(text='分類名稱' if key=='category' else '付款來源名稱',component=self.name))

    async def on_submit(self,i):
        if i.user.id!=self.form.owner:
            await i.response.send_message('請開啟自己的表單。',ephemeral=True);return
        try:
            if self.choice is UNSET:
                name=self.name.value.strip()
                if self.key=='category':
                    sp.set_category(str(self.form.owner),name,True)
                    self.choice=name
                else:
                    self.choice=sp.add_payment_source(str(self.form.owner),name)
            draft={**self.values,self.key:self.choice}
            view=OwnedView(self.form.owner)
            button=discord.ui.Button(label='返回原表單',style=discord.ButtonStyle.primary)
            async def back(event):
                if await view.interaction_check(event):
                    await event.response.send_modal(self.form.reopen(draft))
            button.callback=back;view.add_item(button)
            await i.response.edit_message(content='✅ 已新增。返回原表單會自動選好新項目；確認儲存後才記帳。',view=view)
        except ValueError as error:
            await i.response.send_message(str(error),ephemeral=True)
