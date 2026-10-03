# localhost Web v1 CSV 消費帳目匯出設計規格

狀態：**已實作並完成隔離驗證（0.11.18，2026-09-29）**。使用者另行授權依本規格與TDD計畫實作；設定頁入口、Web四來源搜尋與CSV下載已完成，Discord搜尋及更改規則保持原樣。

日期：2026-09-28。核對基準：既有 `C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`、分支 `codex/localhost-web-auth`、HEAD `b775b76`，README／CHANGELOG 最新版 `0.11.17`。使用者提供的 `DiscordBOT.worktrees` 路徑不存在，使用原 `.worktrees`，不建立新工作樹。

依據：[AGENTS.md](../../../AGENTS.md)；配套：[逐項實作計畫](../plans/2026-09-28-web-v1-csv-expense-export.md)。既有未提交內容保留；本輪只修改核心／Service、Web及其測試與版本文件，不stage、commit、push。核對基準為實作前0.11.17，完成版本為0.11.18。

## 0. 已確認產品方向

近期專注 Web，未來再考慮 App。Discord 保留現有功能、停止新增功能，不要求與 Web 同步；共用核心改動仍須保護既有 Discord 行為，必要的資安與資料安全修正不受停止新增功能限制。不為未來 App 預先增加架構、依賴或認證方式。此方向記錄於本規格與配套計畫，不批量改寫歷史文件。

## 1. 目的、入口與使用流程

使用者登入後，可按日期範圍下載本人已入帳有效生活消費，作為可讀取的帳目副本，而非完整帳號備份。保持 localhost-only、現有 OAuth、首頁記帳／預算／小月曆及搜尋／設定，不重做導覽。

**已確認入口：設定頁。** 在 `/settings` 的既有三個區塊之後新增第四個獨立原生 `<details class="settings-section" data-settings-section="export">`，`<summary>資料匯出</summary>`，預設不帶 open。區塊內提供「CSV 是未加密的分析用帳目資料，不是完整備份，也不能用來完整還原帳號。」及「CSV 匯出」連結，前往獨立 `GET /export` 選擇日期範圍。

沿用現有 details／summary 樣式、鍵盤操作及 focus，不實作動畫；初次設定頁仍預算展開、分類／付款方式與資料匯出收合。資料匯出是入口，不是設定寫入區塊，不增加 POST 操作或 `SETTINGS_SECTIONS` 值；既有設定成功／失敗的 budget/categories/payments 展開及草稿回填不變，新區塊也保持預設收合，不保存展開偏好。

**首頁與搜尋頁不新增匯出入口，也不繼承搜尋條件或導出畫面結果。** 使用路徑為首頁既有「設定」→「資料匯出」→「CSV 匯出」→獨立日期表單，不重做導覽。

`GET /export` 登入後顯示：

- 「CSV 消費帳目匯出」標題，開始與結束日期均使用 required 原生 `input type="date"`，預設核心台灣本月第一天至今天；有標籤、鍵盤焦點與返回首頁連結。
- 固定提醒：「CSV 是可讀取的帳目資料，未加密，也不是能完整還原帳號的備份。請妥善保存，勿分享給不信任的人。」另說明只包含有效生活消費、不含撤銷、設定或投資。
- 固定文字防護說明：「為避免試算表將文字當作公式，危險文字前會加上單引號；只影響匯出副本，不修改原帳目。不同軟體可能顯示這個單引號。」
- 送出按鈕「下載 CSV」，表單 action=`/export/csv`、method=POST；日期／CSRF 放 body，不從 query 取條件或身分。

`POST /export/csv` 成功且有帳目：直接 HTTP 200 attachment 下載。這是唯讀下載，不是寫入操作，不使用 303 到含條件／資料的 GET URL；重送只重新讀取，不重複記帳。下載是否完成由瀏覽器處理，不虛構「已成功儲存檔案」提示。

空結果：HTTP 200 HTML 重繪原表單，保留日期與 session，固定「此日期範圍沒有可匯出的消費帳目。」；不是失敗，不送出只有表頭的 attachment。無效輸入：HTTP 400、安全錯誤、保留可解析單值日期；重複／缺少欄位不選任意一值。CSRF：HTTP 403，session 保留且零查詢。未登入：HTTP 403 既有「請先使用 Discord 登入。」、不查 Service。

## 2. 既有程式核對與重用決定

