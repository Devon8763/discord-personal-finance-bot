# DiscordBOT Web v1：單筆帳目更改／刪除設計規格

> **狀態：已依規格實作，版本 0.11.16；隔離驗證完成。日期：2026-09-28。**
>
> 最初規劃輪只建立文件並標記「尚未實作」。使用者後續明確授權實作，本輪已完成路由、錯誤型別、純讀取選項及 revision 補強；規劃基準保留於第 2／11 節，實際交付與未驗證邊界見第 12 節。未 stage、commit 或 push。

配套文件：[逐項實作計畫](../plans/2026-09-28-web-v1-expense-edit-delete.md)。

## 1. 目標、已確認決策與邊界

本人從搜尋結果或完整月曆每日明細，進入同一個單筆更改頁，修改已入帳生活消費，或經摘要確認後軟刪除。使用者不需輸入消費編號；帳目 ID 只用於站內連結與路由定位。

- 操作範圍只包含 session 本人、存在於 `expenses`、`voided=0`、`kind='consumption'` 的帳目；尚未生成帳目的排程規則不是操作對象。「有效／未撤銷」沿用 `voided=0`，不新增狀態欄位。
- 欄位為日期、金額、消費項目、分類及付款方式。
- **使用者已於本輪確認：保留核心日期限制。手動帳目可改日期；固定、訂閱及分期的已入帳帳目顯示唯讀日期，禁止移動日期。** 其餘四個欄位可依核心規則更改。
- 修改／刪除只影響該筆消費，不修改固定規則、訂閱或分期排程，不停止後續入帳，不呼叫自動同步。
- 保留 `Web → life_ledger_service.py → spending.py → SQLite`。不新增依賴、資料表、欄位、索引、JavaScript、Repository 或其他架構層。
- 不做批次、大表格編輯、Web 撤銷、搜尋來源擴充或任何新的 Discord 指令／按鈕。

## 2. 規劃輪 checkout 與既有變更快照（歷史基準）

規劃輪核對工作目錄：`C:\Users\User\Desktop\DiscordBOT\.worktrees\localhost-web-auth`。下表為當時快照；實作輪重核為 41 個未提交項目（包括本規格／計畫），起始版本仍為 0.11.15。

| 項目 | 2026-09-28 核對結果 |
| --- | --- |
| 分支 | `codex/localhost-web-auth` |
| HEAD | `b775b7637871bfa65920d6e38079e1e6377b0851` |
| README／CHANGELOG 最新版本 | 均為 `0.11.15`（設定頁展開／收合） |
| 適用指示 | 使用者提供的完整 AGENTS.md 指示，及主 checkout／本 worktree 根目錄的 `AGENTS.md` 文件紀錄規則 |
| 更近的 AGENTS.md | 已核對 docs、specs、plans、web、templates、tests 目錄，未發現其他適用檔案 |
| 開工前狀態 | 16 個已追蹤修改、23 個未追蹤檔，共 39 項；無 staged 變更 |

開工前已追蹤修改：`.env.example`、`.gitignore`、`CHANGELOG.md`、`README.md`、`dashboard.py`、`db.py`、`life_ledger_service.py`、`requirements-dev.txt`、`requirements.txt`、`spending.py`、`tests/test_life_ledger_service.py`、`tests/test_phase1_ui.py`、`tests/test_schema_initialization.py`、`tests/test_simple_dashboard.py`、`tests/test_spending.py`、`tests/test_write_reliability.py`。

開工前未追蹤：既有 9 份 Web 設計／計畫文件（phase1、phase2a、phase2b、settings、home-budget-mini-calendar）、`tests/test_web_auth.py`，以及既有 13 個 Web 檔案：`web/__init__.py`、`web/app.py`、`web/auth.py`、`web/routes.py`、`web/settings.py`、`web/static/web.css`、`web/templates/base.html`、`calendar.html`、`error.html`、`home.html`、`login.html`、`search.html`、`settings.html`。

