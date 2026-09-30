# localhost Web v1 本月預算視覺化實作計畫

> **狀態：已實作，隔離驗證完成（0.11.17）。** 使用者已授權以 `superpowers:executing-plans` 與 TDD 逐項執行，並確認原生details預設關閉、完整分類清單。以下步驟已執行，結果見文末；未stage、commit、push或建立worktree。

**Goal:** 在既有首頁預算卡以精確金額、使用比例及原生 progress 呈現本月總預算和已設定分類預算。

**Architecture:** session 本人 → 現有 Service → 同一份 spending 統計。核心保留既有結果並增加精確 cents 欄位；Web 只算差額／呈現比例，不自行查 SQL 或合計消費。分類狀態重用快速記帳已取的啟用名稱；布局留在 Jinja／CSS。

**Tech Stack:** 現有 FastAPI、Jinja、CSS、unittest／TestClient、Decimal、原生 progress／details；不新增依賴或 JavaScript。

**Spec:** [設計規格（已實作）](../specs/2026-09-28-web-v1-budget-visualization-design.md)。基準2026-09-28、0.11.16、HEAD b775b76；已選方案A並完成0.11.17，柔和動畫延後。

## Global Constraints

- 固定既有 `.worktrees/localhost-web-auth`、`codex/localhost-web-auth`，先讀 [AGENTS.md](../../../AGENTS.md) 並重新確認分支、版本和未提交變更，不重建 worktree／reset／rebase／清理。
- 本輪已有明確實作授權，限於下表核心摘要、首頁呈現、隔離測試與交付文件；同批修改升至0.11.17，不新增範圍。
- 不碰正式 data.db、Token、.env、備份、匯出、使用者資料；核心測試只在 temp DB 初始化。保留 Web import boundary、CSRF、OAuth、PRG、revision 與核心交易，不改 schema 或寫入規則。
- 不另建預算 Service、SQL、分頁、快取、圖表抽象或前端系統。核心讀取增加欄位不等於遷移；正常寫入介面全不變。
- 每項先寫測試、執行並確認因預期功能缺少而 RED，再最小實作確認 GREEN。記錄實際失敗／通過數，環境問題不算 RED，不停用既有測試掩蓋失敗。

## Review Focus

| 風險 | 驗證任務 |
| --- | --- |
| float 已失去 cents，不能靠 Decimal(str(float)) 修復 | 任務 1 原有整數合計直接輸出；大額及差 1 cent 斷言 |
| 分類合計被當總支出、停用分類消失 | 任務 1／2／4；未設預算分類也有消費，停用仍有 budget 列 |
| 核心與 Web 本月不一致 | 任務 2 核心日期固定，另讓 Web 時鐘不同，確認同次渲染一致 |
| 超支被視覺截斷／比例四捨五入掩蓋 | 任務 2／3，超支 0.01 及 125% 仍有精確文字 |
| 卡片故障破壞記帳、跨使用者／XSS 或重複寫入 | 任務 3／4 沿用既有安全回歸與草稿檢查 |

## 預計修改檔案與介面總覽

| 精確檔案 | 後續最小修改 |
| --- | --- |
| `spending.py` | `report()`／`month_report()` 由原整數結果增加 cents 欄位，budget 比例前拒絕非法零／負值；不新增 SQL |
| `tests/test_spending.py` | 核心精度、口徑、legacy 值安全與舊結果相容 |
| `tests/test_life_ledger_service.py` | 現有摘要轉交／身分正規化／隔離及欄位，不改 façade |
| `web/routes.py` | 重用核心今天、薄呈現 helper、擴充現有卡片 context |
| `web/templates/home.html` | 原預算卡內金額、progress、分類清單與空／錯誤狀態 |
| `web/static/web.css` | 限定首頁預算區 progress／分類列／focus／長字換行 |
| `tests/test_web_auth.py` | 沿用 fixture 補 context、HTML、安全、帳目／月曆一致性與回歸 |
| `README.md`、`CHANGELOG.md` | 更新實際功能／0.11.17／當輪驗證與限制 |

