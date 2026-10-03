# localhost Web v1 第二階段 B：完整月曆頁 Implementation Plan

> **狀態：計畫、尚未實作。**
>
> 本文件只規劃月曆頁，不代表 Python、HTML、CSS、測試、資料庫、依賴、README、CHANGELOG、版本或 Git 歷史已修改。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有已登入首頁加入連向完整月曆的入口，讓本人以 CSS grid 瀏覽本月／過去月份並點選日期查看所有有效生活消費明細。

**Architecture:** `GET /calendar` 只從已驗證 Discord OAuth session 取得使用者 ID，並只呼叫 `life_ledger_service.get_calendar_days()` 與 `life_ledger_service.list_expenses()`。route 負責嚴格解析月份／日期、組裝可呈現的 context，從同一月份有效消費清單篩出選取日，並處理安全錯誤；核心驗證、使用者隔離與 SQLite 查詢仍留在既有 Services／`spending.py`。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、Starlette SessionMiddleware、既有 Authlib OAuth、標準函式庫 `datetime`、CSS grid、unittest、FastAPI TestClient、既有暫存 SQLite 測試。

**Spec:** `docs/superpowers/specs/2026-09-24-web-v1-phase2b-calendar-design.md`

## Global Constraints

- 本階段只新增完整月曆頁與首頁「查看月曆」連結；不做預算、首頁雙卡／小月曆、搜尋、設定、分類／付款方式管理、固定支出、投資、AI、匯出、備份、JSON API、React 或 App。
- 資料流固定為 `web route → 已驗證 session Discord user_id → life_ledger_service.py → spending.py → SQLite`；Web 不得 import `db.py`、`spending.py`、`ledger.py`，不得直接 SQL，也不得接受 query 或 form `user_id` 作為身分。
- 只重用 `get_calendar_days(user_id, month)` 與 `list_expenses(user_id, month, include_voided=False, limit=None, offset=0)`；不新增 Service、資料表、欄位、索引、遷移、依賴、ORM、Repository、DI、日期套件或 JavaScript 框架。
- `month` 只接受 `YYYY-MM`，`day` 只接受 `YYYY-MM-DD` 且必須屬於 `month`；未來月份／日期一律安全拒絕。台灣今天由既有 `web.routes.taiwan_today()` 決定。
- 日期格採 Monday-first CSS grid，星期標題必為 `Mon Tue Wed Thu Fri Sat Sun`；格內只顯示日期與有資料記號，絕不顯示金額。
- `get_calendar_days` 與預設 `list_expenses` 都排除 `voided`，並限本人指定月份的 `consumption`。日期記號、選取日合計與明細必須使用同一完整資料範圍；route 從 `list_expenses(...)["items"]` 以 `spent_on` 篩選已選日期。測試不可接觸正式 `data.db`，一律使用 `TemporaryDirectory` 和 `patch.object(db, "DB_NAME", ...)`。
- 本計畫執行完成時才依 AGENTS.md 更新 README 與 CHANGELOG；本輪規劃不提前改版本，也不將月曆寫成已完成。執行後不 commit、stage 或 push，除非另獲明確授權。

## Review Focus

- 不合法、月日不相符或未來的 `month`／`day` 不可造成服務查詢、內部例外洩漏或未來月份導覽。
- `?user_id=他人`、他人帳目、他人付款方式與已撤銷帳目不能出現在本人日曆記號、日合計或明細。
- Monday-first 空白格應正確處理不同起始星期與 28／29／30／31 天，不得讓日期看似落在錯誤星期。
- 日期格中不可意外印出任何 `cents` 或格式化金額；金額只能在已選日期下方的明細區。
- 本人手動與固定／訂閱／分期等已正式入帳的有效消費在同一天時，日期記號、日合計及明細必須一致；不能因來源不同出現有記號卻是零合計／空明細。

## 預計修改檔案