規劃輪只新增兩份文件，對 README／CHANGELOG 插入文件段落。實作輪先保存 41 項既有內容雜湊及副本，再只修改本規格範圍；不能把 HEAD 當成完整 Web 功能基準。交付以開工前檔案及 Git index 雜湊核對保留範圍。

## 3. 已有介面與真實規則

### 3.1 Services 已經足夠處理帳務操作

以下介面均已存在；寫入回傳 `None`，不可將其視為新帳目 ID 或更新後資料：

```python
get_expense(user_id, expense_id)  # dict：含 id、cents、revision、source、付款快照等
update_expense(user_id, expense_id, amount, category, note, spent_on,
               payment_source_id=None, expected_revision=None)
void_expense(user_id, expense_id, expected_revision=None)
preview_undo(user_id)  # {'action_id': ..., 'expense_id': ...}
undo_latest_action(user_id, expected_action_id=None)
get_categories(user_id, include_inactive=False)
get_payment_sources(user_id, include_inactive=False)
search_expenses(user_id, keyword='', start=None, end=None)
list_expenses(user_id, month, *, include_voided=False, limit=None, offset=0)
get_calendar_days(user_id, month)
get_month_summary(user_id, month=None)
```

Service 只正規化本人 ID、轉交核心與整理普通資料，不自行 SQL 或交易。核心 `get_expense()`、`edit()`、`void_expense()` 都限制本人有效 consumption。修改及軟刪除在 `ledger.transaction()` 的 `BEGIN IMMEDIATE` 內重新讀取並比較 revision，操作紀錄與帳目更新同一交易，失敗完整 rollback。

### 3.2 修改、來源及停用選項

| 來源／欄位 | 核對到的核心行為 | Web 設計 |
| --- | --- | --- |
| `source='manual'` | 可更改日期，含跨月；仍禁止未來日期 | 原生日期欄位可編輯 |
| `source='固定'／'訂閱'／'分期'` | `on != old['spent_on']` 時拒絕；可改金額、分類、用途、付款 | 同一頁，日期唯讀；POST 竄改仍交由核心拒絕 |
| 排程關聯 | `edit()` 不更新 `source`、`recurring_id`、`period`、規則或期數 | 不提供這些欄位，不改排程 |
| 分類停用 | 原分類不變可保留；改成其他分類須啟用 | 只額外呈現該筆原停用分類，標註「已停用，僅可保留」；其他選項只取啟用分類 |
| 付款停用／改名 | `payment_source_id=None` 保留原 ID 與歷史名稱快照；明確選 ID 則須為本人啟用來源，並取得目前名稱 | 明確的 `keep` 選項表示保留原付款方式；數字 ID 表示選擇啟用來源 |
| 自動來源刪除 | 沿用同一軟刪除，不停止規則 | 確認摘要說明只刪本筆、後續排程仍存在 |

付款即使仍啟用，也預設「保留原付款方式：歷史名稱」，避免單純更改金額時更新名稱快照。原 ID 為 NULL 的歷史／自動帳目同樣可保留；不默默改成「未指定」。重新選取同一個啟用 ID 是明確重新選擇，核心會使用目前名稱。空白、未知或停用 ID 不得轉成 `None`。

### 3.3 金額、日期、項目與列表資料範圍

- `spending.money()` 使用 Decimal，儲存整數 cents；正數、最多兩位有效小數、不超過十億元。保留這套驗證，不換成 float，不套用預算的正整數限制，不四捨五入或截斷。
- 日期由核心 `date.fromisoformat()` 與 UTC+8 `today()` 驗證；Web 表單只要求 `YYYY-MM-DD` 傳輸格式，日期存在性、未來日期及來源限制仍由核心判定。
- 核心消費項目不可全空白，長度 1～200 字。HTML 使用既有 `note` 欄位及 `maxlength=200`。
- 已有 `_format_twd(cents)` 與 `_format_amount(value, grouping=False)`。摘要顯示 `1,000`、`100.5`、`100.25`；金額輸入為 `1000`、`100.5`、`100.25`，不帶千分位或單位。全部從 cents／Decimal 產生。
- 搜尋核心目前**只查手動 consumption**；本次不擴充。月曆使用 `list_expenses()`，涵蓋已入帳四種來源，不得改用搜尋當月曆資料源。
- 搜尋／月曆 route 目前將資料映射成顯示 dict 時移除了 ID；入口需要只補 `id` 或生成的站內 `edit_url`，不將完整含 user_id 的列傳給模板。