不修改 `life_ledger_service.py`、schema、db、OAuth、Web settings、依賴或設定檔。公開介面保留 `get_today()`、`get_month_summary(user_id, month=None)`、`get_calendar_days(user_id, month)`、`get_categories(user_id, include_inactive=False)`；不增加方法。摘要的 additive cents 欄位是唯一資料契約補充。

## Task 0：確認範圍與呈現決定

**Files:** 讀取 AGENTS、兩份規劃及預計修改檔案，必要時只補兩份規劃的確認結果。

- [x] 核對指定 worktree／分支、最新版本／原 diff／Git index；保存既有修改，不讀設定或資料檔。取得明確實作授權。
- [x] 使用者已確認A：原生details預設收合、N為實際已設定分類預算數量，完整清單；無分類預算沒有空details，仍有設定入口。總額在區塊外、零消費分類保留、無總額亦可查看分類。
- [x] 執行後述 focused 舊測試建立當輪基準；失敗先判斷環境與既有問題，不能引用過去 CHANGELOG 結果當本輪基準。

## Task 1：同一份統計提供精確 cents

**Files:** `spending.py`、`tests/test_spending.py`、`tests/test_life_ledger_service.py`。

**Interfaces:**

- consumes：既有 `report(user_id, start, end)`、`month_report(user_id, month=None)` 內全部有效 entries／budget rows。
- produces：report 原 dict 追加 `total_cents: int`、`categories[name]['amount_cents']: int`；budget dict 追加 `budget_cents/spent_cents/remaining_cents: int`。Service 原樣返回，user ID 仍由 `_user()` 正規化。無预算時 budgets=[]，total_cents 仍存在；零消費有效預算 remaining_cents=budget_cents。

