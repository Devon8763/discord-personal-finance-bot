# localhost Web v1：設定頁設計規格

> **狀態：已確認設計、尚未實作。**
>
> 本文件只定義登入者專屬的 localhost Web 設定頁 v1。此文件不代表功能已完成；本輪只新增設計規格與實作計畫，不修改 Python、HTML、CSS、測試、資料庫、依賴、README、CHANGELOG、版本、`.env` 或 Git 歷史。

## 1. 目標與使用者入口

登入後首頁新增普通連結「設定」，導向 `GET /settings`。設定頁採伺服器產生 HTML 的單欄直式排列，依序顯示：

1. 本月預算
2. 分類管理
3. 付款方式管理

頁面只服務目前 Discord OAuth session 的登入者，且網站仍只允許從 `127.0.0.1` 啟動。未登入者進入 `/settings` 時回應既有固定安全登入錯誤，不查詢帳務 Service，也不顯示任何設定名稱、金額或 ID。

本版不做可自訂排列、分頁、modal、JavaScript 元件、AJAX、JSON API 或前端框架。未來若要改成卡片或雙欄，只調整 Jinja 模板與 CSS，不為版面預先建立元件系統。

## 2. 既有行為與最小缺口

已核對現有實作：

- `spending.today()` 使用 UTC+8，應繼續作為帳務「今天」與本月的唯一核心日期來源。
- `spending.set_budget()` 目前會在交易內寫入，但要求先有「總額」，且沒有清除預算或把總預算設為分類合計的操作。
- `spending.set_category()` 已支援新增、停用及同名停用分類的重新啟用；內建分類來自 `CATEGORIES`，使用者狀態由 `spending_categories` 覆寫。
- 分類目前沒有改名操作。帳目、預算、固定／訂閱／分期規則、捷徑和 `expense_actions.before_json` 各自保存分類名稱。
- 付款方式已有新增、改名、停用、使用者隔離與重名檢查；「現金」「未指定」不可改名或停用。`expenses.payment_source_name` 是入帳當時的歷史名稱快照，現有改名不會回寫它。
- `life_ledger_service.py` 已提供本月摘要、分類、付款方式和 `set_budget()` 的薄 façade，但尚未提供設定頁所需的預算清除／合計、分類管理與付款方式管理 façade。
- 現有 Web POST 使用 URL-encoded body、逐欄唯一值檢查、既有 CSRF helper、固定安全錯誤與 `303` POST/Redirect/GET；設定頁沿用同一模式。

本功能不新增資料表、欄位、索引或 migration。需要補的是既有資料模型上的最小核心操作、薄 Service façade 與 Web 頁面。

## 3. 身分、資料流與公開 Service 介面

所有設定讀寫固定使用下列資料流：

```text
Web route
  → auth.current_user_id(request.session)
  → life_ledger_service.py
  → spending.py
  → SQLite
```

Web route 不得 import 或呼叫 `spending.py`、`db.py`、`ledger.py`，也不得寫 SQL。query 或 form 即使出現 `user_id` 也一律忽略，不能改變資料擁有者。

設定頁重用既有 Service：

```python
get_today()
get_month_summary(user_id, month)
get_categories(user_id, include_inactive=False)
get_payment_sources(user_id, include_inactive=False)
set_budget(user_id, month, category, amount)
```

只新增下列必要 façade；每個使用者相關函式都先用既有 `_user()` 正規化 ID，再原樣轉交核心，不含 SQL、交易、Discord 或 Web 物件：

```python
clear_budget(user_id, month, category)
set_total_budget_to_category_sum(user_id, month)
add_category(user_id, name)
rename_category(user_id, old_name, new_name)
disable_category(user_id, name)
add_payment_source(user_id, name)
rename_payment_source(user_id, payment_source_id, name)
disable_payment_source(user_id, payment_source_id)
```

`add_category()` 與 `disable_category()` 轉交既有分類啟用／停用規則；付款方式 façade 轉交既有付款方式核心函式。新的交易與資料一致性規則只存在 `spending.py`。

## 4. 本月定義與頁面讀取

設定頁只管理台灣今天所在月份，不提供月份 query、歷史月份或未來月份切換。

```python
month = life_service.get_today().strftime("%Y-%m")
```

頁面標題明確顯示 `YYYY 年 MM 月`。所有預算 POST 都由 route 重新取得同一個目前月份；不接受 form 或 query 提供 `month`。因此，使用者不能藉由修改網址或表單寫入其他月份。

GET `/settings` 讀取：

