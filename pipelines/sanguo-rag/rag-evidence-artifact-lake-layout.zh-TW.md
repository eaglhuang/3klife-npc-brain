<!-- doc_id: doc_sanguo_ragops_0101_artifact_lake_layout -->
# 三國 RAG 證據資料產線 — Artifact Lake Layout 與保留策略（SANGUO-RAGOPS-0101）

## 目的

定義可承載大量試跑的 artifact lake 目錄分區、壓縮策略與 retention，避免單一目錄堆積上萬小檔；同時保留 JSONL canonical export mirror，作為 vector / PostgreSQL 切換前的回放層。

## 分區原則

1. **runId 為一級分區**：所有 evidence 都歸屬於一次 pipeline run。runId 採 `<lane>-<yyyymmdd>-<hhmm>-<shortHash>` 規則，由 runner 傳入或由 fingerprint 推導，不由 runner 硬寫死。
2. **sourceId 為二級分區**：例如 `3kweb`、`baike-romance`、`novel-tang`、`relationship-db-foo`。sourceId 從 `policy-external-source-benchmark.json` / `policy-3kweb-check-runner.json` 等 source policy 讀取，不允許 runner 硬寫死 source 字串。
3. **artifactType 為三級分區**：raw-page、harvested-page、evidence-seed、evidence-card、anchor-passage、proposal、scoreboard、telemetry、manifest。type 字串由 `policy-artifact-lake-layout.json` 列舉，不允許散落字串。
4. **roundId / shardId 為四級分區（選用）**：multi-round / multi-shard 才下放，以避免單目錄超過 5,000 檔。

## 目錄樣板

```
artifacts/data-pipeline/sanguo-rag/lake/
  <runId>/
    manifest.json                                  # 該 run 的 evidence manifest（M1-0102）
    runs/
      pipeline-run.json                            # run profile / canonicalWrites / status
      run-summary.json                             # run-level ROI、bytes、resume seconds
    sources/
      <sourceId>/
        source-run.json                            # sourceId / fetch count / timeout / ROI
        raw-pages/                                 # 原始抓取（可選壓縮）
          <shardId>/<url-hash>.raw.json[.zst]
        harvested-pages/                           # 清理後頁面
          <shardId>/<url-hash>.harvested.json[.zst]
        evidence-seeds/
          <shardId>/seeds-<roundId>.jsonl[.zst]
        evidence-cards/
          <shardId>/cards-<roundId>.jsonl[.zst]
        anchor-passages/
          <corpusId>/<layerId>/passages-<roundId>.jsonl[.zst]
        proposals/
          alias-<roundId>.jsonl
          noise-<roundId>.jsonl
          source-ref-<roundId>.jsonl
          source-status-<roundId>.jsonl
          body-boundary-residual-<roundId>.jsonl
        scoreboards/
          scoreboard-<roundId>.json
          scoreboard-<roundId>.snapshot.json[.zst]
        telemetry/
          source-run-telemetry-<roundId>.jsonl
          body-boundary-<roundId>.jsonl
```

JSONL canonical mirror 仍掛在 `artifacts/data-pipeline/sanguo-rag/extracted/...`（events、keyword-options、persona-cards），lake 內容是補完證據的 second-tier 儲存，**不取代** canonical mirror。

## artifactUri 規則

格式：`atm://lake/<runId>/sources/<sourceId>/<artifactType>/<relativePath>`。

舉例：
- raw page：`atm://lake/local-20260521-1030-ab12cd/sources/3kweb/raw-pages/0001/sha1-abcd.raw.json.zst`
- harvested page：`atm://lake/.../sources/baike-romance/harvested-pages/0002/sha1-ef01.harvested.json.zst`
- evidence seed：`atm://lake/.../sources/3kweb/evidence-seeds/0001/seeds-r3.jsonl.zst`
- evidence card：`atm://lake/.../sources/3kweb/evidence-cards/0001/cards-r3.jsonl.zst`
- anchor passage：`atm://lake/.../sources/novel-tang/anchor-passages/romance/chapter-080/passages-r3.jsonl.zst`
- proposal：`atm://lake/.../sources/3kweb/proposals/body-boundary-residual-r3.jsonl`
- scoreboard：`atm://lake/.../sources/3kweb/scoreboards/scoreboard-r3.json`