- [x] 先加 `SpendingTests.test_month_summary_exact_cents_and_legacy_keys`：1 元分類預算、0.29 元消費，斷言 total_cents=29、分類 amount_cents=29、budget_cents=100、spent_cents=29、remaining_cents=71；同時原 total/budget/spent/remaining/used_percent 的型別／既有值不變。
- [x] 加大額案例：隔離帳本先以核心新增兩筆十億元，再由一次temp transaction批次準備99998筆相同合法金額，最後核心新增0.01元；避免逐筆提醒掃描的平方成本，仍驗證十萬筆整數合計與float無法還原的最後一分。覆蓋零消費、超支負remaining_cents、分類獨存及非預算分類計入total；無新增產品SQL。
- [x] 加 `test_month_summary_consumption_scope_and_inactive_budget`：手動／固定／訂閱／分期入帳、他人、voided、income／transfer／investment、未入帳下月規則；只有四來源有效數額計入，停用分類仍有 budget 列。資料準備用既有核心與 temp transaction，GET 不執行同步；不得新添 production SQL。
- [x] 加 `test_invalid_legacy_budget_read_is_safe`：僅隔離 DB 人工建立 0／負 cents 預算，期待固定 ValueError，不 ZeroDivisionError，不修補資料；0 元消費合計則正常，不把合法 zero spent 誤判。保留舊小數預算首頁安全回歸。
- [x] 執行 `& '.venv\Scripts\python.exe' -m unittest tests.test_spending.SpendingTests.test_month_summary_exact_cents_and_legacy_keys -v`，確認新欄位缺少導致 RED；跑其餘新案例記錄預期缺口。
- [x] 最小實作：只把 `report()` 現有 total／category amount 整數直接加入結果；`month_report()` spent_cents 使用 total_cents 或該分類 amount_cents，差額整數相減。原欄位保留，比例前非法 budget cents 固定 ValueError；不新增讀取來源／LIMIT／sync／交易／schema。
- [x] 加 `LifeLedgerServiceTests.test_month_summary_passes_exact_fields_and_normalizes_owner`：以 wraps/mock 驗證 `42 → '42'` 及 month 原參數、原 dict 轉交；隔離真資料驗證他人不混入、所有新增欄位為 int。只一組 façade 回歸，不建立新抽象。
- [x] GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_spending tests.test_life_ledger_service -v`。確認其他 report／月結／趨勢／Dashboard舊消費者相容；完整回歸留到任務 4。

## Task 2：首頁薄呈現資料，日期與精度一致

**Files:** `web/routes.py`、`tests/test_web_auth.py`，延用 `WebHomeCardsTests`。

**Interfaces:**

- consumes：`get_today()`、精確版 `get_month_summary()`、既有 `get_calendar_days()`、快速記帳的本人啟用 categories、既有 `_format_twd()`／`_format_amount()`。
- produces：小型純函式 `_budget_display_context(summary: dict, active_categories: list[str]) -> dict[str, object]`；不查詢、不接 Discord 或 Request。
- produces：現有 `_home_cards_context(user_id: str, *, as_of: date, active_categories: list[str]) -> dict[str, object]` 延伸 `home_budget`，`home_calendar/home_cards_error` 不變。`_quick_entry_context()` 取核心今天一次，並將已取 categories 傳入；更新現有直接測 `_home_cards_context` 的呼叫。
- `home_budget` 保留 `month_title/spent/total_budget/remaining/overspent`，追加總額 `used_percent/progress_value/status_text` 及 `category_budgets`。無總預算前三項新增值均 None。分類列為 `name/active/budget/spent/remaining/overspent/used_percent/progress_value/status_text`；金額皆格式字串，差額兩欄互斥，進度數值是 HTML 可用的 decimal 字串。used_percent 不含 %，模板加單位。

- [x] 新測試 `test_home_budget_visualization_context_states` 以表格驗證四種預算組合及每種有預算的 0／部分／100%／125%；沒有預算相應比例及進度為 None，分類不依總額存在而消失。不同分類有消費但沒預算時不建立分類卡。
- [x] `test_budget_percentage_and_amount_precision`：1 元預算／0.29 元→29%、剩餘0.71；100元／125元→125%、progress_value='100'、超支25；10,000元／10,000.01元→比例顯示100%、仍超支0.01；1,000／100.50→金額100.5、比例10.05；0 spent進度'0'。ratio 以 Decimal cents 除法、兩位 ROUND_HALF_UP；金額不採比例的 rounding。
- [x] `test_home_uses_core_today_once`：核心日期固定為2026-10-01、Web clock仍2026-09-30；首頁表單日期／兩卡月份是核心值，summary/calendar 均接 session本人及2026-10。query month/user_id 無效，未登入所有帳務查詢零次。
- [x] `test_home_disabled_budget_kept_without_extra_category_query`：已設定分類停用後仍列 active=False；首頁只取一次啟用分類，不新增 include_inactive 查詢，不過濾 budgets。摘要與月曆在成功呈現各一次；list/search/chart 不用於卡片，sync 不呼叫。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebHomeCardsTests.test_home_budget_visualization_context_states -v`，確認缺少新增 context 而失敗。
- [x] 最小實作純 helper，直接使用 cents，不採舊 float／used_percent 作金額來源；total spent 不加總分類。將 core today 同次傳遞，原日期 helper／其他路由不改；採既有 TypeError/ValueError 固定錯誤。
- [x] GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebHomeCardsTests -v`。更新舊摘要替身使其符合新 additive cents 契約，不放寬既有草稿／查詢次數／不寫入斷言。

## Task 3：原卡內金額、原生 progress 與分類清單

**Files:** `web/templates/home.html`、`web/static/web.css`、`tests/test_web_auth.py`。

**Interfaces:** consumes 任務 2 context；produces 同一 GET / HTML；無新路由或公開 Service。

- [x] 新測試 `test_home_budget_progress_accessible_and_empty_states`：total／category 每個有效 budget 恰一個 progress，max=100、有 value（含0）；無預算時不存在對應 progress或%；實際 visible125%／超支25元與 clipped value100同時存在。label／description IDs存在且互異，不只顏色、title 或 fallback。
- [x] 新測試 `test_home_category_budgets_render_all_configured_and_escape`：N項全都在HTML、未設預算分類不在清單、停用badge存在、分類名稱HTML跳脫；多項不截斷。依使用者選擇斷言 A 的details預設無open與summary項數／B 的直式列。無分類用固定「尚未設定分類預算」及設定入口。
- [x] 新測試 `test_budget_markup_preserves_home_workflow`：搜尋／設定在form上方、原表單／logout／月曆連結保留；無總預算但分類有比例；剛好用完有「已用完」。金額千分位、元、去尾零精確。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebHomeCardsTests.test_home_budget_progress_accessible_and_empty_states -v`，確認HTML缺少progress而失敗。
- [x] 只在原預算section加入progress與分類列；沿用focus及responsive布局，scope CSS到首頁預算，不調整搜尋頁面板／設定頁動畫。aria-describedby完整呈現真實比例及剩餘／超支，progress value保持0–100。
- [x] GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebHomeCardsTests -v`。對舊「超支金額」斷言只更新為已核准「超支 N 元」，不刪安全與workflow測試。

## Task 4：隔離帳本整合、帳目／月曆一致性與完整回歸

**Files:** `tests/test_web_auth.py`；僅若當輪測試揭示本功能造成問題，修任務1–3範圍檔案。

**Interfaces:** 沿用真實 `add_expense/update_expense/void_expense`、Web既有新增／更改／軟刪除POST、OAuth替身建立的session；不新增寫入介面。

- [x] 先加 `test_budget_and_calendar_follow_entry_mutations`：新增0.29元、修改金額／分類／手動日期（含跨月）、以本人expected_revision軟刪除後，每一步重新讀首頁、summary、calendar。assertEqual(summary.total_cents, sum(day.cents))；分類spent_cents對應既有明細來源金額；他人摘要不變。刪除保留原row voided=1，不執行實體DELETE。延伸既有 `WebExpenseNavigationTests.test_soft_delete_updates_search_calendar_and_budget_totals`，不重寫操作系統。
- [x] 先加 `test_budget_sources_and_unlimited_history`：至少31筆有效消費（超過單頁／最近清單）加四來源，精確總額全部計入；其他使用者／voided／非消費／預測排除。非預算分類影響總額、不影響其他分類。執行新測確認所需新增欄位／HTML斷言確會在未實作版本 RED；不要編造已通過舊功能的RED。
- [x] 更新 `test_home_card_failure_preserves_quick_entry_draft`：摘要非法資料／讀取TypeError、ValueError保持固定安全錯誤、原400與五欄draft；不存在假0進度，不洩漏原例外。成功303後刷新GET多次不新增帳目；既有CSRF、外部user_id注入、import-boundary和OAuth回歸全部保留。
- [x] 每項新測RED後只補必要實作或測試整合，不擴張核心寫入／交易；新斷言若已由任務1–3滿足，回溯記錄對應前置RED證據，不為製造失敗破壞既有程式。
- [x] GREEN並完整驗證（以下命令本輪均已執行）：

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_spending tests.test_life_ledger_service -v
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
& '.venv\Scripts\python.exe' tests/run_discord_validation.py
& '.venv\Scripts\python.exe' -m ruff check .
git diff --check
git status --short
git diff --cached --name-only
```

