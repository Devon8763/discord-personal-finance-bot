from presentation import number
from ledger import trade, history, undo as undo_trade, save_fund_price
from message_input import process_message, display_time
import discord
from discord.ext import commands
from db import get_conn, init_db
from scraper import get_price as fetch_price
from portfolio import normalize_symbol, positive, value_position
from ai import analyze
import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

_price_cache = {}

async def get_price(symbol):
    cached = _price_cache.get(symbol)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    data = await asyncio.to_thread(fetch_price, symbol)
    if len(_price_cache) >= 512:
        _price_cache.clear()
    _price_cache[symbol] = (time.monotonic(), data)
    return data

# Discord 權限
intents = discord.Intents.default()
intents.message_content = True

# 建立 bot（關掉內建 help）
class SpacedContext(commands.Context):
    async def send(self, content=None, **kwargs):
        if not getattr(self, '_response_started', False):
            content = '━━━━━━━━━━━━━━━━━━━━\n\n' + (str(content) if content is not None else '')
            self._response_started = True
        return await super().send(content, **kwargs)


class InvestmentBot(commands.Bot):
    async def setup_hook(self):
        from spending_commands import Spending
        await self.add_cog(Spending(self, stock_snapshot, add_funds, InteractionContext))

    async def get_context(self, origin, /, *, cls=SpacedContext):
        return await super().get_context(origin, cls=cls)


bot = InvestmentBot(
    command_prefix="!",
    intents=intents,
    help_command=None
)
print("程式開始")
print("BOT starting")

