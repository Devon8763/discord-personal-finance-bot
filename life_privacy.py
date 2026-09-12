"""Owner-only lifestyle export and explicit deletion, with no disk artifacts."""
import asyncio
import io
import discord
import spending as sp
from selection_ui import OwnedView
from life_transfer import dm_owner,ImportBackup
from backup_bundle import export_bundle


NOTICE='匯出檔包含你的消費與設定資料；有投資資料時亦包含投資紀錄。請勿轉傳給不信任的人。'
DESCRIPTION='''生活記帳會保存：
- Discord 使用者 ID（用於隔離每位使用者的資料）
- 消費、分類、付款來源、預算、固定負擔與常用捷徑

生活記帳不會要求或保存：
- 銀行登入帳密
- 信用卡完整卡號
- 銀行帳單、收據圖片或附件
- 帳戶餘額、聯絡人、位置或通知內容

資料儲存在執行 Bot 的電腦本機資料庫。
Bot 主機管理者仍可能讀取本機資料庫。
Discord 會處理你傳送給 Bot 的訊息與互動；私人回覆僅限制其他 Discord 使用者看見，並不等於端對端加密。

你可以匯出或永久刪除自己的生活資料。
備份包含消費、分類、付款來源、捷徑及目前預算；有投資資料時亦包含投資紀錄。生活刪除不刪投資。
AI 對話、使用紀錄及偏好不搬移；匯入後 AI 關閉，使用前需重新同意。
換帳號請由舊帳號自行匯出，再由新帳號在私訊中「匯入我的備份」。無法登入舊帳號且沒有備份時，Bot 無法找回資料。
本功能不提供管理者查看個別帳本、代匯入或代轉移權限。備份僅檢查格式、版本與完整性，不保證來源真偽；請只使用自己保存的原始檔。
提醒設定、導覽狀態及記帳產生的提醒／撤銷紀錄也屬生活資料；不擷取裝置通知內容。'''


def export_life(user_id):
    return export_bundle(user_id)


async def send_export(i):
    if not await dm_owner(i,i.user.id):return
    await i.response.send_message(NOTICE,ephemeral=True)
    buffer=None;attachment=None
    try:
        buffer=io.BytesIO(await asyncio.to_thread(export_life,str(i.user.id)))
        attachment=discord.File(buffer,filename=f'life-ledger-export-{sp.today():%Y%m%d}.zip')
        await i.followup.send('資料備份匯出完成；有投資資料時亦包含本人投資紀錄。',file=attachment,ephemeral=True)
    except Exception:
        await i.followup.send('匯出或傳送失敗，請稍後重試；資料未被刪除。',ephemeral=True)
    finally:
        if attachment:attachment.close()
        if buffer:buffer.close()


class PrivacyView(OwnedView):
    def __init__(self,dashboard):
        super().__init__(dashboard.owner)
        self.dashboard=dashboard
        for label in ('匯出我的生活資料','匯入我的備份','刪除我的生活資料','關閉'):
            button=discord.ui.Button(label=label)
            async def click(i,action=label):
                if not await self.interaction_check(i):return
                if self.is_finished():return
                if action!='關閉' and not await dm_owner(i,self.owner):return
                if action=='匯出我的生活資料':await send_export(i)
                elif action=='匯入我的備份':await i.response.send_modal(ImportBackup(self.dashboard))
                elif action=='刪除我的生活資料':
                    view=DeleteLife(self.dashboard)
                    await i.response.send_message(embed=view.render(),view=view,ephemeral=True)
                else:
                    self.stop();await i.response.edit_message(content='已關閉。可回原設定選單返回主看板。',embed=None,view=None)
            button.callback=click;self.add_item(button)

    def render(self):
        return discord.Embed(title='我的資料與隱私',description=DESCRIPTION,color=0x3498db)


class DeleteLife(OwnedView):
    def __init__(self,dashboard):
        super().__init__(dashboard.owner)
        self.dashboard=dashboard
        self.closed=False
        for label in ('匯出後再刪除','確認永久刪除','取消'):
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.danger if label=='確認永久刪除' else discord.ButtonStyle.secondary)
            async def click(i,action=label):
                if not await dm_owner(i,self.owner):return
                if self.closed or self.is_finished():
                    await i.response.send_message('此確認已結束，請重新開啟隱私頁。',ephemeral=True);return
                if action=='匯出後再刪除':
                    await send_export(i);return
                self.closed=True;self.stop()
                if action=='取消':
                    await i.response.edit_message(content='已取消，生活資料未刪除。',embed=None,view=None);return
                await i.response.defer()
                try:await asyncio.to_thread(sp.clear,str(self.owner))
                except Exception:
                    await i.edit_original_response(content='刪除未完成，請重新開啟確認流程後再試。',embed=None,view=None);return
                await i.edit_original_response(content='✅ 你的生活資料已永久刪除；投資資料不受影響。',embed=None,view=None)
                try:await self.dashboard.refresh()
                except Exception:
                    await i.followup.send('資料已刪除；請重新開啟生活看板查看。',ephemeral=True)
            button.callback=click;self.add_item(button)

    def render(self):
        return discord.Embed(title='確認刪除生活資料',description='這會永久刪除你的生活消費、預算、付款來源、分類、固定負擔與常用捷徑。\n投資資料不會受影響。\n此操作無法復原，建議先匯出備份。\n\n提醒、撤銷紀錄與新手導覽狀態也會清除。\n「匯出後再刪除」只匯出，仍需另外按「確認永久刪除」。',color=0xe74c3c)

    async def on_timeout(self):
        self.closed=True
