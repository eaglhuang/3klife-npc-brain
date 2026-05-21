<!-- doc_id: doc_sanguo_ragops_0501_decision_packet -->
# 三國 RAG 證據後端 — Cutover / Production Promotion 決策包（SANGUO-RAGOPS-0501）

## 目的

集中提供：

1. PostgreSQL evidence-lake **read-path cutover** 的進入條件、rollback 條件、signoff checklist。
2. Vector smoke namespace → **production namespace promotion** 的進入條件、quota / rollback / dedupe / resume 條件、signoff checklist。
3. 若任一條件未達標的 fallback 行為（保留 JSONL canonical mode 與 vector smoke-only mode）。

本決策包目的是讓後續工程師 / agent 可直接以本包進入下一輪 ATM `claim → close` 流程。

## 整體紅線（不可違反）

- JSONL canonical export 是 source of truth；切換前不可停寫或刪除 `artifacts/data-pipeline/sanguo-rag/extracted/...` 與 `lake/...`。
- PostgreSQL read path 預設仍走 JSONL；feature flag 預設 `jsonl`。
- Vector production namespace 預設停用；只允許 `*-smoke` 寫入。
- 任何 production credentials 不入 repo；rollout 期間 operator 自行注入。

## A. PostgreSQL Read-Path Cutover Packet

### A.1 進入條件（必須全部達成）

| # | 條件 | 工具 / 證據 | 連結 |
|---|---|---|---|
| A1 | M0 baseline 報告完成 | `pipelines/sanguo-rag/rag-evidence-backend-gap-report.zh-TW.md` | `.atm/.../SANGUO-RAGOPS-0001` |
| A2 | Artifact lake layout + retention policy 已落地 | `data/sanguo/policies/policy-artifact-lake-layout.json` | `.atm/.../SANGUO-RAGOPS-0101` |
| A3 | Manifest schema + resume 契約穩定 | `evidence_manifest_smoke_test.py` 6/6 | `.atm/.../SANGUO-RAGOPS-0102` |
| A4 | PostgreSQL evidence-lake schema 已 apply | `apply_postgres_evidence_lake_schema.py --apply --include-base` 成功 | `.atm/.../SANGUO-RAGOPS-0201` |
| A5 | Repository adapter dual-write smoke 通過 | `evidence_repository_smoke_test.py` 13/13 | `.atm/.../SANGUO-RAGOPS-0202` |
| A6 | Backfill parity report `ok=true` 連續 ≥ 3 次 | `backfill_evidence_to_postgres.py` × 3 runs | `.atm/.../SANGUO-RAGOPS-0203` |
| A7 | Dual-write parity gate `ok=true` 連續 ≥ 3 次 | `dual_write_parity_gate.py` × 3 runs | `.atm/.../SANGUO-RAGOPS-0204` |
| A8 | Governance regression（strict-local）連續 ≥ 3 次 0 退步 | `run_sanguo_governance_regression_harness.py` | `policy-governance-regression-harness.json` |
| A9 | 大跑 rehearsal（dual-write 模式）通過 ≥ 1 次 | `run_large_run_rehearsal.py --mode dual-write` | `.atm/.../SANGUO-RAGOPS-0401` |
| A10 | Runbook 紅線無違反，最近 14 天無 ManifestValidationError / canonical-writes-drift | `evidence-backend-runbook.zh-TW.md` 對照 | `.atm/.../SANGUO-RAGOPS-0402` |

### A.2 Rollback 條件（任一觸發即 rollback）

- Parity 連續 2 次 `ok=false`，或單次 `pgErrors` 含 `postgres-upsert-exhausted`。
- `canonical-writes-drift` 出現。
- `read-path-flipped` 警報。
- PG 資料量超過 `policy-postgres-state-store-evaluation` 任一門檻但 read path 切換後 P95 延遲下降幅度 < 設定門檻。
- 大跑 rehearsal `stopReason == "consecutive-low-yield"` 連兩跑且未解。

Rollback 動作詳見 `backfill-rollback-instructions.zh-TW.md` 三模式。執行後必須：

1. 將 `SANGUO_RAG_REPO_MODE` 回到 `jsonl`。
2. 將 feature flag `read-path-feature-flag` 回到 `jsonl`。
3. 跑一次 `governance regression` 確認回到 cutover 前狀態。

### A.3 Signoff checklist

- [ ] A1–A10 全部勾選並附 evidence path
- [ ] Operator A 簽核（governance owner）
- [ ] Operator B 簽核（runtime owner）
- [ ] `proposal_ledger` 寫入 cutover proposal（kind=`source-status`，status=`accepted`，sandbox_outcome 內帶 8 個 evidence path）
- [ ] 切換後 24 小時觀察期，期間 `dual_write_parity_gate` 每 6 小時跑一次，連續 4 次 `ok=true`
- [ ] 觀察期內無 rollback trigger

## B. Vector Production Promotion Packet

### B.1 進入條件（必須全部達成）

