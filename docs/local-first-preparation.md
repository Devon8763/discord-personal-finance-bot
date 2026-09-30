# Local-first 準備：架構與可移植性紀錄

目前補充至 0.13.0；核對日期：2026-09-30。這是現有 Python／SQLite 系統的整理與移植依據；另有獨立瀏覽器合成帳本驗證頁、完整備份互通核心及本機測試入口。**尚未完成整個網站 local-first、正式資料搬移或 App 相容**。

## 已完成：本機合成帳本儲存底座（0.13.0）

原生 IndexedDB 升級到版本 2，九類資料逐筆 store 加 `meta`；舊單一 store 在同一升級交易無損搬移。手動新增／更改／軟刪除只寫受影響列與操作紀錄，保留 revision、原子回滾與完整 JSON v1 備份／空白還原。Python 與瀏覽器備份上限同為 64 MiB、總列數仍最多 100000，不再限制 200 筆消費／1000 筆操作；舊程式不能讀取超過 16 MiB 的新版大檔。已用 20000 筆消費／50000 筆操作及精確容量邊界的合成資料實測，詳見[本機頁面驗證](local-first-browser-validation.md)。以下以舊版本標示的段落保留當時事實，其舊上限／store 描述不代表現況。此頁仍只供合成資料，未加密備份、可信固定來源、正式搬移與主要 Python Web 功能移植仍待處理。

## 已完成：本機驗證頁離線重開（0.12.1）

固定白名單 Service Worker 只快取 `/local-first/` 靜態頁面與必要程式，不接觸 IndexedDB 帳目，也不攔截 Python Web、OAuth、`/tests/` 或備份下載。快取完整且頁面受控制才顯示就緒；同瀏覽器同來源可在停止預覽服務後關頁重開。瀏覽器仍可能清除快取／帳本，未做離線首次安裝、PWA、正式資料保護或正式搬移；見[本機頁面驗證](local-first-browser-validation.md)。

## 已完成：本機合成帳本備份入口（0.11.28）

主驗證頁首次開啟空白帳本只讀取；明確選「開始記帳」才建立，或選 JSON 備份後完整驗證、預覽九類筆數、確認並於交易內重新檢查完全空白才還原。已有帳本可要求下載完整 UTF-8 v1 JSON；沿用現有保真格式、200筆消費／1000筆操作本機上限及空白目標原子交易，未加密且不保證瀏覽器實際保存下載檔。僅使用合成資料驗證，沒有正式帳本搬移、覆蓋／合併／清除、Web／Bot 入口、離線首次載入或 App 相容；詳見[本機頁面驗證](local-first-browser-validation.md)。

## 已完成：完整 JSON v1／IndexedDB 互通合成驗證（0.11.26）

- `local-first/backup.mjs` 以固定本機 lossless-json 4.3.1 保真解析／序列化；JSON 語法由官方套件處理，專案驗證精確欄位／型別／版本／上限與全域 ID、所有引用／before／來源及唯一標記。沒有 DOM／儲存／網路／時鐘；套件完整 MIT、來源／SHA-256 與原 UMD／source map 依[第三方授權](../THIRD_PARTY_LICENSES.md)保存，沒有 npm 專案或通用自製 parser。
- 九類原始資料全部保存，未來已存／撤銷／四來源、所有月份預算、override／停用、付款快照、規則／版本／捷徑、設定及操作順序不因本頁没有管理介面而丟棄。cents／ID 仍為字串，大 revision／periods／position 原生 BigInt，安全小整數才用 Number，再匯出維持 v1 JSON 整數 token；不攜帶原身分，不重建備份可用 ID。
- 沿用單 store／全快照交易；只對獨立 `-portable-checks` 空白目標還原，先完整驗證，readwrite 再確認沒有任何鍵，帳本與標記同交易且 complete 才成功。非空／預設／標記拒絕，無清除／覆蓋／合併；readonly 再匯出不初始化、補記或產生操作。200消費／1000操作上限不提高，另受原格式16MiB／100000列等限制。
- 只對欄位完整的舊本機 v1 狀態做唯讀空欄位補齊；成功手動操作才保存內部 v2，不清空、不丟歷史，不改 IndexedDB store／版本或 SQLite schema。非法備份缺欄位仍拒絕。手動 revision 比較／遞增支援精確 signed 64-bit；達最大值安全拒絕。
- Node16項、Python新增3項（含109種輸入與 Python 契約一致）、完整正式檔案防護隔離437項通過。In-app Browser 原生互通14組與同來源關閉重開通過；實際瀏覽器再匯出 → Python完整中立欄位／引用／順序相等 → 空白SQLite還原，摘要／預算／比較／撤銷及補記語意一致。審查找到相同值 duplicate key／未知 `__proto__` 被官方解析器略過，先RED再於既有深度檢查補鍵檢查，原套件不改；完整語法仍交給官方解析器。
- [互通介面、來源、開啟方式及驗證限制](local-first-browser-validation.md)詳列。0.11.26當時只有合成互通測試入口，主頁尚無備份匯入／下載；0.11.28已補本機驗證頁入口。自動來源仍只保存／讀取，沒有移植管理／入帳、預算／比較、undo介面或正式資料搬移。後續0.11.27已修正Python既存／還原的零元總預算與分類預算摘要，精確cents保留，使用率為None，零元跳過百分比提醒，Web與既有Discord摘要相容；新增／更改預算仍須正整數台幣。新增10項、相關269項與完整防護隔離447項通過，來源／還原摘要及歷史一致；0.11.27未重跑實際瀏覽器，備份v1／SQLite／IndexedDB不變。未驗證正式帳本／真OAuth／實體手機／其他瀏覽器／真正配額耗盡／離線載入。