- [x] 在一次性隔離帳本、測試settings與OAuth替身下，若可安全預覽才檢查320／375／768／1280px、橫向overflow、長分類名、原生details鍵盤focus、progress文字及超支；不讀.env或正式DB。不具備安全預覽時列未驗證。螢幕閱讀器／實體手機／真OAuth／正式並行另列，不假借HTML斷言宣稱實測。

## Task 5：實作後交付文件與版本

**Files:** `README.md`、`CHANGELOG.md`，必要時兩份規劃標記實作結果，不修改其他歷史記錄。

- [x] 先讀當時最新版；若仍0.11.16，完成後依AGENTS升0.11.17，否則從當時實際版升一修訂版。README同步，不提前寫死版本。
- [x] 記錄精確摘要欄位、總／分類預算視覺化、停用／未設定／超支狀態、無schema／依賴／啟動升級變更、當輪實際測試數與未驗證事項；不把搜尋修版、設定動畫或其他延後事項寫成完成。
- [x] 最後查diff空白／連結／版本、Git status與cached diff，回報保留原有修改與未stage/commit/push。若原index非空，只證明未改index，不替使用者清除。

## 本輪實作與驗證紀錄

本計畫產品範圍已完成。開始時分支`codex/localhost-web-auth`、HEAD`b775b76`、版本0.11.16及45項未提交內容已核對並保存；實際路徑為既有`.worktrees/localhost-web-auth`，沒有重建。版本升至0.11.17，所有無關內容與原index保留。

