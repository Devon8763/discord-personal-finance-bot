# 生活記帳介面精簡 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將生活記帳看板整理為「今天、帳目、更多」三個低雜訊入口，同時保留現有所有生活功能與投資入口。

**Architecture:** 僅重新組織 `Dashboard` 的 tab、Embed 與按鈕配置；資料層 `spending.py`、既有表單與私人選單服務不變。今天頁直接重用 `sp.shortcuts()` 取得前三個啟用捷徑，帳目與更多頁透過既有 `open_accounts`、`open_recent`、`open_shortcuts`、`open_reviews` 和既有表單開啟功能。

**Tech Stack:** Python 3.12+、discord.py 2.7.1、SQLite、unittest。

**Spec:** `docs/superpowers/specs/2026-09-11-lifestyle-ui-simplification-design.md`

## Global Constraints

- 不新增或修改資料表、欄位、索引、套件、外部服務或投資資料模型。
- 不改變消費、付款來源、捷徑、預算、固定負擔、圖表、回顧或 AI 的商業規則。
- 所有面板操作與回覆維持僅本人可見，且必須保留操作者／資料擁有者檢查。
- `!help`、`!記帳說明` 與既有生活文字指令保持可用；投資入口及其 AI 分頁不變。
- 測試只可使用隔離資料庫；不可讀取 Token 或修改正式 `data.db`。
- 此工作區目前無 Git 儲存庫，完成每個任務後以完整測試取代提交步驟；不要嘗試建立或重設 Git 狀態。

---

### Task 1: 為三入口導覽與今天摘要建立失敗測試

**Files:**
- Modify: `tests/test_round2.py`
- Modify: `dashboard.py: TABS, card(), Dashboard.render()`

**Interfaces:**
- Consumes: `sp.month_report(user_id, month) -> dict`、`sp.shortcuts(user_id) -> list[dict]`。
- Produces: `card(user_id, month, tab, page=0) -> tuple[discord.Embed, int, int]`，其中 `tab` 為 `今天`、`帳目` 或 `更多`。

- [ ] **Step 1: 寫入失敗測試，鎖定首頁資訊與三個導覽標籤。**

```python
async def test_dashboard_uses_three_simple_tabs_and_today_shows_only_three_shortcuts(self):
    sp.set_budget('42', '2026-09', '總額', 1000)
    for name in ('早餐', '捷運', '咖啡', '午餐'):
        sp.save_shortcut('42', name, '餐飲', None, name)
    view = dashboard.Dashboard(self.cog, 42, '2026-09')
    labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
    self.assertEqual(labels[:3], ['今天', '帳目', '更多'])
    embed, _, _ = dashboard.card('42', '2026-09', '今天')
    fields = {field.name: field.value for field in embed.fields}
    self.assertIn('本月已支出', fields)
    self.assertIn('剩餘總預算', fields)
    self.assertIn('常用捷徑', fields)
    self.assertNotIn('付款來源', fields)
    self.assertNotIn('支出圖表', fields)
```

- [ ] **Step 2: 執行測試並確認因舊五分頁配置而失敗。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: FAIL；舊按鈕仍為「總覽、支出、預算、固定負擔、AI」，且 `今天` 尚未被 `card()` 支援。

- [ ] **Step 3: 最小幅度調整 `dashboard.py` 的常數與 `card()`。**

```python
TABS = ('今天', '帳目', '更多')

if tab == '今天':
    embed = discord.Embed(title='💰 今天', color=color)
    embed.add_field(name='本月已支出', value=f"**{number(report['total'])} 元**", inline=True)
    embed.add_field(name='剩餘總預算', value=(f"**{number(total['remaining'])} 元**" if total else '尚未設定'), inline=True)
    names = [row['name'] for row in sp.shortcuts(user_id)[:3]]
    embed.add_field(name='常用捷徑', value='、'.join(names) if names else '尚無捷徑，可在「更多」建立。', inline=False)
```