### 3.4 操作紀錄、撤銷與同步限制

`edit()`／`void_expense()` 先將舊列存入 `expense_actions.before_json`，再增加帳目 revision。軟刪除只設 `voided=1`，保留列、排程關聯與紀錄，不做 SQLite DELETE。

現有撤銷選取本人 `undone=0`、ID 最大的操作，確認時以 expected_action_id 再查最新操作。舊快照可還原日期、cents、分類、note、voided 及付款 ID／名稱，revision 再增加，不退回舊值；新增操作的 `before_json='null'` 則以軟刪除還原新增。Web 操作使用相同紀錄，因此可由既有 Discord `!記帳撤銷` 還原。

限制必須如實保留：

- 只能撤銷最新尚未撤銷的本人操作，不能指定任意歷史帳目；Web、Bot 及自動同步入帳共用這個順序。新操作出現後，舊確認 action_id 會被拒絕。
- undo 不是用帳目 expected_revision 比對；它確認的是最新 action_id。沒有時間／管道／操作類型／after 快照欄位，不能把現有紀錄描述成新增的完整稽核系統。
- undo 不還原／停止排程、不撤回已送出的通知，也不等於資料備份復原。
- `sync_recurring()` 使用 `INSERT OR IGNORE` 與 `UNIQUE(recurring_id,period)`。同期間的軟刪除列仍占唯一鍵，不會被同步補回；未來期別仍依原規則入帳。
- Discord 修改表單已有 expected_revision；文字 `!支出修改` 未傳 revision，屬既有即時指令。本功能不改該指令的介面；Bot 寫入仍增加 revision，使舊 Web 表單遭拒絕。不能宣稱文字指令新增了表單快照衝突保護。
- 修改沿用既有 `alerts()`；軟刪除不移除既有預算通知／已送出紀錄。即時消費統計依有效列重算，不用通知資料判斷消費。

## 4. 規劃時最小缺口與選擇（本輪已補齊）

此節保留規劃基準的缺口描述；對應補充已於 0.11.16 完成，並未重寫既有帳務流程。

建議直接重用既有三個帳目 Service；讓 Web 自行 SQL 或另建修改／刪除流程會違反邊界。只補以下必要項目：

1. **可分類的安全錯誤**：目前找不到、revision 衝突與欄位錯誤都是 ValueError。於 `spending.py` 定義 `ExpenseUnavailableError(ValueError)`、`ExpenseRevisionConflictError(ValueError)`，在既有讀取／修改／軟刪除的對應分支使用；保留原錯誤文字與既有 ValueError 相容性。Service 原樣轉交並公開同型別，Web 不比對例外文字、不接觸核心。無新錯誤框架。
2. **分類改名增加 revision**：目前 `rename_category()` 直接更新本人相關 expenses.category 及 undo 快照，但不增 revision；舊刪除確認可能在摘要變動後仍執行。只在既有同一交易內讓實際更名的 expenses 列 `revision=revision+1`，含已軟刪除列；保留既有其他引用與 before_json 同步規則，不建立更名操作紀錄或擴大重構。
3. **新增頁面的選項純讀取**：現有 `payment_sources()` 會 `ensure_payment_sources()`，即使是 SELECT 入口也可能 INSERT／推進 sqlite_sequence。增加核心 `payment_sources(user_id, include_inactive=False, *, initialize_defaults=True)` 與對應 Service 同名 keyword；預設 True 保持所有既有呼叫行為，更改頁／錯誤回填明確用 False，只 SELECT 已存在選項，不補建、啟用或更新任何來源。不能宣稱既有首頁／設定頁也已改成純讀取。
4. **Web 頁面與入口**：四個路由、兩個模板、安全返回條件、必要的連結與提示樣式，以及以下驗收測試。

