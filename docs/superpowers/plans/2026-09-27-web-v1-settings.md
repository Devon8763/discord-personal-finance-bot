# localhost Web v1：設定頁 Implementation Plan

> **狀態：計畫、尚未實作。**
>
> 本文件規劃後續實作，不代表 Python、HTML、CSS、測試、資料庫、依賴、README、CHANGELOG、版本或 Git 歷史已修改。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 localhost Web 加入登入者專屬設定頁，讓本人管理台灣本月總預算／分類預算、分類與付款方式，同時保持既有 SQLite 規則、Discord 流程、Web session 邊界與歷史資料相容。

**Architecture:** 所有 Web 讀寫固定走 `web route → OAuth session user_id → life_ledger_service.py → spending.py → SQLite`。`spending.py` 以既有 `BEGIN IMMEDIATE` 實作狀態相依的預算寫入和原子分類改名；Service 只做 user ID 正規化與轉交；Jinja 頁面使用 URL-encoded form、既有 CSRF 與 POST/Redirect/GET，不加入 JavaScript 或新架構層。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、Starlette session、標準函式庫 `json`／`urllib.parse`、SQLite、既有 `ledger.transaction()`、unittest、FastAPI TestClient、TemporaryDirectory。

**Spec:** `docs/superpowers/specs/2026-09-27-web-v1-settings-design.md`

**設定頁文案／預算補充：** 預算僅接受正整數台幣，消費金額仍沿用既有小數規則；Web 欄位用 `type="number"`、`inputmode="numeric"`、`step="1"`、`min="1"` 提示輸入，伺服器核心負責驗證且顯示不含 `.0`／`.00`。分類管理按鈕顯示「更改」，原子同步與 undo 相容性不變。

## Global Constraints

- 只實作設定頁 v1：台灣本月預算、分類新增／改名／停用、付款方式新增／改名／停用，以及首頁入口。
- 不實作固定支出、捷徑、提醒、帳目編輯／撤銷／刪除、匯出、備份、AI、投資、月份切換、搜尋擴充、JSON API、React、App 或可自訂版面。
- 不新增資料表、欄位、索引、migration、ORM、Repository、DI、設定框架、前端框架或依賴。
- Web 不得 import `spending`、`db`、`ledger` 或寫 SQL；Service 不得 import Discord、Web、SQLite／`db.py` 或自行管理交易。
- `user_id` 只取自 `auth.current_user_id(request.session)`。query／form 中同名欄位及未知欄位不得影響身分、月份或資料範圍。
- 所有測試使用 `TemporaryDirectory` 和 `patch.object(db, "DB_NAME", ...)`；不得讀寫正式 `data.db`、Token、`.env`、備份、匯出檔或使用者資料。
- 每個任務先寫最小失敗測試、執行確認 RED，再做最小實作並確認 GREEN。不得以放寬斷言、停用測試或大量 ignore 取得綠燈。
- 實作完成後才依當時最新版更新 README／CHANGELOG。以目前 `0.11.9` 推算預期為 `0.11.10`，但執行時必須先重讀最新版本；本次文件工作不改版本。
- 實作期間不 stage、commit 或 push，除非之後另獲使用者明確授權。

## Public Service Interfaces

設定頁只能呼叫以下既有及新增的公開 Service；後續任務名稱必須保持一致：

```python
# Existing reads/writes
get_today()
get_month_summary(user_id, month=None)
get_categories(user_id, include_inactive=False)
get_payment_sources(user_id, include_inactive=False)
set_budget(user_id, month, category, amount)

# New thin façades
clear_budget(user_id, month, category)
set_total_budget_to_category_sum(user_id, month)
add_category(user_id, name)
rename_category(user_id, old_name, new_name)
disable_category(user_id, name)
add_payment_source(user_id, name)
rename_payment_source(user_id, payment_source_id, name)
disable_payment_source(user_id, payment_source_id)
```

對應核心新增／調整限定為：

```python
spending.set_budget(...)                    # 調整現有規則
spending.clear_budget(...)                  # 新增
spending.set_total_budget_to_category_sum(...)  # 新增
spending.rename_category(...)               # 新增
```

分類新增／停用 façade 轉交既有 `spending.set_category(..., True/False)`；付款方式 façade 轉交既有函式，不另造第二套驗證。

