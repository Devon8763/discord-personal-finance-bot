# localhost Web v1 CSV 消費帳目匯出實作計畫

> **執行狀態：已完成（0.11.18，2026-09-29）**。使用者已另行授權，依 `superpowers:executing-plans` 與TDD逐項執行；下方checkbox記錄實際完成內容，沒有stage／commit／push。隔離驗證與未驗證環境見文末。

**Goal:** Web搜尋涵蓋四來源有效生活消費，並讓登入本人從設定頁按日期範圍下載完整安全CSV；Discord搜尋及更改規則原樣，不要求功能同步。

**Architecture:** Web session → 薄Service → 核心共享完整範圍清單，Web搜尋帶keyword、CSV不帶keyword且不限筆數。抽既有月清單query補必要跨月及字面關鍵字條件，舊manual search_expenses不改。設定頁原生收合入口指向獨立日期表單，標準庫csv／Decimal記憶體產生，空結果安全HTML。

**Tech Stack:** 現有FastAPI／Jinja／unittest／TestClient，標準庫csv／io／Decimal／unicodedata；無新增依賴。

**Spec:** [CSV消費帳目匯出設計規格（已實作）](../specs/2026-09-28-web-v1-csv-expense-export-design.md)。日期2026-09-28、基準版本0.11.17、HEAD b775b76。

## Global Constraints

- 固定既有 `.worktrees/localhost-web-auth`／`codex/localhost-web-auth`，先讀 [AGENTS.md](../../../AGENTS.md)；保存所有既有修改，不重建、reset、rebase、清理或提交。
- 本輪已獲實作授權，完成規格介面與測試，README／CHANGELOG同步0.11.18；以下命令已在隔離環境執行，結果另記於文末。
- 不讀或寫正式data.db、Token、.env、備份、既有匯出檔或真實使用者資料。測試只在TemporaryDirectory的patch DB初始化，OAuth替身不連真Discord，不偽造Cookie。
- scope固定本人、已入帳、有效、voided=0、consumption，含四來源；不從搜尋結果／畫面分頁決定資料。不另建SQL／架構／依賴／資料表／CSV匯入。
- 已確認入口只在設定頁第四個「資料匯出」details預設收合，內含說明及CSV匯出連結；首頁／搜尋頁不加入口，不加動畫／新的設定section參數。
- Web優先、App未來再考慮；Discord維護既有功能、停止新增且不要求同步，必要資安／資料安全修正除外。不預建App架構、依賴或認證，不批量改寫歷史規劃。
- 舊核心／Service search_expenses及Discord文案、三欄、日期、分頁、manual更改guard保持原樣；recent_expenses的manual限制維持。Web搜尋只接新完整範圍，原條件／排序／顯示限制不變，可見範圍不等於修改權限。
- 五欄順序「日期／金額／消費項目／分類／付款方式」，排序spent_on DESC,id DESC；BOM、CRLF、固定ASCII檔名、no-store；金額無千分位／元／防護前綴。
- TDD每任務先寫指定失敗測試、實跑RED、最小實作、GREEN。不可用環境錯誤假裝RED，不停用／放寬既有測試。禁止stage／commit／push，無自動Git操作步驟。

## Review Focus

| 容易漏掉的情況 | 驗證與任務 |
| --- | --- |
| 新完整範圍誤改Discord僅手動搜尋／更改 | 任務1保留舊介面、任務5回歸原範圍／6筆分頁／文案／修改guard，不擴權 |
| Web搜尋／CSV資格相同卻被頁面限制截斷 | 任務1／2／5測同日期items、起迄／同日順序、完整10001筆，不用搜尋頁slice |
| TAB／NUL／零寬字元後有公式，只有引用仍不安全 | 任務3對三文字欄位測Cc／Cf及單引號，數值不變文字 |
| CSRF拒絕仍查帳、空結果變錯誤或partial download | 任務4零查詢、200空HTML、完整序列化後才attachment；設定入口預設收合 |
| 舊備份限制被移植、歷史快照被改或留下檔案 | 任務5完整10001筆、snapshot／禁止寫檔／原歷史名稱 |

