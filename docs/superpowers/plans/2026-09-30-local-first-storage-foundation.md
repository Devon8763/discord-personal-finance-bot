# Local-first Storage Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將合成 local-first 帳本升級為逐筆 IndexedDB 儲存，保留 JSON v1 完整往返、原子記帳與失敗保護；仍不開放真實帳目。

**Architecture:** 在既有 `ledger.mjs` 公開 API 後使用原生 IndexedDB 版本 2 stores；舊版本 1 的整本物件只在原子升級時讀取。`backup.mjs` 仍是唯一 JSON v1 驗證／序列化來源，Python 與瀏覽器同步 64 MiB 上限。規則留在 `rules.mjs`，不新增泛用 Repository、框架或依賴。

**Tech Stack:** 原生 JavaScript modules、IndexedDB、Service Worker、Node test、既有 Python unittest／Ruff、本機靜態預覽與隔離瀏覽器。

**Spec:** `docs/superpowers/specs/2026-09-30-local-first-storage-foundation-design.md`

## Global Constraints

- 只用合成資料；不得讀寫正式 `data.db`、`.env`、Token、既有備份或使用者資料。Python Web／Bot 功能與啟動方式不變。
- 保留 `ledger.mjs` 現有公開函式與回傳 state 形狀，精確 cents、revision、軟刪除、`before` 快照和付款名稱快照不變；不補記自動來源。
- JSON v1 欄位與版本不變；Python／瀏覽器位元組上限同步由 16 MiB 改為 64 MiB，100000 總列及其他驗證界限不放寬。200／1000 測試上限移除後，寫入仍不得造成無法完整匯出的帳本。
- IndexedDB 版本 1 → 2 與空白還原必須原子完成；未知／損壞／未來版本、配額、阻擋、競態、舊 revision 一律保資料並顯示安全錯誤。
- 程式仍只在本機合成頁使用，不部署正式 HTTPS、不加入帳號／雲端／加密／同步／新業務功能。`Service Worker` 只快取固定靜態程式。
- 每項任務先測 RED、再做最小改動、確認 GREEN；每項驗證後做本機 commit，**不 push**。最後依 `AGENTS.md` 更新 README 與繁體中文 CHANGELOG；若起點仍為 0.12.2，這批儲存核心更新記為 0.13.0。

## Review Focus

1. 舊頁籤佔用版本 1：升級被阻擋須提示關閉舊頁籤，不刪除／覆寫舊帳本（Task 2 測）。
2. 單一消費更新成功、操作寫入失敗：整個 transaction abort，revision／操作順序／位元組計數皆不變（Task 3 測）。
3. 64 MiB 或 100000 列邊界：最後一筆超限交易拒絕，先前帳本仍能完整匯出（Task 3／4 測）。
4. 大整數 revision、隨機 action ID 和還原後陣列順序：不得失精、不得以 ID 排序改寫最新操作或備份位元組（Task 2／4 測）。
5. 靜態程式升級中缺檔、停服離線冷啟動：舊可用快取保留；失敗頁不讀寫帳本、不回顯底層錯誤（Task 5 測）。

---

### Task 1: JSON v1 容量契約兩端一致

**Files:** Modify `local-first/backup.mjs`, `life_ledger_backup.py`, `local-first/page.mjs`, `tests/test_local_first_backup.mjs`, `tests/test_portable_life_backup.py`.

**Interfaces:** `MAX_BYTES = 64 * 1024 * 1024`（JS）、`MAX_BYTES = 64 * 1024 * 1024`（Python）；`readBackup`／`writeBackup`、`read_backup`／`export_backup` 的格式與其他限制不變。

- [ ] **Step 1: 寫 RED 測試。** JS／Python 都接受合法、UTF-8 長度大於 16 MiB 且小於等於 64 MiB 的合成 JSON v1，拒絕 64 MiB + 1 byte；`version=1`、欄位、100001 列、過長文字與惡意鍵仍照舊拒絕。頁面過大提示改為 64 MiB，不能把底層訊息寫出來。
- [ ] **Step 2: 跑聚焦測試確認 RED。** `node --test tests/test_local_first_backup.mjs`；`& '.\.venv\Scripts\python.exe' -m unittest tests.test_portable_life_backup -v`（若該檔不支援 module import，改用 `unittest discover` 並記錄原因）。
- [ ] **Step 3: 只改兩端常數與頁面提示。** 不更改 JSON v1 格式、其他界限或現有 Python 正式帳本。確認較小舊備份仍可讀，大檔舊版讀取限制在文件中如實標示。
- [ ] **Step 4: 同組測試 GREEN；檢查 `git diff --check`、本輪檔案與本機 commit。**