## Review Focus

- 每個狀態相依的預算檢查必須在取得 `BEGIN IMMEDIATE` 寫入鎖後，以同一 connection 重新讀取並寫入；不能由 route 或 Service 預算合計。
- 總預算下限包含全部非總額預算，也包含已停用分類仍保留的預算。清除總預算不能連帶刪除分類預算。
- 分類改名不得只改目前有效帳目；voided 帳目、所有月份預算、啟用／停用 recurring、啟用／停用 shortcut 和全部相關 undo JSON 都要在同一交易中更新。
- 改名後實際執行最新撤銷，不能把舊分類寫回帳目。任何中途失敗時，所有表及 JSON 都保持原狀。
- 付款方式改名不能回寫 `expenses.payment_source_name`；「現金」「未指定」仍不可改名或停用。
- 設定頁的每個 POST 都需驗證 CSRF、唯一欄位、session 身分及 PRG。未登入或 CSRF 失敗時不可呼叫 Service。
- 停用分類與付款方式在設定頁保留狀態／歷史，但立即從首頁快速記帳選單消失。

## 預計修改檔案

| 檔案 | 實作時責任 |
| --- | --- |
| `spending.py` | 原子預算規則、預算清除／分類合計、原子分類改名及 undo 快照相容。 |
| `life_ledger_service.py` | 設定頁所需的最小薄 façade。 |
| `web/routes.py` | `/settings`、必要 POST、登入／CSRF／表單結構、PRG 與安全 context。 |
| `web/templates/home.html` | 登入後新增「設定」入口。 |
| `web/templates/settings.html` | 三個直式區塊及 URL-encoded forms。 |
| `web/static/web.css` | 設定頁最小單欄、狀態、表單與小螢幕樣式。 |
| `tests/test_spending.py` | 預算與分類核心規則。 |
| `tests/test_write_reliability.py` | 原子 rollback、鎖內重讀與跨使用者回歸。 |
| `tests/test_life_ledger_service.py` | 新 façade 的 `_user()` 正規化與參數轉交。 |
| `tests/test_web_auth.py` | OAuth session、CSRF、GET／POST、XSS、PRG、快速記帳選項與 import boundary。 |
| `README.md` | 實作完成且驗證後更新版本與可用範圍。 |
| `CHANGELOG.md` | 實作完成且驗證後記錄實際變更、資料影響、驗證與未驗證事項。 |

不修改：`schema.py`、`db.py`、`ledger.py`、`web/app.py`、`web/auth.py`、requirements、`.env.example`、Discord UI／指令、資料庫結構、Token、備份、匯出檔及使用者資料。

---

### Task 1: 放寬並原子化本月預算核心規則

**Files:**
- Modify: `tests/test_spending.py`
- Modify: `tests/test_write_reliability.py`
- Modify: `spending.py`

**Interfaces:**
- Modify: `spending.set_budget(user_id, month, cat, amount) -> None`
- Add: `spending.clear_budget(user_id, month, cat) -> None`
- Add: `spending.set_total_budget_to_category_sum(user_id, month) -> None`

- [ ] **Step 1: 先寫預算規則的失敗測試**

  在 `tests/test_spending.py` 的隔離 fixture 新增測試，逐項固定下列行為：

  ```python
  sp.set_budget("a", "2026-09", "餐飲", 300)
  self.assertEqual(_budget("a", "2026-09", "餐飲"), 300)
  self.assertIsNone(_budget("a", "2026-09", "總額"))

  sp.set_budget("a", "2026-09", "總額", 500)
  sp.set_budget("a", "2026-09", "交通", 200)
  with self.assertRaises(ValueError):
      sp.set_budget("a", "2026-09", "總額", 499)
  with self.assertRaises(ValueError):
      sp.set_budget("a", "2026-09", "購物", 1)
  ```

  另測：

  - `clear_budget(..., "總額")` 後餐飲／交通仍在。
  - `clear_budget(..., "餐飲")` 只移除餐飲。
  - 清除不存在項目拒絕且其他列不變。
  - 停用分類後既有預算仍納入總額下限，可清除但不可修改。
  - `set_total_budget_to_category_sum()` 將總額設為所有分類 cents 精確加總；無分類預算時拒絕且不建立總額。
  - 使用者 `a` 的操作不影響 `b` 同月份、同分類預算。