| 既有介面／位置 | 核對結果 | 決定 |
| --- | --- | --- |
| `life_service.get_today()` → `spending.today()` | UTC+8 共用日期 | 預設日期取一次核心今天；不使用 OS 本地日期 |
| `web.routes._search_date(value: str)` | 嚴格 ISO、日期存在、不得未來，可回 None | 重用後要求兩日期皆非 None，再檢查 start<=end；不採搜尋頁可缺一日期規則 |
| `spending.parse_search_date(value, label)` | 核心支援三種日期格式、拒絕未來日期 | 新核心跨月入口重用解析，再要求輸入等於回傳 ISO，保留 Web 嚴格日期契約 |
| `life_service.list_expenses(user_id, month, include_voided=False, limit=None, offset=0)` | 本人有效 consumption，含所有來源，排序 spent_on DESC,id DESC；無 limit 可完整列月 | 唯一資料範圍／排序實作來源，最小擴充為跨月入口，不複製 SQL |
| `life_service.search_expenses(user_id, keyword='', start=None, end=None)` | 目前底層明確 `source='manual'`，只涵蓋手動 | 原核心與 façade 保留，繼續供 Discord 使用；Web 改接第3節完整範圍查詢，不放寬共用舊搜尋 |
| `backup_bundle.export_bundle()`／`life_privacy.export_life()` | 完整 ZIP 備份含設定、撤銷消費及可能的投資；直接讀資料且 life_privacy 含 Discord | 不呼叫、不 import 到 Web；不是本功能的資料來源 |
| 備份 CSV 序列化 | 標準庫 csv.writer、StringIO(newline='')、UTF-8 BOM；文字 lstrip 後公式開頭加單引號 | 重用現有做法，不動備份程式；Web 局部補足控制／格式字元前綴判斷 |
| `_format_amount(value, grouping=False)` | Decimal、整數去小數尾零，不加千分位 | 使用 Decimal(cents)/100，保留精確金額；不改資料或歷史付款名稱 |
| `_urlencoded_form(request)`／`auth.validate_csrf()` | URL-encoded、parse_qs、單值驗證模式已存在 | 重用；非 ASCII token 先拒絕，避免 compare_digest 例外 |

現有 `list_expenses()` 不支援完整跨月範圍；若逐月由 Web 收集再過濾，會把日期／排序規則移出核心，且多月份讀取有更多並行快照差異。因此建議只在核心抽出**原有清單查詢**供月清單及日期範圍共用，新增薄 façade，不另建匯出專用 SQL、搜尋擴充旗標或一般框架。

### 搜尋相容性：所有呼叫者與明確邊界

`spending.search_expenses()` 與原 Service 同名介面完全不改，保留 manual WHERE、參數、三種核心日期格式、排序與例外。Web 搜尋改接新的完整範圍 façade，保留字面子字串條件 `instr(lower(note),lower(?))`、日期、排序、表單及現有顯示限制；不在 Web 過濾來源或逐月拼接。Web 搜尋、月曆與 CSV 的**消費資格**一致，但查詢條件與呈現限制不同；Discord 搜尋仍維持手動相容契約。

| 已核對的呼叫者／測試 | 影響與保留行為 |
| --- | --- |
| `life_ledger_service.search_expenses()` | 原 `_user()`、簽名、手動範圍、領域 dict 與例外不變；新增同名以外的範圍 façade，不加模式切換 |
| `web/routes.py:search_page()` | 唯一既有搜尋呼叫者接入新 façade；session本人、三欄／strict日期、空白不查、固定錯誤／草稿／HTML及排序不變 |
| `dashboard.py:SearchExpensesModal.on_submit()` → `SearchResults` | 繼續呼叫舊搜尋；僅手動、三欄／三種日期格式、私人結果、排序、每頁6筆、文案及選單原樣；不修改 dashboard.py |
| `dashboard.py:SearchResults.edit()` | 原 manual guard、結果ID、本人與stale驗證不變，不新增來源提示／按鈕／指令 |
| `tests/test_search_expenses.py:SearchTests` | 保留 `test_only_own_valid_manual_consumption_and_read_only` 的原期待與query_only斷言；另驗證新查詢不影響Discord手動搜尋／修改回歸 |
| `tests/test_life_ledger_service.py`、`tests/test_web_auth.py` | 舊Service搜尋測試保留；新增範圍轉交與Web四來源／關鍵字／日期／身分隔離測試 |
| `tests/test_write_reliability.py` | 舊核心搜尋驗證失敗寫入無殘留；原空結果／rollback斷言不變 |

原始碼呼叫掃描未發現 `spending_commands.py` 直接呼叫 search_expenses；Discord搜尋入口由 Dashboard「帳目工具」處理。`recent_expenses()` 的 source='manual' 是最近再記／付款預選的另一用途，不是搜尋呼叫者，**不得一起移除**。

