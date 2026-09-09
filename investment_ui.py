"""Private investment form entry points sharing existing command services."""
import discord
from selection_ui import OwnedView,SafeModal

FIELDS={
 'price':[('symbol','查詢股票代號')],
 'undo':[('trade_id','要撤銷的交易編號（先查看撤銷預覽）')],
 'buy':[('symbol','股票代號'),('price','每股買入價格'),('shares','買入股數（不是張）')],
 'sell':[('symbol','股票代號'),('price','每股賣出價格'),('shares','賣出股數（不是張）')],
 'remove':[('symbol','要移除的股票代號')],
 'watch':[('symbol','觀察代號（多檔以空格分開）')],
 'unwatch':[('symbol','要移除觀察的代號')],
 'fundbuy':[('name','基金名稱'),('amount','投入金額'),('price','買入淨值')],
 'fundsell':[('name','基金名稱'),('units','贖回單位數'),('price','贖回淨值')],
 'fundprice':[('name','基金名稱'),('price','目前基金淨值')],
}
LABELS={'buy':'買入股票','sell':'賣出股票','remove':'移除股票持倉','watch':'加入觀察','unwatch':'移除觀察','fundbuy':'基金申購','fundsell':'基金贖回','fundprice':'更新基金淨值'}
LABELS.update(price='查詢股價',undo='確認撤銷')


class InvestmentModal(SafeModal):
    def __init__(self,view,action):
        super().__init__(title=LABELS[action])
        self.panel,self.action=view,action
        self.fields={}
        for key,label in FIELDS[action]:
            item=discord.ui.TextInput(label=label,max_length=100 if key in ('name','symbol') else 30)
            self.fields[key]=item
            self.add_item(item)

    async def on_submit(self,i):
        if i.user.id!=self.panel.owner:
            await i.response.send_message('請使用自己的投資面板。',ephemeral=True);return
        await i.response.defer(ephemeral=True,thinking=True)
        ctx=self.panel.app.InteractionContext(i)
        try:
            values=[]
            for key in self.fields:
                value=self.fields[key].value.strip()
                values.append(value if key in ('symbol','name') else int(value) if key=='trade_id' else float(value))
            if self.action=='watch':
                values=values[0].split()
            await self.panel.app.bot.get_command(self.action).callback(ctx,*values)
        except ValueError:
            await ctx.send('金額、價格與數量請填有效數字。')
        except Exception:
            await ctx.send('操作暫時失敗，請稍後再試。')


class InvestmentPanel(OwnedView):
    def __init__(self,app,owner):
        super().__init__(owner)
        self.app=app
        self.tab='股票'
        self.render()

    def render(self):
        self.clear_items()
        embed=discord.Embed(title='📈 投資紀錄 · '+self.tab,color=0x3498db,description='選擇操作，逐格填寫；文字指令仍可使用。')
        for tab in ('股票','基金','觀察','歷史','AI'):
            button=discord.ui.Button(label=tab,row=0,style=discord.ButtonStyle.primary if tab==self.tab else discord.ButtonStyle.secondary)
            async def navigate(i,target=tab):
                self.tab=target
                await i.response.edit_message(embed=self.render(),view=self)
            button.callback=navigate;self.add_item(button)
        actions={'股票':['buy','sell','remove','price'],'基金':['fundbuy','fundsell','fundprice'],'觀察':['watch','unwatch'],'歷史':['undo'],'AI':[]}[self.tab]
        for action in actions:
            button=discord.ui.Button(label=LABELS[action],row=1,style=discord.ButtonStyle.success)
            async def open_form(i,name=action): await i.response.send_modal(InvestmentModal(self,name))
            button.callback=open_form;self.add_item(button)
        readers={'股票':[('查看持股','check'),('投資組合','portfolio')],'基金':[('查看基金','fundcheck')],'觀察':[('觀察清單','watchlist')],'歷史':[('最近交易','history'),('撤銷預覽','undo')],'AI':[('AI 投資摘要','分析')]}[self.tab]
        for label,command in readers:
            button=discord.ui.Button(label=label,row=2)
            async def read(i,name=command):
                await i.response.defer(ephemeral=True,thinking=True)
                ctx=self.app.InteractionContext(i)
                try:
                    await self.app.bot.get_command(name).callback(ctx)
                except Exception:
                    await ctx.send('查詢暫時失敗，請稍後再試。')
            button.callback=read;self.add_item(button)
        embed.set_footer(text='僅本人可見 · 記帳不會實際下單 · 閒置5分鐘後重新開啟')
        return embed
