"""Self-service consumption-only transfer, bound to the current DM user."""
import asyncio
import json
import discord
import spending as sp
from backup_bundle import BundleImportFailed
from selection_ui import OwnedView,SafeModal


FIELDS=('spent_on','cents','category','note','payment_source_name','source','voided','kind')
MAX_UPLOAD=8*1024*1024


def strict_json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('備份結構含重複欄位。')
            result[key]=value
        return result
    return json.loads(data,object_pairs_hook=unique)


def read_backup(payload):
    from backup_bundle import read_bundle
    return read_bundle(payload)['expenses']


def validate_records(records):
    if not isinstance(records,list) or len(records)>10000:
        raise ValueError('備份帳目結構錯誤，或超過單次 10000 筆上限。')
    for record in records:
        if not isinstance(record,dict) or set(record)!=set(FIELDS):
            raise ValueError('備份帳目欄位不符，未寫入任何資料。')
        if type(record['cents']) is not int or not 0<record['cents']<=100000000000:
            raise ValueError('備份含無效金額，未寫入任何資料。')
        if type(record['voided']) is not int or record['voided'] not in (0,1) or record['kind']!='consumption':
            raise ValueError('備份含不支援的帳目種類或撤銷狀態。')
        for field,limit in (('spent_on',10),('category',20),('note',200),('payment_source_name',30),('source',30)):
            value=record[field]
            if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:
                raise ValueError('備份含無效文字欄位，未寫入任何資料。')
        try:
            day=sp.date.fromisoformat(record['spent_on'])
            if day.isoformat()!=record['spent_on'] or day>sp.today():raise ValueError()
        except ValueError:
            raise ValueError('備份含無效或未來日期，未寫入任何資料。') from None
    return records


def pending_records(conn,user_id,records):
    validate_records(records)
    existing=set(conn.execute('SELECT '+','.join(FIELDS)+' FROM expenses WHERE user_id=?',(str(user_id),)))
    pending=[]
    for record in records:
        key=tuple(record[field] for field in FIELDS)
        if key not in existing:
            pending.append(key);existing.add(key)
    return pending


def preview_records(user_id,records):
    conn=sp.get_conn()
    try:count=len(pending_records(conn,user_id,records))
    finally:conn.close()
    return dict(total=len(records),added=count,skipped=len(records)-count)


def import_records(user_id,records):
    validate_records(records)
    skipped=0
    try:
        with sp.transaction() as conn:
            pending=pending_records(conn,user_id,records)
            skipped=len(records)-len(pending)
            for values in pending:
                key=conn.execute('INSERT INTO expenses(user_id,'+','.join(FIELDS)+') VALUES(?,?,?,?,?,?,?,?,?)',(str(user_id),*values)).lastrowid
                if not values[FIELDS.index('voided')]:
                    conn.execute('INSERT INTO expense_actions(user_id,expense_id,before_json) VALUES(?,?,?)',(str(user_id),key,'null'))
    except Exception:
        raise ImportFailed(len(records),skipped) from None
    return dict(total=len(records),added=len(pending),skipped=len(records)-len(pending))


class ImportFailed(Exception):
    def __init__(self,total,skipped):
        self.skipped,self.failed=skipped,total-skipped
        super().__init__('匯入交易未完成，新增已全部回滾。')


async def dm_owner(i,owner):
    if i.user.id!=owner:
        await i.response.send_message('請開啟自己的資料與隱私頁。',ephemeral=True);return False
    if getattr(i,'guild',None) is not None:
        await i.response.send_message('請私訊 Bot，使用 !help → 生活記帳 → 更多 → 設定 → 我的資料與隱私。伺服器頻道不處理財務附件或帳目。',ephemeral=True);return False
    return True


class ImportBackup(SafeModal):
    def __init__(self,dashboard):
        super().__init__(title='匯入我的備份',timeout=300)
        self.dashboard=dashboard
        self.upload=discord.ui.FileUpload(min_values=1,max_values=1)
        self.add_item(discord.ui.Label(text='上傳新版 Bot ZIP 備份（最多 8 MiB）',component=self.upload))

    async def on_submit(self,i):
        if not await dm_owner(i,self.dashboard.owner):return
        attachments=self.upload.values
        if len(attachments)!=1 or not attachments[0].filename.lower().endswith('.zip'):
            await i.response.send_message('請選擇一份 Bot 匯出的 ZIP 備份。',ephemeral=True);return
        if attachments[0].size>MAX_UPLOAD:
            await i.response.send_message('備份超過單次 8 MiB 上限。',ephemeral=True);return
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            payload=await attachments[0].read()
        except Exception:
            await i.followup.send('附件讀取失敗，未寫入任何資料。請稍後再試。',ephemeral=True);return
        try:
            from backup_bundle import read_bundle
            records=await asyncio.to_thread(read_bundle,payload)
            view=ImportPreview(self.dashboard,records)
            embed=await asyncio.to_thread(view.render)
        except ValueError as error:
            await i.followup.send(str(error),ephemeral=True);return
        except Exception:
            await i.followup.send('附件讀取或預覽失敗，未寫入任何資料。請稍後再試。',ephemeral=True);return
        await i.followup.send(embed=embed,view=view,ephemeral=True)