上述補充已實作。交易、金額／日期規則、單筆修改／軟刪除本身不需新增 API；Service 的可選 revision 仍保留，Web 每次寫入則強制提供。

## 5. 路由、身份與回應契約

| 路由（已建立） | 行為 |
| --- | --- |
| `GET /expenses/{expense_id}/edit` | 本人有效列重新讀取，顯示更改表單、當前 revision、刪除與返回入口 |
| `POST /expenses/{expense_id}/edit` | session＋CSRF＋表單檢查；重用 update_expense，強制 expected_revision |
| `GET /expenses/{expense_id}/delete` | 從更改頁帶 expected_revision；重新讀取本人有效列，比較版本後顯示已儲存摘要，不使用未儲存草稿 |
| `POST /expenses/{expense_id}/delete` | 再查本人有效狀態／revision，核心交易內再檢查並呼叫 void_expense |

所有路由先使用 `auth.current_user_id(request.session)`；未登入回既有固定登入錯誤 HTTP 403，不查帳目／選項。query／form 的 `user_id` 與未知欄位一律不能改變操作身份。路由 ID 為正整數、revision 為非負整數，僅接受 ASCII 十進位、SQLite 整數範圍，不使用 FastAPI 預設含輸入細節的 JSON 422 回應。

POST 使用既有 `_urlencoded_form()`（最多 20 欄）及 `auth.validate_csrf()`；CSRF 必須恰一值。先驗 CSRF，再查本人有效列，再檢查其他欄位。修改的五欄及 expected_revision、刪除的 expected_revision 都須恰一值；缺漏、重複、空白或不合法 revision 不得默認為 None。路由才是操作 ID，額外 form expense_id 無效。

| 情境 | HTTP／固定安全回應 |
| --- | --- |
| 不存在、他人、已撤銷、非 consumption、無效 ID | 同為 404「無法開啟此筆帳目，請返回列表重新選取。」不顯示帳目、所有權或草稿 |
| 缺漏／重複／錯誤 CSRF、無法解析或非 URL-encoded body | 403「操作驗證失敗，請重新載入頁面後再試。」不執行帳目讀寫、不保留草稿 |
| 無效欄位／revision | 400「資料無法儲存，請確認日期、金額、消費項目、分類及付款方式。」搭配固定欄位規則與安全回填 |
| 有效本人帳目 revision 衝突 | 409「此筆帳目已變動，請重新載入後再操作。」不提供仍可提交的舊表單／刪除確認 |
| SQLite 讀寫失敗 | 503「操作暫時無法完成，請稍後重新載入再試。」不輸出 SQL、路徑、例外文字或堆疊 |

Web 可僅引用標準庫 `sqlite3.Error` 作為捕捉型別，不連線、不 SQL、不 import db／spending／ledger；Service 不增加 sqlite3 import。捕捉順序先 unavailable／conflict，再一般 TypeError／ValueError，最後資料庫錯誤；不能把衝突當成一般欄位錯誤而刷新版本。

成功修改採 **303 PRG** 到固定同筆 GET 更改頁（`saved=1`），顯示「已更改帳目」、最新已儲存值及返回入口。成功刪除採 **303 PRG** 到驗證後的來源列表（`deleted=1`），顯示「已刪除帳目」。寫入只在 POST，重新整理 GET 不重複寫入。驗證失敗沿用既有 Web 的直接 400／403 HTML 回填模式；沒有成功寫入，不為失敗另存 session 草稿。新增帳目頁／確認頁與錯誤回應加 `Cache-Control: no-store`。