- [ ] **Step 2: 執行 focused core tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending.SpendingTests.test_budget_without_total_clear_and_category_sum -v
  ```

  Expected: 現有 `set_budget()` 因沒有總額而失敗，且兩個新函式尚不存在。

- [ ] **Step 3: 實作同交易的最小預算邏輯**

  在 `spending.py`：

  - 保留 `month_date()` 與 `money()` 的既有輸入驗證。
  - 在既有 `transaction()` 內用同一 connection 驗證本人啟用分類、重新讀取本月 budgets、套用候選變更、驗證總額下限，再 upsert。
  - 若總額不存在，只允許分類預算存在；若總額存在，分類合計不得超過它。
  - `clear_budget()` 在同一 transaction 依 `user_id + month + category` 確認列存在後刪除；分類可為已停用名稱。
  - `set_total_budget_to_category_sum()` 在 transaction 內查詢非總額列、拒絕空集合並 upsert cents 合計。
  - 新增一個最小私有 connection-aware 分類驗證 helper，避免在 transaction 中透過 `rows()` 另開連線。
  - 清除預算時同步移除同 user／month／category 尚未送出的 notice；不碰已送出歷史。
  - 寫入完成後仍在同一 transaction 內沿用 `alerts()`；不複製提醒計算。

- [ ] **Step 4: 先寫鎖內重讀與 rollback 失敗測試**

  在 `tests/test_write_reliability.py`：

  - 模擬取得 transaction 前另一寫入已新增分類預算，確認設定總額會以鎖內最新合計拒絕過低值。
  - 分別對 budgets 的 upsert／delete 及相關 notice 寫入建立暫存 trigger 故障，確認預算與 notices snapshot 完全不變。
  - 所有 trigger 與資料都只存在暫存 SQLite。

- [ ] **Step 5: 執行核心 GREEN 與相關回歸**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_write_reliability -v
  ```

  Expected: 新預算語意、原子 rollback、舊預算通知與既有使用者隔離測試全部通過。

### Task 2: 原子分類改名與 undo 快照一致性

**Files:**
- Modify: `tests/test_spending.py`
- Modify: `tests/test_write_reliability.py`
- Modify: `spending.py`

**Interfaces:**
- Add: `spending.rename_category(user_id, old_name, new_name) -> None`
- Reuse: `spending.set_category(user_id, name, active)` for add/reactivate/disable

- [ ] **Step 1: 先寫完整資料範圍的失敗測試**

  在 `tests/test_spending.py` 使用單一使用者建立：

  - 舊分類的有效手動帳目。
  - 舊分類的 `voided=1` 帳目。
  - 至少兩個月份的分類預算。
  - 啟用與停用的 `recurring_expenses`。
  - 啟用與停用的 `spending_shortcuts`。
  - `before_json.category` 為舊分類的未撤銷及已撤銷 action 快照。

  再建立另一使用者的同名資料。呼叫 `sp.rename_category("a", "舊分類", "新分類")` 後斷言：本人上述所有 category 欄位及 JSON 都是新名；另一使用者仍是舊名；目前分類清單只有正確啟用／停用狀態。

  加入真實 undo 驗證：先讓帳目修改產生 `before_json.category == "舊分類"`，改名後執行 `sp.undo()`，最終帳目 category 必須是「新分類」，不可是舊名。

- [ ] **Step 2: 寫內建分類、重名與保留名稱的 RED 測試**

  測試：

  - 將本人內建「餐飲」改為「外食」後，`餐飲` 對本人為停用、`外食` 啟用；另一使用者的「餐飲」仍啟用。
  - 自訂分類改名不留下額外停用舊名；停用仍可用既有同名新增重新啟用。
  - 新名為 `總額`、舊名與新名相同、舊名未啟用、或新名已存在於內建／啟用／停用／歷史相關資料時，全部拒絕且資料不變。
  - 新增已啟用同名不建立重複列；新增已停用同名會重新啟用。