- `get_month_summary(user_id, month)`：目前月份已設定的總預算與分類預算。
- `get_categories(user_id)`：本人目前啟用分類。
- `get_categories(user_id, include_inactive=True)`：本人可管理的全部分類名稱。
- `get_payment_sources(user_id, include_inactive=True)`：本人全部付款方式及啟用狀態。

頁面只組合乾淨 Python 資料。Jinja 不取得 Discord ID、資料庫路徑、帳目 ID、revision、Token 或例外物件。

## 5. 本月預算

### 5.1 顯示

本月預算區分開顯示：

- 「總預算」：目前值、設定／修改表單、存在時的清除操作。
- 「分類預算」：每個啟用分類的目前值與設定／修改表單；已有本月預算但目前停用的分類仍顯示「已停用」與既有金額，且只提供清除，不提供修改。
- 「設為分類預算加總」：獨立的 URL-encoded POST form，只有至少一筆分類預算時才顯示可用按鈕；不用 JavaScript 計算或提交金額。

沒有任何預算時顯示固定空狀態「本月尚未設定預算。」。沒有總預算但已有分類預算時，明確顯示「尚未設定總預算」，分類預算仍正常存在與管理。

### 5.2 設定與修改規則

- 總預算與分類預算只接受正整數台幣，且不得超過既有限額；不接受小數，即使小數部分為零。消費金額仍沿用既有 `money()` 規則。零不是清除；清除必須使用明確的清除 POST。
- 分類預算可以在沒有總預算時單獨設定。
- 若總預算存在，設定或修改任一分類預算後，所有分類預算（包含目前停用分類仍保留的預算）合計不得大於總預算。
- 設定或修改總預算時，新總額不得小於所有分類預算合計。
- 分類預算合計可以小於總預算，不要求相等。
- 只允許為目前啟用的本人分類新增或修改分類預算；停用分類的既有預算只可保留或清除。

### 5.3 清除規則

- 清除總預算只刪除本月 `category='總額'` 的本人預算列，保留所有分類預算。
- 清除分類預算只刪除該本人、本月、該分類的預算列，不影響總預算、帳目、分類、其他月份或其他使用者。
- 清除不存在的預算以可預期的 `ValueError` 拒絕，不回報虛假的成功。
- 清除時移除該本人、月份與預算項目尚未送出的舊預算通知，避免清除後仍送出已失效提醒；已送出的通知歷史不重寫。這不新增提醒設定功能。

### 5.4 設為分類預算加總

按下按鈕後，核心在同一交易中重新讀取本人本月所有非「總額」預算，將其整數 cents 合計寫為總預算：

- 合計包含目前停用分類仍保留的預算。
- 沒有任何分類預算時，核心拒絕操作且不寫入；畫面正常情況下不顯示可用按鈕，但核心仍必須防守直接 POST。
- 不接受瀏覽器提供的合計值；route 只送出使用者 ID 與核心算出的目前月份。

### 5.5 交易要求

`set_budget()`、`clear_budget()` 與 `set_total_budget_to_category_sum()` 的狀態讀取、本人／分類驗證、總額下限驗證、寫入和相應未送出通知處理，必須全部位於同一個既有 `BEGIN IMMEDIATE` 交易內。不得先在 route、Service 或另一條資料庫連線做狀態檢查再寫入。

為此可在 `spending.py` 增加一個私有、connection-aware 的分類查詢／驗證 helper；它不是新架構層。任一步驟或 SQLite 寫入失敗時，預算與通知變更全部 rollback。

## 6. 分類管理

### 6.1 顯示與基本操作

分類區列出本人全部內建及自訂分類，標示「啟用」或「已停用」：

- 新增：名稱沿用既有 1～20 字、不可換行、不可使用「總額」規則。
- 新增已啟用的同名分類是冪等操作，不建立重複列；新增已停用的同名分類會沿用既有行為重新啟用。
- 改名：只對目前啟用的本人分類提供；舊名與新名相同時拒絕。
- 停用：只對目前啟用的本人分類提供；停用不是刪除。

改名目標若已是任何內建分類、本人啟用／停用分類，或已存在於本人相關歷史資料中，必須拒絕，不合併兩個分類。`總額` 始終是預算保留名稱，不能新增或作為改名目標。

### 6.2 內建分類與使用者覆寫

內建分類常數不做全域改名。使用者改名內建分類時，核心只為該使用者：

1. 將舊內建名稱寫成停用 override，避免它再次從內建清單自動出現為啟用。
2. 建立新名稱的啟用 override。
3. 原子更新該使用者所有相關資料。

其他使用者仍看到原內建名稱，不受影響。改名後舊內建名稱會在設定頁的停用清單中保留，使用者日後以「新增／重新啟用」同名方式可恢復；新名稱是目前有效分類。

