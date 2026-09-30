# DiscordBOT Web v1：單筆帳目更改／刪除實作計畫

> **狀態：已實作與隔離驗證，版本 0.11.16。日期：2026-09-28。**
>
> **For agentic workers:** 原規劃輪僅有文件授權；本輪使用者已明確授權實作，依 `superpowers:executing-plans` 與測試先行流程逐項完成，checkbox 表示本輪實際驗收。依技能要求完成一次獨立唯讀審查；沒有委派實作，未 stage、commit 或 push。正式／實機未驗證範圍見末尾紀錄。

**Goal:** 從搜尋與月曆每日明細共用本人單筆更改頁，提供核心規則相容的五欄修改及摘要確認後軟刪除。

**Architecture:** 身分只取 OAuth session；Web 只呼叫 life_ledger_service，核心沿用 spending.edit／void_expense 的 BEGIN IMMEDIATE、舊快照及 expected_revision。只補錯誤分類、分類改名 revision、選項純讀取；HTML 表單用 POST／CSRF，成功寫入用 303 PRG。

**Tech Stack:** 現有 Python／FastAPI／Starlette session／Jinja／SQLite／unittest／TestClient，標準庫 Decimal、urllib.parse 與 sqlite3.Error（僅錯誤捕捉）；不新增依賴或 JavaScript。

**Spec:** [單筆帳目更改／刪除設計規格](../specs/2026-09-28-web-v1-expense-edit-delete-design.md)。執行前已完整閱讀規格、計畫及適用 AGENTS.md；授權來自使用者後續實作要求。

## Global Constraints

- 實際 worktree：`C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`；分支 `codex/localhost-web-auth`；開工重核 HEAD `b775b7637871bfa65920d6e38079e1e6377b0851`、版本 `0.11.15`、41 個既有未提交項目，保存安全內容副本／雜湊。未重建 worktree；交付版本 0.11.16。
- 只操作本人已入帳 `voided=0`、`kind='consumption'` 消費，包含手動、固定、訂閱及分期。
- 已確認：手動可改日期；自動來源日期唯讀，竄改由核心拒絕。任何修改／軟刪除均不改規則、期數、來源或排程關聯。
- Web 不 import db／spending／ledger、不連線 SQL；Service 不加 SQLite／Web／Discord 或交易邏輯。
- 不新增依賴、資料表、欄位、索引、JS、Repository、新架構層、Web 撤銷、Discord 指令／按鈕或其他功能。
- 不讀寫正式 data.db、Token、.env、備份、匯出或使用者資料；測試先 patch db.DB_NAME 到暫存路徑再初始化，使用 OAuth 替身。
- 下列任務已逐項執行。對新增缺口先補測試確認 RED，再最小修改並確認 GREEN；既有鎖內 revision、undo／同步本已通過的特徵測試不虛構 RED。沿用 unittest，不新增框架。
- 本輪未授權 stage／commit／push，未執行這些操作。實際確認起始版本 0.11.15，功能與驗證完成後合併一筆 0.11.16 紀錄；保留 0.11.15 歷史規劃紀錄。
- 設定頁柔和展開／收合只留作延後需求，不修改 settings 模板或動畫樣式。

## Review Focus

1. 分類改名目前不增加 revision，舊刪除摘要會過期卻仍成功：任務 1／3 必須釘住更名後舊版本失敗。
2. 更改頁 GET 若重用付款讀取預設路徑會補建資料：任務 1／2 必須比較全部業務表及 sqlite_sequence，確認新 GET 純讀取。
3. 舊付款即使仍啟用，名稱改名後保留快照與重新選 ID 不同：任務 2 必須分別驗證 keep／明選／NULL。
4. 欄位失敗回填不能將舊草稿配上最新 revision：任務 2 必須插入兩次讀取間的更新，期待 409 且不可直接提交。
5. 自動來源軟刪除仍占 period 唯一鍵、undo 又是全管道最新操作：任務 3／4 必須驗證同月不補回、未來照常入帳與舊 action_id 失效。

## 檔案責任與介面

本輪只動規格第 10 節列出的檔案；實際交付補入共用 `error.html` 的可選標題／reload 連結，解決新帳目錯誤沿用登入標題、刪除衝突無法藉重整過期確認網址恢復的問題。新產品檔僅 `web/templates/expense_edit.html`、`web/templates/expense_delete.html`；CSS 只追加兩頁容器、窄螢幕、焦點與刪除按鈕必要樣式。未新建路由模組、Repository、draft store 或操作紀錄表。

既有帳目介面（任務 2～4 原樣重用）：