- [ ] **Step 3: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending.SpendingTests.test_category_rename_updates_all_owned_references_and_undo -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending.SpendingTests.test_builtin_category_rename_and_conflicts -v
  ```

  Expected: `rename_category()` 尚不存在，測試失敗；既有新增／停用行為維持可觀察。

- [ ] **Step 4: 實作最小原子改名**

  在 `spending.py`：

  - 重用既有分類名稱清理規則，額外拒絕同名與所有目標衝突。
  - 在單一 `transaction()` 取得鎖後重新確認舊分類目前為本人啟用分類，再檢查新名未出現在本人相關分類定義／歷史資料。
  - 內建分類以舊名 inactive override + 新名 active override 表示本人改名；自訂分類更新本人定義鍵。
  - 所有表 UPDATE 都限制 `user_id`，涵蓋 `expenses`（不按 voided／source 篩選）、`budgets`（所有月份）、`recurring_expenses`、`spending_shortcuts`。
  - 讀取本人 `expense_actions.before_json`，以 `json.loads()` 處理非 `null` object，只改 `category == old_name` 的欄位，再參數化 UPDATE。
  - 刪除本人尚未送出的舊分類 budget notices；不重寫已送出歷史。
  - 任何 JSON、唯一性或 SQLite 失敗由既有 transaction rollback；可預期名稱衝突轉為固定 `ValueError`，不暴露 SQLite 文字。

- [ ] **Step 5: 先寫中途失敗 rollback 測試**

  在 `tests/test_write_reliability.py` 對改名中段（例如 recurring 或 shortcut UPDATE）建立 trigger，使寫入失敗。改名前後 snapshot 必須完全相同，包含分類 overrides、expenses、budgets、recurring、shortcuts、actions 與 notices。另驗證 malformed target JSON 使整筆改名拒絕且零部分寫入。

- [ ] **Step 6: 執行核心 GREEN**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_write_reliability -v
  ```

  Expected: 分類資料範圍、內建 override、重名、跨使用者、undo 及 rollback 測試全部通過；付款方式既有 snapshot 測試仍通過。

### Task 3: 補齊設定頁 Service façade

**Files:**
- Modify: `tests/test_life_ledger_service.py`
- Modify: `life_ledger_service.py`

**Interfaces:**
- Add exactly the eight new façade functions listed in **Public Service Interfaces**.

- [ ] **Step 1: 先寫參數轉交與 user ID 正規化失敗測試**

  以 `patch.object(service.sp, ...)` 表格化測試：

  ```python
  cases = (
      ("clear_budget", "clear_budget", (42, "2026-09", "餐飲"),
       ("42", "2026-09", "餐飲")),
      ("set_total_budget_to_category_sum", "set_total_budget_to_category_sum",
       (42, "2026-09"), ("42", "2026-09")),
      ("add_category", "set_category", (42, "寵物"), ("42", "寵物", True)),
      ("rename_category", "rename_category", (42, "寵物", "毛孩"),
       ("42", "寵物", "毛孩")),
      ("disable_category", "set_category", (42, "寵物"),
       ("42", "寵物", False)),
      ("add_payment_source", "add_payment_source", (42, "卡"), ("42", "卡")),
      ("rename_payment_source", "rename_payment_source", (42, 7, "新卡"),
       ("42", 7, "新卡")),
      ("disable_payment_source", "disable_payment_source", (42, 7),
       ("42", 7)),
  )
  ```

  每項都驗證回傳值／例外不被改寫，Service 檔仍只 import `spending as sp`。

- [ ] **Step 2: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_life_ledger_service.LifeLedgerServiceTests.test_settings_facades_normalize_users_and_forward_parameters -v
  ```

  Expected: 新 façade 尚不存在而失敗。

- [ ] **Step 3: 實作一行式薄 façade**

  每個函式只呼叫對應 `sp` 函式並以 `_user(user_id)` 傳入本人 ID；不捕捉例外、不轉換回傳、不加入驗證、SQL、transaction、Discord 或 FastAPI 物件。

- [ ] **Step 4: 驗證 Service 邊界**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_life_ledger_service -v
  rg -n "import discord|from discord|import db|from db|sqlite3|web\." life_ledger_service.py
  ```

  Expected: Service 測試通過；`rg` 無輸出。

