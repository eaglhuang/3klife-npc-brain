<!-- doc_id: doc_sanguo_ragops_0402_runbook -->
# 三國 RAG 證據後端治理 / 回滾 / Retention Runbook（SANGUO-RAGOPS-0402）

## 適用範圍

本 runbook 覆蓋三層 evidence backend：

1. **Artifact lake**（`artifacts/data-pipeline/sanguo-rag/lake/`、`local/codex-smoke/knowledge-growth/`），由 `policy-artifact-lake-layout.json` 管理。
2. **PostgreSQL evidence-lake**（`sanguo_rag` schema，新增表來自 `postgres_evidence_lake_schema.sql`），由 `policy-postgres-state-migration-plan.json` 管理。
3. **Evidence vector smoke namespace**（Pinecone / Qdrant `*-smoke` namespace），由 `policy-vector-ingestion-hardening.json` 與 `policy-vector-production-rollout-plan.json` 管理。

production credentials 與 cloud provider secrets **不得**進入 repo；本 runbook 僅描述操作流程與 env 變數名稱。

## 紅線（永遠不能違反）

- JSONL canonical export 是 source of truth；任何 cutover 前不得停寫或刪除 `artifacts/data-pipeline/sanguo-rag/extracted/...` 與 `lake/`。
- `canonicalWrites=false` 的 run 不得寫入 production namespace、不得寫入 PostgreSQL production schema。
- vector production namespace 預設停用；只有 `*-smoke` 允許 upsert。
- 所有 budget / source / general / cleanup 字串都必須 policy-driven，runner 不得硬寫死。

## 大跑前 gate（pre-flight）

| Gate | 工具 | 通過條件 |
|---|---|---|
| evidence manifest 有效 | `python -B pipelines/sanguo-rag/evidence_manifest_smoke_test.py` | 6/6 PASS |
| backfill dry-run parity | `python -B pipelines/sanguo-rag/backfill_evidence_to_postgres.py --manifest <manifest>` | parityOk=true |
| dual-write parity gate | `python -B pipelines/sanguo-rag/dual_write_parity_gate.py --manifest <manifest>` | ok=true |
| evidence vector exporter | `python -B pipelines/sanguo-rag/export_evidence_vector_records_smoke_test.py` | 13 assertions PASS |
| vector smoke namespace gate | `python -B pipelines/sanguo-rag/run_evidence_vector_smoke_gate.py --manifest <manifest> --provider mock` | probeOk=true |
| rehearsal no-write mode | `python -B pipelines/sanguo-rag/run_large_run_rehearsal.py --mode no-write --sources-config <fixture>` | stopReason in 允許集合 |
| governance regression | `docker exec 3klife-npc-brain-dev python -B pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py --run-profile strict-local --no-write` | exit 0 |

人工 gate：
- 確認 `policy-large-run-rehearsal.budgets` 符合本次目標 ROI。
- 確認 `policy-artifact-lake-layout.retention` 符合保留窗口。
- 確認 vector provider namespace 後綴為 `-smoke`，**不是** `-prod`。
- 簽核者紀錄入 `proposal_ledger`（kind=`source-status`、status=`sandbox-pass`）。

## 大跑中 gate（in-flight）

自動 gate：
- 每輪由 `run_large_run_rehearsal` emit `backpressure-telemetry-ledger.v0.1`，逐輪檢查：
  - `artifactBytes` 累積不得超過 `policy.budgets.maxArtifactBytesPerRun`。
  - `consecutiveLowYieldRounds` 不得超過 `policy.backpressure.consecutiveLowYieldRoundsStop`。
  - `timeoutCount` 不得超過 `policy.budgets.maxSourceTimeoutPerRound`。
  - `resumeScanSeconds` 不得超過 `policy.budgets.maxResumeScanSeconds`。
- `dual_write_parity_gate` 在 dual-write 模式下每輪比對 JSONL 與 PG mirror 一致性；errors 不為空即停跑。
- vector ingestion 失敗超過 `policy-vector-ingestion-hardening.upsertPolicy.retryCount` 後停止 upsert，但保留 JSONL canonical。

人工 gate：
- 操作者必須每輪檢視 ledger，回報是否需要中斷。
- 任何 `backpressureSignals` 含 `artifact-budget-exhausted` 必須立即停跑並走 retention。

## 大跑後 gate（post-flight）

| Gate | 工具 | 預期 |
|---|---|---|
| parity report 留存 | `pipelines/sanguo-rag/fixtures/backfill-parity-report.sample.json` 形式 | `ok=true` |
| vector smoke gate report 留存 | `fixtures/evidence-vector-smoke-gate.sample.json` 形式 | `ok=true` |
| rollback manifest 完整 | `report.rollbackManifest.deleteByRecordIds` | 非空，可用作 rollback |
| anchor provenance isolation | 比對 evidence_cards.anchorEvidence 與 anchor_passages.locator | 沒有跨 corpus 污染 |
| canonicalWrites 一致 | 比對 manifest.canonicalWrites 與 pipeline_runs.canonical_writes | 完全相同 |

人工 gate：
- 寫入 governance 紀錄：`proposal_ledger.kind=source-status` + `sandbox_outcome` 包含本次 rehearsal report URI。
- 若 cutover 條件未達標，明確保留 JSONL canonical mode 與 vector smoke-only mode。

## Retention 操作 SOP

