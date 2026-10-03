"""Discord UI for independent living expenses."""
import asyncio
import time
import discord
from form_ui import InlineForm
from discord.ext import commands, tasks
import life_ledger_service as life_service
import spending as sp
from presentation import number, NOTE, discord_spending_error


async def send(ctx, text):
    for offset in range(0,len(text),1800):
        await ctx.send(text[offset:offset+1800], allowed_mentions=discord.AllowedMentions.none())


def format_report(report, include_note=True):
    lines = [f"💰 {report['start']} ～ {report['end']}｜TWD", f"已記錄支出：{number(report['total'])} 元（{report['record_count']} 筆）", f"其中固定／分期／訂閱：{number(report['fixed'])} 元"]
    for cat, item in report['categories'].items():
        if item['amount']:
            lines.append(f"{cat}：{number(item['amount'])} 元｜占比 {number(item['share'])}%")
    for budget in report.get('budgets',[]):
        usage = f"使用率 {number(budget['used_percent'])}%" if budget['used_percent'] is not None else '使用率不適用（零元預算）'
        lines.append(f"🎯 {budget['category']}：已用 {number(budget['spent'])}／預算 {number(budget['budget'])}｜{usage}｜剩餘 {number(budget['remaining'])}")
    if not report.get('budgets'):
        lines.append('尚未設定本月預算')
    if include_note:
        lines.append(NOTE)
    return '\n\n'.join(lines)


class RecurringModal(InlineForm):
    def __init__(self,cog,kind='固定',selections=None,owner=None,draft=None):
        if owner is None: raise ValueError('固定項目表單必須指定操作者')
        super().__init__(owner,title='新增固定支出／訂閱／分期',draft=draft)
        self.cog=cog
        self.reopen=lambda draft:RecurringModal(cog,kind,owner=owner,draft=draft)
        selected=selections or {}
        self.item_name=self.text('name','項目名稱',limit=100)
        self.amount=self.text('amount','每月／每期金額（元）',limit=30)
        category_default=selected.get('category')
        self.category=self.category(category_default)
        months=[sp.today().strftime('%Y-%m'),sp.next_month(sp.today().replace(day=1)).strftime('%Y-%m')]
        choices=[(f'{k} · {m}',(k,m)) for k in ('固定','訂閱','分期') for m in months]
        self.plan=self.select('plan','種類與開始月份',choices,(kind,selected.get('month',months[0])))
        self.periods=self.text('periods','總期數（分期必填；固定／訂閱留空）',limit=3,required=False)

    async def on_submit(self,interaction):
        if self.owner is not None and self.owner!=interaction.user.id:
            await interaction.response.send_message('請使用自己的表單。',ephemeral=True)
            return
        values=await self.read_form(interaction)
        if values is None: return
        await interaction.response.defer(ephemeral=True,thinking=True)
        ctx=self.cog.interaction_context(interaction)
        try:
            kind,month=values['plan']
            periods=int(values['periods']) if values['periods'] else 0
            await self.cog.prepare(ctx)
            key=sp.add_recurring(str(interaction.user.id),kind,values['name'],values['amount'],values['category'],month,periods)
            await send(ctx,f'✅ 已新增{kind} #{key}\n項目：{values["name"]}\n金額：{number(values["amount"])} 元／月\n分類：{values["category"]}\n開始：{month}'+(f'\n期數：{periods}' if periods else ''))
            await self.cog.prepare(ctx)
        except ValueError as error:
            await ctx.send('請檢查欄位：'+discord_spending_error(error))


class RecurringKindView(discord.ui.View):
    def __init__(self,cog):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label='訂閱（每月持續）',style=discord.ButtonStyle.primary)
    async def subscription(self,interaction,button):
        await self.choose(interaction,'訂閱')

    @discord.ui.button(label='固定支出（例如房租）',style=discord.ButtonStyle.secondary)
    async def fixed(self,interaction,button):
        await self.choose(interaction,'固定')

    @discord.ui.button(label='分期（有總期數）',style=discord.ButtonStyle.success)
    async def installment(self,interaction,button):
        await self.choose(interaction,'分期')

    async def choose(self,i,kind):
        from privacy_rules import private_interaction
        if not await private_interaction(i):return
        await i.response.send_modal(RecurringModal(self.cog,kind,owner=i.user.id))


class SpendingView(discord.ui.View):
    def __init__(self,cog):
        super().__init__(timeout=300)
        self.cog=cog

    @discord.ui.button(label='💰 開啟生活看板',style=discord.ButtonStyle.success)
    async def month(self,interaction,button):
        from dashboard import open_dashboard
        await open_dashboard(self.cog,interaction)

    @discord.ui.button(label='📖 指令說明',style=discord.ButtonStyle.secondary)
    async def instructions(self,interaction,button):
        await interaction.response.send_message(embed=self.cog.detail_embed(),ephemeral=True)