## 6. 更改頁、草稿與衝突

單欄順序為日期、金額、消費項目、分類、付款方式；「儲存更改」與「刪除此筆帳目」分開，另有返回來源連結。自動來源日期用唯讀欄位並說明限制，仍送出原 ISO 日期；POST 不將竄改日期默默改回原值，交核心拒絕。金額／日期／note 用核心規則，分類與付款方式在寫入交易中重新驗證。

付款 `keep` 只在該筆已 scoped 讀取成功後轉為 `None`；其他選擇只接受數字 ID。原停用分類只為該筆保留原值，不使用 include_inactive=True 列出其他停用分類，不更改分類 active。未知付款／分類不當作合法選項回填，顯示固定「原選擇無法使用，請重新選擇」。

CSRF 及本人有效列檢查成功後，欄位錯誤保留唯一值且可安全顯示的五欄輸入，使用 Jinja 自動跳脫。note 保留上限 200 字、金額文字上限 30 字、日期文字上限 10 字；超長輸入不偷偷截短後送出，而清空該欄並提示重新輸入。原生日期欄無法呈現的短無效日期，可於該欄旁以跳脫文字呈現原輸入。缺漏／重複欄位不任選其中一值；身份、Token、未知欄位及原始 exception 不回填、不存入 cookie session 或 URL。

回填仍保留**原提交 revision**。若重新讀取發現帳目變動，改回 409；不能把舊草稿配上最新 revision 自動再送。衝突頁保留安全草稿為只讀參考，顯示「重新載入帳目」固定站內連結，不提供提交舊草稿的表單；重新取得資料後由使用者決定新輸入。

## 7. 刪除確認與原子性

更改頁的刪除連結攜帶當頁 expected_revision。GET 確認重新讀本人有效列並核對 revision；摘要顯示日期、精確格式金額、消費項目、分類及付款快照。提供「確認刪除」「取消，返回更改頁」；不預先軟刪除，也不要求輸入編號。

確認文案：「刪除後不再列入一般搜尋、月曆與消費統計；資料保留為軟刪除紀錄。」自動來源再說明：「只刪除這筆已入帳消費，不會停止後續排程。」可提示既有 Discord 最近操作撤銷，但不得承諾此筆永遠是可還原的最新操作。

POST 確認只含 CSRF、原 expected_revision 及白名單返回條件；重新讀取與核心 transaction 二次核對本人／voided／kind／revision。兩次確認間若修改、軟刪除或 undo，拒絕原版本；不得先寫 action 再獨立更新列，或吞掉失敗後提交。SQLite action INSERT、expense UPDATE、修改 alerts 任一步失敗，所有同交易資料保持原狀；沒有成功提示或成功 redirect。

## 8. 安全返回與兩個入口

只使用 `return_view=search|calendar` 與來源條件，不接受任意 `next`、URL、scheme、host 或完整返回路徑。

- 搜尋入口帶 keyword、start、end，復用目前搜尋規則：日期嚴格 ISO／非未來、start≤end，查詢需 keyword 或 start；保留既有空白 `/search` 初始頁。返回只由 `urlencode()` 組成 `/search` 及通過驗證的條件。
- 月曆入口帶 month、day，復用 `_calendar_month()`／`_calendar_day()`：本月或過去、日期存在且同月、非未來。返回只組成 `/calendar` 及驗證後條件。
- 每次 GET／POST 都驗證上述條件；可選條件重複、非法或 return_view 不在白名單時，整組捨棄並返回固定 `/search`。未知 next／user_id 不參與 URL 組合。文字 keyword 僅用作搜尋字串，編碼後不會成為外部網址或額外 query。
- 日期／項目更改後仍返回**原來源條件**；帳目可能不再符合原查詢或原日明細，這是實際資料結果。刪除後仍能顯示原日期的空明細。
- 搜尋／月曆明細每筆只增加普通「更改」連結到相同 edit route；頁面不加入 inline 編輯、刪除按鈕、JS 或新的來源查詢。