## 已完成：獨立瀏覽器本機驗證頁（0.11.25）

- `local-first/` 以原生 JS 模組分離純金額／日期／手動驗證、IndexedDB 交易與 DOM 呈現；不需 Python 記帳 API 或 Discord 登入，不替換現有 Web／Bot。僅手動新增、有效清單、更改、摘要確認後 voided 軟刪除；精確 cents 字串／BigInt、付款快照、revision 與有序 before 操作紀錄。
- 本頁專用測試 DB／本機擁有者，首次原子建立一次，讀取不重建／不產生操作；交易 complete 才成功、寫入失敗回滾、舊 revision 拒絕。整份帳本簡化儲存上限 200 消費／1000 操作，有 ponytail 註解，沒有刪除歷史以騰出空間。Python、SQLite、OAuth、schema／啟動與舊案例不變。
- 9 項 Node 純規則先 RED 再 GREEN，11 組原生 IndexedDB 與6組實際頁面故障／安全驗證通過。原完整隔離434項、Ruff／JS語法／差異核對通過；實際同來源重整／關閉重開／多分頁衝突、桌面與375px子頁面、停止静態伺服器後已載入操作已測。真正斷網、完整網路追蹤、實體手機／跨瀏覽器／正式帳本未測。
- 使用者確認本頁拒絕 Python Decimal 極端上下文捨入／下溢的非整分輸入，Python保持原樣；一般科學記號與既有金額案例相容。當時缺少的完整備份大整數解析與空白還原已於0.11.26完成互通核心，本機測試備份入口於0.11.28完成；仍沒有正式搬移入口、undo介面、預算／比較移植、離線首次開啟／重載或PWA／App。
- [開啟方式、資料契約與實際驗證](local-first-browser-validation.md)記錄loopback來源、IndexedDB名稱／版本、測試隔離、上限、規則差異與未完成項目。本頁只可輸入合成資料，不能作為唯一正式資料來源。

## 已完成：本人生活帳本可移植備份核心（0.11.24）

- 新增本人 UTF-8 JSON 格式 life-ledger-backup／v1，精確 cents 使用十進位整數字串，消費／付款／規則／版本／捷徑／操作採備份內字串id。保存四來源、voided／revision、付款快照、已入帳月份、全部月份預算與歷史版本；before 快照剝除原身分並映射所有引用及操作順序。
- 同一讀取快照匯出，不補建預設／註冊／同步／寫檔；完整驗證後在原單一寫入交易重新確認空白目標，重建關聯，失敗完整rollback。空白表示目標12個生活資料表都無資料，包含預設付款與導覽／通知；投資與帳號安全狀態不變，沒有合併／覆蓋／替人清除。
- 必要設定只保存提醒門檻及記錄開始日，排除導覽、通知內容／去重狀態、AI同意與身分／憑證。還原不送提醒；後續重新計算可能再次提醒，未宣稱通知去重已搬移。舊ZIP的本月及未來預算攜帶規則完全不變。
- [格式與限制文件](portable-life-ledger-backup.md)詳列16MiB／100000列／12層／4096字元、欄位／型別／非法引用、空白定義、歷史相容及未加密注意事項。沒有使用者入口、Web／Discord改動、schema升級、正式備份／搬移或本機瀏覽器儲存；仍是Python／SQLite核心。
- 新增26項隔離測試，主流程與安全修正先RED再GREEN；相關279項、完整隔離434項通過（子集不另加總）。同基準摘要／預算／比較、再匯出中立內容、撤銷順序、補記不重複／不復活、故障rollback、交錯快照與兩連線空白競爭均核對；不等同正式帳本或瀏覽器／App驗證。

