# 資料版本欄位規格

這份規格定義本 repo 的共用版本命名，讓上游產物、服務健康檢查、排程校正、快取判斷都能用同一套欄位。

## 必備欄位

- `schemaVersion`
  - 用來標示資料結構版本。
  - 例：`healthz.v2`、`relationship-trust-zone.v1`
- `dataVersion`
  - 用來標示資料內容版本。
  - 本 repo 預設以 `git commit SHA` 作為 canonical `dataVersion`。
- `generatedAt`
  - 用來標示產生時間。
  - 不是版本本身，只是輔助追蹤。

## 推薦欄位

- `artifactVersion`
  - 用來標示內容指紋。
  - 當你需要比對「同一個 commit 下的資料內容是否仍一致」時使用。
- `dataVersionSource`
  - 說明 `dataVersion` 的來源，例如 `git-sha`。
- `cacheVersion`
  - 當資料是快取產物時，標示快取規格版本。
- `sourceVersion`
  - 當上游來源本身有版本號時，附上來源版本。

## 判斷規則

1. 只要 `dataVersion` 不同，就視為不同資料快照。
2. `schemaVersion` 不同時，不可直接沿用舊快取。
3. `generatedAt` 只能輔助稽核，不能取代 `dataVersion`。

## 本 repo 的實作原則

- 健康檢查 `/healthz` 必須輸出 `schemaVersion` 與 `dataVersion`。
- 排程校正工具可以另外輸出 `artifactVersion`，用來比對內容指紋。
- 每天早上的自動啟動 / redeploy 流程，必須先讀 `dataVersion` 再決定是否沿用 cache。
- 若未來有新的資料產物，優先補 `dataVersion`，其次才是再加額外快取欄位。
