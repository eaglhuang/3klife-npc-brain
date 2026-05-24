---
task_id: 
SANGUO-RAGOPS-0203
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M2
priority: P1
archive_json: archive-json/SANGUO-RAGOPS-0203.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0203.json
---

# Backfill existing JSONL evidence artifacts into PostgreSQL mirror

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0203.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0203",
  "title": "Backfill existing JSONL evidence artifacts into PostgreSQL mirror",
  "status": "closed",
  "milestone": "RAGOPS-M2",
  "priority": "P1",
  "dependencies": [
    "SANGUO-RAGOPS-0102",
    "SANGUO-RAGOPS-0201"
  ],
  "scope": [
    "pipelines/sanguo-rag",
    "local/codex-smoke/knowledge-growth",
    "artifacts/data-pipeline/sanguo-rag"
  ],
  "acceptance": [
    "?? dry-run backfill嚗敺?artifact manifest ?臬 PostgreSQL mirror??,
    "?‵??idempotent upsert嚗?頝??Ｙ??? row??,
    "?Ｗ JSONL count/hash ??PostgreSQL count/hash parity report??,
    "憭望??? rollback/truncate plan嚗?銝耨?孵? JSONL artifact??
  ],
  "deliverables": [
    "backfill runner",
    "parity report fixture",
    "rollback instructions"
  ],
  "tags": [
    "sanguo-rag",
    "postgres",
    "backfill",
    "parity"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke-test-17-pass | 霈: ? backfill runner?arity report fixture?ollback 銝芋撘?撘?JSONL canonical 銝??dempotent upsert | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0203.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M2 PostgreSQL schema?dapter?ackfill?ual-write"
  },
  "owner": "codex"
}
```