目前核心search無SQL LIMIT、Web搜尋無分頁；Discord SearchResults為6筆呈現分頁，不是只查6筆。這些現行限制／無限制均保留，不能憑使用者的「既有筆數限制」字樣新造limit；CSV也不讀 SearchResults 的頁面slice。

**可見範圍不等於更改權限。** Discord 保留僅手動搜尋，因此不會新增自動來源結果與修改選單的衝突，原先要求確認該衝突的安排取消。Web 原單筆頁已支援四來源，仍沿用本人、有效狀態、revision及自動帳目日期唯讀規則；Web搜尋多顯示來源不授權改變任何更改／刪除規則。

## 3. 最小介面補充與資料範圍

新增 `spending.list_expenses_in_range(user_id, start=None, end=None, *, keyword="")` 與同簽名 `life_service.list_expenses_in_range(...)`，供 Web 搜尋與 CSV **共用一個完整範圍查詢**。Service 只以 `_user()` 正規化 user_id，轉交核心並沿用 `_expense()` 補領域欄位；回傳 `{'items': list[dict], 'total': int}`，沒有 Discord／Web／CSV／SQL 或交易實作。keyword 是字面用途子字串條件，不是來源模式或複雜開關。

start/end 若提供必須是嚴格 YYYY-MM-DD、存在且不晚於核心台灣今天；空字串不當作有效日期。start=None 表示不設歷史下界，end=None 表示核心今天，起迄皆含且start不得晚於end。keyword先strip，沿用舊搜尋instr/lower字面比對語意，不換LIKE或新模糊規則。核心完整清單可沒有keyword／start，但Web仍保留「至少keyword或start、空白不查、end單獨拒絕」；CSV仍強制兩日期非空，且不接受keyword。這些UI輸入契約不可由核心預設放寬。

核心把既有 `list_expenses()` 的 SELECT／COUNT／scope／order 程式段移到 `_list_expenses_between(user_id, start: date | None, until: date, include_voided=False, limit=None, offset=0, *, keyword="")`。until exclusive；既有月入口傳月起與下月起，原參數、limit／offset驗證、include_voided及月曆行為不變。共享query只補可選下界及參數化keyword條件；新公開入口驗證日期後傳end+1天，固定include_voided=False、limit=None、offset=0。不複製清單SQL、不逐月拼接、不增加來源切換或泛用查詢框架，舊手動搜尋實作不動。

Web搜尋僅將原呼叫換為 `life_service.list_expenses_in_range(user_id, start, end, keyword=keyword)["items"]`，既有表單日期正規化與錯誤保持。CSV呼叫同一Service的 `list_expenses_in_range(user_id, start, end)`，不傳keyword或顯示限制。總消費資格由共享核心決定，不使用搜尋頁的結果、slice或修改權限作匯出範圍。

唯一資料口徑：session 本人、`kind='consumption' AND voided=0`、開始與結束日**皆包含**。不依 source 限制，因此包含手動／已入帳固定／訂閱／分期；排除他人、已撤銷、非消費、尚未入帳預測。只取資料庫已有帳目，不呼叫 sync_recurring，不查分類／付款來源重新補建預設。

排序固定 `spent_on DESC, id DESC`，與既有清單一致；同日不同帳目以內部 id 確定順序，但不把 id 輸出到 CSV。資料未變時相同範圍輸出可重現；其中 items 是一次完整 SELECT 的結果。COUNT 與 SELECT 仍沿用既有分開讀取，並行寫入時 total 可能不同，匯出只依完整 items，不據 count 截斷／迴圈分頁，不宣稱新全帳號交易快照。

身分僅取 `auth.current_user_id(request.session)`。GET 不使用 query 設定預設；POST 只讀兩日期與 CSRF，不接受 user_id、keyword、limit、offset、filename、path、next 或來源篩選作為有效參數。

## 4. CSV 資料契約

第一列固定：`日期,金額,消費項目,分類,付款方式`，共五欄，後續每筆同順序：

1. 日期：原 spent_on，YYYY-MM-DD。
2. 金額：整數 cents 經 Decimal 除100，使用現有 `_format_amount(..., grouping=False)`；`1000`、`100.5`、`100.25`、`0.29`，無千分位、元、NT$或浮點數／四捨五入。
3. 消費項目：原 note 的安全匯出副本。
4. 分類：原 category 的安全匯出副本，不排除已停用分類的既有有效帳目。
5. 付款方式：原 payment_source_name **歷史名稱快照**的安全副本，不用目前來源表名稱替換，不輸出付款ID。