### Task 4: 建立受保護的設定頁 GET、首頁入口與三區塊畫面

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/routes.py`
- Modify: `web/templates/home.html`
- Create: `web/templates/settings.html`
- Modify: `web/static/web.css`

**Interfaces:**
- Add: `GET /settings`
- Consume only: `current_user_id()`, `get_today()`, `get_month_summary()`, `get_categories()`, `get_payment_sources()`.

- [ ] **Step 1: 先寫登入、入口、本人隔離與狀態的 RED 測試**

  在 `tests/test_web_auth.py` 新增 `WebSettingsPageTests(_WebLedgerTestFixture, unittest.TestCase)`，重用既有 OAuth mock、CSRF helper、台灣固定日期與暫存 DB。測試：

  - 未登入 `GET /settings` 回 403；patch 四個 Service read 確認都未呼叫；頁面不含本人／他人名稱與預算。
  - 登入後首頁含 `href="/settings"` 與「設定」；未登入首頁不含入口。
  - 登入本人只顯示本人的預算、分類、付款方式；`?user_id=他人&unknown=x` 不改變結果。
  - 標題為固定測試時鐘的 `2026 年 09 月`，route 以 `life_service.get_today()` 決定月份，不讀 query `month`。
  - 頁面依序出現「本月預算」「分類管理」「付款方式管理」。
  - 無預算、無啟用分類及付款方式空狀態使用固定文案。
  - 已停用但仍有預算的分類顯示「已停用」與清除操作，不顯示修改表單；分類合計不存在時不顯示可用合計按鈕。
  - 「現金」「未指定」不渲染改名／停用按鈕。

- [ ] **Step 2: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSettingsPageTests -v
  ```

  Expected: `/settings` 為 404 且首頁沒有設定入口。

- [ ] **Step 3: 實作最小 GET 與 context**

  在 `web/routes.py`：

  - 先取 session user ID，未登入立即固定 403。
  - 以 `life_service.get_today().strftime("%Y-%m")` 取得本月。
  - 只呼叫指定 Service reads，將 active categories 轉為 set 供標示；以摘要 `budgets` 建立總額、啟用分類及有預算停用分類的安全 context。
  - 捕捉預期 `TypeError`／`ValueError`，回固定 `SETTINGS_ERROR` HTTP 400，不將 exception 放入 context。
  - `saved=1` 只映射固定成功訊息；其他值忽略。

  在 `home.html` 新增普通「設定」連結。在新 `settings.html` 以三個 `<section>` 和一般 `<form>` 呈現；所有文字保持 Jinja autoescape，不使用 `|safe`。CSS 只增加單欄 section、列狀態、表單間距與小螢幕規則，不做 JS、modal、拖曳或自訂布局。

- [ ] **Step 4: 驗證 GET 與頁面資料界線**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSettingsPageTests -v
  ```

  Expected: 登入、入口、本人隔離、三區塊、空狀態、停用狀態及保留付款方式畫面測試通過。

### Task 5: 實作預算 POST、CSRF 與 PRG

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/routes.py`
- Modify: `web/templates/settings.html`

**Interfaces:**
- Add: `POST /settings/budgets/set`
- Add: `POST /settings/budgets/clear`
- Add: `POST /settings/budgets/use-category-sum`

- [ ] **Step 1: 先寫三個預算操作的 RED 測試**

  使用真實暫存 SQLite 驗證：

  - 沒有總預算可設定分類預算。
  - 總預算不得低於分類合計；失敗沒有部分寫入。
  - 清除總預算保留分類預算；清除分類預算保留總預算。
  - 合計按鈕把總預算設為目前分類合計；沒有分類預算時直接 POST 安全拒絕。
  - 每個成功 POST 回 `303` 且 Location 為 `/settings?saved=1`；GET 顯示固定「設定已更新」，刷新不重複寫入。
  - query／form 的 `user_id`、`month` 及偽造 `amount` 不改變本人、目前月份或核心合計。

- [ ] **Step 2: 先寫 CSRF、欄位與安全錯誤 RED 測試**

  對每個 route 測試：

  - 缺少／錯誤／重複 CSRF 回 403，Service 未呼叫、session 仍有效。
  - 非 URL-encoded、缺少／重複必要欄位回 400，不寫入。
  - 無效金額、停用／他人分類、Service `TypeError`／`ValueError` 回固定安全訊息，不含原始例外、SQLite、`.db`、路徑、Token 或 Discord ID。
  - 使用者輸入如 `<script>` 只以 escape 後 draft 出現。