| 檔案 | 實作時責任 |
| --- | --- |
| `web/routes.py` | `/calendar` 登入保護、月份／日期解析、Service 呼叫、導覽與模板 context。 |
| `web/templates/home.html` | 在快速記帳區新增「查看月曆」連結。 |
| `web/templates/calendar.html` | 新增月曆、導覽、選取日明細、空／錯誤狀態的 server-rendered 範本。 |
| `web/static/web.css` | Monday-first grid、日期格、記號、選取、導覽與小螢幕樣式。 |
| `tests/test_web_auth.py` | 現有 OAuth 替身與暫存 SQLite 上的月曆 route、安全、版面結構與邊界測試。 |
| `README.md` | 實作完成後才更新目前版本與 localhost 月曆可用範圍。 |
| `CHANGELOG.md` | 實作完成後才新增實際版本、檔案／資料庫影響、驗證結果與未驗證項目。 |

不修改：`life_ledger_service.py`、`spending.py`、`db.py`、`ledger.py`、`schema.py`、`web/auth.py`、`web/app.py`、資料庫結構、requirements、`.env`、Token、備份、匯出檔與使用者資料。

---

### Task 1: 建立隔離月曆測試基線與首頁入口的 RED 測試

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/templates/home.html`
- Verify: `tests/test_life_ledger_service.py`
- Verify: `tests/form_helpers.py`

**Interfaces:**
- Consumes: 現有 `WebQuickEntryTests.setUp()` 的 `TemporaryDirectory`、`patch.object(db, "DB_NAME", ...)`、`patch("spending.today", ...)`、`db.init_db()`、`_login()` 與 `life_service.add_expense(...)`。
- Produces: `WebCalendarTests`（或同一隔離 fixture 上的等效類別），可登入、建立本人／他人／撤銷測試資料，且永遠不開啟正式 `data.db`。

- [ ] **Step 1: 先寫首頁連結與未登入保護的失敗測試**

  在既有 `WebQuickEntryTests` 的隔離 setup 基礎新增月曆測試類別；不要建立第二套 DB fixture，也不要使用 `tests/form_helpers.py`（它是 Discord UI 選項 helper）。加入下列測試：

  ```python
  def test_logged_out_calendar_does_not_expose_ledger_data(self):
      life_service.add_expense(self.user_id, "120", "餐飲", "午餐", "2026-09-24")
      response = self.client.get("/calendar?month=2026-09")
      self.assertEqual(response.status_code, 403)
      self.assertNotIn("午餐", response.text)
      self.assertNotIn("120", response.text)
      self.assertNotIn(self.user_id, response.text)

  def test_logged_in_home_links_to_calendar(self):
      self._login()
      response = self.client.get("/")
      self.assertIn('href="/calendar"', response.text)
      self.assertIn("查看月曆", response.text)
  ```

  與既有登入頁測試一併斷言未登入首頁不出現「查看月曆」或帳務資料。

- [ ] **Step 2: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebQuickEntryTests -v
  ```

  Expected: 新增的首頁連結與 `/calendar` 測試失敗，因 route／連結尚未存在；既有 OAuth、快速記帳測試仍可執行。

- [ ] **Step 3: 實作最小首頁入口**

  在 `web/templates/home.html` 的快速記帳區只加入普通連結：

  ```html
  <a class="button secondary" href="/calendar">查看月曆</a>
  ```

  不加入首頁月曆資料、服務呼叫、預算卡、JavaScript 或 query parameter。`/calendar` 尚未實作前，不修改 `web/routes.py` 的既有快速記帳行為。

- [ ] **Step 4: 驗證首頁入口的最小變更**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebQuickEntryTests -v
  ```

  Expected: 首頁連結測試通過；未登入月曆測試仍為 RED，作為下一項 route 的測試先行證據。

### Task 2: 實作登入保護、嚴格月份／日期解析與月曆 Service context

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/routes.py`
- Create: `web/templates/calendar.html`

