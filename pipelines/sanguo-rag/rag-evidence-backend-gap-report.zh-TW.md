<!-- doc_id: doc_sanguo_ragops_0001_gap_report -->
# 三國 RAG 證據資料產線 — 容量基線與後端落差報告（SANGUO-RAGOPS-0001）

## 目的

提供 RAG 證據資料產線目前的容量基線、PostgreSQL 與向量資料庫的 coverage 與落差，作為後續 milestone（M1 artifact lake → M2 PostgreSQL → M3 vector → M4 大量試跑 → M5 cutover）的決策依據。本報告不修改 runtime 行為，不新增 production DB/vector write。

## 量測方式

| 量測項 | 量測命令 | 出處 |
|---|---|---|
| 工作區證據總量 | `du -sh artifacts/data-pipeline/sanguo-rag/` 與 `du -sh local/codex-smoke/knowledge-growth/` | 本機檔案系統 |
| 文件總數 | `find ... -type f` | 本機檔案系統 |
| JSON / JSONL 分布 | `find ... -name '*.jsonl'` / `find ... -name '*.json'` | 本機檔案系統 |
| 子資料夾分布 | `find -maxdepth 1 -type d` | 本機檔案系統 |
| 既有 vector record | `export_vector_records.py` 輸出 schema | repo |
| 既有 PostgreSQL schema | `pipelines/sanguo-rag/sql/postgres_schema.sql` | repo |

## 容量基線（2026-05-21）

### Artifact 區塊

| 區塊 | 路徑 | 大小 | 文件數 | 備註 |
|---|---|---|---|---|
| Pipeline canonical artifacts | `artifacts/data-pipeline/sanguo-rag/` | 約 505 MB | 1,479（extracted 內） | 對外 canonical export，含 events、keyword-options、persona-cards |
| Multi-round smoke fixtures | `local/codex-smoke/knowledge-growth/` | 約 4.2 GB | 8,507 | 67 個頂層 smoke run，每個 run 內含多 round |
| Extracted JSONL | `artifacts/data-pipeline/sanguo-rag/extracted/**/*.jsonl` | — | 752 | events、relationship、event-question-seeds 等 |
| Extracted JSON | `artifacts/data-pipeline/sanguo-rag/extracted/**/*.json` | — | 4,152 | persona、keywords、scoreboards |
| Codex-smoke JSONL | `local/codex-smoke/knowledge-growth/**/*.jsonl` | — | 5,573 | 含 seeds、evidence cards、relationship edges、precision lane |
| Codex-smoke JSON | `local/codex-smoke/knowledge-growth/**/*.json` | — | 4,869+ | 含 summary、round result、frontier feedback |
| Seed-class JSONL | `**/*seeds*.jsonl` | — | 846 | evidence/event-question 種子 |

### Sub-domain 切片

| Sub-domain | 路徑樣板 | 已對齊 schema | 觀察 |
|---|---|---|---|
| events | `extracted/events/events.jsonl` | 是（events schema） | canonical |
| keyword-options | `extracted/keyword-options/*.keywords.json` | 是 | 1+ files（fixtures） |
| persona-cards | `extracted/persona-cards/*.persona.json` | 是 | 237 cards |
| observed-mentions | `extracted/observed-mentions/*.json` | 部分 | PostgreSQL 已 mirror |
| api-readiness | `extracted/api-readiness/*.json` | 部分 | dialogue probe / multi-general readiness |
| anchor-index | smoke 內 `anchor-index*` 目錄 | 否 | 散落 smoke runs 內，無 canonical mirror |
| evidence-cards | `external-evidence/external-evidence-cards.jsonl` | 否 | per-round，無集中 ledger |
| evidence-seeds | `event-question-seeds.jsonl`、其他 `seeds.jsonl` | 否 | 846 個 seed JSONL，無集中 ledger |
| relationship overlays | `external-relationship-overlay/*.jsonl` | 部分 | per-round 散落 |

## PostgreSQL 現有 coverage

`pipelines/sanguo-rag/sql/postgres_schema.sql` 目前定義 schema `sanguo_rag`，表覆蓋如下：