## 第一輪已完成整理（0.11.23）

- 新增單一 `life_ledger_rules.py`，只依賴標準庫，接收明確資料與基準日期：金額解析、月份／短月扣款日、預算輸入與合計限制、分類加總、比較期間／每日金額／差額與百分比。沒有 Request、session、Discord、HTML、資料庫連線或自行讀取今天。
- `spending.py` 保留既有公開函式與資料操作。比較仍先在同一 SQLite 讀取交易取得分類選項及 A／B 完整消費，再交給純計算；篩選、所有權、有效狀態與已入帳範圍仍由原查詢負責，不在純函式重新建立查詢規則。
- 共用驗證移除 Discord 指令提示；`presentation.discord_spending_error()` 由 `spending_commands.py`、`dashboard.py` 的 Discord 入口補回原提示。錯誤類型、既有 Discord 說明及 Web 固定安全錯誤保留，沒有新增錯誤代碼框架。
- 精確統計仍使用整數 cents／Decimal。原公開回傳內容與相容欄位保留；千分位、元、正負號、期間提示及圖表配置仍在原呈現層。Web／Service／HTML／CSS／JavaScript 沒有修改，0.11.22 的「每日支出比較」與操作流程不變。
- `tests/fixtures/life_ledger_rules.json` 是不含身分或真實資料的規則核對案例，涵蓋精確金額、短月／閏日、跨年、零／未來期間及預算限制；**不是備份格式或新儲存格式**。

## 實際呼叫邊界與尚未抽離的耦合

| 邊界 | 實際位置與呼叫者 | 本輪狀態 |
| --- | --- | --- |
| Web 平台／畫面 | `web/routes.py` 處理 HTTP、表單、session、CSRF、PRG、安全錯誤、CSV 與日期輸入；`_comparison_display()`／`_comparison_chart_data()` 格式化摘要與圖表資料；Jinja 模板產生頁面，本機 Chart.js 腳本只呈現彙總 | 原樣保留；模板仍需 Python 伺服器，不能直接作為本機靜態網站 |
| Discord 平台／畫面 | `spending_commands.py`、`dashboard.py` 處理指令、互動表單、訊息；`presentation.py` 補平台提示 | 共用規則不再要求使用 Discord，入口仍保留原指令提示 |
| 身分與轉接 | `life_ledger_service.py` 將已驗證身分轉為核心使用的字串；Web 只取 OAuth session，Bot 取作者身分 | Service 本身不提供登入驗證；現有 OAuth、所有權、CSRF 與 revision 保護不變 |
| 純規則與計算 | `spending.money()`／`month_date()`／`next_month()` 保留原名稱並重用純模組；`expense_comparison()` 取得完整資料後計算；`set_budget()` 與 `_post_recurring()` 重用預算／扣款日規則 | 可不啟動 Web、不初始化 SQLite 獨立測試，仍是 Python 函式 |
| 儲存與操作 | `spending.py`／`db.py`／`ledger.transaction()`／`schema.py` 負責 SQL、交易、唯一約束、revision、軟刪除、操作紀錄、規則版本及補記；交易模組仍有投資核心依賴 | 未搬動 SQL、未新增抽象層或 schema；不等於已建立平台共用儲存 |

需要繼續處理的具體耦合：

- `spending.payment_sources(initialize_defaults=True)` 是會建立預設付款方式的讀取介面；Web `_quick_entry_context()`、`_settings_context()` 使用預設行為。部分其他入口明確傳 False。不能宣稱所有 GET 都唯讀；比較頁不呼叫此初始化或補記。本輪沒有改動這個既有行為。
- 共用核心的 `register()` 仍建立付款方式／使用者資料。提醒與通知文字仍由核心產生並儲存；部分舊報告／摘要／固定負擔回傳 float 顯示相容欄位及文字，未全部改為中立資料。`spending.trends()` 的舊「月初一次列入」說明仍待校正，實際入帳已依 due_day；本輪不順便改原畫面文字。
- 核心 `spending.today()` 採 UTC+8；比較入口只取一次核心今天並傳入純計算。其他舊流程仍自行取時間，Web 也保留 `taiwan_today()`／月份工具及日期驗證；本輪沒有全面統一。核心搜尋接受既有三種日期寫法，Web 的 ISO 日期表單契約未改。
- `spending.py` 仍依賴 SQLite／交易及 Python；舊 `sp.Decimal` 匯出由 `backup_bundle` 使用，保留相容。比較百分比是 Decimal，不能把完整結果直接當成跨平台 JSON。