- [ ] **Step 3: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSettingsPageTests -v
  ```

  Expected: 三個 POST route 尚不存在或不會寫入，新增測試失敗。

- [ ] **Step 4: 實作最小 URL-encoded POST helper 與 routes**

  在 `web/routes.py` 重用現有 `parse_qs(await request.body(), keep_blank_values=True, max_num_fields=...)` 模式。可增加一個只負責「確認 URL-encoded、限制欄位數、回傳 dict」的私有 helper，不能把核心驗證搬入 Web。

  每個 route 依序：登入 → parse → CSRF → 唯一欄位 → 由 `get_today()` 取得 month → 呼叫單一 Service → 303。任何 query／form `user_id` 或 `month` 不讀取。預期輸入／核心錯誤用同一 `_settings_response()` 重繪 HTTP 400。

- [ ] **Step 5: 驗證預算 Web 流程**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSettingsPageTests -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending tests.test_write_reliability -v
  ```

  Expected: 預算 GET／POST、CSRF、PRG、安全錯誤與核心 transaction 測試全部通過。

### Task 6: 實作分類與付款方式 POST，固定歷史相容行為

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/routes.py`
- Modify: `web/templates/settings.html`

**Interfaces:**
- Add the six category/payment POST routes listed in the spec.
- Consume only the new category/payment Service façades.

- [ ] **Step 1: 先寫分類管理的 RED 測試**

  Web 測試涵蓋：

  - 新增分類、同名停用分類重新啟用、改名與停用各自 `303 → /settings?saved=1`。
  - `總額`、重名、他人專屬分類與無效名稱固定 400；form/query `user_id` 無效。
  - 改名後頁面與快速記帳首頁只顯示新名；停用後分類仍在設定頁標為停用，但不再出現在首頁 `<select name="category">`。
  - 已停用分類保留既有本月預算、只可清除；不因頁面 GET 或 POST 自動刪除／重啟預算。
  - 缺少／錯誤／重複 CSRF、必要欄位重複、Service 例外及 XSS draft 都依 Task 5 的固定安全模式處理。

- [ ] **Step 2: 先寫付款方式管理的 RED 測試**

  Web 測試涵蓋：

  - 新增、改名、停用本人付款方式成功並 PRG。
  - 他人的 `payment_source_id` 不能改名或停用；query/form `user_id` 無效且兩位使用者資料都不被錯寫。
  - 「現金」「未指定」直接偽造 POST 仍回 400 且保持啟用／原名。
  - 停用後仍在設定頁標為停用，但不再出現在快速記帳付款方式 select。
  - 先用該來源新增帳目，再改名來源；斷言歷史 `payment_source_name` 仍是舊名，設定頁顯示來源新名。
  - CSRF、重複欄位、同名含停用來源、XSS 與固定安全錯誤。

- [ ] **Step 3: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSettingsPageTests -v
  ```

  Expected: 六個 route 尚不存在，管理操作測試失敗；現有快速記帳選項測試維持原行為。

- [ ] **Step 4: 實作最小 routes 與 forms**

  - 所有 route 使用 Task 5 的同一 parse／CSRF／安全 response 模式。
  - payment source ID 只在 route 解析為整數，擁有者與保留名稱由核心驗證。
  - category route 只傳 name／old_name／new_name，不讀或推導任何他人 ID。
  - template 只為可操作項目顯示表單；保留付款方式及停用項目用文字狀態呈現。直接 POST 的防護仍由核心負責。
  - 不加入前端確認框、JavaScript、重新啟用付款方式、歷史改寫或其他管理頁。