依 `policy-artifact-lake-layout.retention` 與 `policy-postgres-state-migration-plan` 執行：

| 動作 | 條件 | 命令 |
|---|---|---|
| compress raw-page | `now - createdAt > policy.retention.raw-page.hotDays` | `zstd <path>; sha256sum > manifest.lifecycle.actionLog` |
| archive harvested-page | `> harvested-page.coldDays` | 移到 cold tier；不刪 |
| compress evidence-seed | `> evidence-seed.hotDays` | `zstd <path>` |
| keep proposal | 永遠 | 不刪 |
| archive scoreboard snapshot | `> scoreboard.coldDays` | 移到 archive |
| delete raw-page payload | `> raw-page.expireDays` | 刪 payload，manifest 仍留 reference |
| TRUNCATE PG evidence-lake | run 取消 / parity 失敗 | `psql -f pipelines/sanguo-rag/sql/postgres_evidence_lake_rollback.sql`（Mode 1）|
| DROP TABLE PG evidence-lake | schema migration 出錯 | `... Mode 2` |
| DROP SCHEMA CASCADE | 最後手段 | `... Mode 3` |
| vector smoke namespace rollback | 任何 anomaly | 使用 gate report 內 `rollbackManifest.deleteByRecordIds` |

## Smoke command 清單（governance regression）

```bash
# 全部六支 evidence backend smoke
python -B pipelines/sanguo-rag/evidence_manifest_smoke_test.py
python -B pipelines/sanguo-rag/evidence_repository_smoke_test.py
python -B pipelines/sanguo-rag/backfill_evidence_to_postgres_smoke_test.py
python -B pipelines/sanguo-rag/dual_write_parity_gate_smoke_test.py
python -B pipelines/sanguo-rag/export_evidence_vector_records_smoke_test.py
python -B pipelines/sanguo-rag/run_evidence_vector_smoke_gate_smoke_test.py
python -B pipelines/sanguo-rag/run_large_run_rehearsal_smoke_test.py

# 既有 governance regression harness（dockered）
docker exec 3klife-npc-brain-dev python -B \
  pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py \
  --run-profile strict-local --no-write

# 既有 PostgreSQL readiness
docker exec 3klife-npc-brain-dev python -B \
  pipelines/sanguo-rag/evaluate_postgres_state_store_readiness.py

# 既有 vector ingestion gate
docker exec 3klife-npc-brain-dev python -B \
  pipelines/sanguo-rag/run_vector_ingestion_gate.py
```

## Governance smoke coverage 對照

| 主題 | smoke | 涵蓋條件 |
|---|---|---|
| canonicalWrites | dual_write_parity_gate | canonicalWritesCheck.ok=true，run/source 比對一致 |
| anchor provenance isolation | export_evidence_vector_records | anchor_passage 記錄 anchorVerdict='anchored'，evidence_card 不偽裝 anchor |
| DB parity | backfill_evidence_to_postgres / dual_write_parity_gate | parityOk=true、sha256 比對 |
| vector namespace isolation | run_evidence_vector_smoke_gate | `*-prod` namespace 預設封鎖 |

## Rollback decision tree

```
parity 失敗?
├── 是 → 是否 dual-write run?
│       ├── 是 → 模式 A（單一 run delete）
│       └── 否 → 仍然走模式 A，確認 PG 端清掉該 run 即可
└── 否 → schema drift?
        ├── 是 → 模式 B（drop tables + re-apply schema + re-backfill）
        └── 否 → 是否大範圍 governance regression 失敗?
                  ├── 是 → 模式 C（DROP SCHEMA CASCADE，慎用）
                  └── 否 → 維持現狀，僅補 manifest lifecycle 紀錄
```

詳細命令見 `pipelines/sanguo-rag/backfill-rollback-instructions.zh-TW.md`。

## 環境變數總表（無 secret）

| 變數 | 用途 | 預設 |
|---|---|---|
| `SANGUO_RAG_REPO_MODE` | repository 模式 | `jsonl` |
| `SANGUO_RAG_REPO_DRY_RUN` | dry-run flag | `0` |
| `SANGUO_RAG_PG_DSN` | PostgreSQL DSN | 無預設，apply 模式必填 |
| `SANGUO_RAG_PG_SCHEMA` | schema 名稱 | `sanguo_rag` |
| `SANGUO_RAG_LAKE_ROOT` | lake 根目錄 | `artifacts/data-pipeline/sanguo-rag/lake` |
| `SANGUO_RAG_TEST_TMPDIR` | smoke 暫存根目錄 | `local/tmp/sanguo-rag-smoke` |
| Vector provider env（如 `PINECONE_API_KEY`） | 上線時 operator 注入 | 不入 repo |

## 不變式回顧

- 任何寫入動作必須能透過 `--dry-run` 預覽。
- 任何 destructive SQL 必須包在 `BEGIN ... ROLLBACK` 內預設不執行。
- 任何 production 行為（PG read path、vector prod namespace）必須有顯式 CLI / env flag 才開啟。
- 任何 governance 異動需在 `proposal_ledger` 留紀錄。

---

**對應計畫**：`文件/三國RAG證據資料產線PostgreSQL與向量化開發計畫.md`  
**對應 task**：`.atm/history/tasks/SANGUO-RAGOPS-0402.json`  
**對應 evidence**：`.atm/history/evidence/SANGUO-RAGOPS-0402.json`
