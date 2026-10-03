# 智慧捷徑與帳目月曆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以最近 30 天的消費提供手動確認的捷徑取代建議，並讓帳目頁預設以可選日期的月曆檢視呈現。

**Architecture:** 在 `spending.py` 新增兩個純讀取資料函式：推薦函式產生至多一筆候選與被取代捷徑；月曆函式產生選定月份每日的消費總額和三級標記。`lifestyle_ui.py` 管理推薦確認表單；`dashboard.py` 管理帳目月曆、前半月／後半月選日與既有單筆修改入口。

**Tech Stack:** Python 3.12+、discord.py 2.7.1、SQLite、unittest。

**Spec:** `docs/superpowers/specs/2026-09-11-smart-shortcuts-calendar-design.md`

## Global Constraints

- 不新增資料表、欄位、索引、套件、外部服務或背景排程。
- 首頁三個捷徑只能由使用者建立、排序或確認取代；推薦不可自行寫入。
- 推薦與月曆只讀取自己的有效消費；不可讀取其他使用者、固定／訂閱／分期或已撤銷資料。
- 付款來源、分類、預算、生活 AI、文字指令與投資功能的商業規則不變。
- Discord 文字下拉最多 25 項；日期一律採前半月 1～15 日／後半月 16 日～月底。
- 所有測試使用隔離資料庫，禁止讀取 Token 或修改正式 `data.db`。

---

### Task 1: 實作純讀取的 30 天推薦與月曆資料

**Files:**
- Modify: `spending.py`
- Modify: `tests/test_round2.py`

**Interfaces:**
- Produces: `shortcut_recommendation(user_id, as_of=None) -> dict | None`。
- Produces: `calendar_days(user_id, month) -> list[dict]`，每列為 `{'date': 'YYYY-MM-DD', 'cents': int, 'level': '-'|'░'|'▒'|'▓'}`。

- [ ] **Step 1: 寫入失敗測試，鎖定 30 天邊界與兩倍門檻。**

```python
def test_shortcut_recommendation_needs_three_records_and_twice_lowest_usage(self):
    today = sp.today()
    for name in ('早餐', '捷運', '咖啡'):
        sp.save_shortcut('a', name, '餐飲', None, name)
    for _ in range(2):
        sp.add('a', 100, '餐飲', '午餐', today.isoformat())
    self.assertIsNone(sp.shortcut_recommendation('a', today))
    for _ in range(4):
        sp.add('a', 100, '餐飲', '午餐', today.isoformat())
    result = sp.shortcut_recommendation('a', today)
    self.assertEqual(result['candidate']['note'], '午餐')
    self.assertEqual(result['candidate']['count'], 6)
```

- [ ] **Step 2: 執行測試確認缺少函式而失敗。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: FAIL，`shortcut_recommendation` 尚不存在。

- [ ] **Step 3: 以單一 SQL 聚合與最小 Python 排序實作推薦。**

```python
def shortcut_recommendation(user_id, as_of=None):
    end = as_of or today()
    start = end - timedelta(days=29)
    fixed = shortcuts(user_id)[:3]
    if len(fixed) != 3:
        return None
    # Query only manual, consumption, non-voided rows in [start, end].
    # Group by category, payment_source_id and note; join active payment sources.
    # Remove the exact category/source/note combinations already in fixed.
    # Return the top candidate only if count >= max(3, lowest_count * 2).
```

最低捷徑以相同 30 天範圍、分類／付款來源 ID／用途完全相符的有效手動消費次數計算；同分時以捷徑位置較後者為取代目標。候選付款來源或分類已停用時排除。回傳需包含 `candidate`、`target`、`period_start`、`period_end`、`lowest_count`。

- [ ] **Step 4: 為月曆標記寫入失敗測試。**

```python
def test_calendar_days_marks_only_effective_consumption_and_three_levels(self):
    sp.add('a', 10, '餐飲', '低', '2026-09-01')
    sp.add('a', 40, '餐飲', '中', '2026-09-02')
    sp.add('a', 100, '餐飲', '高', '2026-09-03')
    days = {row['date']: row for row in sp.calendar_days('a', '2026-09')}
    self.assertEqual(days['2026-09-01']['level'], '░')
    self.assertEqual(days['2026-09-02']['level'], '▒')
    self.assertEqual(days['2026-09-03']['level'], '▓')
    self.assertEqual(days['2026-09-04']['level'], '-')
```

- [ ] **Step 5: 實作 `calendar_days()` 並驗證。**

```python
def calendar_days(user_id, month):
    start = month_date(month)
    end = min(next_month(start) - timedelta(days=1), today())
    totals = {day.isoformat(): 0 for day in each_day(start, end)}
    # Sum only voided=0 and kind='consumption' rows, then assign -, ░, ▒, ▓.
    return [{'date': day, 'cents': cents, 'level': level(cents, maximum)} for day, cents in totals.items()]
```

`each_day()` 可作為 `spending.py` 私有產生器；未來月份沿用 `month_report()` 的 `ValueError('尚未到此月份')` 行為。執行 `python -m unittest discover -s tests -p test_round2.py`，預期 PASS。

### Task 2: 在捷徑管理頁顯示並確認採用推薦

**Files:**
- Modify: `lifestyle_ui.py`
- Modify: `tests/test_round2.py`

**Interfaces:**
- Consumes: `sp.shortcut_recommendation(str(owner)) -> dict | None`。
- Produces: `RecommendationView(OwnedView)`；採用動作開啟既有 `ShortcutForm`，不直接寫入。

- [ ] **Step 1: 寫入失敗 UI 測試。**