**Interfaces:**
- Consumes: `auth.current_user_id(request.session) -> str | None`、`taiwan_today() -> date`、`life_service.get_calendar_days(user_id, month) -> list[dict]`、`life_service.list_expenses(user_id, month) -> {'items': list[dict], 'total': int}`。
- Produces: `GET /calendar?month=YYYY-MM&day=YYYY-MM-DD`；登入使用者得到 `calendar.html`，未登入得到既有固定安全錯誤，不合法輸入得到 HTTP 400 固定安全錯誤。

- [ ] **Step 1: 先寫月份、日期與固定安全錯誤的失敗測試**

  固定測試時間為 `2026-09-24`，登入後測試下列情況：

  ```python
  def test_calendar_defaults_to_taiwan_current_month(self):
      self._login()
      response = self.client.get("/calendar")
      self.assertEqual(response.status_code, 200)
      self.assertIn("2026 年 09 月", response.text)

  def test_calendar_rejects_invalid_future_and_mismatched_date_safely(self):
      self._login()
      for target in (
          "/calendar?month=2026-9",
          "/calendar?month=2026-13",
          "/calendar?month=2026-10",
          "/calendar?month=2026-09&day=2026-09-31",
          "/calendar?month=2026-09&day=2026-08-31",
          "/calendar?month=2026-09&day=2026-09-25",
      ):
          response = self.client.get(target)
          self.assertEqual(response.status_code, 400)
          self.assertIn("無法顯示月曆，請返回後重試。", response.text)
          for forbidden in ("SQLite", "Traceback", ".db", self.user_id):
              self.assertNotIn(forbidden, response.text)
  ```

  另分別以 `patch("web.routes.life_service.get_calendar_days", side_effect=ValueError("internal-detail"))` 及 `patch("web.routes.life_service.list_expenses", side_effect=ValueError("internal-detail"))` 驗證預期 Service 例外同樣得到固定錯誤，且不洩漏 `internal-detail`。

- [ ] **Step 2: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCalendarTests -v
  ```

  Expected: `GET /calendar` 目前為 404，測試以 route 不存在失敗。

- [ ] **Step 3: 實作最小的 route helpers 與 context**

  在 `web/routes.py` 新增私有 helper，不 import 核心帳務模組：

  ```python
  CALENDAR_ERROR = "無法顯示月曆，請返回後重試。"

  def _calendar_month(value: str | None) -> date:
      month = (value or taiwan_today().strftime("%Y-%m")).strip()
      if not re.fullmatch(r"\d{4}-\d{2}", month):
          raise ValueError
      parsed = date.fromisoformat(f"{month}-01")
      if parsed.strftime("%Y-%m") != month or parsed > taiwan_today().replace(day=1):
          raise ValueError
      return parsed
  ```

  以同樣嚴格方式解析 `day`，要求 `date.fromisoformat()` 的結果文字完全相同、月份相符、且不晚於 `taiwan_today()`。在 `GET /calendar` 先取得 `auth.current_user_id()`；未登入回 `_error_response(request, "請先使用 Discord 登入。", 403)`，不呼叫 Services。登入後使用同一個 `month_text = month.isoformat()[:7]` 呼叫 `life_service.get_calendar_days(user_id, month_text)` 與 `life_service.list_expenses(user_id, month_text)`，並將預期 `TypeError`／`ValueError` 轉為 `CALENDAR_ERROR` 的 HTTP 400，不將例外文字放入 template。將 `month_expenses = result["items"]` 放入私有月曆 context，供選取日以 `spent_on` 過濾；不呼叫 `search_expenses()`。

  `calendar.html` 此步只渲染月份標題與固定安全欄位，確保 Jinja context 不含 `user_id`。

- [ ] **Step 4: 驗證 route 與安全錯誤**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCalendarTests -v
  ```

  Expected: 已登入預設本月與安全拒絕測試通過；格線、導覽、記號與日明細測試仍在後續任務新增。

