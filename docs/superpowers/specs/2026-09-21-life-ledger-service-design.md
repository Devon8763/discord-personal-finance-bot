# 精簡版生活記帳 Services 層設計

## 目標

建立單一 `life_ledger_service.py`，讓 Discord UI 與未來 Web 後端共用目前的生活消費規則。Services 層只接受 Python 基本資料並回傳 `dict`、`list`、整數或 `None`，不得依賴 `discord.py` 物件。

本輪只整理生活消費的新增、單筆讀取、列表／搜尋、修改、軟刪除、既有撤銷、月曆／摘要／圖表讀取，以及分類與付款方式選項。不得建立 API、網站、登入、前端或新的帳務產品功能。

## 現況與保留邊界

- `spending.py` 已包含金額與日期驗證、分類／付款方式驗證、使用者範圍 SQL、revision 衝突防護、`expense_actions` 操作紀錄、`voided` 軟刪除語意，以及摘要、月曆與圖表查詢。
- `ledger.transaction()` 已統一使用 `BEGIN IMMEDIATE`、成功 commit、例外 rollback 與連線關閉。
- `dashboard.py`、`spending_commands.py`、`form_ui.py`、`selection_ui.py`、`lifestyle_ui.py` 仍直接呼叫 `spending.py`，其中 Discord 看板與文字明細尚有少數原始 SQL。
- Services 不建立資料表、不執行 schema 遷移、不複製交易或驗證規則。`db.init_db()` 與 `spending.init_schema()` 維持既有責任。

## 架構

```text
Discord 表單／文字指令／看板
             |
             v
life_ledger_service.py
             |
             v
spending.py 既有領域與 SQLite 規則
             |
             v
ledger.transaction() / db.get_conn()
```

`life_ledger_service.py` 是函式式 façade，不建立 Service 類別、Repository 介面、依賴注入容器或通用框架。所有 Services 函式都把 `user_id` 正規化為字串，並把同一個 `user_id` 傳入所有底層讀寫。

## 公開 Services 介面

```python
def add_expense(
    user_id,
    amount,
    category,
    note,
    spent_on=None,
    payment_source_id=None,
) -> int: ...

def get_expense(user_id, expense_id) -> dict: ...

def list_expenses(
    user_id,
    month,
    *,
    include_voided=False,
    limit=None,
    offset=0,
) -> dict: ...

def search_expenses(user_id, keyword="", start=None, end=None) -> list[dict]: ...

def update_expense(
    user_id,
    expense_id,
    amount,
    category,
    note,
    spent_on,
    payment_source_id=None,
    expected_revision=None,
) -> None: ...

def void_expense(user_id, expense_id, expected_revision=None) -> None: ...

def preview_undo(user_id) -> dict: ...

def undo_latest_action(user_id, expected_action_id=None) -> dict: ...

def get_calendar_days(user_id, month) -> list[dict]: ...

def get_month_summary(user_id, month=None) -> dict: ...

def get_chart_data(user_id, month=None) -> dict: ...

def get_categories(user_id, include_inactive=False) -> list[str]: ...

def get_payment_sources(user_id, include_inactive=False) -> list[dict]: ...

def get_recent_expenses(user_id) -> list[dict]: ...
```

`list_expenses()` 回傳 `{"items": [...], "total": n}`。預設只回傳有效 `consumption`；`include_voided=True` 僅供既有 Discord 看板「清單」與 `!支出明細` 保留已撤銷歷史。`limit=None` 回傳符合條件的全部項目；`limit` 與 `offset` 只接受非負整數，避免 UI 自行組 SQL。

`preview_undo()` 回傳 `{"action_id": ..., "expense_id": ...}`。`undo_latest_action()` 保留只能撤銷本人最新可撤銷操作的規則；傳入 `expected_action_id` 時必須與目前最新操作相同。兩者不接受任意消費編號來繞過操作順序。

## 單筆使用者隔離

下列介面必須同時接收 `user_id` 與 `expense_id`：

- `get_expense()`
- `update_expense()`
- `void_expense()`

底層查詢與更新都必須包含 `WHERE user_id=? AND id=?`，並限制為有效生活消費。不得先只依 `id` 讀取後再於 Python 比對擁有者，也不得只依 `id` 更新。

列表、搜尋、月曆、摘要、圖表、分類與付款方式查詢同樣必須在 SQL 或既有底層函式中使用傳入的 `user_id`。跨使用者請求回傳空集合，或沿用既有 `ValueError` 拒絕單筆操作。

## 軟刪除與撤銷

`spending.py` 新增最小 `void_expense(user_id, expense_id, expected_revision=None)` 核心操作；Services 同名函式只負責正規化輸入與委派。

核心操作在一個既有 `transaction()` 中：

1. 以 `user_id + expense_id` 讀取本人、未撤銷、`kind='consumption'` 的帳目。
2. 找不到時沿用「找不到自己的有效消費」類型錯誤。
3. 有 `expected_revision` 時套用現有 revision 衝突規則。
4. 依現有 `expense_actions.before_json` 格式保存修改前完整快照。
5. 以 `user_id + expense_id` 將 `voided` 設為 `1`，並將 revision 加一。
6. 任何讀寫失敗由 `transaction()` rollback；不得執行 `DELETE FROM expenses`。