`artifactUri` 持久化於 manifest 與 PostgreSQL `harvested_pages.artifactUri` / `evidence_seeds.payload_uri` 等欄位（M2-0201）。

## 壓縮策略

| 類別 | 壓縮 | 編碼 | 觸發 |
|---|---|---|---|
| raw page | `.zst`（zstd level 5）或保留 `.json` | UTF-8 | 啟用 `compression.rawPages=true` |
| harvested page | `.zst` 預設啟用 | UTF-8 | 永遠 |
| evidence seeds | `.zst` 預設啟用，但保留同名 `.jsonl` 軟連結 | UTF-8 | 永遠 |
| evidence cards | `.zst` 預設啟用 | UTF-8 | 永遠 |
| anchor passages | `.zst` 預設啟用 | UTF-8 | 永遠 |
| proposals | 不壓縮（保留人類可讀） | UTF-8 | — |
| scoreboards | snapshot `.zst`，當前 `scoreboard-<round>.json` 不壓縮 | UTF-8 | snapshot only |
| telemetry | 不壓縮（debug 友善） | UTF-8 | — |
| manifest | 不壓縮 | UTF-8 | — |

壓縮 by default 採 atomic write（temp → rename），且必須與未壓縮版的 sha256 同步寫入 manifest。

## Retention policy

| 類別 | retention（天） | 觸發 | 動作 |
|---|---|---|---|
| raw-page | 30 | 超齡 | 壓縮 → 移到 cold tier；超過 90 天刪除（保留 manifest 引用） |
| harvested-page | 90 | 超齡 | 移到 cold tier |
| evidence-seed | 180 | 超齡 | 壓縮封存 |
| evidence-card | 365 | 超齡 | 不刪，僅壓縮 |
| anchor-passage | 365 | 超齡 | 不刪，僅壓縮 |
| proposal | 永久 | — | 不刪（governance 帳本） |
| scoreboard snapshot | 90 | 超齡 | 移到 archive |
| telemetry | 60 | 超齡 | 壓縮封存 |
| manifest | 永久 | — | 不刪 |

retention 由 `policy-artifact-lake-layout.json` 描述，不允許在 runner 內出現 `delete` 字串或人物名。

## 不變式

- runner 不得在程式碼內出現「絕對路徑」、「人物名稱」、「來源 URL」、「cleanup 字串」。所有來源 / 路徑 / 動作均透過 policy / CLI / env 傳入。
- `canonicalWrites=false` 模式下不得寫入 lake 下任何 production tier；只允許寫入 `lake/<runId>/sandbox/` 並標記 `sandbox=true`。
- 任何刪除 / 壓縮 / 移轉動作需在 `manifest.json` 留下 `lifecycle.lastAction` 事件。
- artifactUri 必為 stable URI；不允許在 retention 動作後改變 manifest 內已記錄的 URI。

## 與既有目錄的並存策略

- `artifacts/data-pipeline/sanguo-rag/extracted/...`：維持 canonical export mirror，不搬遷。
- `local/codex-smoke/knowledge-growth/...`：M1 範圍內保持原樣，僅在 lake 內 mirror 並登錄 manifest；M2 dual-write 啟用後才考慮淘汰路徑。
- lake 目錄出生時為空；首次 runner 寫入時建立。新舊路徑同時可讀，由 repository adapter（M2-0202）判斷。

## 相關 milestone

- M1-0102：manifest schema（含 `artifactUri`、`sha256`、`fileCount`、`compression`、`retentionTier`）。
- M2-0201：`harvested_pages.artifact_uri`、`evidence_seeds.payload_uri`、`evidence_cards.payload_uri`、`anchor_passages.artifact_uri`、`vector_ingestion_records.upsert_manifest_uri` 全部來自此 layout。
- M4-0402：runbook 內 retention 操作 SOP、rollback。

---

**產出時間**：2026-05-21  
**對應計畫**：`文件/三國RAG證據資料產線PostgreSQL與向量化開發計畫.md`  
**policy**：`data/sanguo/policies/policy-artifact-lake-layout.json`  
**evidence 入口**：`.atm/history/evidence/SANGUO-RAGOPS-0101.json`
