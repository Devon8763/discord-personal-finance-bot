# 架構導覽

## 專案目的

這是一個以 Discord 為目前入口的私密消費記帳 Bot；投資記錄則維持為獨立的既有功能。未來會提供網站版，但目前尚未建立網站 API、前端或登入流程。

## 現有主要模組

| 模組 | 目前責任 |
| --- | --- |
| `bot.py` | 啟動 Bot、註冊 Cog／View，串接既有記帳、投資、AI 與安全功能。 |
| `dashboard.py` | Discord 私人看板與消費、投資資訊呈現。 |
| `lifestyle_ui.py` | 生活記帳的 Discord 表單、按鈕與互動流程。 |
| `spending_commands.py` | 消費相關 Discord 指令與互動入口。 |
| `life_ledger_service.py` | 已落地的生活記帳薄 Services façade；提供可重用的消費讀寫、查詢與摘要，並委派給 `spending.py`。 |
| `spending.py` | 生活記帳的既有驗證、交易、revision、撤銷與 SQLite 資料規則。 |
| `ledger.py` | 投資交易與投資歷史的既有核心規則。 |
| `db.py`／`schema.py` | 資料庫位置、連線、單一初始化入口，以及既有資料表與舊欄位相容處理。 |
| `service_safety.py` | 維護、備份與復原等操作的安全檢查。 |
| `life_privacy.py` | 生活資料的隱私、匯出與刪除相關 Discord 流程。 |
| `life_transfer.py` | 生活資料移轉與驗證流程。 |
| 投資相關模組 | `portfolio.py`、`investment_ui.py`、`scraper.py`、`stock_name_map.py` 等，負責投資組合、Discord 介面與行情輔助。 |
| `tests/` | 使用隔離資料庫驗證生活記帳、投資、Discord 流程與相容性。 |

## 現況與整理後目標

**目前狀態**：生活記帳已有 `life_ledger_service.py`，供已整理的表單、指令與看板流程呼叫；它不是完整的全系統 Services 層。投資規則與部分既有生活周邊流程仍直接使用既有核心模組。

```text
Discord UI
  → life_ledger_service.py（生活記帳已落地）
  → spending.py / ledger.py（既有核心規則）
  → db.py / schema.py / SQLite
```

**整理後目標（尚未完成）**：讓 Discord 與未來網站共用一致的服務邊界，並逐步補齊投資與共通設定／錯誤邊界。

```text
Discord UI／未來網站 UI
        → Services 層
        → 生活／投資核心規則
        → SQLite 與設定
```

| 項目 | 狀態 |
| --- | --- |
| `life_ledger_service.py` 的生活記帳 façade | 已完成第一輪；擴展成完整共用 Services 邊界仍屬後續整理。 |
| FastAPI、Discord OAuth、React、App／Expo | 尚未建立；目前沒有 HTTP API、網站前端或登入流程。 |

## 模組邊界規則

- Discord UI 負責互動與顯示，不直接撰寫 SQL；資料規則交給 Services 或既有核心模組。
- 未來網站只能呼叫 Services 層，不能 import Discord 模組。
- 核心資料規則不得 import `discord.py`。
- 所有資料讀寫都必須以 `user_id` 隔離與驗證所有權。
- 開發與測試只能使用隔離資料庫；不得把正式 `data.db`、Token、備份或匯出資料當作測試資料。
- AI 僅讀取完成回應所需資料，不能自動新增或修改帳目。

## 後續整理順序

1. 延續生活記帳 Services 層整理，維持目前已完成的薄 façade 邊界。
2. 維持資料庫初始化程式的集中與舊版相容處理。
3. 統一設定與錯誤邊界。
4. 建立 FastAPI 與 Discord OAuth。
5. 完成網頁第一版。
6. 再處理投資 Services、消耗品週期等後續功能。

## 目前不採用的設計

目前不建立微服務，也不提早導入 Repository、依賴注入、SQLModel、Alembic、PostgreSQL、React 或 Expo。原因是此專案優先目標是個人可用，以及以最少的額外架構快速完成網站第一版；現階段應延用既有 SQLite、交易規則與薄服務邊界。
