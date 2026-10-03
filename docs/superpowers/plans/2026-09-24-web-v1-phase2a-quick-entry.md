# DiscordBOT Web v1 Phase 2A Quick Entry Implementation Plan

> **狀態：計畫、尚未實作。**
>
> 本文件只規劃登入後首頁的生活消費快速記帳；不代表任何程式、資料庫、測試或網站功能已完成。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將登入後首頁 placeholder 改為可安全新增一筆本人 `consumption` 消費的快速記帳表單。

**Architecture:** `web/routes.py` 只由已驗證 session 取得 Discord `user_id`，並只呼叫既有 `life_ledger_service.py`。金額、用途、日期、分類、付款方式、交易與所有權規則繼續由 `spending.py` 處理；Web 不寫 SQL、不重複規則。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、Starlette SessionMiddleware、Authlib、unittest、FastAPI TestClient、既有 SQLite 隔離測試。

**Spec:** `docs/superpowers/specs/2026-09-23-localhost-discord-web-v1-design.md` 與 2026-09-24 已確認的 Phase 2A 表單規則。本階段「其他／未指定」預設優先於設計文件較早的「未分類／未選付款方式」草案。

## Global Constraints

- 只新增首頁快速記帳；不做搜尋、月曆、預算、設定、分類或付款方式管理、固定支出、投資、AI、匯出、備份、JSON API、React 或 App。
- 呼叫方向固定為 `web route → session Discord user_id → life_ledger_service.py → spending.py → SQLite`。Web 不得 import 或呼叫 `spending.py`、`db.py`、`ledger.py`，不得自行寫 SQL。
- 只使用既有 `get_categories`、`get_payment_sources`、`get_recent_expenses`、`add_expense`；不新增 Services、資料表、欄位、索引、依賴或升級機制。
- `user_id` 一律來自 `auth.current_user_id(request.session)`；忽略全部 query parameter 與 form 中的 `user_id`。
- 既有 `auth.validate_csrf` 保護 POST。只接受 `application/x-www-form-urlencoded` body 的單一欄位值；缺少、錯誤或重複 CSRF 不寫入。
- 日期使用原生 `input type="date"`，由標準函式庫的台灣時區日期預設；Web 不得 import `spending.today()`。
- 成功使用 303 PRG；失敗以固定安全訊息重繪表單、保留金額、用途、分類、付款方式、日期，不顯示 SQLite、例外、檔案路徑、Discord ID 或其他使用者資料。
- Web 測試沿用 `tests/test_life_ledger_service.py` 的 `TemporaryDirectory` + `patch.object(db, "DB_NAME", ...)` 方法。`tests/form_helpers.py` 僅服務 Discord UI 欄位，本階段不修改或誤用它。
- 本輪只新增本計畫，不修改 README、CHANGELOG、版本、依賴或 Git 歷史，也不提交或推送。

## Review Focus

- 未登入首頁不可顯示或讀取任何表單、分類、付款方式或帳務資料。
- 最近付款來源僅在它仍為本人啟用來源時預選；停用、不存在或他人來源一律回退「未指定」。
- query/form 的 `user_id` 與他人付款方式 ID 都不得造成跨使用者讀寫。
- CSRF 缺少、錯誤或重複時不得呼叫 `add_expense`，且登入 session 保持有效。
- 失敗 draft 的無效 select 值只能以已跳脫的「原選擇無法使用」disabled option 顯示，不能自動新增／重啟分類或付款方式。

## 預計修改檔案

| 檔案 | 責任 |
| --- | --- |
| `web/routes.py` | 首頁 context、台灣日期、CSRF 驗證、POST 新增與 PRG。 |
| `web/templates/home_placeholder.html` | 以快速記帳表單取代 placeholder，保留登出。 |
| `web/static/web.css` | 表單、錯誤與手機可讀性的最小樣式。 |
| `tests/test_web_auth.py` | OAuth 登入替身上的隔離快速記帳與 import 邊界測試。 |

不修改：`life_ledger_service.py`、`spending.py`、`db.py`、`ledger.py`、`web/app.py`、`web/auth.py`、資料庫結構、依賴、README、CHANGELOG。

---

### Task 1: 建立隔離快速記帳測試基線與首頁 GET 測試

**Files:**
- Modify: `tests/test_web_auth.py`
- Verify: `tests/test_life_ledger_service.py`
- Verify: `tests/form_helpers.py`

**Interfaces:**
- Consumes: `WebOAuthTests._complete_mocked_login()`、`db.init_db()` 與既有隔離 DB patch pattern。
- Produces: `WebQuickEntryTests`，以登入 TestClient 和暫存 SQLite 驗證首頁，不連線真實 Discord 或正式資料庫。