## 目前生活帳本資料

下列來自程式的 schema／升級欄位，不是讀取正式帳本。`user_id` 是字串，數字 id 是 SQLite AUTOINCREMENT；表間多為核心維護的邏輯關聯，沒有 SQL 外鍵約束。

| 資料表 | 實際欄位與關聯 |
| --- | --- |
| `expenses` | `id, user_id, spent_on, cents, category, note, source, recurring_id, period, voided, payment_source_id, payment_source_name, kind, revision`。分類以名稱關聯；付款方式 id 可空、名稱保留歷史快照；自動消費關聯規則 id／月份，`UNIQUE(recurring_id, period)` 包含已撤銷資料 |
| `spending_categories` | `user_id, name, active`，本人＋名稱複合主鍵；核心預設分類不全存成資料列，以覆寫狀態合併 |
| `payment_sources` | `id, user_id, name, active`；本人＋名稱唯一。現金／未指定為保留選項；改名不回寫消費的付款名稱快照 |
| `budgets` | `user_id, month, category, cents`，三欄複合主鍵；「總額」為總預算，其餘為分類名稱；沒有該列表示未設定，不等於零元 |
| `recurring_expenses` | `id, user_id, name, cents, category, kind, start_month, periods, due_day, active, revision`；固定／訂閱／分期共用，分期才有有限期數 |
| `recurring_expense_versions` | `user_id, recurring_id, effective_month, name, cents, category, due_day`；規則＋生效月份主鍵，目前保存固定來源版本，歷史月份取不晚於該月的最後版本 |
| `expense_actions` | `id, user_id, expense_id, before_json, undone`；新增的 before 為 JSON null，其他操作保存原資料；既有撤銷取本人最近未撤銷操作，確認最新操作 id，還原時遞增帳目 revision |

本輪已確認提醒門檻與記錄起點保存、導覽與通知排除；相關表為：`spending_settings(user_id, levels)`、`spending_users(user_id, started)`、`spending_onboarding(user_id)`、`spending_notices(id, user_id, notice_key, body, delivered)`、`spending_shortcuts(id, user_id, name, category, payment_source_id, note, cents, position, active)`；同一 SQLite 還有投資／其他功能資料。

既有規則與狀態：

- 金額為台幣整數 cents；一般消費必須正數、最多兩位小數、單筆不超過十億元。`set_budget()` 的輸入為正整數台幣，不接受小數／bool；已設定總預算時分類合計不可超過總額。既有「依分類合計設定總額」契約保持原樣，未增加新的上限規則。
- 日期為 ISO 日／月字串，日期兩端包含。一般已花統計取本人 `kind='consumption'`、`voided=0`、日期不晚於基準日；來源 manual／固定／訂閱／分期均涵蓋，但有效性不是單靠 source 判定。收入／轉帳／投資與未入帳預測不納入。
- 已發生空期間是零支出；全未來期間為未開始，不能當成零或計差額。比較採同一篩選、整數 cents 合計、Decimal 百分比，以 B 為基準；B 零無百分比。每日序列不是累積值，未發生日期沒有假零。
- 固定／訂閱／分期依 due_day 到期才產生消費，舊未設定為 1 日、短月取月底；補記保留原定日期。固定規則修改下月生效、同月最後成功設定取代同一版本，歷史補記保留舊設定。既有已入帳資料不回寫；停用保留歷史，補記與停用仍在原交易。
- 軟刪除使用 voided 並保留帳目／操作紀錄，沒有 DELETE；revision 防止覆蓋較新修改。自動帳目唯一鍵包含撤銷資料，同步不會復活。單筆自動來源日期不可更改，不改規則排程；Web 沒有撤銷入口。
- 停用分類／付款方式保留歷史，不能當成一般新增選項；分類改名的既有交易會更新相關帳目、預算、規則版本及操作快照，付款方式快照策略不同，移植不能把二者當成相同關聯。

## 身分、前端與移植缺口：尚未實作