不輸出 Discord ID、OAuth、email、expense_id、revision、origin、entry_type、voided、操作紀錄、JSON、設定、投資、系統路徑或 Token。不改原帳本、名稱快照或 undo。

使用 Python 標準庫 `csv.writer`／`io.StringIO(newline='')`，csv dialect 使用預設 excel（QUOTE_MINIMAL、CRLF）；CSV整段 `.encode('utf-8-sig')` 一次，在檔案開頭恰一個 BOM。逗號／雙引號／換行／中文由 csv 正確引用，不能自己拼接行／欄，也不增加 Excel `sep=,` 額外列。

## 5. 公式注入防護與內容影響

僅針對三個使用者可控文字欄位（消費項目、分類、付款方式）使用局部 `_csv_safe_text(value: str) -> str`：

- 為了判斷，從開頭略過所有 `str.isspace()` 空白及 Unicode `unicodedata.category()` 為 Cc／Cf 的控制／格式字元（涵蓋 TAB、CR、LF、NUL、C1、BOM／零寬字元）。**不刪除或改變原字串**。
- 若首個有效字元是 `=`、`+`、`-`、`@`，或前導序列含 Cc／Cf，於整個原字串前加一個 ASCII 單引號 U+0027；其餘原文不變。已以單引號開頭的文字不再因後面的公式符號重複加引號。
- 例如 `=SUM(1,2)` → `'=SUM(1,2)`，`  =1+1` → `'  =1+1`，TAB＋`@x` → 單引號＋原 TAB＋`@x`；`中文,項目`及普通前導空白無公式／控制字元者維持原文。
- 日期與合法數值金額不套用文字防護前綴；尤其金額欄不可變成 `'1000`。csv 引號處理與公式防護不同，兩者都必須存在。

使用標準庫，無新套件；不把備份內聯序列化抽成泛用系統，也不更改備份相容行為。下載頁說明單引號可能可見、CSV不是無損原文／匯入格式。這是降低直接開啟時公式解讀風險，不宣稱所有試算表軟體、再次另存後格式或手動移除前綴都安全；Excel 真實開啟與相容性仍須另行驗證。

## 6. HTTP、認證與錯誤

- GET／POST 均先驗證 session；所有匯出頁、下載與錯誤回應 `Cache-Control: no-store`，避免帳目與日期條件快取。不在日誌記錄 body、帳目或原始例外。
- POST 僅接受 application/x-www-form-urlencoded，重用 body+parse_qs。csrf_token、start、end 各恰一值，日期非空；CSRF缺少、錯誤、重複、非ASCII一律403、session保留、Service零次。不可將表單解析失敗當預設日期下載。
- 日期格式／不存在／未來／start>end／重複／缺少均400；查詢前拒絕。TypeError／ValueError回固定「無法匯出帳目，請檢查日期後重試。」。沿用現有SQLiteError型別處理失敗為503「目前無法匯出，請稍後重試。」；不直接import db／spending／ledger或洩漏原始例外。
- 有帳目才回 `Content-Type: text/csv; charset=utf-8`、`Content-Disposition: attachment; filename="discordbot-expenses.csv"`、`Cache-Control: no-store`。檔名固定ASCII，無身分、日期範圍、消費項目或任意使用者內容。空結果／錯誤是HTML，不帶attachment。
- 不接受任意路徑、不寫磁碟、不建立永久CSV／暫存管理系統；所有序列化完成後才送成功回應，避免邊串流邊失敗卻留下半份檔案。瀏覽器下載到使用者電腦不等於伺服器留檔，下載後檔案需使用者自行保管。

## 7. 最小效能選擇與限制

採既有完整清單加記憶體 StringIO／bytes／Response：簡單、可驗證完整與錯誤，記憶體為 O(範圍帳目數＋CSV大小)，有多份記憶體表示的成本，不是零成本串流。沒有畫面LIMIT、沒有隱含100筆／10000筆截斷，沒有新列數上限。

本輪禁止讀真帳本，因此不知道正式帳目數／輸出大小；隔離合成資料已量測完整10001筆及50001筆，結果見第9節。既有完整備份的8MiB／10000筆匯入限制不是本CSV限制，不直接套用。若證據顯示必須加上限，先提出明確數值／限制理由供使用者確認，超限整份拒絕並提示縮小日期，不悄悄輸出部分。未經授權不引入背景工作、串流資料游標或暫存檔；也不宣稱可支援任意大量帳目。