### Task 2: 版本 2 schema、原子升級及唯讀快照

**Files:** Create `local-first/idb.mjs`, `tests/local_first_storage_browser.mjs`, `tests/local_first_storage_browser.html`; Modify `tests/local_first_preview.mjs` 只增加測試／靜態模組白名單。

**Interfaces:** `openLedgerDatabase(name: string) -> Promise<IDBDatabase>`（開版本 2）、`readSnapshot(db: IDBDatabase, optional = false) -> Promise<State | null>`；`State` 與現有 `readLedger()` 回傳的九類陣列及 `format/version/owner` 相同。`idb.mjs` 專管原生 stores／交易，不建立通用儲存介面。

- [ ] **Step 1: 寫 RED 瀏覽器案例。** 只用新的 `-storage-checks` 合成資料庫：空白 v2 唯讀返回 null；手動種入版本 1 狀態 1／2 與 `created`，升級後九類逐欄、陣列順序與 `writeBackup` bytes 不變；大 revision 保留 BigInt。再測損壞狀態、未知欄位、失敗中止、較新 DB 版本及舊頁籤阻擋，皆不消失原資料。
- [ ] **Step 2: 跑新瀏覽器案例確認 RED。** 用 `node tests/local_first_preview.mjs` 啟動隔離預覽，再開 `/tests/local_first_storage_browser.html`；不得清理主頁 `DB_NAME`。
- [ ] **Step 3: 實作最小 v2 schema／升級／讀取。** 九類 stores 和 `meta` 的鍵依規格；陣列順序用不輸出到備份的內部序位保存，action 順序不可依隨機 ID 排。升級驗證和複製都在同一 `versionchange` 交易；只有成功才移除舊 store。`meta` 保存 `created`、`next_action_order`、`row_count`、`backup_bytes`，後兩項由完整 JSON v1 精確算出。阻擋／錯誤只回固定安全訊息。
- [ ] **Step 4: 新案例 GREEN；重新開連線再讀仍一致；檢查差異後本機 commit。** 此時舊頁面仍用 `ledger.mjs` 的版本 1 API，不指向 v2 測試 DB。

### Task 3: 既有帳本 API 改用逐筆交易

**Files:** Modify `local-first/idb.mjs`, `local-first/ledger.mjs`, `tests/local_first_browser.mjs`, `tests/local_first_portable_browser.mjs`, `tests/local_first_ui_browser.mjs`, `tests/local_first_entry_browser.mjs`, `tests/test_local_first_backup.mjs`；必要時只更新既有測試頁的隔離 helper，不新增產品框架。

**Interfaces:** `ledger.mjs` 繼續匯出 `DB_NAME`、`openDatabase`、`readLedgerIfPresent`、`readLedger`、`initializeLedger`、`addExpense`、`editExpense`、`voidExpense`、`restorePortableBackup`、`exportPortableBackup` 及 `LedgerError`，參數／回傳契約不變。底層只改所需列、操作及 `meta`，不覆寫整本物件。

- [ ] **Step 1: 更新直接操作舊 `ledger` store 的隔離測試，先確認 RED。** 驗證初次建立一次、唯讀無寫入、手動新增／更改／軟刪除、禁用分類、兩連線競態與舊 revision。故障注入在帳目 request 成功後中止交易，驗證帳目、action、meta 全部不變。
- [ ] **Step 2: 實作 v2 的現有公開 API。** 同一 `readwrite` 交易重新查本人有效手動帳目與 revision，重用 `rules.mjs` 驗證；新增／更改／軟刪除把帳目、before action、順序與 meta 一起寫入。兩頁同時寫入由交易序列化，不自製鎖。原頁入口及安全錯誤語意不變。
- [ ] **Step 3: 實作精確容量計數。** 每列的既有 JSON serializer UTF-8 位元組長度加上固定包裝／逗號，增量更新 `meta.backup_bytes` 與 `row_count`；交易提交前檢查 `MAX_BYTES` 及 100000 列。匯出時將實際完整 bytes 與 meta 比對，不一致視為損壞、停止寫入且不自動修復。測一筆剛好可寫、再加一筆超限拒絕的邊界，不讓快照不可匯出。
- [ ] **Step 4: 實作 v2 完整匯出與空白還原。** 匯出同一 readonly 交易讀九類；還原先 `readBackup()`，交易內再確認所有 stores 與 meta 皆空，完整寫入或 abort。保留來源限制：只有現有主合成頁明確確認或 `-portable-checks` 等專用合成測試 DB 可還原；其他 DB 名稱不得趁改造放寬。
- [ ] **Step 5: 聚焦瀏覽器測試 GREEN。** 原生帳本、互通、入口、頁面故障案例均更新為 v2 schema，原先 200／1000 拒絕案例改成 64 MiB／100000 列邊界；不再直接清除或種入主頁 DB。測試新增／更改／軟刪除後 `exportPortableBackup()`，與 Python 合成暫存還原核對。檢查差異後本機 commit。