```python
async def test_shortcut_recommendation_is_private_and_requires_form_confirmation(self):
    # Arrange three fixed shortcuts and six matching candidate expenses.
    await lifestyle_ui.open_shortcuts(self.dashboard, self.interaction)
    view = self.interaction.response.send_message.await_args.kwargs['view']
    await next(item for item in view.children if item.label == '查看推薦').callback(self.interaction)
    self.assertTrue(self.interaction.response.edit_message.awaited or self.interaction.response.send_message.awaited)
    self.assertEqual(sp.month_expenses('42', '2026-09'), self.before_expenses)
```

- [ ] **Step 2: 執行測試確認「查看推薦」尚不存在而失敗。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: FAIL，捷徑管理頁未提供推薦入口。

- [ ] **Step 3: 在 `Shortcuts` 的管理模式加入單一「查看推薦」按鈕。**

按鈕只在 `manage=True` 出現。沒有推薦時回覆「近 30 天尚無明顯高於首頁捷徑的消費習慣。」；有推薦時顯示候選與目標的名稱、分類、付款來源、次數與日期範圍。按「採用並修改」開啟 `ShortcutForm`：`original` 使用目標捷徑 ID 與位置，欄位預填候選分類、來源、用途、名稱為候選用途、金額保留目標捷徑原本 `cents`。只有 `ShortcutForm.on_submit()` 成功後才更新目標捷徑；按返回或關閉不寫入。

- [ ] **Step 4: 執行 UI 測試並確認跨使用者與停用來源防護仍生效。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: PASS；推薦不能自行改捷徑，且既有 `ShortcutForm` 的來源啟用檢查仍阻擋失效來源。

### Task 3: 建立帳目月曆與前／後半月選日

**Files:**
- Modify: `dashboard.py`
- Modify: `tests/test_round2.py`

**Interfaces:**
- Consumes: `sp.calendar_days(user_id, month)`、`sp.month_expenses(user_id, month)`、`EditExpenseModal(view, expense)`。
- Produces: `CalendarAccounts(OwnedView)`，含月曆、清單切換、月份切換、前／後半月日期選單與當日帳目。

- [ ] **Step 1: 寫入失敗 UI 測試。**

```python
async def test_accounts_default_to_calendar_and_date_picker_uses_month_halves(self):
    await dashboard.open_accounts(self.dashboard, self.interaction, '2026-08')
    view = self.interaction.response.send_message.await_args.kwargs['view']
    self.assertIn('月曆', view.render().title)
    self.assertIn('前半月', [item.label for item in view.children if isinstance(item, discord.ui.Button)])
    await next(item for item in view.children if item.label == '後半月').callback(self.interaction)
    select = next(item for item in view.children if isinstance(item, discord.ui.Select))
    self.assertLessEqual(len(select.options), 16)
```

- [ ] **Step 2: 執行測試確認舊 `Accounts(Picker)` 只有清單而失敗。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: FAIL，現有帳目入口開啟清單 Picker，沒有月曆或半月選日。

- [ ] **Step 3: 以 `CalendarAccounts` 取代帳目入口的預設 View。**

月曆 Embed 用星期欄與日期／`—`／`░`／`▒`／`▓` 顯示當月；日期格不顯示金額。View 預設 `mode='calendar'`、`half='first'`。按「前半月」或「後半月」只替換日期 Select 的 1～15 或 16～月底選項，選項標籤使用 `YYYY-MM-DD（星期）`。選日後將同一 View 切換成當日明細 Embed，顯示當日總額與既有 `EditExpenseModal` 選取清單；提供「回月曆」與「清單檢視」。

月份切換重用 `choose_month()`，清單檢視重用現有 `Accounts` 的六筆分頁查詢與修改回呼。未來月份由既有月份選單排除；無帳目月仍顯示月曆與「尚無已記錄消費」提示。

- [ ] **Step 4: 執行月曆 UI 與完整回歸。**

Run: `python -m unittest discover -s tests -p test_round2.py`

Expected: PASS；31 天月份後半月 Select 只有 16 項，選日後才顯示金額，帳目修改仍只允許所有者。

### Task 4: 文件、完整驗證與實測紀錄

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_round2.py`

- [ ] **Step 1: 執行完整隔離驗證。**

Run: `python tests/run_discord_validation.py`

Expected: 所有生活與投資回歸測試通過，測試入口仍拒絕正式資料庫連線。

- [ ] **Step 2: 更新 README 與 CHANGELOG。**

README 說明首頁三個手動捷徑、管理頁的近 30 天推薦規則，以及帳目頁預設月曆、前半月／後半月選日、日期選取後才看金額。CHANGELOG 僅記錄實際新增功能，並包含版本、無資料庫變更、重新啟動步驟、測試數量、隔離資料庫結果，以及 Discord 桌面／手機月曆未實測或已實測結果。

- [ ] **Step 3: 以 Discord 桌面與手機手動檢查。**

檢查三項：月曆星期欄與深淺符號可讀、前半月／後半月切換容易理解、日期選項完整且不超過 25 項。若未登入 Discord，不可宣稱已實測；在 CHANGELOG 與 README 明確保留未驗證事項。

## Plan Self-Review

- Spec coverage：Task 1 覆蓋 30 天、三次、兩倍、排除與月曆資料；Task 2 覆蓋手動確認取代與來源防護；Task 3 覆蓋月曆預設、深淺、半月選日、明細與修改；Task 4 覆蓋回歸與文件。
- Placeholder scan：沒有未填內容標記；每個任務都有測試、失敗預期、最小實作與通過條件。
- Type consistency：資料層固定使用 `shortcut_recommendation()` 與 `calendar_days()`；UI 固定使用現有 `ShortcutForm`、`EditExpenseModal` 與 `open_accounts()`。
