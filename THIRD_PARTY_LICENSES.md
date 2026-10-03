# 第三方資源與授權

## Chart.js 4.5.1

- 用途：生活記帳 Web `/compare` 與獨立 local-first「支出比較」圖表；local-first 僅送必要彙總及精確提示文字，使用者執行比較後才載入，不新增套件。
- 官方版本：[v4.5.1](https://github.com/chartjs/Chart.js/releases/tag/v4.5.1)，2025-10-13 發行；2026-09-29 核對 GitHub 最新穩定發行。
- 來源：[官方發行套件 chart.js-4.5.1.tgz](https://github.com/chartjs/Chart.js/releases/download/v4.5.1/chart.js-4.5.1.tgz)。沒有 npm 安裝或 Node 建置。
- 本機檔案：[chart.umd.min.js](web/static/vendor/chartjs-4.5.1/chart.umd.min.js) 與其原始 [source map](web/static/vendor/chartjs-4.5.1/chart.umd.min.js.map)，均逐位元保留官方套件內容，不修改壓縮程式或 sourceMappingURL。
- 授權：MIT；保留套件內的完整 [LICENSE.md](web/static/vendor/chartjs-4.5.1/LICENSE.md)（2014–2024 Chart.js Contributors）及 UMD 原有 2025 版權聲明，不自行改年份。
- 套件與各檔案 SHA-256、來源與版本見 [provenance.json](web/static/vendor/chartjs-4.5.1/provenance.json)。

## 內嵌 @kurkle/color 0.3.2

- Chart.js UMD／source map 內含此色彩函式庫，版本由官方 source map 的原始模組路徑與標頭核對；不另載入外部腳本。
- 官方來源：[kurkle/color v0.3.2](https://github.com/kurkle/color/tree/v0.3.2)。
- 授權：MIT；完整 [kurkle-color-LICENSE.md](web/static/vendor/chartjs-4.5.1/kurkle-color-LICENSE.md) 直接保留[該版官方授權](https://raw.githubusercontent.com/kurkle/color/v0.3.2/LICENSE.md)（2018–2021 Jukka Kurkela），UMD／source map 原有 2023 版權聲明亦保留。

圖表資源由本機 `/static/` 提供，不使用 CDN、外部字型、圖表 API、adapter、外掛或遙測。圖表呈現不需對外下載；Discord OAuth 登入仍需要既有網路服務，並非離線登入。

local-first 固定入口以明確白名單 `/local-first/vendor/chartjs-4.5.1/chart.umd.min.js` 直接提供上述原檔，沒有另複製或修改發行內容，不開放其他 Web 檔案；必要 JS 列入完整離線快取。無需啟動 Python Web、登入或 CDN，source map／授權／provenance 原檔留於專案供稽核，不列入執行白名單。四檔 SHA-256 與既有 provenance 核對測試通過，Chart.js 與內嵌 @kurkle/color 的 MIT 聲明保留。

## lossless-json 4.3.1

- 用途：獨立 `local-first/backup.mjs` 保真解析／序列化生活帳本 JSON v1；大整數 token 先轉 BigInt，再依安全範圍轉小整數 Number，IndexedDB 不保存 LosslessNumber 類別。生活帳本驗證由本專案負責。
- 官方來源：[josdejong/lossless-json](https://github.com/josdejong/lossless-json)；2026-09-29 核對官方套件的穩定版為 4.3.1。GitHub Releases 沒有發行檔，採作者發布的 [npm 官方套件 4.3.1](https://registry.npmjs.org/lossless-json/-/lossless-json-4.3.1.tgz)，下載校對 registry SHA-512 integrity，固定版本，不進行 npm 安裝。
- 本機 [lossless-json.js](local-first/vendor/lossless-json-4.3.1/lossless-json.js) 是原 `lib/umd/lossless-json.js`；保留原 [source map](local-first/vendor/lossless-json-4.3.1/lossless-json.js.map) 與完整 [LICENSE.md](local-first/vendor/lossless-json-4.3.1/LICENSE.md)。三檔逐位元保留原發行內容，沒有修改壓縮程式或 sourceMappingURL。
- MIT；Copyright (c) 2016–2026 Jos de Jong。核對原 package.json 沒有執行期依賴，source map 的八個來源皆為本套件模組，沒有額外內嵌第三方執行期資源需補授權。開發／建置相依沒有另行安裝或散布。
- 套件與三檔 SHA-256、版本、來源、integrity 及內嵌來源見 [provenance.json](local-first/vendor/lossless-json-4.3.1/provenance.json)。雜湊用於核對所保存發行檔；不是帳本備份的簽章或來源認證。
- 原生 JS 模組在瀏覽器以本機 UMD 副作用匯入，Node 使用相同發行檔的 CommonJS 匯出；兩種環境均已執行驗證。沒有 CDN、遠端 import、遙測、npm 專案／lockfile／建置流程或 Python 依賴；不改既有 Python Web／Bot 的載入方式。
