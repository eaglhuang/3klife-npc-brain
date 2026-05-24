---
task_id: 
SANGUO-RAGOPS-0302
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M3
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0302.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0302.json
---

# Evidence vector smoke namespace ingestion and probe gate

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0302.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0302",
  "title": "Evidence vector smoke namespace ingestion and probe gate",
  "status": "closed",
  "milestone": "RAGOPS-M3",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0301"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_vector_ingestion_gate.py",
    "pipelines/sanguo-rag/upsert_pinecone_records.py",
    "pipelines/sanguo-rag/query_pinecone_records.py",
    "data/sanguo/policies/policy-vector-production-rollout-plan.json"
  ],
  "acceptance": [
    "?舀 evidence smoke namespace upsert?uery probe?edupe manifest?ollback manifest??,
    "provider ??萄? policy嚗?閮剖?迂 qdrant/pinecone嚗? production namespace ?身銝神??,
    "probe 敹?撽? expected record ?航◤?砍?嚗蒂頛詨 topK?atch count?amespace?rovider??,
    "?寞活憭批??etry?ackoff?amespace?imit ?券??policy/CLI/env 閮剖?嚗??刻?祉′撖急香??
  ],
  "deliverables": [
    "evidence vector ingestion smoke gate",
    "dedupe/resume/probe report",
    "rollback manifest"
  ],
  "tags": [
    "sanguo-rag",
    "vector",
    "qdrant",
    "pinecone",
    "smoke"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke-test-4-tests-10-pass | 霈: ? evidence vector smoke gate嚗xporter ??mock 銝 ??probe ??upsert/rollback manifest嚗?policy-driven dedupe/batch/topK嚗roduction namespace ?身撠? | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0302.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M3 Evidence vector export ??smoke namespace ingestion"
  },
  "owner": "codex"
}
```