- [ ] **Step 1: 加入隔離測試類別與登入 helper**

在 `WebQuickEntryTests(WebOAuthTests).setUp()` 建立 `TemporaryDirectory`，patch `db.DB_NAME` 到 `web-entry.db`，patch `spending.today` 為 `date(2026, 9, 21)`，呼叫 `db.init_db()` 後再呼叫 `super().setUp()`。`tearDown()` 反向停止 patch 並清理暫存目錄。新增 `_login_as(user_id="42")`，只重用既有 `_complete_mocked_login()` 並斷言 303；不手造 Cookie 或 session。

- [ ] **Step 2: 先寫 GET 的失敗測試**

補強未登入 `GET /?user_id=43`：不得出現 `快速記帳`、`金額`、`用途`、分類或付款方式資料。

新增登入後測試：本人建立 `本人卡` 與自訂分類 `寵物`，他人建立 `他人卡`；登入本人後 `GET /?user_id=43` 必須顯示金額、用途、`input type="date"`、本人分類與付款方式，預選 `其他`，且不得顯示他人名稱或來源 ID。patch `web.routes.taiwan_today`，斷言日期預設 `2026-09-21`。

- [ ] **Step 3: 執行 focused test，確認 RED**

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
```

Expected: 快速記帳首頁斷言失敗，因目前只呈現 placeholder。

- [ ] **Step 4: 保留既有隔離資料庫策略**

測試資料可用 `sp.add_payment_source` 準備本人／他人來源；這僅在測試 setup 使用既有核心規則。Web route 測試仍只經 Services，並保留 `tests/run_discord_validation.py` 對正式 `data.db` 的拒絕保護。

---

### Task 2: 用既有 Services 呈現快速表單與預設值

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/home_placeholder.html`
- Modify: `web/static/web.css`
- Modify: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: `auth.current_user_id(session)`、`get_categories(user_id)`、`get_payment_sources(user_id)`、`get_recent_expenses(user_id)`。
- Produces: 登入後 `GET /` 的 `{csrf_token, draft, categories, payment_sources, selected_category, selected_payment_source_id, error}` context。

- [ ] **Step 1: 先寫付款來源預選的失敗測試**

新增測試：本人最近手動消費使用 `常用卡` 時，`GET /` 預選該來源；若該來源被 `sp.disable_payment_source` 停用，首頁只可預選本人啟用來源清單中的 `未指定`。以 patch `web.routes.life_service.get_recent_expenses` 回傳不存在或他人 `payment_source_id`，確認 route 仍只從本人啟用清單選擇 `未指定`。

- [ ] **Step 2: 實作最小 context helper**

在 `web/routes.py` 使用標準函式庫建立：

```python
TAIPEI = timezone(timedelta(hours=8))

def taiwan_today() -> date:
    return datetime.now(TAIPEI).date()
```

以 `import life_ledger_service as life_service` 建立私有 `_quick_entry_context(user_id, csrf_token, draft=None, error=None)`：取得本人啟用分類、啟用付款方式、最近有效手動消費。新表單預選 `其他`；若使用者已依既有 Bot 規則停用它，絕不重啟或新增，改選第一個既有啟用分類。最近列的 `payment_source_id` 只有存在於本人啟用清單才採用，否則選本人名為 `未指定` 的來源。新表單日期為 `taiwan_today().isoformat()`；失敗 `draft` 覆蓋五個欄位。讀取失敗只回固定安全訊息，不渲染例外。

- [ ] **Step 3: 取代 placeholder 為表單**

保留登入後標題與 POST `/logout`。新增 POST `/expenses` 表單：hidden `csrf_token`、required `amount`、required `note`、required `category` select、required `payment_source_id` select、required `spent_on` date、`儲存消費` button。option value 只能是既有分類字串或來源 ID。失敗 draft 的無效 select 以已跳脫的 `disabled selected`「原選擇無法使用」option 顯示。CSS 只增加欄位間距、錯誤文字、全寬輸入及窄螢幕單欄；不加 JS、卡片、圖表或其他首頁功能。

