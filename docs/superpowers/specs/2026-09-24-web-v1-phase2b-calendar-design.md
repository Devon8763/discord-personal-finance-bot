# localhost Web v1 第二階段 B：完整月曆頁設計

> **狀態：已確認設計、尚未實作。**
>
> 本文件只定義 localhost Web v1 的完整月曆頁。它不代表功能已完成；本輪只建立這份規格與配套計畫，不修改 Python、HTML、CSS、測試、依賴、資料庫、設定、版本紀錄，也不 stage、commit 或 push。

## 1. 目標與使用者流程

本階段在已登入首頁的快速記帳表單旁新增一個「查看月曆」連結，導向完整月曆頁；不在首頁加入小月曆預覽或預算雙卡。

使用者流程固定為：

```text
首頁的「查看月曆」
  → /calendar（預設本月）
  → 上個月／下個月／回到本月
  → 點選日期
  → 在同頁月曆下方查看該日合計與明細
```

- 月曆只顯示生活 `consumption` 的「當日已有記錄」小記號，不在日期格中顯示金額、分類、用途或付款方式。
- 點選日期才顯示資料。沒有消費的日期以固定空狀態顯示；沒有消費的月份仍完整顯示可點日期的月曆。
- 使用者只能檢視本月及過去月份，不能檢視未來月份。

## 2. 身分、權限與資料流

本機 Web 仍只監聽 `127.0.0.1`，由 Discord OAuth session 的已驗證 Discord ID 判定身分。

```text
GET /calendar
  → auth.current_user_id(request.session)
  → life_ledger_service.py
  → spending.py
  → SQLite
```

- `user_id` 一律取自 `auth.current_user_id(request.session)`；`month` 與 `day` 只可選擇畫面內容，絕不可作為身分。
- route 不得 import 或呼叫 `db.py`、`spending.py`、`ledger.py`，不得自行寫 SQL。
- 未登入者不可讀取月曆、日期記號、日合計或明細，維持既有登入頁的安全行為。
- 所有讀取都以 session 使用者為界。其他 Discord ID 即使出現在 URL query、form 或資料庫 ID 中，也不能讀取或影響結果。
- 已撤銷帳目不得出現在月曆記號、日合計或每日明細。

## 3. 既有 Services 與資料語意

本階段只重用既有 Services，不新增方法、資料表、欄位、索引或依賴：

```python
life_ledger_service.get_calendar_days(user_id, month)
life_ledger_service.list_expenses(
    user_id, month, include_voided=False, limit=None, offset=0
)
```

已核對的實際行為如下：

- `get_calendar_days(user_id, "YYYY-MM")` 回傳該月每一天的 `date`、`cents`、`level`。資料以 `user_id`、`voided=0`、`kind='consumption'` 篩選；未來月份或格式錯誤會拋出 `ValueError`。
- 日期格只以 `cents > 0` 決定是否有小記號；不可輸出 `cents` 或 `level` 造成金額／深淺視覺效果。
- `list_expenses(user_id, "YYYY-MM")` 預設回傳 `{'items': [...], 'total': ...}`；`items` 以使用者、該月份、`voided=0`、`kind='consumption'` 篩選，不依 `source` 排除已正式寫入的固定／訂閱／分期生活消費。

月曆日期記號、選取日合計與選取日明細必須使用完全相同的資料範圍：目前登入使用者本人、已入帳、有效、未撤銷且 `kind='consumption'` 的所有生活消費。route 在取得該月 `list_expenses(...)["items"]` 後，僅以 `spent_on == selected_day.isoformat()` 篩選選取日，並由同一清單加總。因此，手動快速記帳及既有固定／訂閱／分期等已正式入帳項目，都會同時反映於記號、日合計與日明細；已撤銷、他人資料、投資、收入、轉帳及尚未入帳的未來預測都不會出現。