自訂分類改名則把該使用者的分類定義由舊名換成新名，不另外留下同名停用副本。停用自訂分類仍保留原定義，可用同名新增重新啟用。

### 6.3 改名的完整資料範圍

`rename_category(user_id, old_name, new_name)` 必須在同一個既有立即交易內，且所有 SQL 都帶 `user_id`，原子處理：

- `expenses.category`：該使用者全部生活帳目，包含有效及 `voided=1` 的歷史帳目，也包含手動與已入帳固定／訂閱／分期。
- `budgets.category`：該使用者所有月份的分類預算。
- `recurring_expenses.category`：該使用者所有啟用／停用的固定、訂閱與分期規則。
- `spending_shortcuts.category`：該使用者所有啟用／停用捷徑。
- `expense_actions.before_json`：該使用者全部非 `null` 快照中 `category == old_name` 的欄位。
- 尚未送出的舊分類預算通知：移除，避免之後顯示舊分類；已送出歷史不改寫。
- `spending_categories`：依上一節的內建／自訂規則更新本人 override。

`before_json` 必須以標準函式庫 JSON 解析及重新序列化，只修改 `category`，保留日期、金額、用途、付款方式快照、撤銷狀態等其他欄位。若任何應處理快照無法安全解析、任何唯一性檢查失敗或任一步寫入失敗，整筆改名 rollback，不留下部分新舊名稱。

完成改名後，「撤銷最近操作」若還原先前帳目快照，只能還原成新分類，不得重新帶回舊分類。這項相容性必須用真實 undo 流程驗證，不能只檢查 JSON 字串。

### 6.4 停用但仍有資料的規則

停用分類不刪除或改寫帳目、各月份預算、固定規則、捷徑或 undo 快照：

- 歷史帳目與報表仍顯示原分類。
- 本月既有分類預算仍參與總預算下限與「設為分類預算加總」。
- 設定頁的預算區將它標為「已停用」，保留金額並只允許清除；分類區仍列出它並允許透過同名新增重新啟用。
- 首頁快速記帳繼續只使用 `get_categories(user_id)` 的啟用清單，因此停用後立即不再出現在下拉選單。
- 既有固定規則與捷徑資料不被刪除或改寫；其後續使用仍沿用現有核心驗證，本設定頁不提供管理介面。

## 7. 付款方式管理

付款方式區使用 `get_payment_sources(user_id, include_inactive=True)` 列出本人付款方式與狀態。

- 新增、改名、停用完全沿用既有名稱驗證、本人隔離與同名限制。
- 同名付款方式即使已停用也不能再次新增或改名為該名稱；本版不新增重新啟用功能。
- 「現金」與「未指定」顯示為保留付款方式，不顯示改名或停用操作；直接 POST 仍由核心拒絕。
- 停用不是刪除。既有帳目、捷徑關聯與歷史資料保留；首頁快速記帳只列啟用付款方式。
- 付款方式改名只更新 `payment_sources.name`。既有 `expenses.payment_source_name` 是入帳時名稱快照，必須維持原值；本輪不將分類改名的回寫規則套用到付款方式。

若付款方式資料意外為空，顯示固定空狀態；正常讀取仍沿用既有核心確保「未指定」「現金」存在的行為，不在 Web 重做。

## 8. Web 路由與無 JavaScript 表單

### 8.1 GET 與入口

- `GET /settings`：登入保護，顯示本人本月預算、全部分類與全部付款方式。
- 登入後首頁新增 `<a href="/settings">設定</a>`；未登入首頁不顯示此入口。
- 預算金額以原生數字欄位提示整數、步進 1 並設定最小值 1；伺服器端共用預算核心驗證才是唯一依據，頁面顯示不保留 `.0` 或 `.00`。
- 分類管理的分類改名按鈕使用者可見文字為「更改」；分類同步及 undo 規則不變。

### 8.2 POST 路由

為保持表單小而明確，使用下列獨立 route：

| Route | 唯一必填欄位（另含 `csrf_token`） | 核心動作 |
| --- | --- | --- |
| `POST /settings/budgets/set` | `category`, `amount` | 設定／修改總預算或啟用分類預算 |
| `POST /settings/budgets/clear` | `category` | 清除本人本月指定預算 |
| `POST /settings/budgets/use-category-sum` | 無 | 將總預算設為分類預算合計 |
| `POST /settings/categories/add` | `name` | 新增或重新啟用分類 |
| `POST /settings/categories/rename` | `old_name`, `new_name` | 原子改名本人啟用分類 |
| `POST /settings/categories/disable` | `name` | 停用本人啟用分類 |
| `POST /settings/payment-sources/add` | `name` | 新增付款方式 |
| `POST /settings/payment-sources/rename` | `payment_source_id`, `name` | 改名本人非保留付款方式 |
| `POST /settings/payment-sources/disable` | `payment_source_id` | 停用本人非保留付款方式 |

