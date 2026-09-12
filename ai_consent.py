"""Per-user local AI opt-in, deliberately separate from portable ledger data."""
import discord
import db
from ledger import transaction
from selection_ui import OwnedView


def enabled(user_id):
    conn=db.get_conn()
    try:
        row=conn.execute('SELECT enabled FROM ai_preferences WHERE user_id=?',(str(user_id),)).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


class ConsentView(OwnedView):
    async def interaction_check(self,interaction):
        if getattr(interaction,'guild',None) is not None:
            await interaction.response.send_message('請私訊機器人設定 AI。',ephemeral=True)
            return False
        return await super().interaction_check(interaction)

    async def save(self,interaction,value):
        if not await self.interaction_check(interaction): return
        with transaction() as conn:
            conn.execute('INSERT INTO ai_preferences(user_id,enabled) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled',(str(self.owner),int(value)))
        await interaction.response.edit_message(content='已啟用本地 AI，請重新選擇剛才的 AI 功能。' if value else 'AI 維持關閉，記帳與報表仍可使用。',view=None)
        self.stop()

    @discord.ui.button(label='同意啟用本地 AI',style=discord.ButtonStyle.primary)
    async def enable(self,interaction,button):
        await self.save(interaction,True)

    @discord.ui.button(label='保持關閉')
    async def keep_off(self,interaction,button):
        await self.save(interaction,False)


async def ensure_consent(ctx):
    interaction=getattr(ctx,'interaction',None)
    if getattr(ctx,'guild',None) is not None or getattr(interaction,'guild',None) is not None:
        await ctx.send('AI 功能僅限私訊，請私訊機器人後再使用。')
        return False
    if enabled(ctx.author.id): return True
    await ctx.send('AI 預設關閉。啟用後，你的問題及該次查詢所需帳目或持倉摘要會交給此電腦的本地 AI 模型處理；AI 不會修改帳目。此同意不會隨資料匯出或移轉。是否同意？',view=ConsentView(ctx.author.id))
    return False