既有 `spending.undo()` 保留為唯一撤銷實作。`preview_undo()` 與 `undo_latest_action()` 只轉換其回傳值為具名 `dict`，不建立第二套撤銷規則。直接 void 產生的操作可由既有最新操作撤銷流程還原。

## 列表與讀取語意

`spending.py` 新增最小、使用者範圍的列表 helper，集中既有 Discord 清單與 `!支出明細` 的查詢條件、排序、計數及分頁。Services 委派此 helper；Discord 不再自行組生活消費清單 SQL。

- 一般列表：只包含 `voided=0`、`kind='consumption'`，依日期與編號由新到舊。
- 搜尋：保留只搜尋本人有效手動消費的現況。
- Discord 看板「清單」與 `!支出明細`：明確使用 `include_voided=True`，維持目前顯示已撤銷歷史的結果。
- 月曆、摘要與圖表：沿用既有 `voided=0` 條件，不顯示或統計已撤銷帳目。
- 單筆讀取：只回傳本人有效生活消費。

## Discord 整合

Discord 顯示、文案、按鈕、Modal、Embed、頁數與錯誤提示維持不變。

- `dashboard.py`：新增、單筆讀取、有效列表、搜尋、修改、月曆、月摘要與圖表改呼叫 Services。
- `spending_commands.py`：`!支出`、`!支出補登`、`!支出修改`、`!記帳撤銷`、`!月報`、`!支出明細` 與分類選項讀取改呼叫 Services。
- `form_ui.py`：分類與付款方式選項讀取改呼叫 Services；管理寫入仍留在原模組，因分類／付款方式管理不在本輪抽取範圍。
- `selection_ui.py`：分類與付款方式清單讀取改呼叫 Services；月份選單中的預算與其他範圍外查詢維持現況。
- `lifestyle_ui.py`：確認新增、最近消費與單筆重新讀取改呼叫 Services；捷徑管理與推薦維持現況。

預算、固定負擔、提醒、捷徑管理、AI、投資、備份／復原、匯出／匯入及生活資料整體清除仍直接沿用現況，不納入新 Services 公開介面。

## 錯誤處理

- 金額、日期、用途、分類、付款方式與 revision 錯誤沿用 `spending.py` 的既有 `ValueError` 與文案。
- Services 不把資料庫例外改寫為可能洩漏內容的文字，也不捕捉後假裝成功。
- SQLite 寫入失敗先由既有 transaction rollback，再交給目前 Discord 安全錯誤處理。
- Services 不接受 Discord context、interaction、message、Modal、View 或 Embed。

## 測試設計

新增 `tests/test_life_ledger_service.py`，每個測試使用暫存 SQLite 資料庫並呼叫 `db.init_db()`，不得操作正式 `data.db`。

直接 Services 測試至少包含：

1. 新增後可由本人單筆讀取、列表與搜尋取得，回傳資料不含 Discord UI 物件。
2. 他人無法用相同 `expense_id` 單筆讀取、修改或 void，且資料完全不變。
3. 本人修改保留分類、付款方式與 revision 衝突防護。
4. `void_expense()` 將本人帳目設為 `voided=1`，不實體刪除；一般列表、搜尋、月曆、摘要與圖表不再包含該筆。
5. `preview_undo()` 只回傳本人最新操作；`undo_latest_action()` 可還原直接 void，錯誤 action id 不改資料。
6. 對操作紀錄寫入、帳目 void 更新與撤銷完成標記分別注入 SQLite 失敗，驗證整個交易 rollback。
7. 月曆、月摘要與圖表只統計本人資料，排除他人與已撤銷帳目。
8. 分類與付款方式選項只回傳指定使用者資料。
9. 列表分頁的 `total`、`limit`、`offset`、排序與 `include_voided` 符合既有 Discord 顯示規則。

另以現有 Discord 測試確認 UI 改走 Services 後仍保留既有結果，並執行：

```powershell
python -m ruff check .
python tests/run_discord_validation.py
```

不把隔離測試描述成真實 Discord、Web、Ollama、正式資料庫或 GitHub 雲端驗證。

## 文件與版本

- README 新增開發者說明：Discord UI 只處理互動，生活記帳 Services 負責可重用資料規則；目前沒有 API 或網站。
- `CHANGELOG.md` 以繁體中文記錄服務介面、受影響 Discord 入口、無資料庫遷移、必要啟動步驟、實際驗證與未驗證項目。
- 這是跨多個 Discord 入口的共用流程調整，版本由 `0.10.2` 升為 `0.11.0`，README 目前版本同步更新。

## 明確不處理

- FastAPI、HTTP 路由、OpenAPI、登入、Discord OAuth、權限 middleware。
- React、Expo、其他 Web／App UI。
- SQLModel、Alembic、新 ORM、Repository 框架或新資料庫。
- 投資、基金、AI、備份／復原、匯出／匯入、預算 CRUD、固定負擔、提醒、多幣別。
- Discord 新刪除按鈕或新刪除指令。
- 正式 `data.db`、`token.txt`、真實備份、匯出檔或使用者資料。
