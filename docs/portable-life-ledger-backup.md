# 本人生活帳本可移植備份與空白帳本還原 v1

文件核對版本：0.11.27；格式仍為 v1，最初於0.11.24建立；日期：2026-09-29。**Python契約不變；新增瀏覽器純格式／IndexedDB互通合成驗證，沒有正式使用者搬移入口；未完成整個網站 local-first、離線載入或 App 相容。**

## 介面與邊界

- `life_ledger_backup.export_backup(user_id) -> bytes`：同一 SQLite `BEGIN` 讀取快照，產生單一 UTF-8 JSON；只查該擁有者，不讀時鐘，不限制截至今天，不受搜尋顯示上限影響，不初始化預設值、註冊、補記、送提醒或寫檔。
- `life_ledger_backup.read_backup(payload: bytes) -> dict`：完整解析與驗證，不接觸儲存。
- `life_ledger_backup.restore_backup(user_id, payload: bytes) -> dict`：先驗證，再於既有 `BEGIN IMMEDIATE` 交易重新檢查空白目標、映射與還原；回傳各資料清單筆數，不回傳擁有者或 SQLite id。
- `life_ledger_service.export_portable_backup()`／`restore_portable_backup()` 只正規化可信呼叫者的身分字串。Service 不提供登入／授權；未來入口仍需先驗證本人，不能接受備份指定目標擁有者。
- 舊 Web CSV、帳號 ZIP 的合併匯入與主機 SQLite 備份完全保留。**舊 ZIP 仍只攜帶本月及未來預算，且目標已有預算時依原契約略過**；新版則保存所有月份，僅能還原至空白生活帳本。

## 隱私與檔案安全

JSON **未加密，包含個人財務資料**。只在記憶體產生／解析，本輪沒有下載、上傳頁、CLI 或正式備份目錄操作。沒有原 user_id、Discord 身分、OAuth、Token、session、密碼、秘密金鑰、AI同意、投資、其他使用者資料、一般 UI 使用紀錄、通知內容或可重算圖表。

只有下列白名單純資料欄位；不存在 HTML／SQL 程式、可執行欄位或檔案路徑。用途等文字作為普通字串完整保留，還原不執行、不渲染字串；未來呈現端仍須依自身安全規則顯示文字。本版沒有 SHA-256、簽章或來源認證；結構驗證不能證明來源可信，也不能辨識被人改成另一組合法金額的檔案。

不攜帶舊 AI 同意，也不修改目標既有帳號安全／投資狀態；目前沒有 AI 同意入口。排除 `spending_onboarding` 導覽紀錄及 `spending_notices` 的內容／送出狀態／去重鍵。**還原不產生或重播通知**；後續正常操作若重算歷史預算門檻，可能再次建立已在來源提醒過的通知，未來入口需明確處理，不能宣稱已保留提醒去重能力。

## 格式、型別與必需欄位

頂層必須恰為 `format, version, data`；`format = "life-ledger-backup"`、`version = 1`。以下 data 的9個鍵全部必需，空資料使用空清單與明確 null：

```json
{"format":"life-ledger-backup","version":1,"data":{"expenses":[],"categories":[],"payment_sources":[],"budgets":[],"recurring_rules":[],"recurring_versions":[],"shortcuts":[],"actions":[],"settings":{"reminder_levels":null,"recording_started_on":null}}}
```

| data 清單 | 每筆必需欄位 |
| --- | --- |
| `expenses` | `id, spent_on, cents, category, note, source, recurring_id, period, voided, payment_source_id, payment_source_name, kind, revision` |
| `categories` | `name, active` |
| `payment_sources` | `id, name, active` |
| `budgets` | `month, category, cents` |
| `recurring_rules` | `id, name, cents, category, kind, start_month, periods, due_day, active, revision` |
| `recurring_versions` | `id, recurring_id, effective_month, name, cents, category, due_day` |
| `shortcuts` | `id, name, category, payment_source_id, note, cents, position, active` |
| `actions` | `id, expense_id, before, undone` |