## 9. 驗收契約（隔離驗證已執行，實際邊界見第 12 節）

使用既有 unittest、OAuth 替身、TestClient、固定 UTC+8 日期與 TemporaryDirectory；先 patch `db.DB_NAME` 再 `db.init_db()`。隔離測試可查 SQL／建立 ABORT trigger 做快照，產品 Web 不 SQL。不得使用本機 launcher、真 OAuth 或正式帳本執行測試。

| 驗收組 | 可核對結果 |
| --- | --- |
| 本人更改 | 五欄成功；例如 120.50→100.25 儲存 cents=10025，revision +1、恰一舊快照；303 後 GET 不新增 action |
| 四種來源 | 手動跨月成功；固定／訂閱／分期更改其他欄成功、日期唯讀且竄改拒絕；recurring 表、source／recurring_id／period 原封不動 |
| 停用／快照 | 保留原停用分類／付款成功；換至另一停用或他人付款失敗；keep 保存歷史名稱，明選啟用 ID 使用目前名稱；選項在開頁後停用仍由核心檢查 |
| 資料隔離 | 他人、不存在、已 voided、undo 新增後的 voided、非 consumption、尚未入帳規則均無法操作；同安全回應、無摘要、無 action |
| CSRF／表單 | GET 不寫入；missing／wrong／duplicate CSRF、JSON body、過多欄位、缺漏／重複業務欄位、非法 ID／revision、query／form user_id 竄改均不越權、不新增紀錄 |
| revision | 多分頁、Bot 先改、undo、分類改名使舊 edit POST／delete GET／delete POST 失效；第二次送相同 POST 也不能重寫；lock 取得前變動由核心攔截 |
| 無效輸入／回填 | 0、負數、NaN、Infinity、1.001、超上限／千分位文字、空 note、201 字、無效／未來日期均拒絕；單值安全回填、自動跳脫，絕不把草稿配上新 revision |
| 軟刪除／確認 | GET 摘要正確且不寫入；POST voided=1、revision +1、舊快照 +1、列仍在；取消不改資料；不執行 DELETE；雙重確認／修改後舊確認遭拒 |
| rollback／安全錯誤 | action INSERT、expense UPDATE、alerts 失敗完整回滾；503 無 private detail／SQL；action_count 與所有業務表快照一致 |
| 還原／同步 | 既有 preview_undo／undo_latest_action 可還原 Web edit／void，revision 增加；新操作使舊 action_id 無效；同期間 sync 不補回 voided，後續期別正常且規則不變 |
| 列表／統計 | 刪除筆不再出現搜尋、日明細；該日最後一筆刪除後記號消失，有其他筆則保留；daily_total、首頁／月報總額及分類／總預算 spent 都減少精確 cents |
| 返回／介面 | 兩入口同頁、來源條件保留；next 外部／scheme-relative／編碼攻擊無效；鍵盤、焦點、窄螢幕、清楚錯誤及確認／取消可用 |

相關測試見 `tests/test_life_ledger_service.py`（owner、stale revision、soft void、undo、void rollback）、`tests/test_spending_phase1.py`（停用選項／付款快照）、`tests/test_spending.py`（分類改名／排程）、`tests/test_write_reliability.py`（寫入 rollback）、`tests/test_web_auth.py`（OAuth／CSRF／搜尋／月曆與 Service 邊界）。本輪補強其中 Service、spending、Web 三檔並重跑相關及全隔離測試。

## 10. 實作檔案與未決事項