### Task 3: 以 Monday-first CSS grid 顯示日期、記號與月份導覽

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/routes.py`
- Modify: `web/templates/calendar.html`
- Modify: `web/static/web.css`

**Interfaces:**
- Consumes: Task 2 的已驗證月曆 context 與 `get_calendar_days()` 每日 `{"date", "cents", "level"}`。
- Produces: 由 `calendar_days`、`leading_blanks`、`previous_month`、`next_month`、`can_go_next` 組成的 month context；日期格只輸出日期、選取 URL 與有資料記號。

- [ ] **Step 1: 先寫格線、月份長度、記號與導覽的失敗測試**

  新增本人資料到 `2026-09-02`，並針對不同月份檢查 server-rendered 結構：

  ```python
  def test_calendar_is_monday_first_for_different_month_lengths(self):
      self._login()
      september = self.client.get("/calendar?month=2026-09")  # Tue, 30 days
      self.assertEqual(september.text.count('class="calendar-blank"'), 1)
      self.assertEqual(september.text.count('class="calendar-day"'), 30)

      february = self.client.get("/calendar?month=2024-02")  # Thu, 29 days
      self.assertEqual(february.text.count('class="calendar-blank"'), 3)
      self.assertEqual(february.text.count('class="calendar-day"'), 29)
      self.assertIn("Mon", february.text)
      self.assertIn("Sun", february.text)
  ```

  另斷言 `2026-09-02` 格含固定記號（例如 `class="calendar-marker"`），但日期格 HTML 區塊不含 `120`、`120.00`、`NT$120`、`▓` 或 `▒`。測試 `month=2026-08` 有可用的下個月連結到 `2026-09`；`month=2026-09` 不含 `month=2026-10`，且仍有回到本月連結。確認每個星期標題依序為 `Mon Tue Wed Thu Fri Sat Sun`。

- [ ] **Step 2: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCalendarTests -v
  ```

  Expected: 月份標題雖可能通過，但空格、日期格、記號與導覽斷言因尚未實作 grid 而失敗。

- [ ] **Step 3: 以最小 context 與 template 實作 grid**

  在 `web/routes.py` 用標準函式庫組裝，不依賴 Service 的 `level`：

  ```python
  leading_blanks = month_start.weekday()
  calendar_days = [
      {"date": row["date"], "day": date.fromisoformat(row["date"]).day,
       "has_expense": row["cents"] > 0}
      for row in rows
  ]
  ```

  以 month start 的前後一個月計算導覽值。若下一月大於台灣本月，context 令 `can_go_next=False`，template 以 `<span aria-disabled="true">下個月</span>` 顯示，不輸出未來 href。`calendar.html` 必須先輸出七個英文星期 header，再輸出 `leading_blanks` 個 `<span class="calendar-blank" aria-hidden="true"></span>`，以及一個個 `<a class="calendar-day ..." href="/calendar?month=...&amp;day=...">`。日期格中只有日數字與在 `has_expense` 為真時的 `<span class="calendar-marker" aria-label="當日有消費">•</span>`。

  `web.css` 只新增 `grid-template-columns: repeat(7, minmax(0, 1fr))` 的月曆 grid、最小點擊面積、marker、selected、disabled 導覽與窄螢幕縮排；不加入 JavaScript、文字月曆、圖表或金額樣式。