```python
get_expense(user_id, expense_id) -> dict
update_expense(user_id, expense_id, amount, category, note, spent_on,
               payment_source_id=None, expected_revision=None) -> None
void_expense(user_id, expense_id, expected_revision=None) -> None
preview_undo(user_id) -> dict
undo_latest_action(user_id, expected_action_id=None) -> dict
```

必要介面補充（任務 1 已完成）：

```python
# spending.py 定義；life_ledger_service.py 公開同型別，保留 ValueError 相容
ExpenseUnavailableError(ValueError)
ExpenseRevisionConflictError(ValueError)
# Service façade / spending.py 核心分別同參數
get_payment_sources(user_id, include_inactive=False, *, initialize_defaults=True)
payment_sources(user_id, include_inactive=False, *, initialize_defaults=True)
```

Web helper 限定為 `web/routes.py` 內的普通函式，不建立新層：

- `_expense_return_context(values: dict[str, list[str]]) -> dict[str, str]`：檢查 return_view 與唯一且有效的 keyword/start/end 或 month/day；錯誤回固定搜尋來源。
- `_expense_return_url(context: dict[str, str], *, deleted: bool = False) -> str`：僅產生 `/search`／`/calendar` 的 urlencode query；不接收任意 URL。
- `_expense_edit_context(user_id: str, expense: dict, csrf_token: str, return_context: dict[str, str], *, draft: dict[str, str] | None = None, error: str | None = None) -> dict`：由已 scoped 列與純讀取選項產生五欄與原 revision，不替草稿升級版本。

## 任務 1：補齊既有核心的安全契約

**檔案：** 修改 `spending.py`、`life_ledger_service.py`；測試 `tests/test_life_ledger_service.py`、`tests/test_spending.py`。不改 schema 或 Discord。

**消費介面：** 既有 get／edit／void、rename_category、payment_sources、transaction。
**產出介面：** 上節兩種 ValueError 子型別、付款純讀取 keyword；分類改名對實際更名帳目增加 revision，其他引用規則不變。

- [x] 新增 `test_expense_errors_are_typed_and_value_error_compatible`：本人不存在／他人／voided／other kind 的 get／edit／void 使用 unavailable；stale edit／void 使用 conflict；兩者仍為 ValueError，原文字保持。斷言失敗前後 expense/actions 快照一致。
- [x] 新增 `test_payment_options_read_only_preserves_database`：False 不建立預設來源、不改 active、不推進 sqlite_sequence；True 保留原初始化結果；Service 正規化本人 ID 並原樣轉交 keyword。
- [x] 新增 `test_category_rename_increments_only_affected_owner_revisions`：本人舊分類 active／voided 列各 +1；不同分類、他人不變；before_json、budgets、recurring、shortcuts 繼續原子同步；舊 expected_revision 的 update／void 拒絕。延伸既有分類更名 rollback trigger 檢查 revision 也回滾。
- [x] 新增 `test_revision_is_rechecked_after_acquiring_write_lock`：重用 write-reliability 的 transaction 包裝方式，在寫入 lock 前以另一交易完成修改；舊 revision 的 edit／void 必須 conflict，保留已提交的新列且不多出失敗操作紀錄，不能只驗證 route 先前讀取。
- [x] 執行以下既有測試檔，確認新增斷言 RED 來自缺口；不以修改相容斷言掩蓋失敗。
- [x] 僅修改對應錯誤分支、既有分類更名 UPDATE 及付款讀取路徑，Service 薄轉交；不要重寫 edit／void／undo。
- [x] 重跑確認 GREEN，包含原本 default 付款初始化與 ValueError caller 相容。

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_life_ledger_service.py -v
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_spending.py -v
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_write_reliability.py -v
```

**驗收：** 新純讀取路徑只 SELECT；rename 與 action 快照在同一交易；typed errors 可供 Web 區分但不改 Bot 的 ValueError 處理。

## 任務 2：共用更改頁與安全修改 POST

**檔案：** 修改 `web/routes.py`，新增 `web/templates/expense_edit.html`；測試 `tests/test_web_auth.py` 中新增 `WebExpenseEditTests`，重用 `_WebLedgerTestFixture`。

**消費介面：** 任務 1 型別及 pure payment read，get_expense／get_categories／update_expense、auth、既有 form helper 和 Decimal 格式。
**產出介面：** `expense_edit_page(request: Request, expense_id: str)`（GET）與 `expense_update(request: Request, expense_id: str)`（POST），同路徑 `/expenses/{expense_id}/edit`；上節三個 Web helper。

- [x] 新增 `test_edit_get_is_owner_scoped_read_only`：未登入 403 且 Service 未呼叫；本人 200；其餘 unavailable 同 404／同正文且不顯示摘要；對每個 GET 比較業務表與 sqlite_sequence；回應 no-store。
- [x] 新增 `test_edit_post_updates_five_fields_with_prg`：手動 120.50 改 100.25、交通、新 note、啟用付款及跨月日期；cents=10025、revision +1、恰一舊快照；303 location 固定同筆 GET，刷新不新增 action，輸入無千分位。
- [x] 新增 `test_edit_all_origins_keep_schedule_and_lock_auto_date`：用四來源 subTest，snapshot recurring 表與 source／recurring_id／period；自動來源原日期成功、改日失敗且唯讀、手動跨月成功，不呼叫 sync_recurring。
- [x] 新增 `test_edit_preserves_inactive_values_and_payment_snapshot`：原停用分類可保留但不列其他停用分類；keep 保存 ID／舊名含 NULL；明選啟用 ID 使用目前名稱；停用／他人／空白 ID 不視為 keep；載入後停用仍拒絕新選。
- [x] 新增 `test_edit_post_enforces_csrf_unique_fields_and_session_identity`：wrong／missing／duplicate CSRF、JSON／無法解析／超過 20 欄 body 拒絕；missing／duplicate 五欄或 revision 拒絕；user_id、expense_id、未知欄位竄改不能越權；缺 revision 不轉 None。
- [x] 新增 `test_edit_invalid_values_preserve_only_safe_draft`：規格第 9 節 invalid 金額／日期／note，HTML 攻擊跳脫、非法選項不變合法、超長不默默截短；無 action，固定 400，不曝光核心錯誤。
- [x] 新增 `test_edit_conflict_never_upgrades_draft_revision`：兩 client／Bot 先改／undo／分類改名／回填讀取間更新後舊表單 409、不改較新列，保留跳脫且有長度限制的只讀草稿、固定 reload link，無可提交的混合版本；POST 重送失效。
- [x] 先執行 Web 測試確認新頁面／安全行為 RED，再建立最小 helper、兩個 route 與單欄模板。驗證順序固定 auth→CSRF→本人有效列→欄位→核心寫入；expected_revision 強制傳遞。成功 303、失敗直接 HTML；不將草稿存 session。
- [x] 重跑 Web 測試確認 GREEN，固定錯誤對應 400／403／404／409／503。既有 `_format_twd`／`_format_amount(grouping=False)` 直接重用。

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_web_auth.py -v
```

