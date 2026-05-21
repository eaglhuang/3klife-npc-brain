<!-- doc_id: doc_sanguo_ragops_0203_rollback -->
# JSONL → PostgreSQL Backfill Rollback Instructions（SANGUO-RAGOPS-0203）

## 目的

提供 `backfill_evidence_to_postgres.py` 在執行後若需回滾的標準操作流程。原則：

1. **JSONL canonical 不變**：backfill 任何模式都不會修改 `artifacts/data-pipeline/sanguo-rag/lake/` 或 `extracted/` 內的 JSONL artifact。
2. **Rollback 限定 PostgreSQL mirror**：所有回滾僅針對 PostgreSQL `sanguo_rag` schema 內的 evidence-lake 表。
3. **預設 dry-run**：runner 預設 `--dry-run`，必須加 `--apply` 才會寫 PostgreSQL。沒 `--apply` 時不需要回滾。

## 何時需要回滾

| 觸發 | 影響 | 動作 |
|---|---|---|
| Parity report `ok=false` | PostgreSQL row 與 JSONL count/hash 不符 | 模式 A（單一 run 回滾） |
| `pgErrors` 含 `postgres-upsert-exhausted` | 重試耗盡，僅部分 row 寫入 | 模式 A |
| Schema 異常（migration 未對齊） | 全表 row 與預期欄位不一致 | 模式 B（重建 schema） |
| 重大 governance regression 失敗 | 不確定哪些 row 被污染 | 模式 C（schema cascade）|

## 模式 A：單一 run 回滾（推薦）

利用 `pipeline_runs.run_id` 的 ON DELETE CASCADE 連動，只刪除指定 run 的 mirror row：

```sql
BEGIN;

-- 1. 先確認該 run 的所有相關表 row 數，做為事後對照
SELECT 'evidence_seeds'         AS table_name, COUNT(*) FROM sanguo_rag.evidence_seeds         WHERE run_id = :run_id
UNION ALL
SELECT 'evidence_cards',                 COUNT(*) FROM sanguo_rag.evidence_cards         WHERE run_id = :run_id
UNION ALL
SELECT 'harvested_pages',                COUNT(*) FROM sanguo_rag.harvested_pages        WHERE run_id = :run_id
UNION ALL
SELECT 'anchor_passages',                COUNT(*) FROM sanguo_rag.anchor_passages        WHERE run_id = :run_id
UNION ALL
SELECT 'proposal_ledger',                COUNT(*) FROM sanguo_rag.proposal_ledger        WHERE run_id = :run_id
UNION ALL
SELECT 'source_runs',                    COUNT(*) FROM sanguo_rag.source_runs            WHERE run_id = :run_id
UNION ALL
SELECT 'vector_ingestion_records',       COUNT(*) FROM sanguo_rag.vector_ingestion_records WHERE run_id = :run_id;

-- 2. 刪除 pipeline_runs，ON DELETE CASCADE 會連動清掉其餘表
DELETE FROM sanguo_rag.pipeline_runs WHERE run_id = :run_id;

-- 3. anchor_passages 的 run_id 是 ON DELETE SET NULL；如需嚴格清除，可額外執行：
-- DELETE FROM sanguo_rag.anchor_passages WHERE run_id IS NULL;
-- DELETE FROM sanguo_rag.vector_ingestion_records WHERE run_id IS NULL;

COMMIT;  -- 或 ROLLBACK，視確認結果
```

執行後需重新跑：

```bash
python -B pipelines/sanguo-rag/backfill_evidence_to_postgres.py \
  --manifest <manifest.json> \
  --mode postgres --apply --output <new-parity.json>
```

並 diff 新舊 parity report，確認 `pgWritten` 等於 JSONL `jsonlRowCount`。

## 模式 B：重建 evidence-lake schema

當 schema migration 出錯（例如欄位名稱不對齊）：

```bash
# 1. 先確保 JSONL canonical 完整
ls artifacts/data-pipeline/sanguo-rag/extracted/

# 2. 套用 rollback SQL（drop tables）
psql "$SANGUO_RAG_PG_DSN" -f pipelines/sanguo-rag/sql/postgres_evidence_lake_rollback.sql
#    手動把 Mode 2 的 ROLLBACK 改成 COMMIT，或拷貝該段執行

# 3. 重新 apply schema
python -B pipelines/sanguo-rag/apply_postgres_evidence_lake_schema.py --apply

# 4. 重新 backfill
python -B pipelines/sanguo-rag/backfill_evidence_to_postgres.py \
  --manifest <manifest.json> --mode postgres --apply --output <parity.json>
```

## 模式 C：DROP SCHEMA CASCADE（最後手段）

僅在 evidence-lake schema 與 base schema 同時污染時：

```bash
# 警告：會連同 observed_mentions / alias_map_entries / triage_label_decisions 一起刪
psql "$SANGUO_RAG_PG_DSN" -c "BEGIN; DROP SCHEMA IF EXISTS sanguo_rag CASCADE; COMMIT;"

# 之後依序重建 base 與 evidence-lake schema
psql "$SANGUO_RAG_PG_DSN" -f pipelines/sanguo-rag/sql/postgres_schema.sql
psql "$SANGUO_RAG_PG_DSN" -f pipelines/sanguo-rag/sql/postgres_evidence_lake_schema.sql

# 再 backfill
python -B pipelines/sanguo-rag/backfill_evidence_to_postgres.py \
  --manifest <manifest.json> --mode postgres --apply --output <parity.json>
```

## 後續驗證

- `python -B pipelines/sanguo-rag/backfill_evidence_to_postgres.py --manifest <manifest.json> --mode postgres`（dry-run）
- 比對 parity report：`pgWritten + pgSkippedDuplicate == jsonlRowCount`
- 比對 sha256：`jsonlSha256` 在不同次 backfill 必須一致（idempotent 證明）
- 重跑 `python -B pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py --run-profile strict-local --no-write`

## 紅線

- 永遠不可在 rollback 動作中刪除 `artifacts/data-pipeline/sanguo-rag/` 內的 JSONL 檔。
- 永遠不可在 production credentials 下執行 rollback；rollback 限制在 dev DSN。
- 任何 rollback 行為必須在 `proposal_ledger` 之外另行寫入 `lifecycle.actionLog` 紀錄（M1 manifest）。
- 不可在 rollback 後直接 cutover；必須先重跑 governance regression。

---

**對應 task**：`.atm/history/tasks/SANGUO-RAGOPS-0203.json`  
**對應 evidence**：`.atm/history/evidence/SANGUO-RAGOPS-0203.json`