- [ ] **Step 4: 執行 focused test，確認 GREEN**

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
```

Expected: 表單、`其他`、台灣日期、本人付款方式與「未指定」回退測試通過；未登入首頁仍通過。

---

### Task 3: CSRF 保護的新增消費、錯誤回填與 PRG

**Files:**
- Modify: `web/routes.py`
- Modify: `web/templates/home_placeholder.html`
- Modify: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: `_quick_entry_context(...)`、`auth.validate_csrf(...)`、`life_service.add_expense(user_id, amount, category, note, spent_on, payment_source_id)`。
- Produces: `POST /expenses`；成功 303 到 `/`，失敗以 400 重繪表單且不寫入。

- [ ] **Step 1: 先寫成功、所有權與 PRG 的失敗測試**

從登入首頁擷取 CSRF。以本人來源送出正確表單，並在 URL、form 中混入 `user_id=43`：斷言 303 `/`、`service.search_expenses("42", "午餐")` 恰一筆、使用者 43 為空。重新整理成功後首頁仍恰一筆，不能建立第二筆。再以本人 CSRF 搭配他人付款方式 ID，斷言 400、兩者帳目均未新增，HTML 不含他人付款方式名稱或 ID。

- [ ] **Step 2: 先寫 CSRF 與欄位失敗測試**

對有效 draft 分別測試缺少、錯誤、重複 CSRF token：每個都回 403、帳目不變、session 仍可讀首頁。使用正確 CSRF 對 `amount="0"`、空白 `note`、未來或不存在日期、無效分類、不存在或他人付款來源做 subtest；每次都回 400、不含 `sqlite`、`traceback`、`exception`、`.db` 或絕對路徑、本人與他人帳目不變，並保留五個已輸入欄位值。

- [ ] **Step 3: 執行 focused test，確認 RED**

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
```

Expected: `POST /expenses` 尚未存在，或未通過 CSRF、PRG、所有權與回填斷言。

- [ ] **Step 4: 實作嚴格 POST route**

沿用 logout 的標準函式庫模式，以 `request.body()` 與 `parse_qs(..., keep_blank_values=True, max_num_fields=10)` 解析 form body。要求 `csrf_token`、`amount`、`note`、`category`、`payment_source_id`、`spent_on` 各恰一值；不讀 query parameter。順序為：確認 session 使用者、確認 content type、驗證唯一 CSRF、將付款來源轉成整數、以 session user ID 呼叫 `life_service.add_expense`。`ValueError` 或輸入解析失敗回固定「無法儲存這筆消費，請確認欄位後重試。」與原 draft 的 400；成功只回 `RedirectResponse(url="/", status_code=303)`，不保存帳目或身分資料到 session。

- [ ] **Step 5: 執行 focused test，確認 GREEN**

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
```

Expected: 快速新增、CSRF、跨使用者拒絕、失敗回填與 PRG 測試全部通過。

---

### Task 4: Web import 邊界與完整隔離回歸

**Files:**
- Modify: `tests/test_web_auth.py`
- Verify: `web/app.py`
- Verify: `web/auth.py`
- Verify: `web/routes.py`
- Verify: `tests/run_discord_validation.py`

**Interfaces:**
- Consumes: 完成的快速記帳 route、既有 Phase 1 OAuth 測試與完整離線驗證入口。
- Produces: Web import 邊界與至少既有 202 項隔離回歸的可重現證據。

- [ ] **Step 1: 加入 import 邊界測試**

用 `ast.parse` 讀取 `web/app.py`、`web/auth.py`、`web/routes.py`，收集 `Import`、`ImportFrom` 的頂層模組名。三者都不得 import `spending`、`db` 或 `ledger`；只允許 `web/routes.py` import `life_ledger_service`。此測試不執行 SQLite，也不取代帳務隔離測試。

- [ ] **Step 2: 執行 focused test，確認 RED 後 GREEN**

先新增邊界測試並執行：

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
```

Expected before Task 2: route 尚未 import Service 或快速表單測試失敗；Task 2、3 完成後同一命令 GREEN。任何 Phase 1 OAuth 測試失敗都只修正快速記帳變更，不改身份或 session 規則。

- [ ] **Step 3: 執行完整驗證**

```powershell
& '.venv\Scripts\python.exe' -m ruff check .
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
& '.venv\Scripts\python.exe' tests\run_discord_validation.py
git diff --check
git status --short
```

Expected: Ruff、Web tests、完整隔離驗證皆成功；回歸至少保留既有 202 項，實際數字若改變必須如實記錄；diff 無空白或衝突標記。

- [ ] **Step 4: 完成前安全檢查**

確認測試仍拒絕正式 `data.db`，新增測試只用暫存資料庫；沒有 Web route、template 或測試讀取 `.env`、`token.txt`、正式資料庫、備份、匯出或真實使用者資料；沒有新增任何非本階段功能；不提交、不推送，等待使用者授權。

## Plan Self-Review

- [x] 四個既有 Services 已涵蓋需求，未計畫新增資料庫或服務介面。
- [x] 12 項指定測試需求都分配至 Task 1 至 Task 4，並重用既有 OAuth TestClient 與隔離 SQLite pattern。
- [x] session 所有權、CSRF、PRG、安全錯誤與失敗回填都有明確任務。
- [x] README、CHANGELOG、版本、依賴、資料庫結構與未來頁面未列入本輪實作範圍。