| # | 條件 | 工具 / 證據 | 連結 |
|---|---|---|---|
| B1 | Evidence vector record schema 穩定 | `fixtures/evidence-vector-record.schema.json` 過 smoke | `.atm/.../SANGUO-RAGOPS-0301` |
| B2 | Vector smoke gate 連續 ≥ 5 次 `probeOk=true` | `run_evidence_vector_smoke_gate.py` × 5 runs | `.atm/.../SANGUO-RAGOPS-0302` |
| B3 | Dedupe manifest 在重跑時 `entryCount` 不變 | gate report `recordCounts.dedupedManifestEntries` | gate report fixtures |
| B4 | Resume probe 在缺檔 / hash mismatch fixture 上正確報錯 | `evidence_manifest_smoke_test.py` resume tests | `.atm/.../SANGUO-RAGOPS-0102` |
| B5 | Rollback manifest 經一次演練（smoke 命名 → 刪除 → re-upsert） | gate report `rollbackManifest.deleteByRecordIds` + 演練紀錄 | runbook 對應段 |
| B6 | Provider quota / namespace 設定已 operator 簽核 | 簽核紀錄不入 repo | runbook 環境變數表 |
| B7 | Production rollout policy trigger conditions 全部 `true` | `policy-vector-production-rollout-plan.triggerConditions` | policy file |
| B8 | 大跑 rehearsal `vector-smoke` 模式連續 ≥ 2 次完成 | `run_large_run_rehearsal.py --mode vector-smoke` | `.atm/.../SANGUO-RAGOPS-0401` |

### B.2 Promotion 條件門檻

- `policy-vector-production-rollout-plan.requiredRolloutSteps` 全部完成：
  1. dry-run parity 過
  2. upsert manifest 落盤
  3. smoke namespace upsert + probe 過
  4. dedupe / resume probe 過
  5. production namespace upsert 在 quota 內
  6. rollback manifest + post-rollout probe 留檔
- production namespace 命名規則：`<service>-<lane>-prod-<yyyymm>`；`*-prod` 必須由 `--allow-production-namespace` 顯式 opt-in。
- 任何 retry 超過 `policy-vector-ingestion-hardening.upsertPolicy.retryCount` 立即停止 promotion 並執行 rollback manifest。

### B.3 Rollback 條件（任一觸發即 rollback）

- production namespace upsert 失敗率 > 1% 或 retry 超過 policy 限制。
- production namespace probe `matchCount < minRequiredMatchCount` 連續 2 次。
- production namespace 與 smoke namespace 的 record sha256 不一致。
- provider quota 警報觸發。

Rollback 動作：

1. 立即用 gate report 中的 `rollbackManifest.deleteByRecordIds` 與 `deleteByDedupeKeys` 清掉 production namespace。
2. 將 vector ingestion mode 回到 `vector-smoke`。
3. 寫入 `proposal_ledger`（kind=`source-status`，status=`rejected`）。

### B.4 Signoff checklist

- [ ] B1–B8 全部勾選並附 evidence path
- [ ] Provider quota 簽核 operator 已紀錄（不入 repo）
- [ ] Rollback manifest 演練紀錄留檔
- [ ] Promotion 後 48 小時觀察期，每 12 小時跑一次 smoke probe，連續 4 次 `probeOk=true`
- [ ] 觀察期內無 rollback trigger

## C. Fallback：條件未達標

如果 A 或 B 任一條件未滿足：

| 區塊 | Fallback 行為 |
|---|---|
| PostgreSQL read path | 強制保留 `SANGUO_RAG_REPO_MODE=jsonl`；dual-write 可繼續，但 read 必走 JSONL canonical |
| Vector production | 強制保留 vector 寫入僅 `*-smoke` namespace；ingestion mode = `vector-smoke` |
| Pipeline runner | 不允許切換 default mode；CI 必須 fail-fast 拒絕 cutover/promotion PR |
| Operator | 必須在 `proposal_ledger.status=rejected` 留紀錄，並提出補強 task card |

## D. 直接交付清單（給下一輪 ATM claim/close）

1. 本決策包：`pipelines/sanguo-rag/cutover-promotion-decision-packet.zh-TW.md`
2. Go / No-Go 機器可讀檢核表：`pipelines/sanguo-rag/cutover-promotion-checklist.json`
3. 既有任務卡：`.atm/history/tasks/SANGUO-RAGOPS-0001 ~ 0402`
4. Runbook：`pipelines/sanguo-rag/evidence-backend-runbook.zh-TW.md`
5. Rollback 指引：`pipelines/sanguo-rag/backfill-rollback-instructions.zh-TW.md`
6. 大跑 rehearsal policy：`data/sanguo/policies/policy-large-run-rehearsal.json`
7. 兩個生產 rollout policy（既有）：`data/sanguo/policies/policy-postgres-state-migration-plan.json`、`policy-vector-production-rollout-plan.json`

下一輪 agent 入手命令：

```bash
# 1. 確認當前狀態
node atm.mjs next --json

# 2. 列出本 milestone 內仍未通過的條件
python -B - <<'PY'
import json, pathlib
checklist = json.loads(pathlib.Path('pipelines/sanguo-rag/cutover-promotion-checklist.json').read_text(encoding='utf-8'))
for section in checklist['sections']:
    print(section['id'], '-', section['label'])
    for item in section['items']:
        print('  ', item['id'], '-', item['label'], '(', item['status'], ')')
PY

# 3. 對應 ATM 任務（如 cutover 進入下一輪）
node atm.mjs start --goal "PostgreSQL evidence-lake read path cutover" --json
```

---

**對應計畫**：`文件/三國RAG證據資料產線PostgreSQL與向量化開發計畫.md`  
**對應 task**：`.atm/history/tasks/SANGUO-RAGOPS-0501.json`  
**對應 evidence**：`.atm/history/evidence/SANGUO-RAGOPS-0501.json`
