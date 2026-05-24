---
task_id: 
SANGUO-RAGOPS-0501
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M5
priority: P1
archive_json: archive-json/SANGUO-RAGOPS-0501.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0501.json
---

# PostgreSQL read-path cutover and vector production promotion decision packet

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0501.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0501",
  "title": "PostgreSQL read-path cutover and vector production promotion decision packet",
  "status": "closed",
  "milestone": "RAGOPS-M5",
  "priority": "P1",
  "dependencies": [
    "SANGUO-RAGOPS-0402"
  ],
  "scope": [
    "data/sanguo/policies/policy-postgres-state-migration-plan.json",
    "data/sanguo/policies/policy-vector-production-rollout-plan.json",
    "pipelines/sanguo-rag"
  ],
  "acceptance": [
    "?Ｗ cutover decision packet嚗???PostgreSQL read path ??璇辣?ollback 璇辣??signoff checklist??,
    "?Ｗ vector production promotion packet嚗???smoke probe?uota?edupe/resume?ollback manifest ??namespace promotion 璇辣??,
    "?乩遙銝璇辣?芷?璅??Ⅱ靽? JSONL canonical mode ??vector smoke-only mode??,
    "瘙箇???湔鈭斤策撌亦?撣急? agent ?脣銝?頛?ATM claim/close??
  ],
  "deliverables": [
    "PostgreSQL cutover packet",
    "vector production promotion packet",
    "go/no-go checklist"
  ],
  "tags": [
    "sanguo-rag",
    "cutover",
    "production-rollout",
    "decision-packet"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: decision-packet + machine-readable checklist | 霈: ? PostgreSQL read-path cutover + vector production promotion 瘙箇??? go/no-go checklist嚗?隞嗆????fallback ??jsonl + vector-smoke | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0501.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M5 cutover/promotion 瘙箇???
  },
  "owner": "codex"
}
```

