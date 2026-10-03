# 本機完整加密備份封裝 v1

0.13.6，2026-10-02。**僅供合成資料測試，請勿輸入真實帳目。** 帳本開啟不需密碼；IndexedDB 未加密，Python Web 尚未被取代。密碼只保護下載檔，忘記密碼無法取回；不加密 JSON 可被任何取得檔案的人讀取。

## 二進位格式（`.llbk`）

封裝版本與內部 JSON 版本獨立。沒有壓縮、Base64、附帶帳目名稱或明文摘要。所有多位元組整數均為 unsigned big-endian；長度以 bytes 計。

| Offset | 長度 | 值／用途 |
| --- | --- | --- |
| 0 | 8 | Magic：`4c 4c 42 4b 45 4e 43 00`，即 `LLBKENC\0` |
| 8 | 1 | 封裝版本 `1` |
| 9 | 1 | KDF ID `1`：PBKDF2-HMAC-SHA-256 |
| 10 | 1 | Cipher ID `1`：AES-256-GCM，128-bit tag |
| 11 | 4 | Iterations：只接受 `600000` |
| 15 | 16 | 每次由 `crypto.getRandomValues` 產生的新 salt |
| 31 | 12 | 每次產生的新 AES-GCM IV |
| 43 | 4 | 原始 JSON v1 UTF-8 明文長度 |
| 47 | 明文長度 | AES-GCM ciphertext |
| 47 + 明文長度 | 16 | Authentication tag |

完整原始 47-byte header 是 AES-GCM `additionalData`；鹽、IV、版本、演算法、迭代次數及長度皆受驗證。金鑰長度 256 bits，Web Crypto 輸出為 ciphertext 後接 tag。密文不隱藏總檔案長度；本格式沒有密碼取回、可驗證來源身份或防止換成另一份合法備份的機制。

密碼直接 UTF-8 編碼，不 trim、不 Unicode normalization，包含開頭 U+FEFF 的合法字元亦原樣保留；拒絕未配對 surrogate。新匯出至少 12 Unicode codepoints，最多 1024 UTF-8 bytes；解密接受非空、最多 1024 bytes 的合法字串，沒有套用新建最低長度。確認密碼必須逐字相同。

## 驗證、容量與相容性

1. 讀檔前限制 `File.size`；JSON 明文最多 64 MiB（67108864 bytes），加密檔最多 67108927 bytes（明文 + 63）。總資料列上限仍為既有 100000；封裝不改九類資料、精確數值、引用或歷史規則。
2. KDF 前檢查 magic、完整 header、版本／演算法／固定 iterations、明文正長度與上限、完整檔案精確長度（47 + 明文 + 16）；不接受尾隨 bytes。v1 支援的 iterations 範圍就是單一值 600000，不接受檔案自行指定更昂貴參數。
3. 導出不可匯出的金鑰，驗證 AES-GCM 後才交給既有 `readBackup` 與 `validateStorageLimits`。驗證成功後才顯示九類筆數；使用者確認後，由既有 IndexedDB 單一交易重新檢查完全空白並原子還原。錯密碼或改動 salt／IV／密文／tag 使用同一固定錯誤訊息。
4. 未知版本／演算法拒絕，不能降級成 JSON 或猜測參數。未來改工作因子或演算法時建立新封裝版本，保留明確的舊版讀取路徑；內部 JSON v1 是否升版另依其契約決定。現有 Python Web 仍只讀 JSON v1，不能直接讀 `.llbk`；本輪沒有修改 Python。

PBKDF2 600000 參考 [OWASP PBKDF2-HMAC-SHA-256 工作因子](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)；這是本封裝採用的固定參數，並非 FIPS 認證聲明。[Web Crypto 規範](https://www.w3.org/TR/WebCryptoAPI/)定義 PBKDF2 與 AES-GCM 的 additionalData／tag 行為。實作不加入加密套件。

## 密碼生命週期與限制

密碼／金鑰不寫入 IndexedDB、JSON、URL、日誌、靜態快取、伺服器或第三方。完成／取消、切換匯出方式及改為開始記帳時清空密碼欄；錯誤保留輸入供修正。取消耗時計算只丟棄結果，Web Crypto 本身不能中斷；期間阻止重複計算／寫入。匯出是唯讀，沒有自動寫檔；只顯示下載要求，使用者須確認檔案存在。

導出用暫存密碼 bytes 在 importKey 後清零，但 JavaScript 字串、CryptoKey 或垃圾回收記憶體無法保證立即清除。瀏覽器／系統／擴充套件可能讀取已解鎖頁面與記憶體。本功能不能抵禦本機惡意程式、惡意擴充套件、已解鎖電腦或弱密碼離線猜測；也不保證瀏覽器帳本永久保存。

格式互通測試：`node --test tests/test_local_first_backup_crypto.mjs`，含獨立 Node 標準庫解密。其測試只證明合成資料與 bytes 契約，不證明瀏覽器下載已落地或正式資料安全。
