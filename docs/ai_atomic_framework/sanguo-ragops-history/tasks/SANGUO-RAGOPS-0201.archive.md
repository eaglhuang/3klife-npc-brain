---
task_id: 
SANGUO-RAGOPS-0201
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M2
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0201.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0201.json
---

# PostgreSQL evidence lake schema migration

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0201.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0201",
  "title": "PostgreSQL evidence lake schema migration",
  "status": "closed",
  "milestone": "RAGOPS-M2",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0001"
  ],
  "scope": [
    "pipelines/sanguo-rag/sql/postgres_schema.sql",
    "pipelines/sanguo-rag/docker-compose.postgres.yml",
    "data/sanguo/policies/policy-postgres-state-migration-plan.json"
  ],
  "acceptance": [
    "?啣? evidence lake schema嚗???pipeline_runs?ource_runs?arvested_pages?vidence_seeds?vidence_cards?nchor_passages?roposal_ledger?ector_ingestion_records??,
    "瘥撐擃?銵券??idempotent key?ext/hash key?un/source ?亥岷蝝Ｗ???JSONB raw payload 甈???,
    "schema migration 銝霈??JSONL 頛詨憟?嚗?銝撥餈?runtime ? PostgreSQL??,
    "?? dry-run schema apply ??rollback/truncate 閮剛???
  ],
  "deliverables": [
    "PostgreSQL evidence schema migration",
    "schema smoke report"
  ],
  "tags": [
    "sanguo-rag",
    "postgres",
    "schema",
    "evidence-lake"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: dry-run-plan-32-stmts | 霈: ? evidence_lake schema嚗? 銵?22 蝝Ｗ? 1 view嚗?dry-run runner + 3-mode rollback SQL嚗? additive-only 銝蔣??JSONL canonical export | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0201.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M2 PostgreSQL schema?dapter?ackfill?ual-write"
  },
  "owner": "codex"
}
```