## 本輪修改檔案與介面

| 修改檔案 | 最小職責 |
| --- | --- |
| `spending.py` | 抽現有list清單查詢、增加完整跨月／可選keyword入口，舊manual search不改 |
| `life_ledger_service.py` | 新 `list_expenses_in_range` 薄轉交／本人ID正規化／領域dict |
| `tests/test_search_expenses.py` | 保留原manual期待，僅補必要新／舊介面共存回歸；Discord三欄／日期／6筆分頁／手動修改及拒絕自動不改 |
| `tests/test_spending.py`、`tests/test_life_ledger_service.py` | 共享scope／日期／原介面相容與轉交 |
| `web/routes.py` | Web搜尋接新facade、兩個受保護匯出路由、日期表單、局部CSV與文字安全helper |
| 新 `web/templates/export.html` | 警告、兩日期、下載／空／錯誤及返回入口 |
| `web/templates/settings.html` | 增加第四個資料匯出details／說明／入口，預設收合，原三區塊不改 |
| `tests/test_web_auth.py` | Web搜尋全來源、設定四區塊及入口位置、WebCsvExportTests安全／下載 |
| `README.md`、`CHANGELOG.md` | 同步0.11.18的實際完成內容、驗證與限制 |

CSS優先沿用entry/search-form及settings-section樣式，**預計不改web.css**；不修動畫／搜尋超寬。首頁和搜尋模板不新增入口，不改dashboard、backup_bundle、life_privacy、schema、db、auth、settings、requirements。`tests/test_write_reliability.py`是舊搜尋直接呼叫者，必須執行回歸，預計不修改其rollback測試。所有呼叫者影響詳見規格第2節，只有Web搜尋轉用新介面。

## Task 0：已確認產品方向與實作基準

- [x] 已取得本輪實作授權；設定頁入口、Web四來源、Discord不跟進新功能均已確認，不再要求入口二選一或Discord自動結果互動決定。其他CSV設計保留。
- [x] 核對規格第2節所有搜尋呼叫者；舊manual search與更改保持原樣，新完整範圍只接Web搜尋／CSV。若實際呼叫者另有業務衝突，先列出供確認，不自行改Discord或新增模式開關。
- [x] 重新讀分支／版本／status／cached diff及規格，保留未提交內容；建立當輪隔離測試基準，不引用既有CHANGELOG測試數。
- [x] 若實作時的現有query／介面已改，不自行另建範圍規則；先說明差異並調整計畫取得確認。

## Task 1：完整範圍共用核心與薄 Service

**Files:** `spending.py`、`life_ledger_service.py`、`tests/test_spending.py`、`tests/test_life_ledger_service.py`，以及任務5新／舊介面共存測試 `tests/test_search_expenses.py`。舊搜尋及Dashboard只讀核對，不改行為。

**Interfaces:**

- consumes：既有 `list_expenses(user_id, month, include_voided=False, limit=None, offset=0)`、`month_date()`／`next_month()`、`parse_search_date(value,label)`、舊搜尋的instr/lower字面比對語意。
- produces private：`_list_expenses_between(user_id, start: date | None, until: date, include_voided=False, limit=None, offset=0, *, keyword="") -> dict`；until exclusive，抽出既有COUNT／SELECT／scope／order，只補可選下界及參數化keyword，月入口原驗證順序與參數不變。
- produces public core及同簽名Service：`list_expenses_in_range(user_id, start=None, end=None, *, keyword="") -> dict`；回items／total，無limit／offset或來源模式。None start無歷史下界，None end核心今天；提供的日期須嚴格ISO、存在、非未來且start<=end，空字串無效。keyword.strip及字面用途子字串語意不變。
- façade只做`_user()`、核心轉交、items逐列`_expense()`，不寫SQL／驗證日期／交易。核心清單允許沒有keyword／start，不代表Web可放寬空白初始／end單獨拒絕規則；CSV仍要求兩日期。

