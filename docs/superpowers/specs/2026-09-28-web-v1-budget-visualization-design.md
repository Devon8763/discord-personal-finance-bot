# localhost Web v1 本月預算視覺化設計規格

狀態：**已實作，隔離驗證完成（0.11.17）**。使用者已授權實作，並確認分類採原生 details、預設關閉及完整清單；柔和動畫延後。真OAuth、實體手機與正式帳本未驗證。

日期：2026-09-28。核對基準：實際 worktree `C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`、分支 `codex/localhost-web-auth`、HEAD `b775b76`、README／CHANGELOG 最新版 `0.11.16`。使用者提供的 `DiscordBOT.worktrees` 路徑不存在，採用既有 `.worktrees`，不建立新工作樹。

配套：[逐項實作計畫](../plans/2026-09-28-web-v1-budget-visualization.md)。適用根目錄 [AGENTS.md](../../../AGENTS.md)。已有未提交的 Web／核心／測試／文件修改，全部保留；本輪不 stage、commit 或 push。

## 1. 目標與範圍

在登入首頁既有「本月預算」卡內，先提供正確、可用的金額與原生進度條，不建立另一頁報表或圖表系統。保留表單上方搜尋／設定入口、快速記帳、登出、小月曆與完整月曆入口；桌面等寬雙卡及窄螢幕上下排列維持。

總預算與分類預算都是同一批消費的不同觀察方式，不能相加扣款。首頁只顯示已設定預算的分類，不替未設定分類建立空卡，也不自動以分類預算合計代替總預算。

不新增依賴、JavaScript、圖表套件、SQL 查詢、資料表、migration、Service 框架、ORM、Repository、DI、JSON API、預算週期、儲蓄目標或新的寫入功能。手機搜尋頁超寬、設定頁柔和展開／收合、實體手機驗證、配色大改與最終首頁設計維持延後。

## 2. 既有實作核對與可重用介面

| 既有位置／介面 | 已有行為 | 本功能使用方式 |
| --- | --- | --- |
| `life_service.get_today()` → `spending.today()` | 共用 UTC+8 台灣日期 | 每次首頁呈現取一次，統一表單預設、本月卡及小月曆的日期基準 |
| `life_service.get_month_summary(user_id, month=None)` → `spending.month_report()` | 回傳總支出、分類統計、已設定 budgets | 同月份只呼叫一次；不改 Service 簽名、不新增 Service |
| `spending.report(user_id, start, end)` | 不限筆數讀取本人 `voided=0 AND kind='consumption'`；不依 source 篩除自動帳目 | 仍是唯一消費彙總來源；不另計算第二套總額 |
| `life_service.get_calendar_days(user_id, month)` | 全月日期與本人有效消費 cents，實際統計截至核心今天 | 每次首頁一次，既有記號、未來日期及共用日期格不變 |
| `life_service.get_categories(user_id, include_inactive=False)` | 預設只列本人啟用分類；含內建分類及本人覆寫 | 重用快速記帳已取得的啟用名稱，為 budget 列標示停用，不拿啟用清單過濾 budget 列 |
| `web/routes.py` 的 `_format_amount()`／`_format_twd()` | 千分位、移除小數尾零；不截斷消費 cents | 金額呈現沿用；輸入格式不改 |
| `_quick_entry_context()`／`_home_cards_context()`、`home.html`、`web.css` | 表單、草稿、固定卡片錯誤及雙卡布局已存在 | 僅延伸 context 和預算卡，不拆頁或抽象化 UI |
| 設定頁 `_settings_context()` | 停用但留有預算的分類仍可看金額／清除；不得新設定停用分類 | 首頁同樣顯示「已停用」，管理仍導向設定頁 |

`month_report()` 的現有 budget 列含 `category/budget/spent/remaining/used_percent`；總額使用整月總支出，分類使用該分類所有有效消費。`report()` 已涵蓋手動、已入帳固定、訂閱、分期；資料庫 WHERE 排除他人、撤銷及非 consumption，未入帳規則不在 expenses 內，讀取不呼叫 `sync_recurring()`。總支出包含沒有分類預算的消費。

這是靜態程式核對，不是本輪功能測試或正式帳本驗證。摘要與月曆各自查詢，仍可能在並行寫入時讀到不同瞬間；本功能不新增跨查詢交易或快取，刷新重讀即可，不宣稱正式並行一致快照。

## 3. 實作前缺口與已完成的最小補充