- [ ] **Step 4: 驗證 grid 與導覽**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCalendarTests -v
  ```

  Expected: Monday-first、閏年二月、30 天九月、英文星期、只含記號與不產生未來下月 URL 的測試通過。

### Task 4: 選取日期的本人日合計、明細與空狀態

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `web/routes.py`
- Modify: `web/templates/calendar.html`
- Modify: `web/static/web.css`

**Interfaces:**
- Consumes: Task 2 的安全 `selected_day`，以及 `life_service.list_expenses(user_id, month)["items"]` 回傳的帳目 dict（`cents`、`note`、`category`、`payment_source_name`、`spent_on`、`status`、`origin`）。
- Produces: 月曆下方的 selected-day section；只有有效、本人、所有已正式入帳的 `consumption` 帳目和由它們加總的金額可見。

- [ ] **Step 1: 先寫本人隔離、voided 排除與空狀態的失敗測試**

  建立本人於 `2026-09-01` 的手動消費，並以既有 `sp.add_recurring()` 為 `固定`、`訂閱`、`分期` 各建立一個 `start="2026-09"` 的規則（分期傳入有效的 `periods=1`），再呼叫 `sp.sync_recurring(self.user_id, date(2026, 9, 24))` 產生同日已入帳消費；再建立他人同日消費，以及本人同日後被 `life_service.void_expense()` 的消費。測試 fixture 使用下列具體資料：

  ```python
  life_service.add_expense(
      self.user_id, "120.50", "餐飲", "午餐", "2026-09-01", own_source
  )
  for kind, name, amount, periods in (
      ("固定", "房租", "900", 0),
      ("訂閱", "影音訂閱", "80", 0),
      ("分期", "筆電分期", "300", 1),
  ):
      sp.add_recurring(
          self.user_id, kind, name, amount, "居住", "2026-09", periods
      )
  sp.sync_recurring(self.user_id, date(2026, 9, 24))
  ```

  登入本人並請求：

  ```python
  response = self.client.get("/calendar?month=2026-09&day=2026-09-01")
  self.assertIn("午餐", response.text)
  self.assertIn("房租", response.text)      # 固定
  self.assertIn("影音訂閱", response.text)  # 訂閱
  self.assertIn("筆電分期", response.text)  # 分期
  self.assertIn("餐飲", response.text)
  self.assertIn("本人卡", response.text)
  self.assertIn("1400.50", response.text)
  self.assertNotIn("他人晚餐", response.text)
  self.assertNotIn("已撤銷用途", response.text)
  self.assertNotIn("Discord ID", response.text)
  self.assertNotIn(self.user_id, response.text)
  self.assertNotIn("revision", response.text)
  ```

  同時斷言 `2026-09-01` 格有記號，選取後合計不為零且同時可見「午餐」、「房租」、「影音訂閱」與「筆電分期」，以固定這三者同範圍的規則。再測 `day=2026-09-03` 顯示零合計與固定「此日期沒有已記錄的消費。」。以 patch `life_service.list_expenses` 拋 `ValueError("private-db-detail")`，斷言 HTTP 400 固定 `CALENDAR_ERROR`，不含原例外。不得建立「有記號但空明細」的測試預期。

- [ ] **Step 2: 執行 focused tests，確認 RED**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCalendarTests -v
  ```

  Expected: 選日後 route 尚未從月份完整清單篩選資料，日合計／列表、本人手動加固定消費、voided／他人排除與空狀態斷言失敗。

- [ ] **Step 3: 實作最小的 selected-day context 與安全呈現**

  只在 Task 2 已取得的 `month_expenses` 上於 `selected_day` 存在時篩選：

  ```python
  expenses = [
      item for item in month_expenses
      if item["spent_on"] == selected_day.isoformat()
  ]
  daily_cents = sum(item["cents"] for item in expenses)
  ```

  不接受帳目 ID，不接觸 SQL，不自行查詢、修改或排除任何來源。`list_expenses` 已定義本人、有效、未撤銷 `consumption` 的完整月份資料範圍；Template 只用 Jinja 自動跳脫輸出金額、用途、分類與 `payment_source_name`，並顯示由 `daily_cents` 格式化的消費合計。沒有 items 時，輸出固定空狀態；有 items 時完整顯示所有來源的明細，不輸出 `status`、`origin`、`entry_type`、ID 或 revision。將 Service 的 `TypeError`／`ValueError` 導向 Task 2 的安全錯誤頁。

- [ ] **Step 4: 驗證選日明細與資料隔離**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCalendarTests -v
  ```

  Expected: 本人手動與固定／訂閱／分期的有效明細、合計、金額、分類、付款方式、零資料日，以及 voided／他人排除測試通過；任何有記號日期都有非零合計和相符明細。

### Task 5: Web 邊界、文件紀錄與完整隔離驗證

**Files:**
- Modify: `tests/test_web_auth.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Verify: `web/app.py`, `web/auth.py`, `web/routes.py`, `tests/run_discord_validation.py`