| 實作／驗證檔案 | 必要內容 |
| --- | --- |
| `spending.py` | 兩個 ValueError 子型別、分類改名增加帳目 revision、付款選項純讀取參數；不重寫 edit／void／undo |
| `life_ledger_service.py` | 公開同錯誤型別，付款選項 keyword 原樣轉交；其餘帳目 façade 不變 |
| `web/routes.py` | 四路由、固定錯誤、draft／revision、白名單返回、兩列表的帳目連結 context 與完成提示 |
| 新 `web/templates/expense_edit.html`、`expense_delete.html` | 單欄表單及獨立確認摘要 |
| `web/templates/search.html`、`calendar.html` | 普通更改連結與刪除成功提示 |
| `web/templates/error.html` | 最小補充新帳目錯誤標題與 409 重新載入連結，避免刪除衝突只能重整過期 revision 的確認網址 |
| `web/static/web.css`（僅需要時） | 更改／刪除表單必要狀態、焦點或窄螢幕樣式，不調整設定頁 |
| 三個補強測試檔及其餘既有相關測試 | 對應上表隔離驗收，重用 fixture，不另建測試框架 |
| README／CHANGELOG、此規格／計畫 | 實作交付後記錄實際結果、版本與未驗證範圍 |

規劃輪已解決唯一需求衝突：自動來源日期維持唯讀。實作輪已取得使用者授權並重核 worktree、版本與未提交內容，核心行為與文件一致，沒有新增未決規則。真 OAuth、正式 SQLite、Web／Bot 真正並行及實體手機仍未驗證。

**延後需求：** 設定頁展開／收合希望更柔和，避免突然跳動與版面位移；本輪只記錄，不實作動畫或修改 settings 模板／樣式，也不把此需求併入單筆帳目功能。

## 11. 規劃輪文件交付驗證（歷史紀錄）

規劃輪只執行文件差異、tracked／新文件空白檢查與範圍保留核對；README／CHANGELOG 保持 `0.11.15`，新增「尚未實作」文件紀錄。當時未執行功能、Ruff、瀏覽器、OAuth 或正式資料驗證，歷史結果保留於 CHANGELOG 0.11.15。

## 12. 0.11.16 實作交付與驗證

- 已完成規格範圍：同一更改頁、五欄核心修改、唯讀自動日期、摘要確認與軟刪除、所有權與 revision 再核對、安全草稿／錯誤／返回及統計一致性。沒有新增 schema、依賴、架構層、Web 撤銷或設定頁動畫。
- 自動驗證：相關核心／Service／Web 141 項、寫入可靠性 9 項及全隔離 299 項通過；全部使用隔離 SQLite、OAuth 替身及固定測試日期。鎖取得前變動、兩 client 舊版本與回填競態為確定性模擬，未宣稱真 Web／Bot 並行。
- 獨立審查實際重跑 20 項重點測試；非 ASCII CSRF 例外已先補兩操作回歸測試確認 RED，再於共用新 POST 入口安全拒絕，確認 GREEN。刪除確認明示資料保留為軟刪除紀錄。
- 交付檢查補上刪除 409 的固定更改頁重新載入連結與「無法完成操作」標題，先補回歸確認 RED 再修改；共用 error 模板只增加可選內容，其他既有呼叫保留原登入標題。沒有新增帳務規則或錯誤頁架構。
- In-app 實際瀏覽器使用一次性隔離帳本與 OAuth 替身，走過搜尋／每日明細入口、更改成功、錯誤回填、確認／取消／刪除與安全返回。新更改／確認頁在 320／375 px 的 viewport 檢查無水平溢出，唯讀日期、Tab 焦點及長摘要換行可用。搜尋頁既有面板略微超寬（375 px 下 clientWidth=360、scrollWidth=368），保留原樣；未將此既有版面問題併入本功能。
- README／CHANGELOG 版本一致為 0.11.16；Ruff、差異／空白、文件連結及保留範圍檢查通過。無需資料庫初始化或升級；沿用原啟動方式，重啟 Web 載入路由。
- 真 Discord OAuth、其他外部瀏覽器、實體手機、真 Bot／Web 程序並行及共用正式帳本未驗證。不讀寫正式 data.db、Token、.env、備份、匯出或使用者資料，未 stage、commit 或 push。