**驗收：** 同一頁支持四來源，符合日期已確認決策；沒有 GET 寫入、exception 原文、草稿版本升級或另一套核心驗證。

## 任務 3：摘要確認與軟刪除 POST

**檔案：** 修改 `web/routes.py`、`web/templates/expense_edit.html`，新增 `web/templates/expense_delete.html`；測試 `tests/test_web_auth.py`（`WebExpenseDeleteTests`）及必要延伸 `tests/test_life_ledger_service.py`。

**消費介面：** 任務 1～2 安全型別、return helpers、get_expense／void_expense；既有 undo 只於隔離測試呼叫。
**產出介面：** `expense_delete_page(request: Request, expense_id: str)`（GET）及 `expense_delete(request: Request, expense_id: str)`（POST），同路徑 `/expenses/{expense_id}/delete`。

- [x] 新增 `test_delete_confirmation_rechecks_owner_status_and_revision`：更改頁連結帶當頁 revision；摘要使用已儲存五欄，不用 unsaved draft；缺／重複 revision 400，stale 409，foreign／missing／voided／other kind 同 404；GET 無 action／資料變化，取消只連回同筆 edit。
- [x] 新增 `test_delete_post_is_soft_atomic_and_uses_prg`：確認前後兩次讀取間改值、改分類／undo／void，舊版本不執行；本人成功 303 到固定來源、voided=1、revision +1、action +1，完整列仍在。trace 檢查無 expenses DELETE；重送與返回舊頁後再送不能再增加 action。
- [x] 新增 `test_delete_post_rejects_csrf_and_identity_tampering`：覆蓋 CSRF、body 類型、重複／缺 revision、user_id、ID 範圍；不透過 GET 寫入或繞過 Service。刪除與修改採同一錯誤 contract。
- [x] 新增 `test_web_write_failures_roll_back_and_hide_storage_details`：隔離 ABORT trigger 分別使 action INSERT、expense UPDATE、修改 alerts 失敗；完整業務表 snapshot 相同、HTTP 503 無 private detail／SQL／路徑、無成功 redirect。重用既有 trigger pattern，不 mock 成功來證明 rollback。
- [x] 新增 `test_existing_undo_restores_web_edit_and_void`：實際 Web POST 後，既有 preview_undo／undo_latest_action 還原五欄／voided，revision 繼續增加；另筆／自動入帳後舊 action_id 拒絕。原 undo row 更新失敗的 rollback 測試保留。
- [x] 執行測試確認 RED；新增摘要與確認／取消模板，GET 比對版本、POST 強制原 expected_revision，交易只交給 void_expense。依規格捕捉 sqlite3.Error，固定 503；不新增撤銷 route、狀態 token 或刪除流程層。
- [x] 重跑確認 GREEN，固定本人／有效狀態／revision 在執行交易內再次核對，任何失敗無半完成紀錄。

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_web_auth.py -v
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_life_ledger_service.py -v
```

**驗收：** 不 SQLite DELETE；更改頁、確認 GET、執行 POST 都不能依賴舊檢查；只還原既有最近操作，不宣稱任意歷史復原。

## 任務 4：兩入口、安全返回及統計相容

**檔案：** 修改 `web/routes.py`、`web/templates/search.html`、`web/templates/calendar.html`；必要時最小修改 `web/static/web.css`；測試 `tests/test_web_auth.py`，來源／同步補充放既有 `tests/test_life_ledger_service.py` 或 `tests/test_spending_phase1.py`。

**消費介面：** 已有 search／list／calendar／summary Services，任務 2 的 edit route、return helpers，任務 3 的 delete PRG。
**產出介面：** 本人結果 dict 僅補 id／edit_url；兩來源的普通更改連結、已驗證 return_context 與 `deleted=1` 固定成功提示。

- [x] 新增 `test_search_and_day_details_share_edit_links`：搜尋手動列與月曆四來源都進同一 edit 路徑；無消費 ID 輸入欄、不傳 user_id、revision 不出現在列表、非 consumption／voided 無連結；維持搜尋手動範圍。
- [x] 新增 `test_return_context_is_validated_and_never_redirects_externally`：keyword 特殊字元 urlencode、有效 start/end、同月 month/day 保留；duplicate／invalid／future／跨月 day 整組落回 `/search`；next=https／//／編碼 scheme、未知欄位無法成為 Location。更改日期後仍回原日，刪除後空明細可用。
- [x] 新增 `test_soft_delete_updates_search_calendar_and_budget_totals`：精確 cents 驗證搜尋／日明細移除、日合計減少、首頁小月曆與完整月曆記號；最後一筆刪除記號消失，另有有效筆則保留；summary total、總額與分類預算 spent 同步減少、其他人不受影響。
- [x] 新增 `test_auto_edit_void_sync_preserves_periods_and_future_schedule`：四來源 Web 操作後規則快照一致；自動來源同月 sync=0、voided 列仍在；下一期仍照常生成，使用固定月份的隔離時鐘，不等待真排程。
- [x] 先確認 RED，再只補結果 ID／link context、普通 anchor 與成功提示。安全返回 helper 重用既有 `_search_date`、`_calendar_month`／`_calendar_day`；不接受任意 URL，不改既有列表資料範圍。
- [x] 重跑 GREEN；In-app 實際瀏覽器以 OAuth 替身／隔離資料檢查鍵盤焦點、標籤、唯讀日期說明、確認／取消與新更改／確認頁 320／375 px 無水平溢出。未使用正式帳本；搜尋頁既有超寬另記限制，未改原面板樣式。

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_web_auth.py -v
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_spending_phase1.py -v
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_life_ledger_service.py -v
```