## 8. 隔離測試與排除範圍

實際沿用OAuth替身建立session及TemporaryDirectory+patch.object(db,'DB_NAME',…)；只在temp初始化／造資料，不讀真.env、Discord、正式DB、備份或既有匯出檔。瀏覽器只核對本輪新下載的合成CSV。

- 日期同一天、跨月／跨年／閏日、起迄邊界、未來／錯誤格式／不存在／倒序／缺少與重複欄位；空結果保留表單但不attachment。
- 本人／他人、四來源、voided、非消費、未入帳預測、停用分類與付款歷史快照；超過6筆最近、單頁limit以及10000筆仍全部輸出，排序固定。
- Web四來源均能搜尋，關鍵字單獨歷史、start至今天、keyword加end、完整範圍及原無效輸入規則仍有效。相同範圍、空keyword的Web搜尋和CSV核心items ID順序一致，有keyword為匹配子集；呈現限制不影響CSV完整性。月曆同資格，不自動同步未入帳規則。Discord舊核心／Service仍只搜manual，三欄、日期格式、6筆分頁、原文案與手動修改／非手動guard保留；新查詢不得改變這些期待。
- 設定頁第四個「資料匯出」details預設收合且有說明／入口；原三區塊開啟／回填不變，首頁／搜尋頁沒有新增匯出連結；無動畫或新section參數。
- 精確Decimal、千元無逗號、中文及BOM恰一個；csv.reader重讀引號／逗號／多行；三文字欄位危險ASCII開頭、普通空白、TAB／CR／LF／NUL／C1／Cf前綴、已帶單引號、金額不加前綴。
- Attachment／Content-Type／no-store、未登入零查詢、CSRF四種拒絕、外部user_id／path／filename／limit無效、HTML錯誤跳脫／無內部訊息。
- 匯出前後全業務表snapshot一致、無新CSV檔、禁止寫檔替身、無sync／來源初始化；既有OAuth／首頁／搜尋／月曆／設定／帳目編輯及Web import boundary保留。

不做CSV匯入、完整備份、帳號還原、收入／投資／外幣匯出、下載歷史、伺服器留檔或首頁重設計。不改Discord搜尋／更改／文案／按鈕／指令；Web只擴大搜尋可見來源，操作、關鍵字／日期／排序／顯示限制不變。設定動畫與搜尋手機超寬繼續延後；App及其架構／依賴／認證不在本輪。

## 9. 實際完成與驗收（0.11.18）

已依順序完成共享範圍核心／薄Service、Web四來源搜尋、CSV純序列化、設定頁第四區塊與受保護下載、Discord原搜尋／更改回歸。月清單與舊manual search契約保留；來源可見不放寬修改權限。只補規格介面，不新增依賴、SQL副本、資料表、架構或Discord功能。

新增16項TDD測試，核心／Service／Web162項、Discord搜尋7項、寫入可靠性9項及完整隔離驗證328項通過（子集不另加總）。Ruff、差異／空白、文件連結及版本一致性檢查通過；真資料沒有讀寫。詳見[配套計畫的實際證據](../plans/2026-09-28-web-v1-csv-expense-export.md#實際驗證紀錄)。

| 合成有效帳目數 | CSV bytes | 程序內HTTP生成秒數 | Python tracemalloc峰值bytes |
| --- | --- | --- | --- |
| 10001 | 508993 | 0.2646 | 14091127 |
| 50001 | 2588993 | 1.3453 | 70028214 |

兩組皆確認完整筆數；只代表隔離合成資料的query／序列化／回應生成，不是RSS、下載時間、正式規模或任意大量效能保證，仍為O(n)記憶體。沒有新增列數上限或截斷。

In-app瀏覽器375px使用OAuth替身與一次性帳本，確認設定入口預設收合／鍵盤展開、日期表單、跨月五筆實際下載及正常空結果保留日期；新匯出頁重用既有expense-panel，無橫向溢出。只檢查本輪新下載的合成CSV之BOM、五欄、精確金額、中文引用、公式前綴及歷史付款快照。

獨立唯讀審查核對本輪13檔增量與必要呼叫者，未發現需修正問題；沒有另跑測試或實際環境驗證。

未實測Excel／其他試算表、真OAuth、外部瀏覽器、螢幕閱讀器、實體手機、Web／Bot共用正式帳本或真並行；不宣稱完整備份／還原或全帳號交易快照。設定動畫、搜尋手機超寬及其餘延後需求未實作。重啟Web載入即可，不需正式資料庫初始化；未stage、commit或push。