以下僅指現有 Python Web 的整體移植；獨立合成驗證頁已完成的本機測試擁有者、IndexedDB 與手動 JavaScript 規則除外。

- 現有 `user_id` 由 Discord OAuth session／Bot 作者提供；未有本機擁有者或帳本識別。未來需決定本機命名空間及舊身分映射，不能直接暴露／沿用 Discord 登入資訊作為必要本機身分。
- SQLite 數字 id 與 `recurring_id`、`expense_id`、`payment_source_id` 等跨表引用必須一起映射；分類名稱主鍵、版本月份、軟刪除與唯一入帳標記也需保留。不能只搬有效消費後讓已撤銷自動帳目重新產生。
- HTML／CSS、原生表單設計、Chart.js 本機資源與呈現用腳本可參考／重用，但目前資料、文字、安全 token 與頁面均由 Jinja／Python 提供。沒有瀏覽器儲存、免登入入口、第二套 JavaScript 規則、Service Worker、PWA 或 App 包裝。
- Python 純函式只是便於核對規則，瀏覽器不能直接執行。未來實作需對照現有 JSON 案例，決定日期基準與精確整數表示；多筆合計可能超過 JavaScript 安全整數，不能把 float 繪圖座標當儲存／計算精度。

## 完整備份與 CSV 的差別

| 現有入口 | 實際範圍與限制 |
| --- | --- |
| 新生活帳本 JSON v1 | 本輪已實作核心：本人完整定義範圍（含撤銷／revision／規則版本／操作／白名單設定），僅還原至空白目標；字串id及cents、無原身分，未加密，無使用者入口。詳見[格式文件](portable-life-ledger-backup.md)；非整台主機或全部使用者資料 |
| Web CSV | 五欄日期／精確金額／項目／分類／付款名稱，完整指定範圍有效消費；BOM／文字公式防護，未加密。沒有 id、規則／版本、撤銷帳目、revision、操作紀錄或設定；分析用，不能完整還原 |
| `backup_bundle`／`life_transfer` 帳號 ZIP v2 | 消費含日期、cents、分類、項目、付款名稱、來源、voided、kind，另含分類／付款／捷徑、當月及未來預算、選用投資；不保留原身分／數字 id／規則關聯／revision、固定規則及版本、操作紀錄、過去預算與提醒等。匯入是去重合併、名稱映射及保留目標既有設定，不是完整帳本原樣還原 |
| `service_safety.Safety` 主機 SQLite 維護備份 | 全資料庫快照與既有完整性／鎖定／復原防護，可能含其他人及投資資料；是主機維護用途，不能直接作為本人瀏覽器可移植備份。本輪未執行任何正式備份／還原 |

## 後續最小步驟：尚未實作

1. 本機合成測試頁已有備份匯入／下載入口；仍需獨立驗證正式 SQLite 資料搬移、正式檔案保管／還原、異常資料及失敗復原，不能以測試入口代替正式流程。
2. 預算、比較及自動來源管理／入帳仍未移植到瀏覽器；依原中立案例逐項核對，保留精確整數、歷史版本與撤銷標記，不能把「已保存」當成「已能操作全部來源」。
3. 離線載入／資料淘汰保護與瀏覽器相容另行驗證；目前只在同來源有上限的合成帳本通過持久化與互通，尚無PWA／App／同步。沒有正式搬移／備份驗證或唯一正式資料來源承諾。

## 第一輪（0.11.23）驗證

- 整理前相關隔離基準264項通過；新8項先失敗再通過，其中純規則6項可單獨執行，不需要 OAuth、SQLite 初始化或網站。新鮮 Python 程序也核對純模組未載入 SQLite、DB、Discord、Web、dotenv 或投資核心。
- 整理後相關核心／Services／Web／Discord／寫入可靠性291項通過；既有完整隔離驗證408項通過（子集不另加總）。保留原測試及正式檔案防護；歷史測試的互相匯入以暫存啟動器補 tests 搜尋路徑，未修改產品以處理執行環境。
- 核對66個原公開函式參數、SQL內容、16組整理前後完整比較物件及原月報／圖表結果一致；獨立唯讀審查另確認備份與快照相容。版本、原有內容保存、Ruff 與差異／空白檢查另記於 CHANGELOG。
- 全部是 OAuth 替身／隔離 SQLite 或純計算；本輪沒有瀏覽器／手機、真 OAuth、正式 Web／Bot 共用帳本、正式備份／搬移或瀏覽器／App 核心測試。沒有宣稱 local-first、離線使用或 App 相容已完成。
