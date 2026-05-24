---
task_id: 
SANGUO-RAGOPS-0606
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M6
priority: P1
archive_json: archive-json/SANGUO-RAGOPS-0606.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0606.json
---

# M6 runbook, handoff, and promotion checklist update

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0606.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0606",
  "title": "M6 runbook, handoff, and promotion checklist update",
  "status": "closed",
  "milestone": "RAGOPS-M6",
  "priority": "P1",
  "dependencies": [
    "SANGUO-RAGOPS-0604",
    "SANGUO-RAGOPS-0605",
    "SANGUO-RAGOPS-0402",
    "SANGUO-RAGOPS-0501"
  ],
  "scope": [
    "pipelines/sanguo-rag/evidence-backend-runbook.zh-TW.md",
    "pipelines/sanguo-rag/cutover-promotion-checklist.json",
    "pipelines/sanguo-rag/cutover-promotion-decision-packet.zh-TW.md",
    "pipelines/sanguo-rag/evidence-backend-smoke-commands.json",
    ".atm/history/evidence"
  ],
  "acceptance": [
    "Update runbook and cutover checklist with M6 opt-in convergence-loop repository mode, disable path, rollback path, and operator decision points.",
    "Document the exact commands for no-write rehearsal, jsonl-only rehearsal, dual-write parity rehearsal, vector-smoke linkage, and governance regression.",
    "Leave production cutover blocked until repository parity, smoke namespace, provider quota, and observation-window gates are all satisfied.",
    "Close M6 with evidence packages that map every acceptance criterion to artifacts, smoke results, and residual risks."
  ],
  "deliverables": [
    "M6 runbook update",
    "promotion checklist update",
    "smoke command manifest update",
    "handoff summary and evidence package"
  ],
  "tags": [
    "sanguo-rag",
    "runbook",
    "handoff",
    "cutover",
    "atm"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: docs updated + checklist C1-C6 added | 霈: evidence-backend-runbook.zh-TW.md ?啣? M6 ?嗆?敺芰?游?蝡?嚗utover-promotion-checklist.json ?啣? C section (C1-C6)嚗vidence-backend-smoke-commands.json ?啣? convergence-loop-integration group (4 commands, 21 total smoke tests) | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0606.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M6 convergence loop evidence repository opt-in integration"
  },
  "owner": "codex"
}
```