1. 目前 `report()` 雖先以整數 cents 合計，回傳 `total` 和分類 `amount` 時轉成 float；`month_report()` 又以 float 相減／算比例。首頁把 float 的十進位字串轉回 cents，不能保證大量歷史總額仍精確。後續僅在**同一份既有彙總結果**增加精確整數欄位，不新增查詢或重寫帳務規則：
   - `report()`：`total_cents: int`；`categories[name].amount_cents: int`。
   - `month_report()` 各 budget 列：`budget_cents: int`、`spent_cents: int`、`remaining_cents: int`（可負）。
   - 舊 `total/amount/budget/spent/remaining/used_percent` 保留原語意及型別，既有 Discord／其他呼叫者不用遷移。Service 原樣轉交新增資料，`life_ledger_service.py` 不需修改。
2. 首頁目前用 Web 自己的 `taiwan_today()`，設定頁及統計用核心日期。首頁改取既有 `life_service.get_today()`，同次日期傳給原 context／月曆函式，不改其他路由的日期驗證或另建時鐘。
3. 首頁目前沒有分類預算、比例或進度條。route 只將精確摘要整理成呈現 dict；模板／CSS 決定版面及外觀，不能重算消費。

新設定預算仍沿用正整數台幣限制，零元不是合法預算。歷史不合法的零／負預算不得除以零、冒充未設定或顯示假進度：核心讀取在算比例前以固定 `ValueError` 拒絕，首頁呈現既有安全資訊卡錯誤，絕不修補／回寫資料。舊小數預算沿用首頁已有安全錯誤行為，不新增轉換或四捨五入政策。

## 4. 本月與身分／資料口徑

- OAuth 身分僅取 `auth.current_user_id(request.session)`；query／form 的 `user_id`、month 等不能改變首頁本人／本月範圍。未登入只顯示既有登入頁，不查摘要、分類或月曆。
- 資料流維持 `Web → life_ledger_service.py → spending.py → SQLite`。Web 不 import／呼叫 `db.py`、`spending.py`、`ledger.py` 或寫 SQL。
- 本月為核心台灣今天的 `YYYY-MM`，統計起日當月 1 日、迄日核心今天；固定／訂閱／分期必須已正式入帳才計入，未來預測或來源規則不能當支出。
- 不使用 `recent_expenses()`、`list_expenses()`、搜尋結果或分類預算的 spent 加總作為總支出。最近消費僅保留原付款預選用途。
- 沒有預算也可顯示已記錄支出；「尚未設定」不同於有預算且已花零元。
- 首頁開啟不同步固定支出；原付款方式讀取可能補建預設項目的行為維持，不把整個首頁誤稱為完全無寫入。

## 5. 金額、比例與狀態

金額來自整數 cents。`difference_cents = budget_cents - spent_cents`；非負顯示「剩餘 N 元」，負值顯示「超支 N 元」，N 是差額絕對值；剛好用完顯示「剩餘 0 元（已用完）」。總額已花讀 `total_cents`，分類已花讀該 budget 的 `spent_cents`，不重複扣款。

金額統一沿用 `_format_twd(cents)`／`_format_amount()`：`1,000 元`、`100.5 元`、`100.25 元`、`0 元`，消費與差額不截斷或四捨五入，總預算仍整數。所有比例僅供顯示，不回寫帳本。

有效預算才計算 `Decimal(spent_cents) / Decimal(budget_cents) * 100`。文字比例最多兩位小數、ROUND_HALF_UP、去尾零，例如 `已使用 0%`、`12.05%`、`100%`、`125%`；不將超過 100 的文字比例截斷。視覺值使用未取小數顯示精度前的比例，限制在 0–100。極小超支即使顯示比例四捨五入到 100%，仍必須顯示精確「超支 0.01 元」，不能靠比例判斷超支。

| 預算狀態 | 顯示 | 進度 |
| --- | --- | --- |
| 全部未設定 | 本月、已記錄支出、「尚未設定總預算」、「尚未設定分類預算」、前往設定 | 不顯示進度或比例 |
| 只有分類預算 | 總額區仍未設定；分類照常顯示 | 僅分類有進度 |
| 只有總預算 | 總額三個金額與比例；分類固定空狀態／設定入口 | 僅總額有進度 |
| 兩者都有 | 總額與已設定分類分別顯示，不加總兩者支出 | 各自對應自己的預算 |
| 有預算、無消費 | 已花 0、完整剩餘、0% | 有 value=0 的有效進度，不是 indeterminate |
| 部分使用／用完／超支 | 明確剩餘／已用完／超支文字與精確金額 | 超支填滿，但文字保留真實比例 |
| 停用分類且有預算 | 分類名稱＋「已停用」、原預算及支出／差額／比例 | 仍呈現，不重新啟用，也不提供首頁寫入 |

