# localhost Web 首頁預算與小月曆卡片設計規格

> 日期：2026-09-28。狀態：依已確認需求完成規格，功能尚未實作。
> 本輪只新增本文件及配套計畫；不改版本、README、CHANGELOG、程式、測試、依賴或資料庫，不 stage、commit、push。

## 1. 目標與範圍

讓本人登入 localhost 首頁後，能直接記錄消費，並在同頁看見台灣本月的已記錄支出、總預算狀態與有消費的日期。保留 Discord OAuth、快速記帳、搜尋、設定及完整月曆流程。

```text
已登入
[搜尋帳目] [設定]
快速記帳表單（既有欄位、預設與記錄消費按鈕）
┌ 本月預算 ─────────┐  ┌ 小月曆 ───────────┐
│ 已記錄支出／預算狀態 │  │ 本月七欄日期及小記號 │
└──────────────────┘  └──────────────────┘
登出（既有 CSRF 表單）
```

桌面兩卡等寬並排、間距 1rem；窄螢幕依序為預算、小月曆，單欄上下排列。採既有 Jinja、CSS 與標準庫，不新增 JavaScript、圖表套件、框架、資料表或 Service API。沒有分類進度條、消費速度、首頁月份切換、拖曳或自訂小工具。

## 2. 實際基準與程式缺口

核對位置：`C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`；分支 `codex/localhost-web-auth`，HEAD `b775b76`。README 與 CHANGELOG 最新版本均為 **0.11.12**；0.11.11 的整數預算規則與「更改」文字已存在。本輪不調整版本。

本次讀取工作樹程式與相關測試，沒有執行產品或功能測試；CHANGELOG 的歷史通過數不視為本輪驗證。既有未提交變更包含核心、Service、測試、文件與未追蹤的 Web 目錄，均須保留。

| 位置 | 已有行為 | 本功能需要的最小差異 |
| --- | --- | --- |
| `web/templates/home.html` | 快速記帳、表單下方的月曆／搜尋／設定入口、登出 | 搜尋／設定移到上方，表單下方新增雙卡；完整月曆入口收進小月曆卡 |
| `web/routes.py` | 首頁與錯誤回填都用 `_quick_entry_context`；已有摘要、月曆與金額函式 | 加首頁卡片 context，抽取共用月曆呈現資料，保留表單行為 |
| `web/templates/calendar.html` | Mon–Sun、前置空格、記號與每日明細 | 未來日仍產生連結，但 `_calendar_day` 會拒絕；需最小修正為無連結日期格 |
| `web/templates/base.html`、`web/static/web.css` | 所有頁共用 28rem `.panel`；width 加 padding 沒有 border-box | 首頁可指定專用 panel class，足夠容納雙卡且不影響其他頁寬度；補首頁響應式 CSS |
| `life_ledger_service.py`、`spending.py` | 已有月份摘要、有效消費月曆及整數預算驗證 | 重用，不改核心或 façade |
| `tests/test_web_auth.py` | 登入、快速記帳、安全邊界、完整月曆、設定測試 | 補首頁卡片驗收與未來日連結測試，調整兩項受新畫面影響的舊斷言 |

兩項舊斷言：`test_logged_in_home_links_to_calendar` 的「查看月曆」改為「查看完整月曆」；`test_saved_page_refresh_does_not_duplicate` 不再禁止整頁出現剛記錄的金額，改檢查表單已清空及記錄沒有重複，因為摘要現在應顯示該金額。

## 3. 資料流、範圍與副作用

```text
GET / 或通過登入／CSRF後的 POST /expenses 錯誤回填
 → auth.current_user_id(request.session)
 → _quick_entry_context（既有選項與草稿）
 → _home_cards_context（台灣今天、本月）
 → life_ledger_service.get_month_summary(user_id, month)
   life_ledger_service.get_calendar_days(user_id, month)
 → spending.month_report / spending.calendar_days
 → SQLite
 → Jinja 首頁雙卡
```

- 身分只能取 OAuth session；忽略 URL、表單中的 `user_id`，首頁也不受外部 `month`、`day` 改變本月。
- Web 不得 import `spending.py`、`db.py`、`ledger.py` 或寫 SQL。Service 維持 ID 正規化、薄轉交與資料形狀處理。
- 摘要與記號都是本人、`voided=0`、`kind='consumption'`，不篩選來源：包含已入帳手動、固定、訂閱及分期，排除他人、撤銷及其他 kind。只計到核心的台灣今天，不把未來預測當消費。
- 首頁每次呈現僅呼叫上述兩個 Service **各一次**；摘要取支出與預算，月曆取每日聚合。兩者是不同目的的既有聚合讀取，不是同一份明細重查。首頁不需 `list_expenses`、`search_expenses`、`get_chart_data` 或 `_settings_context`。
- `get_month_summary` 回傳 `start/end` ISO 日期、`record_count`、`total`（元，既有 float）、`has_records`、`categories`、`budgets`。各 budget 為 `category/budget/spent/remaining/used_percent`，金額為元；總預算只認 `category == '總額'`。
- `get_calendar_days` 回傳完整月份的 `list[dict]`，每列 `date`（ISO）、`cents`（int）、`level`（既有深淺符號）。呈現只使用日期與 `cents > 0`，不輸出金額或 level。
- 完整月曆保留既有 `list_expenses(...)["items"]` 取得當日明細；小月曆只連向它，不複製每日明細功能。