- [x] 先新增 `SpendingTests.test_expense_range_reuses_scope_order_and_boundaries`：核心今天2026-09-24，8/31、9/1及9/24都含，8/30不含，同日id倒序；四來源均入列，他人／voided／income／transfer／investment與未入帳預測排除。新入口完整月與舊list items相等，舊include_voided／limit=2／offset=1保持。
- [x] 新增 `test_expense_range_optional_bounds_and_keyword`：keyword單獨跨月歷史，keyword+end無start，start至核心今天及完整範圍；普通關鍵字／大小寫／引號／%／_沿用literal substring而非LIKE，不符合關鍵字或日期者排除。空keyword+無下界是完整有效歷史，不使用recent或monthly迴圈。
- [x] 新增 `test_expense_range_rejects_invalid_dates`：空字串／斜線／basic／非補零／不存在／未來／倒序在查詢前拒絕；None可選，同一天／閏日／跨年兩端inclusive。月入口既有月份能力不受新公開入口的未來日期限制影響。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_spending.SpendingTests.test_expense_range_reuses_scope_order_and_boundaries -v`，確認新入口不存在而失敗。
- [x] 實作前一併新增任務5的共存回歸，執行 `& '.venv\Scripts\python.exe' -m unittest discover -s tests -p test_search_expenses.py -k full_range_query -v`，確認缺少新查詢的RED；核心／Service完成後重跑GREEN，任務5再作最終Discord回歸。
- [x] 最小實作：抽現有list SQL至共享helper，月入口仍傳月起／下月起；新入口用既有parse_search_date並要求ISO等於原值，end+1天作until、include_voided=False、不限筆數。不要複製查詢或移除舊search的manual條件；不引入來源切換、通用框架／sync。
- [x] GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_spending -v`，原清單及新範圍都通過。
- [x] 先新增 `LifeLedgerServiceTests.test_range_facade_normalizes_owner_and_passes_domain_rows`：mock核心，assert_called_once_with('42','2026-08-31','2026-09-24',keyword='午餐')，另驗證None／預設keyword轉交；cents／status／origin及total保留，隔離真資料他人不入列，核心例外原樣。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_life_ledger_service.LifeLedgerServiceTests.test_range_facade_normalizes_owner_and_passes_domain_rows -v`；實作薄轉交後以 `& '.venv\Scripts\python.exe' -m unittest tests.test_life_ledger_service -v` GREEN，保留舊search façade測試、無Discord／Web／db／SQLite import。

## Task 2：Web 搜尋接入完整範圍，保留既有操作

**Files:** `web/routes.py`、`tests/test_web_auth.py`。不改Discord、搜尋模板或舊search介面。

**Interfaces:** `search_page()` 只將資料呼叫改為 `life_service.list_expenses_in_range(user_id, start, end, keyword=keyword)["items"]`。user_id仍只取session；既有strict ISO／空白不查／至少keyword或start／end單獨拒絕／draft／安全錯誤／排序與現行顯示限制不變。不在路由過濾source、寫SQL或逐月查詢；查詢可見不改Web既有revision／自動日期唯讀或Discord僅手動更改權限。

- [x] 先補 `WebSearchTests.test_search_all_sources_keeps_keyword_date_and_identity`：OAuth替身及temp DB同關鍵字的手動／固定／訂閱／分期皆顯示；他人／voided／非消費／預測排除，query user_id無效。不同keyword／日期外不顯示，原安全欄位／edit連結與排序保留，不新增來源提示或操作。
- [x] 補keyword-only全歷史、start至今天、keyword+end、完整範圍；未登入／空白初始／end單獨／無效或未來／倒序均不查新Service。TypeError／ValueError固定安全訊息，原草稿／XSS與import boundary保持。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSearchTests.test_search_all_sources_keeps_keyword_date_and_identity -v`，舊manual查詢缺少三種來源。
- [x] 最小實作只改呼叫與items取值；針對Web搜尋的mock斷言改指向新facade，不更改舊Service搜尋測試或Discord期待。保留既有GET條件與日期正規化，不新增顯示上限。
- [x] GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebSearchTests -v`，再跑完整Web測試以確認OAuth／CRUD／草稿等不變。
- [x] 補 `test_web_search_and_csv_range_share_eligibility_not_page_limits`：相同起迄、空keyword時Web所用新query與CSV所用新query items ID順序相同；keyword為同範圍匹配子集。limit=2月清單或Discord6筆呈現不影響新query完整筆數；不要拿舊manual搜尋要求四來源相等。

## Task 3：純CSV序列化與文字副本防護

**Files:** `web/routes.py`、`tests/test_web_auth.py`。測試類別 `WebCsvExportTests(_WebLedgerTestFixture, unittest.TestCase)`。

**Interfaces:** consumes items的spent_on／cents／note／category／payment_source_name與`_format_amount(value, grouping=False)`；produces `_csv_safe_text(value: str) -> str`、`_expense_csv(items: list[dict]) -> bytes`。只序列化，不查Service、不寫檔，不含Request／身分解析。

- [x] 先新增 `test_csv_exact_amount_bom_and_csv_escaping`：input cents100000／10050／10025／29→csv.reader金額['1000','100.5','100.25','0.29']；表頭五欄精確，payload.startswith(b'\xef\xbb\xbf')且第一欄不是含BOM的日期。中文、逗號、雙引號、內嵌CRLF經csv.reader恢復副本原文；無id、revision或額外列。
- [x] 新增 `test_csv_formula_prefixes_preserve_original_and_numeric_fields`：表格測=、+、-、@，普通前導空白，TAB／CR／LF／NUL／C1／BOM／零寬字元後公式、控制前綴後普通字串；三文字欄各預期單引號+原文，原items不變。普通中文／引號／逗號／已'開頭不重複prefix，日期與金額仍純數值字串。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCsvExportTests.test_csv_exact_amount_bom_and_csv_escaping -v`，helper不存在而失敗。
- [x] 最小實作：StringIO(newline='')／csv.writer預設excel dialect；用Decimal(cents)/100與既有無grouping formatter。三文字欄用規格第5節判斷前導空白／Cc／Cf，不剝除原文，危險才prefix；完成後encode utf-8-sig一次。不可呼叫backup exporter或自己拼欄／行。
- [x] GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCsvExportTests -v`；此時只放本任務測試，沒有尚未實作路由的case。

## Task 4：設定頁入口、登入匯出頁與安全下載

**Files:** `web/routes.py`、`web/templates/settings.html`、新`web/templates/export.html`、`tests/test_web_auth.py`。首頁／搜尋模板不改。

**Interfaces:**

- consumes：`auth.current_user_id()`／`validate_csrf()`、`_urlencoded_form()`、`_search_date()`、core today／任務1 range façade／任務3 CSV helper；CSV呼叫不帶keyword或limit，兩日期必填。
- produces routes：`GET /export`、`POST /export/csv`；不加入JSON／query篩選／新CSRF實作。
- produces局部回填：`_export_response(request: Request, values: dict[str,str], *, error: str | None = None, empty: bool = False, status_code: int = 200) -> HTMLResponse`，csrf取既有session，模板autoescape，所有response no-store。

- [x] 先新增 `test_export_page_defaults_warning_and_login`：未登入GET／POST403，range façade零次且無內容；登入GET不查帳，兩日期預設2026-09-01／24，required date input、CSRF、未加密／非完整備份／文字單引號說明。query user_id／start／end不改預設。
- [x] 先新增 `test_export_entry_is_only_in_collapsed_settings_section`：登入/settings有第四個data-settings-section="export"，summary精確「資料匯出」，無open、有說明與href=/export；首頁與已登入/search沒有/export或/export/csv入口。既有預算展開、另三區塊不變、設定POST成功／錯誤與draft維持。把原 `test_home_links_to_settings_and_page_has_three_sections` 名稱／區塊斷言最小調整為四區塊，不改舊三區塊安全驗收。
- [x] 先新增 `test_csv_download_headers_and_session_scope`：POST正確CSRF+日期→200，固定attachment filename、Content-Type、no-store，range一次session本人+日期。form/query含另一user_id、limit=1、path與filename無效，回完整本人資料且不落檔。
- [x] 先新增 `test_export_rejects_csrf_and_dates_without_query`：缺少／錯誤／重複／非ASCII CSRF403；非URL-encoded解析為空form，故403；有有效CSRF的缺少／重複日期、無效／未來／倒序400。全部服務零次、session有效。固定錯誤，HTML保留可解析單值draft、注入值跳脫。
- [x] `test_export_empty_and_provider_errors_are_safe`：空items→200HTML固定提示，日期保留、noattachment、不稱失敗。ValueError／TypeError→400，SQLiteError→503，不含原SECRET／SQLite／路徑／Discord ID，no-store與表單回填。CSRF拒絕無範圍／帳目洩漏。
- [x] 在路由實作前一併新增任務5列出的完整10001筆、歷史快照、snapshot與禁止寫檔測試；先用小型範圍執行新測確認缺少路由的RED，再讓完整資料案例在實作後驗收，不為製造RED改壞既有核心。
- [x] RED：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCsvExportTests.test_export_page_defaults_warning_and_login -v`，路由404等預期缺口。
- [x] 最小實作兩路由：身分先驗、URL-encoded單值csrf先驗（isascii），日期兩個恰一／非空，用既有嚴格helper並檢查順序，然後新Service。空結果HTML；有資料先完整序列化後Response attachment；不PRG到GET條件、不接受檔案路徑、不增加日誌／sync／來源初始化。錯誤只catch既有預期型別，固定訊息，不把異常當空結果。
- [x] 實作settings.html第四個獨立details預設收合，沿用settings-section／summary樣式，加入「CSV未加密，是分析用帳目資料，不是完整備份，也不能完整還原帳號」與入口；不加新SETTINGS_SECTIONS值、動畫或展開偏好。先跑入口新測確認缺少區塊RED，最小模板後GREEN。
- [x] 匯出模板沿用表單CSS；GREEN：`& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth.WebCsvExportTests tests.test_web_auth.WebSettingsPageTests -v`。首頁／搜尋原入口保持，搜尋條件規則不改。