- [ ] **Step 5: 驗證管理流程與既有歷史語意**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSettingsPageTests -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending_phase1 -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_shortcut_dropdowns -v
  ```

  Expected: Web 管理、快速記帳選項、付款方式保留名稱／歷史快照及 Discord 下拉既有行為全部通過。

### Task 7: 強化邊界、完整回歸與實作交付文件

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify after implementation only: `README.md`
- Modify after implementation only: `CHANGELOG.md`
- Verify: `web/app.py`, `web/auth.py`, `web/routes.py`, `life_ledger_service.py`, `schema.py`

**Interfaces:**
- Preserve: 現有 AST Web import boundary。
- Preserve: `tests/run_discord_validation.py` 隔離正式資料庫的完整驗證。

- [ ] **Step 1: 補齊 import boundary 與資料庫結構不變測試**

  擴充既有 `test_web_modules_keep_database_access_behind_life_service`，持續斷言：

  - `web/app.py`、`web/auth.py`、`web/routes.py` 都不 import `spending`、`db`、`ledger`。
  - 只有 `web/routes.py` import `life_ledger_service`。
  - `life_ledger_service.py` 不 import Discord、Web、`db`、`sqlite3` 或 `ledger`。

  既有 schema 初始化測試必須保持完全相同的資料表與欄位；本功能不得修改 `schema.py`。

- [ ] **Step 2: 執行功能與相鄰回歸**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_spending_phase1 -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_write_reliability -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_life_ledger_service -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_simple_dashboard -v
  ```

  Expected: 新核心、Service、Web 設定頁與既有 OAuth、快速記帳、月曆、搜尋、Dashboard、付款方式、撤銷測試全部通過。

- [ ] **Step 3: 實作完成後才更新 README／CHANGELOG 與版本**

  重新讀取當時 `CHANGELOG.md` 最新版本。若仍為 `0.11.9`，設定頁作為新的使用者可用功能升至 `0.11.10`；若已前進，只遞增一個適當修訂版。同步 README 目前版本。

  CHANGELOG 以繁體中文只記錄實際完成內容：

  - localhost 設定頁三區塊與入口。
  - 預算新語意、原子分類改名及 undo 相容。
  - Service/Web 邊界、CSRF、PRG、跨使用者隔離。
  - 無資料表／依賴變更及無額外升級步驟。
  - 實際測試命令、數量與結果。
  - 尚未驗證的真實 Discord／瀏覽器／正式資料庫互動。

  不把固定支出管理、捷徑、提醒、App、React、月份切換或其他未做功能寫成已完成。

- [ ] **Step 4: 執行完整品質閘門**

  ```powershell
  & '.venv\Scripts\python.exe' -m ruff check .
  & '.venv\Scripts\python.exe' -m unittest tests.test_life_ledger_service -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_simple_dashboard -v
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
  & '.venv\Scripts\python.exe' tests\run_discord_validation.py
  git diff --check
  git status --short
  ```

  Expected: Ruff 與所有 focused／完整隔離測試通過；`git diff --check` 無空白錯誤。`git status --short` 只包含本功能預期檔案及實作前既有未提交變更，不含正式 `data.db`、Token、`.env`、備份、匯出或使用者資料；沒有 staged、commit 或 push。

---

## 計畫自我檢查

- **需求覆蓋：** Task 1 處理無總額分類預算、總額下限、兩種清除與分類合計；Task 2 處理內建／自訂分類、完整資料範圍、undo 與 rollback；Task 3 固定 Service 介面；Tasks 4–6 完成登入頁面、三區塊、CSRF、PRG、注入防護、保留付款方式與快速記帳選項；Task 7 完整回歸及文件版本。
- **介面一致：** 規格與計畫使用相同的八個新增 Service façade、三個預算 POST、三個分類 POST 及三個付款方式 POST；Web 不直接呼叫核心。
- **交易完整：** 預算狀態檢查／寫入與分類全部引用／JSON 更新都在既有 `BEGIN IMMEDIATE` 內完成；失敗注入測試要求完整 rollback，沒有 route 預先檢查競態。
- **資料語意：** 分類改名回寫分類名稱和 undo snapshot；付款方式改名刻意保留歷史帳目名稱 snapshot。停用不刪資料，停用分類預算仍計入總額下限。
- **範圍控制：** 沒有 schema、migration、Repository、DI、ORM、新依賴、JavaScript、React、App 或非設定頁功能；Discord UI／指令不在修改清單。
- **文件狀態：** 本計畫只描述未來工作；README、CHANGELOG 與版本只在功能真正完成並取得實際驗證結果後更新。
- **未決事項：** 無。路由、Service 名稱、月份來源、重名、內建 override、停用預算、付款快照、錯誤狀態及驗證順序均已定義。