### Task 4: 大量資料、離線與安全回歸

**Files:** Modify `tests/local_first_storage_browser.mjs`, `tests/local_first_portable_browser.mjs`, `tests/local_first_entry_browser.mjs`, `tests/test_local_first_offline.mjs`, `tests/local_first_preview.mjs`, `local-first/sw.js`；若實測證明瓶頸或不一致，只修本規格涉及的 `local-first/idb.mjs`／`ledger.mjs`／`backup.mjs`。

**Interfaces:** 不新增公開產品 API；輸出可重跑的合成資料基準與實測記錄。

- [ ] **Step 1: 先加入大量／故障 RED 案例。** 至少 20000 消費＋50000 操作，含中文／惡意文字、已撤銷、自動來源、歷史版本、極端精確值；完整備份須 ≤64 MiB 並可空白還原、再匯出、關頁重開。另測 64 MiB／100000 列邊界、配額 abort、重複還原、未知版本、兩頁競態與 XSS 純文字呈現。
- [ ] **Step 2: 修至 GREEN，記錄實際環境與時間。** 不以合成測試推稱實體手機或真實配額耗盡已驗證；若大樣本超限或跑不完，停下修訂規格，不縮小樣本卻宣稱達標。
- [ ] **Step 3: 更新本機靜態資源白名單與 Service Worker 快取版本。** 新 `idb.mjs` 必須可在停止預覽服務、關閉原頁後同網址冷啟動；缺檔更新時舊快取保留。CacheStorage、網址、Resource Timing 不得有帳目、備份或外部寫入。
- [ ] **Step 4: 跑完整驗證。** `node --test tests/test_local_first_rules.mjs tests/test_local_first_backup.mjs tests/test_local_first_offline.mjs`；隔離瀏覽器頁面與 Node 互通、`& '.\.venv\Scripts\python.exe' tests/run_discord_validation.py`、`& '.\.venv\Scripts\python.exe' -m ruff check .`、全部修改的 `.mjs`／`.js` 語法檢查、`git diff --check`。若 CRLF 造成既有誤報，另做 `cr-at-eol` 相容檢查並如實回報原命令結果。檢查後本機 commit。

### Task 5: 文件與交付邊界

**Files:** Modify `README.md`, `CHANGELOG.md`, `docs/local-first-browser-validation.md`, `docs/local-first-preparation.md`, `docs/portable-life-ledger-backup.md`, `docs/superpowers/specs/2026-09-30-local-first-storage-foundation-design.md`（只標記實際完成／偏差）。

**Interfaces:** 文件精確對照實作，不建立新產品功能。

- [ ] **Step 1: 只記錄實際結果。** 若起點仍是 0.12.2，README／CHANGELOG 記 0.13.0；列 schema 升級、64 MiB 雙端限制、舊版大檔不相容、啟動是否改變、實測容量／時間、實際測試項數及未驗證項目。
- [ ] **Step 2: 保留明顯警告。** 本機頁仍只供合成資料；未加密備份、正式可信 HTTPS 來源、功能對照、真實資料搬移、跨瀏覽器／實體手機和正式資料保護尚未完成。不得把本輪寫成 Python Web 已被取代。
- [ ] **Step 3: 核對只變更規格範圍檔案、無敏感資料與外部依賴；再跑相關測試及差異檢查，做最後本機 commit，不 push。** 回報 commit、文件連結與未驗證事項。