分類列只遍歷 `summary['budgets']` 中非「總額」項目，以分類名稱排序提供穩定次序；分類名稱不在本人啟用清單時標示「已停用」。所有 budget 列保留，不因下拉選單不含該分類而消失。

## 6. 分類多時的呈現：已確認並完成方案 A

已採 **A：預算卡內放原生 `<details>` 分類清單，預設收合**。標題「分類預算（N 項）」，N是實際呈現的已設定分類預算數量；展開後呈現全部已設定分類的緊湊直式列；每列金額、比例與單一 progress，不做每分類一張大卡。總額摘要始終可見；沒有分類預算時用固定空狀態及設定入口，而不是空的收合區塊。鍵盤／無 JavaScript 可操作，不限制分類筆數、不增分頁、不保存展開偏好。

原備選B未採用；使用者於實作授權確認A。已設定但零消費仍顯示0元／0%，未設定總預算不影響分類呈現。

不採用截斷前幾項、不顯示停用預算或新設定頁報表，避免遺漏財務資訊或擴張範圍。這是首頁的原生收合選擇，不處理既有設定頁動畫。

## 7. 可存取性、安全與失敗狀態

- 採原生 `<progress max="100" value="…">`，總額／各分類各一個；`aria-labelledby` 綁定可見名稱，`aria-describedby` 綁定比例／剩餘或超支完整文字。DOM ID 用固定前綴和 loop index，不直接拿分類名稱拼未處理 ID。
- 截斷的 progress value 不冒充完整比例；可見及可存取說明包含實際比例與超支金額。保留元素內 fallback 文字，不能只靠顏色、tooltip 或長短傳達狀態。
- 金額、狀態及設定連結可在沒有配色的情況理解；收合標題具可見 focus，原生鍵盤互動。長分類名換行；progress 不超過卡片寬度，手機不新增大表格。
- Jinja autoescape 保持開啟；分類名不可 `safe`，不顯示 Discord ID、原始欄位、SQLite、路徑、Token 或例外。資訊卡維持固定「無法顯示本月資訊，請稍後重試。」。
- 延用既有 `TypeError/ValueError` 失敗處理和兩卡失敗策略，不為此重做錯誤系統；保留快速記帳表單、五欄草稿、原錯誤及 HTTP 狀態。錯誤不是零支出／零比例。
- 既有 POST 的 CSRF、session、PRG、revision、付款／分類預選及軟刪除不動；刷新成功頁只重讀摘要，不重複記帳。

## 8. 隔離驗收與未驗證項目

後續測試沿用 `TemporaryDirectory + patch.object(db, 'DB_NAME', …)`、固定核心台灣時鐘、既有 OAuth 替身建立真實 session，禁止真 Discord 網路或正式帳本；不偽造 Cookie。完整分工見配套計畫。

- 四種有無預算組合、零消費／部分／剛好用完／超支、正數預算 1 元、0.29 元、千分位及不同小數尾零。
- 同一日手動／固定／訂閱／分期均計入；尚未入帳規則、他人、voided、非 consumption 排除；超過既有列表容量仍完整計算。
- 未設定分類預算的支出仍計入總支出；停用分類預算不消失；總額和分類不重複計算。
- 新增、改金額／分類／手動日期、soft void 後，當月摘要與月曆 sum cents 一致；跨月手動修改檢查兩個月；不新增 Web 編輯／刪除功能。
- 大額多筆加總仍精確，比例顯示／視覺截斷分離、legacy 零值安全錯誤、金額及 accessible 說明、分類 XSS 跳脫、query 身分注入。
- 未登入不查服務；summary/calendar 各一次；不 sync recurring；資訊卡失敗與草稿／PRG回歸；CSS 不改搜尋／設定頁。

本輪相關隔離測試147項、完整隔離驗證312項及Ruff通過。一次性帳本、測試settings／OAuth替身的In-app瀏覽器檢查320／375／768／1280px；收合／展開無橫向溢出，完整八項、長分類名、停用／零值／超支、Enter／Space及可見focus均已檢查。真實Discord OAuth、外部瀏覽器、螢幕閱讀器、實體手機、正式SQLite／Bot-Web真並行仍未驗證。不同查詢不提供並行一致快照；不拿隔離測試代替正式環境驗證。

## 9. 文件與版本

原規劃輪於`0.11.16`保留文件紀錄；本輪實作完成後README／CHANGELOG同步至`0.11.17`，並記錄實際驗證及未驗證事項。既有啟動方式不變，重啟Web載入程式；不需正式schema初始化或依賴升級。

預計範圍皆已實作；`life_ledger_service.py`、schema、依賴、OAuth和既有寫入規則均未改動。設定動畫、搜尋頁手機超寬、預算週期與儲蓄目標維持延後；未stage、commit或push。