每日明細可安全顯示：日期、格式化後的消費合計、每筆金額、用途、分類及既有付款方式名稱。不得顯示 Discord ID、帳目 ID、revision、`voided`、來源內部值、SQLite 資訊、檔案路徑或其他使用者資料。

## 4. URL、月份與日期規則

月曆入口為 `GET /calendar`。

- `month` query 的唯一格式是嚴格的 `YYYY-MM`；省略時使用台灣時區今天所在月份。
- `day` 是選取日期的可選 query，唯一格式是嚴格的 `YYYY-MM-DD`；它必須屬於目前 `month`，且不可在未來。
- `month` 不合法、`day` 不合法或不屬於所選月、或任一值在未來時，回應 HTTP 400 的固定安全錯誤頁。錯誤頁不得回傳服務例外、SQLite、Traceback、Token、檔案路徑、Discord ID 或帳務內容。
- URL 不接受、也不使用 `user_id`。多餘的 query 不改變身分或資料範圍。

月份導覽：

- 上個月始終連到前一個有效月份。
- 回到本月連到目前台灣月份。
- 若目前檢視過去月份，下個月連到下一個月；若目前已是本月，下個月呈現不可點擊的 disabled 文字／按鈕，不產生未來月份 URL。

## 5. 畫面與可用性

月曆頁採伺服器產生 HTML 與 CSS grid，不使用 Discord 的文字月曆格式、JavaScript 框架或日期套件。

```text
[上個月]  2026 年 09 月  [回到本月]  [下個月／停用]
 Mon  Tue  Wed  Thu  Fri  Sat  Sun
 [空] [ 1] [ 2•] ...
 ...

已選日期：2026-09-02
當日消費合計：NT$...
明細，或「此日期沒有已記錄的消費。」
```

- 星期標題固定使用英文：`Mon Tue Wed Thu Fri Sat Sun`。
- CSS grid 固定 Monday-first；月初之前的空格數使用 Python `date.weekday()`（Monday 為 0），使 28、29、30、31 天月份保持欄位對齊。
- 每個真實日期格是帶可辨識 label 的連結／按鈕；有消費時再加一個非金額小記號，已選日期有明確選取樣式。
- 選取日明細永遠位於月曆下方，不覆蓋格線或以浮動視窗遮蔽日期。
- 手機寬度仍保留七欄格線；縮小格距與字級，確保日期可點擊、星期與記號仍清楚，日明細維持單欄在下方。不做首頁小工具、拖曳排版或行動 App。

## 6. 固定狀態與安全錯誤

- 無消費月份：月曆完整顯示、沒有任何記號，並顯示固定「本月尚無已記錄的消費。」狀態。
- 無消費日期：只有該日期沒有任何有效 `consumption` 時，選取後才顯示固定「此日期沒有已記錄的消費。」狀態與零合計，不帶出其他日期資料。
- 有任何有效 `consumption` 的日期，日合計與明細都必須完整呈現，能解釋同一日期格的記號；已撤銷與他人資料不得出現。
- 服務讀取發生預期的 `ValueError`（如無效月份）或安全可處理的讀取失敗時，一律用固定泛化文案回應，不顯示內部細節。
- 不在本階段新增讀取快取、JSON API、客戶端狀態、AJAX 或資料回寫。

## 7. 明確不在本輪範圍

- 預算、首頁雙卡／小月曆預覽、搜尋頁、設定頁、分類／付款方式管理、固定支出管理、投資、AI、匯出、備份、JSON API、React 與 App。
- 編輯、撤銷、刪除或建立任何非首頁快速記帳的帳務操作。
- 資料庫結構、Services 介面、核心驗證規則、OAuth 設定、依賴、遷移或架構層。

不新增 Service、資料庫或依賴的原因是既有月曆摘要和使用者隔離搜尋已能完成本階段所需的最小讀取流程。先保持薄 route 與既有核心規則，避免為單一頁面提早加入 ORM、Repository、DI、日期元件或 API 層。
