---
task_id: 
SANGUO-RAGOPS-0402
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M4
priority: P1
archive_json: archive-json/SANGUO-RAGOPS-0402.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0402.json
---

# Evidence backend governance, rollback, and retention runbook

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0402.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0402",
  "title": "Evidence backend governance, rollback, and retention runbook",
  "status": "closed",
  "milestone": "RAGOPS-M4",
  "priority": "P1",
  "dependencies": [
    "SANGUO-RAGOPS-0401"
  ],
  "scope": [
    "?辣",
    "pipelines/sanguo-rag/validate_sanguo_governance.py",
    "pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py"
  ],
  "acceptance": [
    "鋆? evidence backend runbook嚗???PostgreSQL mirror?rtifact retention?ector smoke/prod namespace?ollback?uota?leanup??,
    "governance smoke 閬? canonicalWrites?nchor provenance isolation?B parity?ector namespace isolation??,
    "?Ⅱ摰儔憭扯???銝准?敺?鈭箏極 gate ???gate??,
    "runbook 銝?瘙?production credentials嚗??蝡?provider secrets 撖怠 repo??
  ],
  "deliverables": [
    "governance and rollback runbook",
    "retention policy",
    "smoke command list"
  ],
  "tags": [
    "sanguo-rag",
    "governance",
    "rollback",
    "runbook"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: runbook + machine-readable smoke list | 霈: ? evidence backend runbook嚗G mirror / retention / vector smoke / rollback / quota / cleanup嚗? smoke commands JSON + 蝝?/gate 銝挾瘚? | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0402.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M4 憭折?閰西? profile?ackpressure?祥??runbook"
  },
  "owner": "codex"
}
```