@bot.event
async def on_message(message):
    await process_message(bot, message)

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    # 🏠 主頁
    def main_embed(self):
        embed = discord.Embed(
            title="📊 生活記帳與投資機器人",
            description="📈 投資紀錄：持股、基金、觀察與投資摘要\n💰 生活記帳：支出、預算、固定負擔與趨勢\n兩邊獨立；自然語言查詢請輸入 `!問 問題`。",
            color=0x3498db
        )

        embed.add_field(
            name="📌 快速開始",
            value="""1️⃣ `!watch`
2️⃣ `!buy`
3️⃣ `!portfolio`""",
            inline=False
        )

        embed.add_field(
            name="⚠️ 風險提醒",
            value="投資一定有風險，基金投資有賺有賠，申購前應詳閱公開說明書",
            inline=False
        )

        embed.set_footer(text="📩 建議使用私訊操作")
        return embed

    # 🔙 主頁按鈕
    @discord.ui.button(label="🏠 主頁", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainHelpView()
        await interaction.response.edit_message(embed=view.main_embed(), view=view)

    @discord.ui.button(label="🤖 AI 摘要", style=discord.ButtonStyle.success, row=1)
    async def ai_summary(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await run_analysis(InteractionContext(interaction))

    @discord.ui.button(label="💰 生活記帳", style=discord.ButtonStyle.primary, row=1)
    async def spending(self, interaction: discord.Interaction, button: discord.ui.Button):
        from spending_commands import SpendingView
        cog = bot.get_cog('Spending')
        await interaction.response.send_message(embed=cog.help_embed(), view=SpendingView(cog), ephemeral=True)

    @discord.ui.button(label="📈 投資紀錄", style=discord.ButtonStyle.secondary, row=1)
    async def investments(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HelpView()
        await interaction.response.edit_message(embed=view.main_embed(), view=view)

    # 📈 股票
    @discord.ui.button(label="📈 股票", style=discord.ButtonStyle.primary)
    async def stock(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📈 股票 / ETF",
            description="""**`!price 2330`** → 查看股價  

**`!buy 2330 580 10`** → 買入（代號 / 股價 / 股數）  
**`!sell 2330 600 5`** → 賣出（代號 / 股價 / 股數）  

**`!check`** → 查看報酬  
**`!remove 2330`** → 移除股票""",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=self)

    # 💰 基金（你要求的全部補上🔥）
    @discord.ui.button(label="💰 基金", style=discord.ButtonStyle.success)
    async def fund(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="💰 基金",
            description="""**`!fund`** → 開啟基金觀測站  

**`!fundbuy 名稱 金額 淨值`**  
例：`!fundbuy 台灣50 10000 150`

**`!fundsell 名稱 單位 價格`**  
例：`!fundsell 台灣50 10 160`

**`!fundcheck`** → 查看報酬  

**`!fundprice 名稱 淨值`** → 保存淨值供查詢使用""",
            color=0x2ecc71
        )
        await interaction.response.edit_message(embed=embed, view=self)

    # 👀 觀察
    @discord.ui.button(label="👀 觀察", style=discord.ButtonStyle.secondary)
    async def watch(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👀 觀察清單",
            description="""**`!watch 2330`** → 加入  
**`!watchlist`** → 查看  
**`!unwatch 2330`** → 移除""",
            color=0x95a5a6
        )
        await interaction.response.edit_message(embed=embed, view=self)

    # 📊 組合
    @discord.ui.button(label="📊 組合", style=discord.ButtonStyle.primary)
    async def portfolio(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📊 投資組合",
            description="""**`!portfolio`**\n**`!分析`** → 本地 AI 摘要

包含：
✔ 股票（股數）  
✔ 基金  
✔ 總報酬""",
            color=0xe74c3c
        )
        await interaction.response.edit_message(embed=embed, view=self)

    # 🧹 系統（新增🔥）
    @discord.ui.button(label="🧹 系統", style=discord.ButtonStyle.danger)
    async def system(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🧹 系統功能",
            description="""**`!clear yes`** → 清除投資資料（不可恢復）  
**`!privacy`** → 隱私說明
**`!history`** → 交易歷史
**`!undo`** → 撤銷預覽  

🔒 儲存你主動建立的記錄，不保存一般聊天或 AI 問答""",
            color=0xe67e22
        )
        await interaction.response.edit_message(embed=embed, view=self)
class MainHelpView(HelpView):
    def __init__(self):
        super().__init__()
        for item in list(self.children):
            if item.label not in ('📈 投資紀錄','💰 生活記帳','🤖 AI 摘要'):
                self.remove_item(item)


@bot.command()
async def help(ctx, category=None):

    # 👉 按鈕 UI
    if category is None:
        view = MainHelpView()
        await ctx.send(embed=view.main_embed(), view=view)
        return

    # 👉 保留指令模式
    category = category.lower()

    if category == "stock":
        await ctx.send("📈 股票：!price / !buy / !sell / !check / !remove")

    elif category == "fund":
        await ctx.send("💰 基金：!fund / !fundbuy / !fundsell / !fundcheck / !fundprice")

    elif category == "watch":
        await ctx.send("👀 觀察：!watch / !watchlist / !unwatch")

    elif category == "portfolio":
        await ctx.send("📊 投資組合：!portfolio")

    elif category == "ai":
        await ctx.send("🤖 點擊 !help 的 AI 摘要按鈕，或輸入 !分析／!analyze。按鈕結果僅點擊者可見，使用點擊者自己的持倉。")

    else:
        await ctx.send("❌ 找不到分類")
@bot.command()
async def ping(ctx):
    await ctx.send("pong!")
@bot.command()
async def privacy(ctx):
    await ctx.send("""🔒 隱私說明

✔ 儲存主動輸入的投資、生活支出、用途、預算與固定項目
✔ 不儲存聊天內容
✔ 不分享資料

🧹 !clear yes 刪除投資資料；!生活清除 yes 刪除生活資料
""")
@bot.command()
async def guide(ctx):
    embed = discord.Embed(
        title="📊 投資機器人使用指南",
        description="快速了解功能與使用方式",
        color=0x3498db
    )

    # ========================
    # 功能
    # ========================
    embed.add_field(
        name="📌 功能",
        value="""• 股票 / ETF（股數管理）
• 基金投資追蹤
• 投資組合分析
• 觀察清單""",
        inline=False
    )

    # ========================
    # 使用方式
    # ========================
    embed.add_field(
        name="🚀 使用方式",
        value="""1️⃣ 加入觀察 → `!watch 2330`
2️⃣ 建立持倉 → `!buy 2330 580 10`
3️⃣ 查看資產 → `!portfolio`""",
        inline=False
    )

    # ========================
    # 為什麼用私訊
    # ========================
    embed.add_field(
        name="📩 為什麼建議使用私訊",
        value="""• 不會被聊天洗版  
• 操作更專注  
• 體驗更像個人投資工具""",
        inline=False
    )

    # ========================
    # 隱私
    # ========================
    embed.add_field(
        name="🔒 隱私說明",
        value="""• 儲存主動輸入的投資、生活支出、用途、預算與固定項目  
• 不儲存聊天內容  
• 不會分享任何資料  

🧹 `!clear yes` 刪投資；`!生活清除 yes` 刪生活記帳""",
        inline=False
    )

    # ========================
    # 快速開始
    # ========================
    embed.add_field(
        name="⚡ 快速開始",
        value="""`!help` → 查看所有指令  
`!portfolio` → 查看資產  
`!price 2330` → 查股價""",
        inline=False
    )

    embed.set_footer(text="💡 建議私訊使用，體驗最佳")

    await ctx.send(embed=embed)
import math

# ========================
# 📊 price
# ========================
@bot.command()
async def price(ctx, symbol):
    symbol = normalize_symbol(symbol)
    data = await get_price(symbol)

    if data is None:
        await ctx.send(f"""❌ 查詢失敗

    📌 標的：{symbol}
    原因：無資料或代碼錯誤

    👉 請確認代碼（例如 2330 / AAPL）
    """)
        return

    sign = "+" if isinstance(data['change'], (int, float)) and data['change'] > 0 else ""

    await ctx.send(f"""📊 {data['name']} ({data['symbol']})

    💵 現價：{data['price']} {data['currency']}
    📈 漲跌：{sign}{data['change']} ({sign}{data['percent']}%)
    """)
async def record_trade(ctx, kind, name, price=0, quantity=0):
    try:
        result = trade(str(ctx.author.id), kind, name, price, quantity)
    except ValueError as error:
        await ctx.send(str(error))
        return
    profit = '' if result['profit'] is None else f"\n本次損益：{number(result['profit'], signed=True)}"
    await send_long(ctx, f"✅ 已記錄 #{result['id']} {kind} {result['name']}\n數量：{result['quantity']:g}｜剩餘：{result['remaining']:g}\n平均成本：{number(result['average'])}{profit}\n!history 查看紀錄；!undo 查看撤銷預覽")


@bot.command()
async def buy(ctx, symbol, price: float, shares: float):
    await record_trade(ctx, 'buy', symbol, price, shares)


@bot.command()
async def sell(ctx, symbol, price: float, shares: float):
    await record_trade(ctx, 'sell', symbol, price, shares)


@bot.command()
async def remove(ctx, symbol):
    await record_trade(ctx, 'remove', symbol)


@bot.command()
async def check(ctx):
    snapshot = await stock_snapshot(str(ctx.author.id))
    await send_snapshot(ctx, snapshot)

@bot.command()
async def watch(ctx, *symbols):
    if not symbols:
        await ctx.send('用法：!watch 2330 AAPL（也可每行一條 !watch）')
        return
    user_id = str(ctx.author.id)
    added, existing = [], []
    conn = get_conn()
    try:
        with conn:
            for symbol in dict.fromkeys(normalize_symbol(s) for s in symbols):
                if not symbol:
                    continue
                if conn.execute('SELECT 1 FROM watchlist WHERE user_id=? AND symbol=?', (user_id, symbol)).fetchone():
                    existing.append(symbol)
                else:
                    conn.execute('INSERT INTO watchlist(user_id,symbol) VALUES(?,?)', (user_id, symbol))
                    added.append(symbol)
    finally:
        conn.close()
    lines = ['👀 觀察清單更新']
    if added:
        lines.append('已加入：' + '、'.join(added))
    if existing:
        lines.append('已在清單：' + '、'.join(existing))
    if not added and not existing:
        lines.append('請提供有效的標的代號')
    await send_long(ctx, '\n'.join(lines))

@bot.command()
async def watchlist(ctx):
    user_id = str(ctx.author.id)

    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT symbol FROM watchlist WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await ctx.send("❗ 沒有觀察清單")
        return

    msg = "👀 觀察清單\n"

    for (symbol,) in rows:
        data = await get_price(symbol)
        if not data:
            msg += f"\n{symbol}：暫時無法取得行情（仍在觀察清單）\n"
            continue

        percent = data.get("percent", "-")
        sign = "+" if isinstance(data.get("change"), (int, float)) and data["change"] > 0 else ""

        msg += f"""
{data['name']} ({symbol})
現價：{data['price']} {data['currency']}
漲跌：{sign}{percent}%
"""

    await send_long(ctx, msg)
@bot.command()
async def unwatch(ctx, symbol):
    user_id = str(ctx.author.id)
    symbol = normalize_symbol(symbol)

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    DELETE FROM watchlist
    WHERE user_id = ? AND symbol = ?
    """, (user_id, symbol))

    conn.commit()
    conn.close()

    await ctx.send(f"❌ 已移除 {symbol}")

@bot.command()
async def fund(ctx):
    await ctx.send("https://www.fundclear.com.tw/")

@bot.command()
async def fundcheck(ctx):
    snapshot = {'positions': [], 'missing': []}
    await add_funds(ctx, snapshot)
    await send_snapshot(ctx, snapshot)


@bot.command()
async def fundbuy(ctx, name, amount: float, price: float):
    await record_trade(ctx, 'fundbuy', name, price, amount)


@bot.command()
async def fundsell(ctx, name, units: float, price: float):
    await record_trade(ctx, 'fundsell', name, price, units)


@bot.command()
async def fundprice(ctx, name, price: float):
    try:
        timestamp = save_fund_price(str(ctx.author.id), name, price)
    except ValueError as error:
        await ctx.send(str(error))
        return
    await send_long(ctx, f'✅ {name} 淨值已保存：{price}\n更新時間（台灣）：{display_time(timestamp)}')


@bot.command(name='history')
async def trade_history(ctx, limit: int = 20):
    if not 1 <= limit <= 100:
        await ctx.send('請輸入 !history 1～100')
        return
    rows = history(str(ctx.author.id), limit)
    lines = ['📜 交易歷史（本次更新後開始記錄，時間為台灣時間）']
    for row in rows:
        status = '已撤銷' if row['undone'] else '有效'
        profit = '' if row['profit'] is None else f"｜損益 {number(row['profit'], signed=True)}"
        lines.append(f"#{row['id']} {display_time(row['created_at'])} [{status}]\n{row['kind']} {row['name']}｜價格 {row['price']:g}｜數量 {row['quantity']:g}{profit}")
    await send_long(ctx, '\n'.join(lines) if rows else '尚無新交易紀錄；更新前的持倉仍保留。')


@bot.command()
async def undo(ctx, trade_id: int = None):
    user_id = str(ctx.author.id)
    if trade_id is None:
        rows = history(user_id, 1, active_only=True)
        if not rows:
            await ctx.send('沒有可撤銷的交易')
            return
        row = rows[0]
        await send_long(ctx, f"準備撤銷 #{row['id']}：{row['kind']} {row['name']}，價格 {row['price']:g}，數量 {row['quantity']:g}。\n確認請輸入 !undo {row['id']}")
        return
    try:
        name = undo_trade(user_id, trade_id)
    except ValueError as error:
        await ctx.send(str(error))
        return
    await send_long(ctx, f'✅ 已撤銷 #{trade_id} {name}，持倉與成本已還原。')


@bot.command()
async def clear(ctx, confirm=None):
    if confirm != "yes":
        await ctx.send("⚠️ 請輸入 !clear yes 確認清除投資資料")
        return

    user_id = str(ctx.author.id)

    conn = get_conn()
    c = conn.cursor()

    c.execute("DELETE FROM assets WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM fund_transactions WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM fund_prices WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM trade_history WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

    await ctx.send("""🧹 已清除投資資料

⚠️ 此操作無法復原
    """)
# ========================
# 📊 portfolio（最穩版本🔥）
# ========================
async def stock_snapshot(user_id):
    conn = get_conn()
    try:
        stocks = conn.execute("SELECT symbol, buy_price, shares FROM assets WHERE user_id = ?", (user_id,)).fetchall()
    finally:
        conn.close()
    result = {"queried_at": datetime.now(timezone.utc).isoformat(), "positions": [], "missing": []}
    for symbol, cost, shares in stocks:
        data = await get_price(symbol)
        try:
            if not data:
                raise ValueError('無行情')
            position = value_position(symbol + ' ' + data['name'], (cost or 0) * (shares or 0), shares or 0, data['price'], data.get('currency') or '股票幣別未知')
            result['positions'].append(position)
        except (ValueError, TypeError, KeyError):
            result['missing'].append(symbol)
    return result


async def add_funds(ctx, snapshot):
    conn = get_conn()
    try:
        funds = conn.execute("SELECT t.fund_name, SUM(t.amount), SUM(t.units), p.price, p.updated_at FROM fund_transactions t LEFT JOIN fund_prices p ON p.user_id=t.user_id AND p.fund_name=t.fund_name WHERE t.user_id=? GROUP BY t.fund_name HAVING SUM(t.units)>0", (str(ctx.author.id),)).fetchall()
    finally:
        conn.close()
    for name, cost, units, price, updated in funds:
        try:
            position = value_position(name, cost, units, price, '基金幣別未設定：' + name)
            position['price_updated_at'] = updated
            position['price_source'] = '手動保存淨值，非即時行情'
            snapshot['positions'].append(position)
        except ValueError:
            snapshot['missing'].append(name + '（請用 !fundprice "' + name + '" 淨值 保存價格）')


async def send_long(ctx, text):
    for offset in range(0, len(text), 1900):
        await ctx.send(text[offset:offset + 1900], allowed_mentions=discord.AllowedMentions.none())


async def send_snapshot(ctx, snapshot):
    lines = ['📊 持倉估值（目前持倉未實現損益）']
    totals = {}
    for item in sorted(snapshot['positions'], key=lambda p: p['profit'], reverse=True):
        lines.append(f"{item['name']}｜{item['currency']}\n價格：{number(item['price'])}｜數量：{item['quantity']:g}\n平均成本：{number(item['cost'] / item['quantity'] if item['quantity'] else 0)}｜成本：{number(item['cost'])}｜市值：{number(item['value'])}\n損益：{number(item['profit'], signed=True)}（{number(item['percent'], signed=True)}%）")
        if item.get('price_updated_at'):
            lines.append('淨值更新（台灣）：' + display_time(item['price_updated_at']) + '（手動保存，非即時）')
        currency = item['currency']
        if currency == '股票幣別未知':
            continue
        total = totals.setdefault(currency, [0, 0])
        total[0] += item['cost']
        total[1] += item['value']
    for currency, (cost, value) in totals.items():
        percent = (value - cost) / cost * 100 if cost else 0
        lines.append(f'{currency} 小計｜成本 {number(cost)}｜市值 {number(value)}｜損益 {number(value-cost, signed=True)}（{number(percent, signed=True)}%）')
    if snapshot['missing']:
        lines.append('⚠️ 估值不完整，以下項目未計入：' + '、'.join(snapshot['missing']))
    if not snapshot['positions'] and not snapshot['missing']:
        lines.append('尚無持倉資料')
    await send_long(ctx, '\n\n'.join(lines))


@bot.command()
async def portfolio(ctx):
    await watchlist.callback(ctx)
    snapshot = await stock_snapshot(str(ctx.author.id))
    await add_funds(ctx, snapshot)
    await send_snapshot(ctx, snapshot)


class InteractionContext:
    def __init__(self, interaction):
        self.interaction = interaction
        self.author = interaction.user
        self._response_started = False

    async def send(self, content=None, **kwargs):
        if not self._response_started:
            content = '━━━━━━━━━━━━━━━━━━━━\n\n' + (content or '')
            self._response_started = True
        await self.interaction.followup.send(content, ephemeral=True, **kwargs)

    @asynccontextmanager
    async def typing(self):
        yield


_analysis_users = set()


async def run_analysis(ctx):
    user_id = str(ctx.author.id)
    if user_id in _analysis_users:
        await ctx.send('請等待上一個分析完成。')
        return
    _analysis_users.add(user_id)
    try:
        await build_analysis(ctx)
    except Exception:
        await ctx.send('❌ 分析暫時無法完成，請稍後再試。')
    finally:
        _analysis_users.discard(user_id)


@bot.command(name='分析', aliases=['analyze'])
async def analysis(ctx):
    await run_analysis(ctx)


async def build_analysis(ctx):
    snapshot = await stock_snapshot(str(ctx.author.id))
    await add_funds(ctx, snapshot)
    await send_snapshot(ctx, snapshot)
    if not snapshot['positions']:
        return
    await ctx.send('正在使用本地模型整理摘要，請稍候…')
    try:
        async with ctx.typing():
            text = await analyze(snapshot)
        await send_long(ctx, '🤖 AI 摘要（依上方估值資料）\n' + text)
    except Exception:
        await ctx.send('❌ 本地 AI 暫時無法使用。請確認 Ollama 已啟動，且已下載設定的模型；原有記帳與查價仍可使用。')


@bot.event
async def on_command_error(ctx, error):
    if getattr(ctx, 'spending_error_handled', False):
        return
    if isinstance(error, commands.CommandNotFound):
        await ctx.send('❌ 找不到此指令，請輸入 !help 查看用法。')
        return
    if isinstance(error, commands.UserInputError):
        await ctx.send(f'❌ 參數格式錯誤。用法：!{ctx.command.qualified_name} {ctx.command.signature}')
    elif isinstance(error, commands.MaxConcurrencyReached):
        await ctx.send('請等待上一個分析完成。')
    else:
        print(type(error).__name__, str(error))
        await ctx.send('❌ 操作失敗，請稍後再試。')


if __name__ == '__main__':
    from config import load_token
    try:
        token = load_token()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    init_db()
    try:
        bot.run(token)
    except discord.LoginFailure:
        raise SystemExit('Discord 不接受此 Token。請更新 token.txt；若終端曾設定 DISCORD_TOKEN，請先執行 Remove-Item Env:DISCORD_TOKEN -ErrorAction SilentlyContinue 再啟動。')