class ImportPreview(OwnedView):
    def __init__(self,dashboard,records):
        super().__init__(dashboard.owner)
        self.dashboard,self.records=dashboard,records
        self.closed=False
        for label in ('確認匯入','取消'):
            button=discord.ui.Button(label=label)
            async def click(i,action=label):
                if not await dm_owner(i,self.owner):return
                if self.closed or self.is_finished():
                    await i.response.send_message('此預覽已結束，請重新上傳備份。',ephemeral=True);return
                self.closed=True;self.stop()
                records,self.records=self.records,[]
                if action=='取消':
                    await i.response.edit_message(content='已取消，帳目未變更。',embed=None,view=None);return
                await i.response.defer()
                try:
                    if isinstance(records,dict):
                        from backup_bundle import merge_bundle
                        result=await asyncio.to_thread(merge_bundle,str(i.user.id),records,True)
                    else:result=await asyncio.to_thread(import_records,str(i.user.id),records)
                except BundleImportFailed as error:
                    await i.edit_original_response(content='匯入失敗，所有變更已回滾。\n'+bundle_counts(error.counts),embed=None,view=None);return
                except ImportFailed as error:
                    await i.edit_original_response(content=f'新增 0 筆、略過 {error.skipped} 筆、失敗 {error.failed} 筆。匯入交易未完成，新增已全部回滾；請稍後重新預覽。',embed=None,view=None);return
                except Exception:
                    await i.edit_original_response(content='新增 0 筆；匯入失敗，整批回滾，未保留新增或設定變更。略過／失敗筆數尚未完成核算，請重新預覽。',embed=None,view=None);return
                if isinstance(records,dict):
                    await i.edit_original_response(content='匯入完成；AI 已關閉，使用前需重新同意。\n'+bundle_counts(result),embed=None,view=None)
                else:await i.edit_original_response(content=f"新增 {result['added']} 筆、略過 {result['skipped']} 筆、失敗 0 筆。",embed=None,view=None)
                try:await self.dashboard.refresh()
                except Exception:await i.followup.send('匯入已完成；請重新開啟生活看板查看。',ephemeral=True)
            button.callback=click;self.add_item(button)

    def render(self):
        if isinstance(self.records,dict):
            from backup_bundle import merge_bundle
            result=merge_bundle(str(self.owner),self.records,False)
            return discord.Embed(title='匯入我的備份 · 私人預覽',description=bundle_counts(result,True)+'\n\n同名或衝突設定保留目前帳號；已有預算時略過備份預算，同一投資標的已有資料時整組略過。確認時重新去重。\n匯入後 AI 關閉；不搬移舊 Discord ID 或 AI 資料。格式與完整性檢查不保證來源真偽，請只使用自己的備份。')
        result=preview_records(str(self.owner),self.records)
        return discord.Embed(title='匯入我的備份 · 私人預覽',description=f"可匯入帳目：{result['total']} 筆\n重複略過：{result['skipped']} 筆\n預計新增：{result['added']} 筆\n\n目標為目前操作的 Discord 帳號。只搬移消費帳目（含撤銷狀態），不搬移其他設定。確認時會重新檢查重複；備份中內容完全相同的帳目也會合併。\n只檢查格式、版本與完整性，不保證來源真偽；請確認這是你自己的備份。")

    async def on_timeout(self):
        self.closed=True;self.records=[]


BUNDLE_LABELS={'expenses':'消費帳目','categories':'自訂分類','payment_sources':'付款方式','shortcuts':'固定捷徑','budgets':'預算','assets':'股票持倉','fund_transactions':'基金交易','watchlist':'觀察清單','fund_prices':'基金淨值','trade_history':'投資交易歷史'}


def bundle_counts(result,preview=False):
    return '\n'.join(f"{BUNDLE_LABELS[name]}：備份 {r['total']}、略過 {r['skipped']}、"+(f"預計新增：{r['added']}" if preview else f"新增 {r['added']}、失敗 {r.get('failed',0)}") for name,r in result.items())