**驗收：** 日明細／記號／合計／預算共用有效 consumption 範圍；不新增搜尋來源、不停止自動規則，不接受外部返回。

## 任務 5：完整隔離驗證與實際交付紀錄

**檔案：** README、CHANGELOG 及此規格／計畫狀態。不為驗證改動其他產品範圍。

- [x] 執行全隔離驗證及 Ruff，確認無 failures／errors；既有新功能只在實際 GREEN 後記錄為完成。不寫入歷史測試數，採當次輸出。
- [x] 核對 Service 邊界測試、四來源與 Bot 表單 ValueError 相容、undo／分類更名既有測試。若全部成功後沒有新修改或疑慮，不反覆加跑相同測試。
- [x] 看實際 diff 與未追蹤檔內容，確認無新增依賴／schema／JS／Discord UI／設定頁動畫，且每一變動對應本規格。核對所有無關既有修改仍保留。
- [x] 起始版本重核為 0.11.15，功能完成後合併本批 0.11.16 紀錄；README 與 CHANGELOG 一致，列實際測試數、未驗證真 OAuth／正式 DB／真並行／實體手機及無資料庫升級步驟。
- [x] 再執行文件差異與空白檢查，連結規格／計畫與更新紀錄；未獲另行授權前仍不 stage、commit 或 push。