- id 與引用是備份內的字串識別，符合 `[a-z][a-z0-9_-]{0,63}`，各清單 id 跨清單唯一。匯出以來源 SQLite id 排序，建立 `e1/p1/r1/v1/s1/a1` 等內部標記，不暴露原數字 id；重複匯出未改動來源具有固定順序。同一備份內引用穩定，不宣稱是跨備份永久身分。
- 金額單位為台幣分（1元＝100 cents），沒有多幣別。cents 一律是規範的十進位整數字串，不接受符號、前置零、小數、指數、bool 或 JSON數字；消費／規則／非空捷徑須正數，預算允許保存既有明確零元列。單值不超過 SQLite signed 64-bit 整數最大值9223372036854775807；空捷徑金額使用 null，不當成零。統計／未來瀏覽器需使用精確整數，不能轉 float 後加總。
- `revision, periods, position` 是非負 JSON整數，不接受 bool，最大值同 SQLite；`active, voided, undone` 恰為整數0或1。日期嚴格 `YYYY-MM-DD`、月份嚴格 `YYYY-MM`，且日期必須存在。文字為 UTF-8，名稱／分類／付款名稱不可全空白，用途可保留歷史空字串；不得含NUL或未配對 surrogate。
- 消費 `kind` 恰為 consumption，`source` 為 manual／固定／訂閱／分期；有效與voided皆保留，已儲存的未來日期也保留，未產生的預測不匯出。付款 id 可 null，付款名稱快照與目前名稱互相獨立。
- 規則 `kind` 為固定／訂閱／分期；固定／訂閱 `periods=0`，分期為正整數；`due_day` 為1～31或歷史null（沿原核心視為1日）。不套用目前新增表單的開始月份、分類啟用狀態、金額上限或分期期數上限至合法歷史資料。
- `categories` 只保存實際 override 列，不把內建預設分類補成明確設定；名稱唯一，「總額」不當分類。付款名稱本人唯一，現金／未指定如存在須保持啟用；不存在就不補建。預算保存全部月份／停用與歷史分類，不補未設定的總額或分類；不重算／修補歷史預算合計。

## 關聯、操作歷史與 ID 映射

- 消費的付款引用指向同一備份付款方式；捷徑的付款引用必需。分類是名稱關聯，歷史分類不一定仍有 override／啟用設定，不強制重新建立或啟用。
- 自動消費有規則引用時，規則種類必須與來源一致；`period` 必需，與帳目日期同月、不早於規則開始，有限分期不得超過期數；規則＋月份唯一包含voided。既有「月初提前入帳」歷史日期不依新due_day重算。
- 沒有規則引用時 `period` 必須null。舊 ZIP 合併匯入可留下沒有規則的固定／訂閱／分期來源，這種合法歷史保留為未關聯資料，不虛構規則或入帳標記。
- 版本引用固定規則，生效月份不早於開始月份，規則＋生效月份唯一；保存全部歷史／待生效設定與due_day，不重寫規則目前設定，不另外生成版本。
- `actions` 按來源操作id升冪輸出，**清單順序就是操作順序**。`before` 是原 before_json 解析後的null或完整 `expenses` 形狀物件；移除原擁有者，id／付款／規則引用亦改為備份內字串。快照必須指向該操作的同一帳目，所有關聯必須在備份內；source／recurring_id／period／kind 須與目前帳目一致，因既有撤銷不回寫這些欄位。自動帳目的歷史快照日期可在同一月份內不同，不套用現行表單禁止改日限制。
- 來源原操作快照若缺少舊 schema 後增的4欄，依已存在的相容規則保存為 `payment_source_id=null, payment_source_name="未指定", kind="consumption", revision=0`；不補其他缺失、不接受未知欄位／外來擁有者或非法引用。這是舊schema預設值的明確相容，不修復損壞資料。
- 還原依付款方式→規則→版本→消費→捷徑→操作的關聯順序插入，由目標 SQLite 產生新數字id；建立每類完整映射，版本映射至規則id＋生效月。操作快照重建目標 user_id／數字id，保留歷史付款名稱、日期、金額、revision及undone，不逐筆呼叫新增消費，不另生成操作歷史。
- 原 `spending.undo()` 的最新操作id確認方式不變；還原後最近未撤銷操作仍可沿原規則撤銷，還原時不觸發撤銷、不重設 revision。自動消費唯一標記包含撤銷帳目，再同步不會重建／復活。

## 必要設定白名單

| settings 欄位 | 儲存位置／型別／意義 |
| --- | --- |
| `reminder_levels` | 對應 `spending_settings.levels`；null表示沒有設定列、保留核心預設80／100；清單最多10個不重複的1～1000整數，空清單代表明確停用，不等同null |
| `recording_started_on` | 對應 `spending_users.started`；ISO日期或null，保留記錄涵蓋起點，不因還原改成今天／另註冊 |

不加入其他設定欄位；明確拒絕備份內身分、認證、AI偏好、導覽與通知欄位。

## 空白目標與交易

對目標擁有者，以下任一表有任何資料列就拒絕，包含停用、voided、非consumption、孤立紀錄、預設付款方式及UI／通知紀錄：expenses、expense_actions、budgets、spending_categories、payment_sources、recurring_expenses、recurring_expense_versions、spending_shortcuts、spending_settings、spending_users、spending_onboarding、spending_notices。