所有 POST：

- 只接受 `application/x-www-form-urlencoded`，用既有 `request.body()` 與 `urllib.parse.parse_qs()` 方式解析。
- `csrf_token` 與該 route 的必要欄位都必須各自恰好一個值；CSRF 缺少、錯誤或重複回 HTTP 403，session 保留，不呼叫 Service。
- 缺少、重複、格式錯誤、核心 `TypeError`／`ValueError` 回 HTTP 400，以固定安全訊息重繪設定頁。
- 只使用 session `user_id`；未知欄位、query 及 form 的 `user_id` 不傳給 Service。
- 成功一律 `303` 到 `/settings?saved=1`；GET 只把 `saved=1` 映射為固定「設定已更新」訊息，重新整理不重複寫入。
- 不接受任意 `next` 或 redirect URL。

## 9. 成功、空白與錯誤狀態

- 成功：固定綠色提示「設定已更新」，不顯示金額、名稱、Discord ID 或資料庫資訊。
- 預算空白：顯示「本月尚未設定預算。」；表單仍可使用。
- 分類無啟用項目：保留停用清單與新增／重新啟用表單，顯示固定說明；不自動啟用內建分類。
- 付款方式空白：顯示固定「目前沒有可管理的付款方式。」；不在模板建立預設資料。
- 輸入錯誤：各區使用固定訊息，例如「無法更新設定，請檢查後重試。」；不顯示原始例外。失敗表單的使用者文字可在同區保留，但必須由 Jinja 自動跳脫。
- 讀取錯誤：HTTP 400 固定安全錯誤頁，不部分呈現其他設定資料。

錯誤頁與表單不得包含 SQLite、Traceback、`.db`、絕對路徑、Token、OAuth secret、Discord ID、其他使用者資料或原始 exception 文字。

## 10. 安全與一致性

- **登入保護：** GET 與所有 POST 先取得 session 使用者；未登入時不呼叫任何設定 Service。
- **CSRF：** 每個 POST 都使用既有 `auth.validate_csrf()`，不可因操作簡單而省略。
- **跨使用者隔離：** 分類名稱、預算複合鍵與付款方式 ID 的核心讀寫都同時限制傳入的正規化 `user_id`；他人付款方式 ID 或他人專屬分類名稱不能被讀取、改名、停用或套用。
- **XSS：** 所有名稱、draft 與訊息使用 Jinja 預設 autoescape；不得使用 `|safe`、字串拼接 HTML 或把例外內容當文案。
- **交易／rollback：** 預算寫入和分類改名使用既有 `BEGIN IMMEDIATE`。驗證依賴的資料必須在取得寫入鎖後重新讀取；任何失敗全部 rollback。
- **輸入界線：** 金額、分類名稱與付款名稱沿用核心驗證；route 只做表單結構與整數 ID 解析，不複製業務規則。

## 11. 明確不在本版範圍

- 固定支出／訂閱／分期管理、捷徑管理、提醒設定、帳目編輯／撤銷／刪除、匯出／備份、AI、投資。
- 歷史／未來月份預算切換、預算比例分配、負數／零預算、自動平分、預算圖表或首頁預算卡。
- 付款方式重新啟用、付款方式歷史快照改寫、分類實體刪除。
- 搜尋擴充、JSON API、React、App、JavaScript 元件、ORM、Repository、DI、migration 或新依賴。
- 可自訂版面、拖曳排序、多欄配置偏好或其他推測性設定。

## 12. 驗收條件

- 登入者能在單一設定頁完成本月總預算、分類預算、分類及付款方式的指定操作；未登入者完全看不到資料。
- 無總預算時可設定分類預算；有總預算時任何操作都不能使其低於分類預算合計。
- 清除總預算保留分類預算；清除分類預算不影響其他項目；分類合計按鈕由核心交易内重新計算。
- 分類改名對本人所有指定資料原子一致，包含 voided 帳目和 undo JSON；改名後 undo 不會恢復舊分類。
- 停用分類／付款方式不會刪除歷史，且不再出現在快速記帳選項。
- 付款方式保留名稱與歷史帳目名稱快照維持既有行為。
- 所有寫入都有 CSRF、PRG、固定安全錯誤、跨使用者隔離與隔離 SQLite 測試。
- 沒有資料庫結構、依賴、前端框架或非本頁產品範圍的變更。