```powershell
& .\.venv\Scripts\python.exe tests/run_discord_validation.py
& .\.venv\Scripts\python.exe -m ruff check .
git diff --check
git status --short
```

**驗收：** 上列驗證已執行並通過；實際數字、審查修正與驗證邊界見下方執行紀錄。未以隔離測試宣稱正式 OAuth、手機或共用帳本驗證。

## 未決問題、停止條件與延後需求

- 自動來源日期衝突已由使用者選擇保留核心限制，無尚待選擇的需求問題。使用者本輪明確要求實作，核心行為重核與規格一致。
- 若執行時核心或分支已改變、出現新的來源／排程規則衝突，停止依舊假設修改，列出實際差異並詢問；不可默默擴大日期或排程權限。
- 設定頁展開／收合更柔和、避免跳動與版面位移：只記錄為後續獨立需求，本計畫不實作動畫或修改設定頁。

## 本輪執行與驗證紀錄

| 任務 | 先補測試／RED | 實作與 GREEN |
| --- | --- | --- |
| 1 核心契約 | 缺錯誤型別、純讀取 keyword、分類改名 revision 的斷言失敗；原鎖內版本檢查特徵測試本已通過 | Service 18、spending 15、write-reliability 9 項通過；三處最小核心補充，沒有重寫交易／undo |
| 2 更改頁 | 7 項新方法因缺路由／行為失敗 | 7 項通過，連既有 Web 共 92 項通過；五欄、四來源、純 GET、停用快照、回填競態 |
| 3 刪除頁 | 5 項新方法有 6 個缺路由預期失敗；先修正測試 trigger 的 finally 清理，避免失敗污染下一 subTest | 5 項通過，Web 共 97；實際 action／expense／alerts ABORT triggers 全表 rollback、undo 與雙重確認 |
| 4 入口／統計 | 4 項方法中 3 項因缺連結／成功提示失敗；既有核心同步特徵測試本已通過 | 4 項通過，Web 共 101；固定安全返回、日記號／精確合計／預算一致、同月不補回／下月入帳 |
| 最終審查 | 1 次獨立唯讀審查，實跑 20 項重點測試通過；非 ASCII CSRF 兩操作新增回歸先確認 errors=2 | 新 POST 共用入口加入 ASCII guard，兩項回歸通過；刪除確認補明軟刪除資料保留 |
| 交付回復入口 | 刪除 409 的重新載入更改頁／正確錯誤標題回歸確認 RED | 共用錯誤模板最小可選內容，刪除 5 項通過；404 不顯示帳目或 reload 連結 |
| 5 最終驗證 | 重跑審查修正後的相關與完整隔離驗證 | 相關 core／phase1／Service／Web 141、寫入可靠性 9、全隔離 299 項通過；Ruff、差異／空白與文件連結檢查通過 |

相關測試的最終執行方式：

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_spending tests.test_spending_phase1 tests.test_life_ledger_service tests.test_web_auth -v
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_write_reliability.py -v
& .\.venv\Scripts\python.exe tests/run_discord_validation.py
& .\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

- 初次將 write-reliability 併入 dotted-module 命令時，其既有 `import test_phase1_ui` 找不到模組；按原計畫使用 discover 後 9 項通過，未修改測試或產品來繞過。既有 Starlette／Authlib httpx 棄用警告保留，無依賴更新。
- 實際 In-app 瀏覽器使用暫存 SQLite 與 OAuth 替身：搜尋／月曆進同頁、更改成功、無效金額回填、取消／確認軟刪除、原條件返回；自動日期 readonly、Tab 到刪除連結的 2px 焦點、320／375 px 新頁面及長摘要換行均檢查。搜尋頁原有面板於 375 px 可用寬360／內容寬368，保留原樣；不宣稱既有全站版面無溢出。
- 未驗證真 OAuth、其他外部瀏覽器、實體手機、真 Web／Bot 程序並行或共用正式帳本。全隔離 runner 禁止連線正式帳本；瀏覽器伺服器也僅指向一次性測試 DB，結束後已關閉。
- 本輪 16 個修改／新增檔案：9 個產品檔、3 個測試檔、README／CHANGELOG 及規格／計畫。41 個原有項目中 27 個無關檔雜湊不變，其餘按開工副本審閱增量；Git index／HEAD 不變，未 stage、commit 或 push。沒有正式資料讀寫或初始化。