## Task 5：大於舊限制、無修改／無留檔與完整回歸

**Files:** `tests/test_web_auth.py`、必要新增回歸於 `tests/test_search_expenses.py`；只修前4任務產生的問題，不改Dashboard行為。

- [x] 執行任務1先寫的 `SearchTests.test_full_range_query_keeps_discord_manual_search_and_edit_rules`：隔離同日四來源，新range全含、舊spending／Service search只含manual；原 `test_only_own_valid_manual_consumption_and_read_only` 不更名／不放寬。保留私密結果／三種日期格式／literal keyword／6筆分頁／有效手動消費文案；手動編輯仍正常，他人／stale／非manual仍被guard拒絕。最後再次執行舊全套搜尋／更改測試；不為製造RED改壞既有Discord。

- [x] 執行任務4先寫的 `test_export_over_previous_limits_is_complete_and_read_only`：於temp帳本造10001筆本人有效消費（含邊界／四來源），另外他人／撤銷及未入帳預測；csv.reader精確10001資料列、順序對核心結果，無截斷、無備份8MiB／10000限制移植。使用temp-only transaction批次造資料，不污染正式DB；不拿Web或CSV列表再算預算。
- [x] `test_export_keeps_history_snapshot_and_creates_no_files`：先建付款來源、記帳後改名與停用，CSV仍歷史名稱；記錄業務表與sqlite_sequence snapshot，GET表單／POST下載／空結果／錯誤前後一致。先經POST完整登入後清除測試呼叫記錄，避免 `_csrf_token()` 經首頁原初始化混入匯出證據。
- [x] 替身禁止開檔寫入／tempfile寫入，patch sync_recurring及付款初始化為遇呼叫即失敗；只在一次性測試目錄核對沒有新CSV或其他檔案，不掃正式exports。Web helper不import backup／life_privacy、Web route仍只有Service資料入口。
- [x] 任務1–4GREEN後執行上述整合新測，若出現本輪問題先依證據修正，保留OAuth／CSRF／budget／calendar／CRUD／boundary及Discord搜尋／更改回歸。相同範圍、空keyword的Web新查詢與CSV資格一致，舊Discord manual查詢是刻意不同的相容契約；呈現分頁／limit不影響CSV總筆數。隔離生成10001筆量測時間／bytes及可取得的記憶體證據，如實回報；未量測不得聲稱大量效能充分。需上限才停下提出明確全份拒絕方案供確認。
- [x] 完整隔離驗證命令（本輪已執行）：