| 表 | 目的 | 索引 / 約束 |
|---|---|---|
| `observed_mentions` | 觀察到的人物/事件提及；alias triage 來源 | `(match_status, normalized)`、`(source_ref)` |
| `alias_map_entries` | alias normalization 結果 | `(status)` |
| `triage_label_decisions` | 人工/規則 triage 判決 | `(decision)` |
| view `unresolved_label_summary` | 未解析 label 聚合 | — |

policy 約束（policy-postgres-state-store-evaluation.json）：
- 推薦門檻：runHistoryRowCount ≥ 100k、reviewStateRowCount ≥ 50k、incrementalStateFileCount ≥ 250、averageResumeScanSeconds ≥ 30、manifestFanoutCount ≥ 80。
- guards：`no-runtime-schema-change-before-adapter`、`jsonl-export-remains-canonical-until-cutover`、`regression-harness-required-before-migration`。

policy-postgres-state-migration-plan.json 規定 read repository、jsonlExportMirror、migrationBackfill、rollbackPlan 四層 adapter，並要求 dual-write feature flag 與 backfill parity report。

## 向量資料庫現有 coverage

`export_vector_records.py` 目前輸出三類 record（events / keywords / persona），metadata 含 `recordType`, `generalIds`, `sourceRef(s)`, `confidence`, `canonicalWrites`。

`run_vector_ingestion_gate.py` 串接：
1. fingerprint（events + keyword-options + persona-cards） → 2. export `vector-records.*.jsonl` → 3. dual upsert（qdrant + pinecone）→ 4. dual query probe → 5. readiness report。

policy-vector-ingestion-hardening.json 規範：
- providers：`pinecone`, `qdrant`（default 即此兩者）。
- upsert：smoke default limit 20，max smoke limit 500，retry 2x backoff 2s，dedupe key `(namespace,id,sha256)`。
- resume：state file 必含 `inputFingerprint`、`fileCount`、`files`、`providers`、`updatedAt`，writer 採 `atomic-json`。
- probe：default topK 5、min match 1、`expectedRecordMustAppear=true`。

policy-vector-production-rollout-plan.json 規範 6 步 rollout（dry-run parity → upsert manifest → smoke namespace → dedupe/resume probe → production promotion → rollback record），目前 `enabledByDefault=false`，無 production write。

## 落差分析

### PostgreSQL 落差（M2 範圍）

| 缺項 | 影響 | 對應 milestone |
|---|---|---|
| `pipeline_runs` 表 | 無 run profile / canonicalWrites / status / summary 持久層 | M2-0201 |
| `source_runs` 表 | source-level ROI / timeout / seed-card-count 無集中查詢 | M2-0201 |
| `harvested_pages` 表 | URL / textHash / bodyStart-End / sourcePolicyId 散落 JSONL | M2-0201 |
| `evidence_seeds` 表 | 846 seed JSONL 無 idempotent 主鍵 / score / anchor 查詢 | M2-0201 |
| `evidence_cards` 表 | external-evidence-cards 無 quote/locator 統一索引 | M2-0201 |
| `anchor_passages` 表 | anchor corpus / locator / textHash 無 ledger | M2-0201 |
| `proposal_ledger` 表 | alias/noise/sourceRef/sourceStatus/bodyBoundary residual 無單一帳本 | M2-0201 |
| `vector_ingestion_records` 表 | upsert/probe/rollback manifest 無 DB 對應 row | M2-0201 |
| Repository adapter | 無 jsonl/postgres/dual 抽象，無法切讀 | M2-0202 |
| Backfill runner | 無 dry-run JSONL → PG 批匯 | M2-0203 |
| Parity gate | 無 row count / sha256 / canonicalWrites 比對閘 | M2-0204 |

### 向量庫落差（M3 範圍）

| 缺項 | 影響 | 對應 milestone |
|---|---|---|
| `anchor_passage` record type | RAG 召回時無 anchor 文段語意層 | M3-0301 |
| `evidence_card` record type | accepted/candidate evidence 無向量化 | M3-0301 |
| metadata `runId / sourceFamily / sourceLayer / locator / textHash / anchorVerdict / payloadUri` | 召回後無法做 governance filter | M3-0301 |
| Smoke namespace gate | 目前僅 `vector-backend-check`，無 explicit `evidence-smoke` namespace + rollback manifest | M3-0302 |
| Provider production lock | policy 已禁止，但 runner 尚未強制 namespace 命名規則 | M3-0302 |