**副作用核對：** `month_report → report → rows`、`calendar_days → rows` 均未呼叫 `sync_recurring`，未執行帳務寫入或初始化。尚未補記的 recurring 規則不會因首頁打開而入帳；只展示已有有效消費。既有 `_quick_entry_context → get_payment_sources → payment_sources` 會在交易中 `ensure_payment_sources`，可能補建本人「現金／未指定」，因此不能宣稱整個首頁請求純讀取或完全沒有寫入。本功能不新增同步、提醒、undo 或其他寫入副作用。SQLite 一般連線並非唯讀模式，本輪不開啟任何實際帳本。

摘要與月曆是分開讀取，沒有同一交易快照；Bot 同時新增帳目時可能短暫不同步，刷新後更新。此階段不新增交易整合、快取或鎖。首頁一次取得 Web 台灣今天並共用給本次呈現；核心仍依自己既有台灣時鐘截斷日期，午夜跨日的極短窗口不在本輪重構範圍。

## 4. 本月預算卡

標題「本月預算」，月份 `YYYY 年 MM 月`；永遠顯示「已記錄支出：X 元」，並提示「僅依已記錄資料統計。」。

| 狀態 | 呈現 |
| --- | --- |
| 有總預算、未超支 | 「總預算：B 元」「剩餘金額：R 元」；剛好用完為 `0.00 元` |
| 有總預算、超支 | 「總預算：B 元」「超支金額：O 元」；O 為差額絕對值，不以負剩餘代替超支文字 |
| 無總預算 | 「尚未設定總預算」及「前往設定」→ `/settings`；已記錄支出仍顯示 |
| 僅有分類預算 | 同無總預算；不得自行合計、寫入總預算或顯示分類進度 |
| 無消費 | 已記錄支出 `0.00 元`；總預算狀態照常顯示 |

沿用 Web `_format_twd(cents)` 的消費精度（兩位小數），單位用「元」，不在卡片加入 NT$。摘要元值先以 `Decimal(str(value))` 轉回整數 cents，剩餘額以 cents 相減；不要用 float 相減後截斷。超支使用非負絕對值再套 `_format_twd`，避免此既有函式對負 cents 的除法呈現問題。

有效總預算以整數元顯示，不帶 `.0/.00`。既有 `_format_amount` 使用 `:g`，大整數會縮位／科學記號，因此首頁預算使用固定整數格式，不改設定頁共用函式。驗收包含十億元上限。若 Service 回傳舊小數預算，不得偷偷截斷、四捨五入或修改原資料：呈現下節固定卡片錯誤，保留表單；歷史資料升級另行確認，不新增 migration。

## 5. 小月曆卡

標題「小月曆」、月份 `YYYY 年 MM 月`。七欄固定 `Mon Tue Wed Thu Fri Sat Sun`，月初空格數為 `month_start.weekday()`；回傳的 28／29／30／31 個真實日期均保留，不需要補齊六週或下個月日期。

- 日期格只含日數及有有效消費時的「•」；有可辨識的日期 aria-label，記號也有「當日有消費」文字說明，不靠顏色辨識。
- 今天與過去日期（含無消費日）皆可點，連到 `/calendar?month=YYYY-MM&day=YYYY-MM-DD`，HTML 的 `&` 正確 escape。點入沿用既有選取日明細及空日狀態。
- 本月未來日照樣顯示日數，但使用帶 `aria-disabled="true"` 的非連結日期格，沒有 href 或偽按鈕；不能產生點後只得到 400 的 URL。共用呈現規則同步套用完整月曆，僅修正其未來日連結，不放寬 route 日期驗證。
- 「查看完整月曆」→ `/calendar`（既有台灣本月預設）；小卡不提供月份導覽。
- 無任何記號仍顯示完整日期格及「本月尚無已記錄的消費。」；不能以空白、消失或單獨訊息取代日期格。
- `level`、cents、category、note 等不得進小卡日期格或相關屬性。完整月曆點入後的明細保留原樣。

## 6. 最小呈現介面與排版