將原 `支出` 的六筆清單分支改名為 `帳目`；將原預算／固定負擔／AI 的摘要分支移除，改由 `更多` Embed 以兩個欄位列出「財務設定」與「洞察」。保留 `page` 回傳與 footer 格式，以避免破壞既有 View 更新流程。

- [ ] **Step 4: 再執行該測試並確認通過。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: PASS；`今天` 不顯示低頻功能，且捷徑顯示數量最多三個。

- [ ] **Step 5: 檢查未設定總預算與無捷徑的空狀態。**

```python
embed, _, _ = dashboard.card('empty', '2026-09', '今天')
fields = {field.name: field.value for field in embed.fields}
self.assertEqual(fields['剩餘總預算'], '尚未設定')
self.assertIn('尚無捷徑', fields['常用捷徑'])
```

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: PASS；沒有預算時不顯示偽造的剩餘金額。

### Task 2: 重新配置三個入口的按鈕與既有流程

**Files:**
- Modify: `dashboard.py: Dashboard.render()`
- Modify: `tests/test_round2.py`

**Interfaces:**
- Consumes: `start_entry(view, interaction, 'expense')`、`open_accounts(view, interaction)`、`open_recent(view, interaction)`、`open_shortcuts(view, interaction)`、`open_reviews(view, interaction)`。
- Produces: `Dashboard.render()` 中每個 tab 只出現對應功能的 Discord buttons，回呼沿用現有服務。

- [ ] **Step 1: 寫入失敗測試，驗證按鈕分組與保留功能。**

```python
async def test_dashboard_groups_actions_by_today_accounts_and_more(self):
    view = dashboard.Dashboard(self.cog, 42, '2026-09')
    labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
    self.assertIn('＋記一筆消費', labels)
    self.assertNotIn('付款來源', labels)
    await next(item for item in view.children if item.label == '帳目').callback(self.interaction)
    labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
    self.assertIn('帳目管理', labels)
    self.assertIn('最近再記', labels)
    self.assertNotIn('支出圖表', labels)
    await next(item for item in view.children if item.label == '更多').callback(self.interaction)
    labels = [item.label for item in view.children if isinstance(item, discord.ui.Button)]
    self.assertIn('預算', labels)
    self.assertIn('付款來源', labels)
    self.assertIn('支出圖表', labels)
```

- [ ] **Step 2: 執行測試並確認舊頁籤的按鈕配置不符合預期。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: FAIL；帳目／更多頁尚未依需求顯示或隱藏按鈕。

- [ ] **Step 3: 以既有回呼重組 `Dashboard.render()`，不複製資料邏輯。**

```python
if self.tab == '今天':
    self.button('＋記一筆消費', expense, style=discord.ButtonStyle.success)
    for shortcut in sp.shortcuts(str(self.owner))[:3]:
        async def quick(i, key=shortcut['id']):
            await open_shortcut(self, i, key)
        self.button(shortcut['name'], quick)
elif self.tab == '帳目':
    self.button('帳目管理', accounts)
    self.button('最近再記', recent)
    self.button('切換月份', select_month)
else:
    for label, callback in (
        ('預算', budget), ('固定負擔', recurring), ('付款來源', payments),
        ('分類／提醒', help), ('常用捷徑管理', shortcuts),
        ('支出圖表', charts), ('回顧', reviews), ('生活 AI', ask),
    ):
        self.button(label, callback)
```

在實作中，首頁捷徑不可呼叫不存在的包裝函式；從 `lifestyle_ui` 匯入或新增一個明確、可測的 `open_shortcut(dashboard, interaction, shortcut_id)`，其唯一責任是重新讀取 `sp.shortcut()` 後開啟既有 `ConfirmExpense`。`open_shortcuts()` 仍用於管理頁。保留 `interaction_check()`、`refresh()`、`on_error()` 與 `sp.sync_recurring()` 的現有行為。

