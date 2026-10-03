# localhost Web 首頁預算與小月曆卡片實作計畫

> **For agentic workers:** 後續取得實作授權後使用 `superpowers:executing-plans` 逐項執行，步驟以 checkbox 追蹤。本輪只產出文件，不執行本計畫；不自動啟動子代理或 Git 提交。

**Goal:** 保留快速記帳，在登入首頁下方加入本月預算與小月曆等寬雙卡。

**Architecture:** Web 從 OAuth session 取本人 ID，沿用 `life_ledger_service` 的月份摘要與月曆服務，各呼叫一次。route 僅整理呈現資料；首頁與完整月曆共用日期格規則，核心帳務、交易及 Service 介面不動。

**Tech Stack:** 現有 FastAPI／Starlette、Jinja、CSS grid、標準庫 date／Decimal、既有 unittest／TestClient；不新增依賴或 JavaScript。

**Spec:** [首頁預算與小月曆卡片設計規格](../specs/2026-09-28-home-budget-mini-calendar-design.md)。執行者先讀規格與此計畫，這份計畫不是實作授權。

## Global Constraints

- 工作樹 `C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`、分支 `codex/localhost-web-auth`，文件核對基準 0.11.12／HEAD b775b76。執行前重新確認；保留全部既有未提交變更。
- 本輪只新增兩份文件，不改版本、README、CHANGELOG、程式、測試、依賴或資料庫，不 stage、commit、push。下列勾選項全為未執行的後續工作。
- 所有資料本人 session 隔離；Web 不直連核心／SQLite。卡片顯示已記錄消費，不是銀行餘額或未來預測。
- 重用既有查詢及格式，無分類進度／消費速度／JS／新 Service／新資料表；不自動同步 recurring。
- 預算整數元，消費及差額兩位小數，單位「元」；僅 category「總額」算總預算，不自動加總分類。
- 月曆 Monday-first，Mon–Sun，日期格只日數與記號；未來日非連結，日期連向既有 `/calendar` 明細。
- 功能測試只用隔離 SQLite、固定台灣時鐘、OAuth 替身，不讀實際 `.env`、Token、正式 data.db、備份或匯出。純文件輪不執行功能測試。
- 後續實作交付時依當時授權及版本更新 README／CHANGELOG；不得為本輪文件變更新增版本。提交／推送仍需使用者另行明確授權。

## Review Focus

| 容易漏掉的情況 | 預期行為與負責任務 |
| --- | --- |
| 只有分類預算 | 支出仍顯示，總預算顯示尚未設定；任務 1 |
| 0.29 元與十億元預算、負差額 | cents 計算精確、大整數不縮位、超支取正值；任務 1 |
| 本月未來日 | 留日期格、不產生 href；首頁與完整月曆一致；任務 1、2 |
| 讀取資訊時的寫入與失敗 | 不同步 recurring；付款方式原有補建如實保留；安全錯誤不破壞草稿；任務 1、3 |
| 成功後頁面開始出現支出金額 | 只要求表單清空與不重複寫入，不禁止摘要金額；任務 3 |

## 最小檔案範圍

| 後續修改檔案 | 用途 |
| --- | --- |
| `web/routes.py` | 首頁卡片 context、共用月曆格資料與既有首頁整合 |
| `web/templates/home.html` | 上方搜尋／設定、表單下雙卡、空與失敗狀態 |
| `web/templates/calendar.html` | 僅以共用 is_future 改掉不可用未來日連結 |
| `web/templates/base.html` | 加 panel_class block，預設保持 `.panel` |
| `web/static/web.css` | 首頁專用寬度、雙卡、窄螢幕及未來日呈現 |
| `tests/test_web_auth.py` | 沿用 fixture 補測試與調整兩項舊斷言 |

不新增程式檔，不改 `life_ledger_service.py`、`spending.py` 或 Service 測試。後續實作交付另需 README／CHANGELOG；這兩檔不在本輪修改範圍。

## 驗證命令與執行環境

