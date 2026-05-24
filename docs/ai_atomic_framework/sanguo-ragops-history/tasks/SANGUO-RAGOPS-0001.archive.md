---
task_id: 
SANGUO-RAGOPS-0001
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M0
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0001.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0001.json
---

# RAG evidence volume baseline and backend gap report

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0001.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0001",
  "title": "RAG evidence volume baseline and backend gap report",
  "status": "closed",
  "milestone": "RAGOPS-M0",
  "priority": "P0",
  "dependencies": [],
  "scope": [
    "pipelines/sanguo-rag/evaluate_postgres_state_store_readiness.py",
    "pipelines/sanguo-rag/run_vector_ingestion_gate.py",
    "data/sanguo/policies/policy-postgres-state-store-evaluation.json",
    "data/sanguo/policies/policy-vector-production-rollout-plan.json"
  ],
  "acceptance": [
    "?Ｗ?桀? artifacts?SONL fanout?ow-count estimate?esume scan seconds?ector-ready record count ?捆?蝺??,
    "?Ⅱ? PostgreSQL ?暹? coverage ?撩???? harvested pages?eeds?ards?nchor passages?roposal ledger??,
    "?Ⅱ? vector pipeline ?暹? coverage ?撩???銝Ⅱ隤?raw seeds 銝?仿?vector DB??,
    "銝耨??runtime 銵嚗??啣? production DB/vector writes??
  ],
  "deliverables": [
    "RAG evidence backend gap report",
    "摰寥??瑼餉?銝??挾 go/no-go 撱箄降"
  ],
  "tags": [
    "sanguo-rag",
    "evidence-backend",
    "postgres",
    "vector",
    "baseline"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: gap-report-deliverable | 霈: ?Ｗ RAG evidence backend capacity ?箇??撌桀??撱箇???milestone go/no-go 撱箄降 | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0001.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M0 撱箇?摰寥??箇??撌桀??
  },
  "owner": "codex"
}
```