class Spending(commands.Cog):
    def __init__(self, bot, interaction_context):
        self.bot = bot
        self.interaction_context = interaction_context
        self.notice_lock = asyncio.Lock()
        self.dm_retry_after = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.monthly.is_running():
            self.monthly.start()

    def cog_unload(self):
        self.monthly.cancel()

    @tasks.loop(minutes=1)
    async def monthly(self):
        try:
            sp.sync_recurring()
            users = sp.rows('SELECT DISTINCT user_id FROM spending_notices WHERE delivered=0')
            for row in users:
                if time.monotonic() < self.dm_retry_after.get(row['user_id'],0):
                    continue
                try:
                    user = self.bot.get_user(int(row['user_id'])) or await self.bot.fetch_user(int(row['user_id']))
                    async with self.notice_lock:
                        for notice in sp.notices(row['user_id']):
                            await user.send(notice['body'],allowed_mentions=discord.AllowedMentions.none())
                            sp.delivered(notice['id'],row['user_id'])
                except (discord.HTTPException,ValueError):
                    # Keep pending so the user's next spending command can show it.
                    self.dm_retry_after[row['user_id']] = time.monotonic()+3600
                    continue
        except Exception as error:
            print('生活記帳背景作業：',type(error).__name__)

    async def prepare(self,ctx):
        sp.sync_recurring(str(ctx.author.id))
        await self.notify(ctx)

    async def cog_app_command_error(self, interaction, error):
        text = '生活入口暫時無法開啟，請稍後重試或使用 !help／!記帳說明。'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def notify(self,ctx):
        # Send actual notices only after the batch receipt; never mark buffered notices delivered.
        if getattr(ctx, 'spending_batch', False):
            return
        user_id = str(ctx.author.id)
        async with self.notice_lock:
            for notice in sp.notices(user_id):
                await send(ctx,notice['body'])
                sp.delivered(notice['id'],user_id)

    async def process_batch(self, message, lines):
        from copy import copy
        from message_input import BATCH_SPENDING_COMMANDS
        if message.guild is not None or not 2 <= len(lines) <= 50 or any(line.split()[0] not in BATCH_SPENDING_COMMANDS for line in lines):
            raise ValueError('批次生活記帳限私訊的2～50筆支援指令')
        # Keep background delivery behind the batch receipt. Reuse the existing notice lock.
        async with self.notice_lock:
            receipts = []
            ctx = None
            for index, line in enumerate(lines, 1):
                single = copy(message)
                single.content = line
                ctx = await self.bot.get_context(single)
                original_send = ctx.send
                ctx.spending_batch = True
                messages = []
                async def collect(content=None, **kwargs):
                    if content:
                        messages.append(str(content))
                ctx.send = collect
                try:
                    # Keep Discord's parser, checks, hooks and each command's atomic write.
                    await ctx.command.invoke(ctx)
                except commands.CommandError as error:
                    cause = getattr(error, 'original', error)
                    if isinstance(cause, ValueError):
                        detail = discord_spending_error(cause)
                    elif isinstance(error, commands.UserInputError):
                        detail = f'參數格式錯誤：!{ctx.command.qualified_name} {ctx.command.signature}'
                    else:
                        detail = '操作未完成，請先核對帳目再重試此筆。'
                    messages.append(f'❌ {detail}')
                finally:
                    ctx.send = original_send
                    ctx.spending_batch = False
                if messages:
                    receipts.append(f'{index:02}｜'+'\n    '.join('\n'.join(messages).splitlines()))
                await asyncio.sleep(0)
            await send(ctx, '🧾 批次生活記帳結果（各筆獨立，勿重貼已成功項目）\n\n' + '\n'.join(receipts))
        await self.notify(ctx)

    async def cog_command_error(self,ctx,error):
        cause = getattr(error,'original',error)
        if isinstance(cause,(ValueError,TypeError)):
            ctx.spending_error_handled = True
            await send(ctx,discord_spending_error(cause))

    @commands.command(name='記帳說明')
    async def help(self,ctx):
        await ctx.send(embed=self.help_embed(),view=SpendingView(self))

    def help_embed(self):
        embed=discord.Embed(title='💰 生活記帳',description='**把支出、預算與固定負擔放在同一個看板。**\n\n今天 · 帳目 · 更多\n\n點下方按鈕開啟，資料僅自己可見。',color=0x2ecc71)
        embed.set_footer(text='文字指令仍可使用 · 看板閒置5分鐘後重新開啟')
        return embed

    def detail_embed(self):
        embed = discord.Embed(title='💰 生活支出與預算',description='獨立於投資；台幣記帳，不記收入或銀行餘額。',color=0x2ecc71)
        embed.add_field(name='記錄一筆支出',value='格式：`!支出 金額 分類 用途`\n例：`!支出 150 餐飲 午餐`\n150＝金額；餐飲＝分類；午餐＝用途\n\n`!支出明細` 查看編號；`!記帳撤銷` 還原最近操作',inline=False)
        embed.add_field(name='快速記帳／帳目管理／圖表',value='生活看板「＋記一筆消費」直接開啟表單，內含分類與付款來源下拉、日期、金額與用途。分類與付款來源可直接新增。\n「帳目」內的帳目管理可按月選取單筆修改；「更多」提供付款來源、支出圖表、預算、固定負擔、提醒與捷徑管理。\n原文字記帳未填來源一律記為「未指定」；文字修改保留原來源。',inline=False)
        embed.add_field(name='每月預算',value='格式：`!預算 月份 總額或分類 金額`\n例：`!預算 '+sp.today().strftime('%Y-%m')+' 總額 20000`\n先設總額，再設定餐飲等分類。',inline=False)
        embed.add_field(name='固定支出／訂閱／分期',value='**點下方「➕ 新增」按鈕，用表單填寫。**\n欄位：項目名稱、每月金額、分類、開始月份。\n只有分期需要總期數，訂閱與固定支出不必填。\n\n文字格式：`!固定新增 種類 名稱 金額 分類 月份 [期數]`\n例：`!固定新增 訂閱 影音 390 娛樂 '+sp.today().strftime('%Y-%m')+'`\n訂閱可省略期數；舊寫法的 0 表示持續至停用。\n`!固定清單` 查看；`!固定停用 編號` 停止',inline=False)
        embed.add_field(name='資料清除',value='`!生活清除 yes` 只刪除生活資料',inline=False)
        embed.add_field(name='分類與提醒',value='`!分類清單` 查看分類\n`!分類新增 寵物`／`!分類刪除 寵物`\n`!提醒設定 50 80 100`\n`!提醒設定` 查看；`!提醒重設` 恢復80%/100%',inline=False)
        embed.set_footer(text='依預定扣款日自動記帳，非銀行扣款；預算使用率以%顯示。名稱含空格請加雙引號。')
        return embed

    @commands.command(name='分類清單')
    async def categories(self,ctx):
        user_id = str(ctx.author.id)
        active = life_service.get_categories(user_id)
        inactive = [n for n in life_service.get_categories(user_id,include_inactive=True) if n not in active]
        await send(ctx,'📂 啟用分類\n'+('、'.join(active) or '尚無分類，請用 !分類新增')+('\n\n已停用（歷史保留）：'+ '、'.join(inactive) if inactive else ''))

    @commands.command(name='分類新增')
    async def add_category(self,ctx,*,name:str):
        sp.set_category(str(ctx.author.id),name,True)
        await send(ctx,f'✅ 已新增／啟用分類：{name}')

    @commands.command(name='分類刪除')
    async def remove_category(self,ctx,*,name:str):
        sp.set_category(str(ctx.author.id),name,False)
        await send(ctx,f'✅ 已停用分類：{name}\n舊帳目、預算與既有固定項目保留；新增支出不可再選用。重新新增同名即可恢復。')

    @commands.command(name='提醒設定')
    async def reminders(self,ctx,*levels:str):
        user_id = str(ctx.author.id)
        if levels:
            try:
                values = [int(n.removesuffix('%')) for n in levels]
            except ValueError:
                raise ValueError('提醒門檻請填整數百分比，例如 !提醒設定 50 80 100')
            sp.set_reminders(user_id,values)
        await send(ctx,'🔔 目前預算提醒：'+'／'.join(f'{n}%' for n in sp.reminder_levels(user_id))+'\n適用所有月份的總預算與分類預算。')
        await self.notify(ctx)

    @commands.command(name='提醒重設')
    async def reset_reminders(self,ctx):
        sp.set_reminders(str(ctx.author.id))
        await ctx.send('✅ 提醒已恢復預設：80%／100%')
        await self.notify(ctx)

    @commands.command(name='支出')
    async def expense(self,ctx,amount:str,cat:str,*,note:str):
        await self.prepare(ctx)
        key = life_service.add_expense(str(ctx.author.id),amount,cat,note)
        await ctx.send(f'✅ 支出 #{key} 已記錄：{cat} {number(amount)} 元；!記帳撤銷 可還原')
        await self.notify(ctx)

    @commands.command(name='支出補登')
    async def backdate(self,ctx,on:str,amount:str,cat:str,*,note:str):
        await self.prepare(ctx)
        key = life_service.add_expense(str(ctx.author.id),amount,cat,note,on)
        await ctx.send(f'✅ 支出 #{key} 已補登 {on}')
        await self.notify(ctx)

    @commands.command(name='支出修改')
    async def edit(self,ctx,key:int,on:str,amount:str,cat:str,*,note:str):
        await self.prepare(ctx)
        life_service.update_expense(str(ctx.author.id),key,amount,cat,note,on)
        await ctx.send(f'✅ 已修改支出 #{key}；未來固定項目金額不受此單筆修改影響')
        await self.notify(ctx)

    @commands.command(name='記帳撤銷')
    async def undo(self,ctx,confirm:int=None):
        await self.prepare(ctx)
        if confirm is None:
            result = life_service.preview_undo(str(ctx.author.id))
        else:
            result = life_service.undo_latest_action(str(ctx.author.id),confirm)
        action,key = result['action_id'],result['expense_id']
        await ctx.send(f'確認還原支出 #{key} 的最近操作，請輸入 !記帳撤銷 {action}' if confirm is None else f'✅ 操作 #{action} 已撤銷；自動項目本月不會重複補記')

    @commands.command(name='預算')
    async def budget(self,ctx,month:str,cat:str,amount:str):
        await self.prepare(ctx)
        sp.set_budget(str(ctx.author.id),month,cat,amount)
        await ctx.send(f'✅ {month} {cat}預算已設為 {number(amount)} 元')
        await self.notify(ctx)

    @commands.command(name='月報')
    async def month(self,ctx,month:str=None):
        await self.prepare(ctx)
        await send(ctx,format_report(life_service.get_month_summary(str(ctx.author.id),month)))

    @commands.command(name='支出明細')
    async def details(self,ctx,month:str=None,page:int=1):
        await self.prepare(ctx)
        month = month or sp.today().strftime('%Y-%m')
        sp.month_date(month)
        if page < 1:
            raise ValueError('頁數需大於零')
        listing = life_service.list_expenses(str(ctx.author.id),month,include_voided=True,limit=20,offset=(page-1)*20)
        entries = listing['items']
        lines = [f'🧾 {month} 第 {page} 頁（每頁20筆）']
        for row in entries:
            lines.append(f"#{row['id']} {row['spent_on']} {row['category']} {number(row['cents']/100)} 元｜{row['note']}｜付款來源：{row['payment_source_name']}｜{row['source']}"+('（已撤銷）' if row['voided'] else ''))
        await send(ctx,'\n'.join(lines) if entries else '此頁無紀錄')

    @commands.command(name='固定新增')
    async def recurring(self,ctx,kind:str,name:str,amount:str,cat:str,start:str,periods:int=0,due_day:int=None):
        await self.prepare(ctx)
        key = sp.add_recurring(str(ctx.author.id),kind,name,amount,cat,start,periods,due_day)
        await ctx.send(f'✅ 固定項目 #{key} 已建立，每月 {number(amount)} 元；開始月 {start}。分期金額為每期金額。')
        await self.prepare(ctx)

    async def show_fixed(self,ctx):
        await self.prepare(ctx)
        data = life_service.get_fixed_burdens(str(ctx.author.id))
        lines = [f"📅 {data['month']} 已列入的固定負擔：{number(data['total'])} 元"]
        for item in data['entries']:
            lines.append(f"支出 #{item['id']} {item['note']} {number(item['amount'])} 元"+('（已撤銷，不計入）' if item['voided'] else ''))
        lines.append('設定項目：')
        for rule in data['rules']:
            suffix = f"｜剩餘 {rule['remaining_periods']} 期／{number(rule['remaining_scheduled_amount'])} 元" if rule['periods'] else ''
            status = '停用' if not rule['active'] else ('已結束' if rule['periods'] and not rule['remaining_periods'] else '啟用')
            lines.append(f"#{rule['id']} {rule['kind']} {rule['name']} 每月 {number(rule['monthly_amount'])}｜開始 {rule['start_month']}｜{status}{suffix}"+(f"｜預計每月{rule['due_day']}日付款（短月月底）" if rule['due_day'] else ''))
        await send(ctx,'\n'.join(lines))

    @commands.command(name='固定清單')
    async def fixed(self,ctx):
        await self.show_fixed(ctx)

    @commands.command(name='固定停用')
    async def stop(self,ctx,key:int):
        await self.prepare(ctx)
        sp.stop_recurring(str(ctx.author.id),key)
        await ctx.send('✅ 已停用，已產生的支出保留，之後不再產生；改金額請停用後於下月新增。')

    @commands.command(name='生活清除')
    async def clear(self,ctx,confirm:str=None):
        if confirm != 'yes':
            await ctx.send('確認刪除所有生活支出、固定項目、預算與提醒紀錄，請輸入 !生活清除 yes；投資資料不受影響。')
            return
        sp.clear(str(ctx.author.id))
        await ctx.send('✅ 生活資料已清除；投資資料保留。')