### Artifact lake 落差（M1 範圍）

| 缺項 | 影響 | 對應 milestone |
|---|---|---|
| 分區 layout | 67 個頂層目錄混合 round/run/source，目錄掃描 O(N) | M1-0101 |
| 壓縮 / retention | 4.2 GB smoke 與 505 MB canonical 未壓縮、無 retention policy | M1-0101 |
| 集中 manifest | 無 `manifest.json` 含 inputFingerprint/fileCount/sha256/artifactUri | M1-0102 |
| Resume 契約 | runner resume 需重新掃整個 tree | M1-0102 |
| 缺檔 / hash mismatch 偵測 | 無 regression check | M1-0102 |

### 大量試跑與治理落差（M4–M5 範圍）

| 缺項 | 影響 | 對應 milestone |
|---|---|---|
| Large-run rehearsal profile | 無 budget / backpressure 配置 | M4-0401 |
| Backpressure telemetry ledger | 無集中 ROI / timeout / new-evidence / bytes 帳本 | M4-0401 |
| Governance / rollback runbook | 缺 evidence backend 章節（PG mirror、vector smoke）| M4-0402 |
| Retention policy | 無 artifact retention / compression policy | M4-0402 |
| Cutover decision packet | 無 PostgreSQL read-path 切換 checklist | M5-0501 |
| Vector production promotion packet | 無 namespace promotion 條件清單 | M5-0501 |

## go / no-go 建議

| 階段 | 建議 | 觸發條件 |
|---|---|---|
| M1 Artifact lake | **GO** | 已超過 1 GB、5,000+ 檔案，需先分區與壓縮 |
| M2 PostgreSQL schema/adapter/backfill | **GO，但僅 dry-run / dual-write，不可單寫** | 等 manifest（M1-0102）穩定後再 dual-write |
| M2 dual-write parity gate | **GO（在 M1 後）** | 必須先有 manifest fingerprint 與 backfill parity 報告 |
| M3 vector exporter & smoke namespace | **GO（不寫 production namespace）** | 沿用 policy-vector-ingestion-hardening 限制，僅 smoke namespace |
| M3 vector production namespace | **NO-GO** | 等 policy-vector-production-rollout-plan 全部 trigger 條件達標 |
| M4 大量試跑 | **GO（dry-run / no-write / dual-write 四模式）** | 不得在 production namespace 試跑 |
| M5 PostgreSQL read-path cutover | **NO-GO（保留為決策包）** | 等 M4 backpressure 帳本與 governance regression 收斂 |
| M5 vector production promotion | **NO-GO（保留為決策包）** | 等 smoke probe 重複穩定、quota 確認、rollback manifest 經演練 |

## 紅線提示

- `canonicalWrites=false` 的治理語意不可被資料庫切換改變；JSONL 在 cutover 完成前永遠是 canonical export mirror。
- PostgreSQL 初期僅作 mirror / readiness，不可直接成為 source of truth。
- Vector DB 不能保存 raw seed 或無 provenance 的頁面雜訊；只允許 `anchor_passage`、`evidence_card`、`event`、`persona`、`keyword` 五類 retrieval-ready record。
- 所有 budget / threshold / namespace / source policy 必須資料化或讀 policy；不可硬寫死人物名、來源、字串、條件於 runner。

## 後續任務

- M1：`SANGUO-RAGOPS-0101`、`SANGUO-RAGOPS-0102`
- M2：`SANGUO-RAGOPS-0201` → `SANGUO-RAGOPS-0204`
- M3：`SANGUO-RAGOPS-0301`、`SANGUO-RAGOPS-0302`
- M4：`SANGUO-RAGOPS-0401`、`SANGUO-RAGOPS-0402`
- M5：`SANGUO-RAGOPS-0501`

---

**產出時間**：2026-05-21  
**對應計畫**：`文件/三國RAG證據資料產線PostgreSQL與向量化開發計畫.md`  
**evidence 入口**：`.atm/history/evidence/SANGUO-RAGOPS-0001.json`