- [ ] **Step 4: 執行 UI 測試並確認分組及私人流程通過。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: PASS；今天只有快速操作，帳目只有帳目流程，更多能打開所有低頻功能。

- [ ] **Step 5: 加入直接捷徑確認與跨使用者防護回歸測試。**

```python
async def test_today_shortcut_reopens_confirmation_without_writing(self):
    key = sp.save_shortcut('42', '早餐', '餐飲', None, '早餐')
    view = dashboard.Dashboard(self.cog, 42, '2026-09')
    button = next(item for item in view.children if item.label == '早餐')
    await button.callback(self.interaction)
    self.interaction.response.send_modal.assert_awaited_once()
    self.assertEqual(sp.month_expenses('42', '2026-09'), [])
```

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: PASS；首頁捷徑僅開確認表單，不提前新增消費。

### Task 3: 完整回歸、文件與版本紀錄

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_round2.py`

**Interfaces:**
- Consumes: Task 1 與 Task 2 的三入口 `Dashboard`。
- Produces: 對應版本文件與完整隔離測試結果。

- [ ] **Step 1: 將與舊五分頁相關的測試名稱、預期標籤與首頁敘述更新為三入口配置。**

```python
self.assertEqual([item.label for item in view.children[:3]], ['今天', '帳目', '更多'])
self.assertNotIn('AI', [item.label for item in view.children])
self.assertNotIn('固定負擔', [item.label for item in view.children])
```

保留針對 `open_accounts`、`Charts`、`Reviews`、付款來源、固定負擔、生活 AI 的既有測試；這些功能只是移到更多頁，不能被刪除。

- [ ] **Step 2: 執行完整驗證。**

Run: `python tests/run_discord_validation.py`

Expected: 全部測試通過，測試入口仍拒絕正式資料庫連線。

- [ ] **Step 3: 手動檢查 Discord 元件容量。**

檢查每一個 tab 建立後的 `view.children` 數量不超過 25；若更多頁按鈕超過 Discord 單一 View 限制，將「財務設定」與「洞察」各做成一個二級私人 View，而不是刪除功能或在首頁重新塞按鈕。

Run: `python -m unittest discover -s tests`

Expected: PASS；所有原有生活與投資回歸測試通過。

- [ ] **Step 4: 更新使用說明與更新紀錄。**

將 README 主入口說明更新為：`!help` → 生活記帳 → 今天／帳目／更多；清楚列出今天頁的本月已支出、剩餘總預算、快速記帳與三個常用捷徑。將帳目與更多頁的功能依新位置列出，並保留投資入口與投資 AI 位置說明。

在 CHANGELOG 最新版本新增本次實際 UI 行為、影響入口、無資料庫變更、重新啟動步驟、完整測試結果與 Discord 桌面／手機實測狀態。只有完成程式及驗證後才將目標版本改為實際交付版本；不要把此計畫當成已完成的功能。

- [ ] **Step 5: 完成後執行最終驗證並記錄未驗證事項。**

Run: `python tests/run_discord_validation.py`

Expected: PASS；README 與 CHANGELOG 的版本一致，且測試沒有修改正式 `data.db`。

## Plan Self-Review

- Spec coverage：Task 1 實作今天頁與三導航；Task 2 實作帳目／更多分組和首頁前三個捷徑；Task 3 覆蓋相容性、空狀態、測試與文件。投資入口與資料層不在修改範圍，已列入全域限制。
- Placeholder scan：沒有未填內容標記或未定義的功能名稱；Task 2 明確要求新增 `open_shortcut()` 並指定其責任與呼叫的現有服務。
- Type consistency：所有 UI 開啟函式都接收 `(dashboard, interaction)`；`sp.shortcuts()`、`sp.shortcut()` 與既有 `ConfirmExpense` 均已在現有模組提供或可直接重用。