```powershell
& '.venv\Scripts\python.exe' -m unittest discover -s tests -p test_search_expenses.py -v
& '.venv\Scripts\python.exe' -m unittest discover -s tests -p test_write_reliability.py -v
& '.venv\Scripts\python.exe' -m unittest tests.test_spending tests.test_life_ledger_service -v
& '.venv\Scripts\python.exe' -m unittest tests.test_web_auth -v
& '.venv\Scripts\python.exe' tests/run_discord_validation.py
& '.venv\Scripts\python.exe' -m ruff check .
git diff --check
git status --short
git diff --cached --name-only
```

- [x] 僅安全temp帳本／OAuth替身預覽才查入口、日期、空狀態及下載；Excel若另有授權及安全測試副本才檢查中文／金額／公式前綴，不能以csv.reader測試宣稱Excel相容。本輪及未做的真OAuth／正式DB／真並行／Excel各列未驗證。

## Task 6：實作後文件與版本

**Files:** `README.md`、`CHANGELOG.md`。

- [x] 完成實作與驗證後重新核對最新版；若仍0.11.17，依AGENTS升0.11.18，否則從實際最新版升一修訂版。README同步，說明設定頁匯出入口、CSV非加密／非備份、文字單引號／完整範圍，以及Web搜尋四來源；Discord搜尋／更改維持原樣，不宣稱同步新功能。
- [x] 記錄實際完成介面、無schema／依賴／正式初始化變更、實際測試數與性能證據、未驗證Excel／外部瀏覽器／正式帳本，不把匯入、完整備份或動畫寫成完成。
- [x] 最後核對文件連結／空白／版本與Git status／cached diff；保留其他未提交內容，不stage／commit／push。