以下命令從指定 worktree 執行，全部是後續實作檢查，並非本輪結果。已確認 worktree 的 `.venv\Scripts\python.exe` 檔案存在，但沒有執行，不能因此認定環境有效。

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_web_auth -v
& .\.venv\Scripts\python.exe tests/run_discord_validation.py
& .\.venv\Scripts\python.exe -m ruff check .
git diff --check
git status --short
```

若 venv 無法啟動，先用 Codex `load_workspace_dependencies` 找既有 bundled Python，檢查能否 import 現有依賴；不得自行安裝或改依賴。全回歸入口已核對會拒絕連線正式 DB；新增測試仍須使用 `_WebLedgerTestFixture` 的暫存資料庫。預期 unittest／runner 結尾 OK、Ruff 通過、diff check 無錯；紀錄當輪實際數量，不引用歷史數量。

## Task 1：重用服務，建立首頁資料與共用日期格

**Files:** 修改 `web/routes.py`；測試 `tests/test_web_auth.py`，沿用 `_WebLedgerTestFixture`，可新增 `WebHomeCardsTests` 類別。

**Interfaces:**

- consumes：`life_service.get_month_summary(user_id, month=None)`、`life_service.get_calendar_days(user_id, month)`；規格第 3 節的既有資料形狀；`taiwan_today()`、`_format_twd(cents: int) -> str`。
- produces：`_calendar_grid_context(month_start: date, calendar_rows: list[dict], *, as_of: date) -> dict[str, object]`，鍵為 `month_title/month_text/leading_blanks/calendar_days/has_month_expenses`；日期列 `date/day/has_expense/is_future`。
- produces：`_home_cards_context(user_id: str, *, as_of: date) -> dict[str, object]`，鍵為 `home_budget/home_calendar/home_cards_error`；完整欄位及失敗狀態依規格第 6 節。
- 整合 `_quick_entry_context(..., as_of: date | None = None)`；未傳時只取一次 Web 今天，同次用於表單預設與卡片，不改其他參數。完整月曆重用 grid context，保留每日明細、導覽及驗證。

- [ ] 1. 先寫失敗測試 `test_home_budget_context_states_and_precision`。固定 2026-09-24，以隔離資料或真實結構替身檢查：1000 元、支出 120.50 → 預算 `1000`、spent `120.50`、remaining `879.50`；100 元 → overspent `20.50`、remaining None；剛好用完 `0.00`；無支出 `0.00`；無總預算及分類獨存 → total_budget None；1 元／0.29 元 → `0.71`；十億元 → `1000000000`。`test_home_legacy_fractional_budget_is_safe` 驗證舊 1000.50 元不得被四捨五入成整數或回寫，僅回固定卡片錯誤。
- [ ] 2. 寫 `test_home_month_queries_are_once_and_session_scoped`：登入後 GET `/?user_id=另一測試ID&month=2024-02&day=2024-02-01`，兩項 Service 各一次、均接 session ID 與 `2026-09`；list/search/chart/sync 均未呼叫。未登入時新增兩項 Service 均零次。錯誤回填的呼叫次數在任務 3 補驗。
- [ ] 3. 寫 `test_home_calendar_grid_lengths_and_links`：固定 Web 與核心今天為各測試月份月底，2025-02（28 天、5 空格）、2024-02（29、3）、2026-09（30、1）、2026-08（31、5）；星期順序 Mon–Sun。另固定 2026-09-24：24 日可連、25–30 日 is_future；ISO 日期及 month 正確。以 Web 台灣時鐘 2030-01-02／對應核心時鐘驗證跨年本月不是執行機器的月份。
- [ ] 4. 執行 Web 測試確認新案例因缺少 context／卡片失敗，記錄 RED；原有失敗若來自環境先處理環境，不回退累積變更。
- [ ] 5. 實作上述兩個小函式與 context 整合。摘要 float 經 `Decimal(str(value))` 轉 cents；以 cents 算差額，預算先確認整數再固定整數字串，超支絕對值用 `_format_twd`。只取「總額」，不遍歷分類重新加總。共用 grid 函式不查詢，並以 row cents > 0 決定空月份。
- [ ] 6. 預期 TypeError／ValueError 以固定 `home_cards_error` 回傳，兩卡資料 None；不改表單 status/error/draft，不包住寫入操作或加敏感日誌。重跑 Web 測試；純 context 案例應 GREEN，尚無模板的 HTML 案例留待任務 2 完成。

## Task 2：首頁雙卡與一致的未來日期呈現

**Files:** 修改五個 Web 檔案中的模板／CSS，以及 `tests/test_web_auth.py`。route 僅在任務 1 已提供的介面不足時修正同一 context，不新增 Service。

**Interfaces:** consumes 任務 1 的 home_budget、home_calendar、home_cards_error 及完整月曆的 calendar_days.is_future；produces 既有 `GET /` 與 `GET /calendar` HTML，不新增 route 或 JSON API。

- [ ] 1. 寫 `test_home_cards_layout_and_fixed_states`：HTML 順序為搜尋／設定、entry-form、預算卡、小月曆、登出；有預算／無預算／超支文案依規格，單位元，無分類進度或速度。無消費仍有全月日期、無記號及固定空狀態；卡片 error 有 alert，表單保留。更新舊「查看月曆」斷言為「查看完整月曆」，href 保留 `/calendar`。
- [ ] 2. 寫 `test_home_mini_calendar_marks_all_recorded_consumption`：沿用完整月曆既有資料準備方式，已入帳手動、固定、訂閱、分期共同反映；撤銷、他人及隔離庫中的其他 kind 不反映。摘要合計與小卡日期記號範圍一致；比較小卡與完整月曆各日記號。檢查日期格及屬性沒有金額、分類、用途或 level（限制到卡片內，不能禁止預算卡的金額）。
- [ ] 3. 寫 `test_home_date_link_opens_existing_day_details`：擷取並 HTML-unescape 2026-09-01 連結，GET 後 status 200、選取日期與本人當日明細正確；無消費日連結仍可用。`test_calendar_future_dates_have_no_links` 同時檢查小卡與完整月曆 25–30 日保留格子但沒有 href，24 日可點，直接請求未來 day 仍回原 400。
- [ ] 4. 執行 Web 測試，確認新增模板行為為 RED；不因新雙卡刪掉其他安全測試。
- [ ] 5. 在 base 增加預設 `.panel` 的 `panel_class` block，首頁覆寫加 `.home-panel`；搜尋／設定移至上方，快速記帳表單原欄位與 CSRF 不動，下方插入雙卡。未來日用 span、aria-disabled；兩模板共用日期 context，不複製日期運算。保留 autoescape、date aria-label、記號文字與可見焦點。
- [ ] 6. 首頁 CSS 採 border-box、最大 64rem／viewport 減 2rem、窄 padding 1rem、桌面 2rem；雙卡 min-width 0、間距 1rem，48rem 以上兩等寬欄，以下單欄。小月曆七欄 minmax(0,1fr)，避免固定寬度或 overflow-hidden。只以首頁作用域調整卡片樣式，不重設其他頁 panel。
- [ ] 7. 重跑 Web 測試至 GREEN。實際瀏覽器用隔離測試資料檢查 320／375／768／1280 px 的單欄／雙欄、無橫向捲動、日期格可讀可點、鍵盤焦點；可檢查 `scrollWidth <= clientWidth` 及兩卡實際等寬。沒有安全的隔離預覽環境就標記手機／瀏覽器未驗證，不啟動正式帳本作為預覽。

## Task 3：副作用、表單回歸與最終交付

**Files:** 測試 `tests/test_web_auth.py`；必要修正限定前兩任務既定 Web 檔。後續實作交付的 README／CHANGELOG 須按當時授權處理，本輪不改。

**Interfaces:** 原 `POST /expenses` 五欄、CSRF、400 草稿回填、303 到 `/?saved=1`；不改服務、帳務寫入或正式資料。

- [ ] 1. 寫 `test_home_information_reads_do_not_sync_or_mutate_existing_ledger`：隔離庫先建立本人預設付款方式，再建立尚未同步的固定／訂閱／分期規則及他人規則；讀首頁前後比較所有隔離表內容，完全相同、sync 未呼叫、規則尚未產生消費。另測新本人無付款方式時只有原有「現金／未指定」補建，不生成帳目、action 或 notice。測試 SQL 僅用於隔離測試準備及斷言，絕不放進 Web。
- [ ] 2. 寫 `test_home_card_failure_preserves_quick_entry_draft`：以有效 session/CSRF 提交非法金額草稿，summary/calendar 分別拋 TypeError／ValueError；固定錯誤不洩漏內部訊息，原五欄與失效選項仍保留，status 400，各新增 Service 最多一次。正常卡片可讀的錯誤回填兩者各一次，與 GET 一致。再驗證卡片讀取失敗不阻止有效 POST 的 303。
- [ ] 3. 修正 `test_saved_page_refresh_does_not_duplicate`：成功記錄 120.50 後，金額／消費項目 input value 清空，卡片顯示已記錄支出 `120.50 元`，success 提示不改，刷新後隔離庫僅一筆。原分類、付款方式、台灣日期預設、他人來源拒絕、CSRF 拒絕與草稿測試照舊。這是畫面需求改變，不是降低不重複寫入的檢查。
- [ ] 4. 先確認新增副作用／回歸案例可抓到相應錯誤，再完成必要最小修正。執行本計畫命令的 Web、全回歸、Ruff 與 diff check；逐一檢查結果。沒有新失敗不擴大重測或重構。
- [ ] 5. 對照規格六組驗收逐項記錄已驗證／尚未實測，尤其真實 OAuth、手機、與 Bot 並行操作不能用替身測試代稱。核對 git status 只多出本功能授權修改，保留舊變更，沒有正式資料／憑證／依賴或核心範圍擴張。
- [ ] 6. 後續實作完成且獲准更新交付文件時，再依實際最新版本更新繁體中文 CHANGELOG 與 README，說明未來日連結修正、無資料庫／依賴變更與實際測試證據。本計畫不要求 stage、commit、push；不要清理工作樹或把既有變更混作本功能新變更。

## 本輪文件自查與交接

- 規格第 3／4／6 節 → 任務 1：資料介面、本人身分、各一次查詢、金額與安全失敗。
- 規格第 1／5／6 節 → 任務 2：入口位置、兩卡、日期格、明細連結、未來日與響應式排版。
- 規格第 3／7 節 → 任務 3：既有副作用、無新同步、快速記帳、隔離與驗證交付。
- 本輪只做文件一致性、Git diff whitespace 及前後工作樹保留檢查；沒有任何 checkbox 已執行，也不宣稱新功能、測試、真實 OAuth 或手機畫面已完成。