其他擁有者的資料與目標投資／`users`／`ai_preferences` 不屬於此空白判定，也不修改。沒有覆蓋、合併、刪除或清除功能；不因拒絕而替使用者整理目標。完全空的來源還原不建立預設資料。

完整解析後，先取得原交易寫入鎖再重新確認空白，所有白名單INSERT同一交易完成，失敗rollback，包括已插入的規則、操作紀錄與SQLite序號。`PortableBackupError`／`NonemptyLedgerError` 是分類固定、安全的資料錯誤；寫入失敗為 `PortableRestoreError`，不回傳SQL、原例外或帳目內容。沒有資料表初始化／schema升級。

## 大小與結構限制

- 只接受bytes，單一檔案最多16MiB，嚴格UTF-8，不接受BOM／ZIP／SQLite／任意物件。
- 解碼前限制位元組；JSON解析前掃描結構深度，最多12層；拒絕重複key、NaN、Infinity與所有浮點／指數JSON數值。必要欄位集合精確比對，未知欄位也拒絕。
- 8個資料清單總合最多100000筆，包含操作與版本；設定／嵌套快照另依固定形狀限制，不視為額外資料列。單一文字最多4096個字元。
- 匯出套用相同型別／關聯／大小限制。來源過大或存在異常即拒絕並指出類型，沒有靜默截斷、丟棄或修正。日期／cents不依現在表單改寫，範圍不依今天裁切。

## 0.11.24 Python 核心驗證紀錄

合成資料與暫存SQLite驗證：四來源／有效與撤銷／未來已存、精確小數及超過JavaScript安全整數的合計、跨月／年／短月、全部月份與零元／停用預算、override／付款改名快照、規則版本／有限分期、捷徑／操作ID及撤銷順序、空資料／只有設定、10001筆完整消費、本人隔離、唯讀無預設寫入、嚴格格式／大小／關聯、安全錯誤、故障rollback、交錯WAL讀取快照及兩連線競爭空白還原。

還原前後以同一基準日期核對摘要、圖表、比較及重新匯出的中立內容，另在隔離帳本補記驗證唯一入帳／撤銷不復活。完整測試數、Ruff與原內容保存結果見CHANGELOG。隔離兩連線不是正式Web／Bot並行驗證；本輪未用正式資料、真OAuth、瀏覽器／App、實體手機或正式備份／搬移，也未宣稱所有歷史異常都能修復。

## 0.11.26 瀏覽器互通驗證

`local-first/backup.mjs` 對照本契約，以本機 lossless-json 4.3.1 保真解析／序列化；safe小整數為Number，超過JavaScript安全範圍的整數為原生BigInt，IndexedDB不保存套件類別。cents／ID保持字串，再匯出revision／periods／position等仍為JSON整數token，不直接對完整備份使用一般JSON.parse／JSON.stringify。

全部九類資料與引用／before／undone／操作順序保存，合法歷史值不套用新增表單限制。備份內ID在空白IndexedDB可用，故原樣保留；重新還原至SQLite仍由既有Python核心映射新數字ID。原完整格式上限不變；目前瀏覽器另限制200筆消費（含voided）／1000筆操作，超限拒絕整份，不能把Python可接受100000列當成目前瀏覽器容量。

還原僅對專用 `discordbot-localfirst-synthetic-v1-portable-checks`，完整驗證後readwrite交易確認整個store無鍵；帳本／標記一起完成或rollback，非空／初始化／預設拒絕且不清除。readonly匯出不初始化、同步或修改。主驗證頁無匯入／下载按鈕，產品解析與儲存不呼叫PythonAPI；Python僅生成／核對合成測試。

實際流程已驗證：Python匯出 → 原生瀏覽器保真驗證 → 空白IndexedDB原子還原 → 同來源關閉重開 → 保真再匯出 → Python read_backup／暫存SQLite空白還原；全部中立欄位、引用、快照及順序相等，同基準摘要／比較、撤銷與補記語意一致。**0.11.27已修正Python歷史零元預算讀取：總預算／分類預算0元保持明確設定，month_report()保留精確已花／剩餘cents，used_percent為None；零元不產生百分比提醒。來源與還原後摘要及全部中立資料一致，新增／更改預算仍只接受正整數台幣，格式v1不變。本次只做隔離回歸，沒有重跑實際瀏覽器或正式搬移。**

詳見[瀏覽器驗證文件](local-first-browser-validation.md)與[第三方授權](../THIRD_PARTY_LICENSES.md)。未加密／排除通知狀態等注意事項不變；未做正式備份／搬移／使用者入口、覆蓋／合併、離線載入、跨瀏覽器／實體手機或App。現有Web／Bot啟動流程完全不變，不需SQLite初始化／升級。