在 `web/routes.py` 抽取 `_calendar_grid_context(month_start: date, calendar_rows: list[dict], *, as_of: date) -> dict[str, object]`，只處理呈現、不查資料。回傳 `month_title/month_text/leading_blanks/calendar_days/has_month_expenses`；days 每列為 `{date: str, day: int, has_expense: bool, is_future: bool}`。完整月曆與首頁重用它；完整月曆保留原本選取日及導覽 context，不增加查詢。

新增 `_home_cards_context(user_id: str, *, as_of: date) -> dict[str, object]`，回傳：

- `home_budget`：`month_title`、`spent`（顯示字串）、`total_budget`（整數字串或 None）、`remaining`／`overspent`（兩位小數字串或 None）。兩種差額欄位不會同時有值。
- `home_calendar`：上述共用月曆 context。
- `home_cards_error`：正常為 None；遇既有可處理的 TypeError／ValueError 時固定「無法顯示本月資訊，請稍後重試。」且兩份卡片資料為 None。不偽裝成零支出或無預算，不展示例外細節。

`_quick_entry_context` 新增可選 keyword `as_of: date | None = None`，缺省時一次取 `taiwan_today()`，用於預設表單日期與卡片；草稿、選項與既有 error/saved 欄位不改。卡片失敗只顯示資訊區的 alert，不吞掉原本表單錯誤，也不阻擋記帳或改變 HTTP 狀態。非預期基礎設施故障沿用既有伺服器錯誤處理，不增加敏感日誌。

`base.html` 只提供 `panel_class` Jinja block，預設 `.panel`，首頁覆寫加 `.home-panel`。首頁 border-box、最大 64rem、寬度 `min(64rem, calc(100% - 2rem))`；padding 窄螢幕 1rem、桌面 2rem。雙卡容器預設單欄，48rem 以上 `repeat(2, minmax(0, 1fr))`、gap 1rem；卡片 `min-width: 0`，文字可換行。七欄沿用 `.calendar-grid`，小卡樣式加首頁作用域；日期連結保留鍵盤焦點，觸控格至少 2.4rem 高。

不以隱藏 overflow 掩蓋寬度問題。需在 320／375／768／1280 px 實際瀏覽器檢查無橫向捲動；320／375 單欄，768／1280 並排且兩卡等寬。若版面尚未跑過瀏覽器，僅可報 HTML／CSS 已檢查，不能報手機已驗證。

## 7. 安全、回歸與驗收

- 未登入首頁只顯示登入，不呼叫新增兩個摘要／月曆服務；query 不可改身分。延續模板 autoescape，不用 `safe`。
- CSRF、付款方式最近有效選項、分類預設、日期預設、錯誤草稿回填、POST 303 PRG 與刷新不重複寫入均保持原樣；資訊卡也須出現在正常首頁、成功回首頁與錯誤回填首頁。
- 所有功能測試用 TemporaryDirectory 的隔離 SQLite，沿用 `_WebLedgerTestFixture` 與 OAuth 替身；不連外、不讀 `.env`、不操作正式資料庫、備份或匯出。

| 驗收群組 | 必要證據 |
| --- | --- |
| 預算 | 有／無／用完／超支、零消費、僅分類預算；1000 元與 120.50 元得到 879.50 元，100 元與 120.50 元得到超支 20.50 元；含小額精度、大整數、舊小數預算安全失敗 |
| 資料與安全 | 手動、固定、訂閱、分期已入帳共同計入；撤銷、他人、非 consumption 排除；未同步規則不因首頁產生帳目；外部 user_id/month/day 無效 |
| 日期 | 28／29／30／31 天、Monday-first、前置空格、台灣本月、空月份、今天可點／明日不可點；連結能顯示正確日期明細 |
| 查詢與副作用 | GET 及錯誤回填的 summary/calendar 各一次；無 list/search/chart/sync 查詢；已有預設付款方式時讀首頁不變動任何隔離表內容；不存在時僅允許既有補建行為 |
| 表單回歸 | 五欄草稿保留、失效選項、CSRF、303、成功後表單清空、刷新資料筆數不變；卡片出錯仍可操作表單 |
| 排版 | 上方入口／中間表單／下方雙卡，手機無橫捲、七欄清晰、鍵盤焦點與記號文字，真實瀏覽器紀錄 |

## 8. 文件自查與交付邊界

配套計畫：`../plans/2026-09-28-home-budget-mini-calendar.md`。規格與計畫均按上述介面、查詢上限、未來日限制與六組驗收對應；未包含產品實作或任何已通過功能測試的宣稱。

本次例外遵循使用者明確要求：即使 AGENTS.md 一般要求同步更新 CHANGELOG，本輪也只新增兩份文件，不改 README／CHANGELOG。未來實作完成後，另依當時授權與最新版本更新紀錄；本規格不預先指定新版本或 Git 操作。