| 任務 | 實際結果 |
| --- | --- |
| 0 | 既有核心／Service／Web基準134項通過，使用者確認方案A |
| 1 | 四項新測先RED：缺少cents、零除錯誤及負預算未拒絕；補最小核心欄位後核心／Service37項通過 |
| 2 | 四項新測先RED：缺helper／分類context及日期仍採Web時鐘；補呈現helper與核心日期後首頁／記帳33項通過 |
| 3 | 三項HTML新測先RED：缺progress／details／空狀態；實作後首頁卡18項通過。零消費測試先調高總額以遵守既有分類預算合計限制，核心規則未改 |
| 4 | 兩項新增整合覆蓋新增、三次更改含跨月及軟刪除、全來源與超過31筆；使用任務1–3已示範RED的cents／context／HTML，不為製造RED破壞既有寫入。操作紀錄斷言含原新增，共五筆；相關147項、完整隔離312項及Ruff通過 |
| 瀏覽器 | 一次性帳本與OAuth替身；320／375／768／1280px收合／展開無新增橫向溢出，八項完整清單、長名稱、停用／零值／超支、Enter／Space及focus檢查通過 |
| 5 | README／CHANGELOG0.11.17、規格／計畫完成狀態、文件連結與差異／空白檢查通過；無stage、commit或push |

本輪新增13項測試；資料庫均在temp隔離，完整驗證器禁止正式DB連線。Ruff未使用修正模式；換行處理僅限本輪新改區塊，保留原有內容。技能進度與基準放在TEMP，避免Git bookkeeping造成index／ignore變更。

獨立唯讀審查通過，未發現需修正問題；審查者另跑核心／Service／首頁卡57項隔離測試通過（屬既有312項的子集）。最後核對：本輪11檔變更、34個無關未提交檔案逐位元不變，45項status、index與HEAD皆維持原狀；22個本地文件連結、版本與差異空白檢查通過。隔離瀏覽器視窗已還原、預覽服務及頁籤已關閉；分支與worktree保留。

需求覆蓋自查：

- 規格第5節四種預算與支出狀態 → 任務1–3；無總額／獨立分類／非預算分類 → 任務1、2、4。
- 全來源／跨使用者／voided／不限筆數 → 任務1、4；停用分類 → 任務1–3。
- 金額與比例精度／零值／原生accessibility → 任務1–3；CRUD與月曆一致 → 任務4。
- scope無新增依賴／SQL／寫入／抽象，Service簽名一致、cents欄位一致，計算與模板分離；交易仍由既有核心承擔。
- 分類呈現A已由使用者確認並實作；沒有未決產品選項。真OAuth、外部瀏覽器、螢幕閱讀器、實體手機及Web／Bot共用正式帳本未驗證；兩讀取不保證並行一致快照。設定動畫、搜尋頁手機超寬、預算週期與儲蓄目標仍延後。