**Interfaces:**
- Consumes: Tasks 1–4 的已完成 Web 頁面、既有 AST import boundary test、現有 `tests/run_discord_validation.py` 的正式資料庫防護。
- Produces: 可重現的驗證證據與只記載實作事實的文件紀錄；不新增 API、資料庫變更或正式資料操作。

- [ ] **Step 1: 先擴充 import 邊界與隔離資料庫測試**

  保留並擴充既有 `test_web_modules_keep_database_access_behind_life_service`：以 `ast.parse()` 掃描 `web/app.py`、`web/auth.py`、`web/routes.py`，斷言三者都不 import `db`、`spending`、`ledger`，且只有 `web/routes.py` import `life_ledger_service`。月曆測試仍只透過 `life_service` 寫入／讀取；test setup 明確 patch `db.DB_NAME` 至 `TemporaryDirectory`，不讀取或使用 `data.db`。

- [ ] **Step 2: 先執行 Web 測試，確認所有行為為 GREEN**

  ```powershell
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
  ```

  Expected: 現有 OAuth、快速記帳與新增月曆測試全部通過，且輸出沒有正式資料庫、Token 或真實 OAuth 請求。

- [ ] **Step 3: 只在功能與驗證完成後更新文件**

  讀取當時的 `README.md` 和 `CHANGELOG.md` 最新版本；以目前 `0.11.3` 為基準，在實作交付時升至適當修訂版（預期 `0.11.4`，但以檔案當下版本為準）。README 只補充 localhost Web 的已完成月曆入口、登入限制與尚未實作範圍。CHANGELOG 以繁體中文記錄實際路由／畫面、Services 重用、無資料庫／依賴變更、實際測試命令與結果、未驗證的真實 Discord OAuth／瀏覽器互動。不得將未做的預算、搜尋、API、React 或 App 寫成已完成。

- [ ] **Step 4: 執行完整專案驗證與 Git 檢查**

  ```powershell
  & '.venv\Scripts\python.exe' -m ruff check .
  & '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
  & '.venv\Scripts\python.exe' tests\run_discord_validation.py
  git diff --check
  git status --short
  ```

  Expected: Ruff 顯示 `All checks passed!`；Web OAuth／快速記帳／月曆測試與完整 Discord 隔離驗證均通過；`git diff --check` 無空白錯誤。`git status --short` 只列本階段預期檔案以及執行前已存在的未提交變更，沒有 `.env`、Token、正式 `data.db`、備份、匯出檔或使用者資料被新增追蹤。

---

## 計畫自我檢查

- **規格覆蓋：** Task 1 實作首頁入口與未登入保護；Task 2 處理月份／日期規則與安全錯誤；Task 3 實作 Monday-first grid、記號、月份長度與導覽；Task 4 處理選日明細、本人隔離、voided 排除與空狀態；Task 5 驗證 Web 邊界、隔離 SQLite、文件與完整回歸。
- **OAuth／快速記帳相容性：** `/calendar` 不改 OAuth callback、session 內容、CSRF、`GET /` 或 `POST /expenses`。首頁只增加一個 GET 連結；日曆是讀取 route，沒有新的 POST 或寫入。
- **既有 Service 相容性：** 計畫僅以既有 `get_calendar_days` 和預設 `list_expenses` 的實際回傳／驗證行為組裝畫面；後者提供同月所有有效 `consumption`，使記號、日合計與日明細不因來源不同而分歧，不以 Web 補 SQL。
- **範圍控制：** 無 Service、資料庫、依賴、API、JavaScript 框架、ORM、Repository、DI 或首頁小工具擴張。README／CHANGELOG 只在真正完成實作和驗證後更新。
- **模糊項目檢查：** `month`／`day` 格式、未來限制、選取日與同月限制、下一月停用、固定空／錯誤文案、所有有效 `consumption` 的共同資料範圍及可顯示欄位均已固定。