## 文件自查與本輪驗收

自查：Web優先／所有呼叫者／Discord保持原樣→任務0、5；完整範圍核心／Service→任務1；Web四來源／keyword／日期／隔離及CSV資格一致→任務2、5；金額／BOM／公式防護→任務3；設定第四區塊／入口位置／session／CSRF／header／空與錯誤→任務4；無寫入／留檔及性能→任務5。順序為完整範圍→Web搜尋→CSV入口／下載→Discord回歸，不調換。新公開介面簽名與items／total一致，不新增泛用框架或來源模式。

入口位置、Web搜尋資格與Discord維護方向已確認，已移除放寬共用舊search、修改Discord文案及自動結果互動待確認安排。可見範圍不改更改權限；本輪實作授權已取得。其他CSV設計不變，沒有隱藏截斷、未定義介面或留待猜測的安全策略。

## 實際驗證紀錄

- 基準：實際工作樹為 `C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`，分支codex/localhost-web-auth、HEAD b775b76、初始0.11.17，47項既有未提交內容；相關147項、Discord搜尋6項通過。實作後版本0.11.18，13檔本輪修改（含新模板），其他36項既有內容逐檔雜湊保存，HEAD與index不變。
- TDD：3個核心範圍、1個新舊共存及1個Service測試先因缺少介面失敗；2個Web搜尋先因欠缺自動來源／歷史範圍失敗；2個CSV純序列化先因helper不存在失敗；設定入口／新路由先404或缺少區塊，其餘安全／完整性測試均在路由實作前寫入。完成後新增16項全部通過，沒有停用測試或放寬安全期待。
- GREEN：核心／Service／Web162項、Discord搜尋7項、寫入可靠性9項、完整 `tests/run_discord_validation.py` 328項；全部OAuth替身與隔離SQLite。完整數包含上述子集，不加總。Ruff、`git diff --check`、新模板空白、本地文件連結、版本與保存檢查通過；既有httpx棄用警告保留。
- 完整性／唯讀：10001筆包含四來源及兩端日期，另造他人／撤銷／非消費／預測驗證排除，與核心排序一致；snapshot含所有業務表與sqlite_sequence，禁止寫檔、同步規則或來源初始化替身通過。舊Discord手動搜尋／日期格式／私密回應／6筆分頁／更改guard與既有rollback回歸維持。
- 隔離HTTP／記憶體量測：10001筆508993 bytes／0.2646秒／tracemalloc峰值14091127 bytes；50001筆2588993 bytes／1.3453秒／70028214 bytes。確認完整筆數；只是合成資料與Python配置峰值，不是RSS、真下載耗時或正式帳本規模保證。仍採記憶體O(n)，無新列數上限。
- In-app瀏覽器375px：OAuth替身及一次性帳本完成設定→預設收合資料匯出→Enter展開→獨立日期表單→跨月實際下載→空結果提示與日期保留。只讀本輪新下載的五筆合成CSV，BOM／五欄／精確1000.5與0.29／中文引用／公式前綴／歷史付款名稱通過。新頁重用既有expense-panel修正8px溢出，未修改CSS或既有搜尋頁。
- 獨立唯讀審查：核對本輪13檔增量、完整規格／計畫及必要呼叫者，沒有Critical／Important／Minor發現；沒有另執行測試／瀏覽器，主執行最後完整328項與Ruff已核對。其他既有修改、延後UI、匯入／還原／App／Discord新功能不納入本輪；分開讀取COUNT／items及O(n)記憶體維持已確認限制，無需擴張範圍。
- 未驗證：Excel／其他試算表、真Discord OAuth、外部瀏覽器、螢幕閱讀器、實體手機、Web／Bot共用正式帳本及真並行。CSV不是加密資料或完整還原備份；COUNT／items各自讀取，不宣稱新跨查詢快照。
- 沒有讀寫正式data.db、Token、.env、備份、既有匯出或使用者資料；沒有依賴／schema／正式初始化、設定動畫、搜尋超寬或Discord新功能。重啟Web即可載入；沒有stage／commit／push。

### 執行判斷與最小補充

- 使用者明確禁止Git寫入；技能的工作區／提交輔助改為TEMP ledger與初始檔案基準，不操作index；唯讀CSV按已批准規格直接attachment、不採寫入PRG。代價是未提交增量仍須由使用者後續管理，沒有隱藏Git動作。
- 新匯出頁沿用預設panel曾在375px顯示client360／scroll368；只重用既有expense-panel後client375／scroll375。這是本輪新頁必要樣式相容補充，不改既有搜尋或動畫；若樣式未來變更需重新量測。
- 瀏覽器CSV核對使用核心既有分期項目「（第 1/2 期）」快照，修正測試核對腳本的裸名稱假設，沒有改產品來源名稱或CSV內容。
