---
task_id: 
SANGUO-RAGOPS-0102
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M1
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0102.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0102.json
---

# Evidence manifest, fingerprint, and resumable scan contract

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0102.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0102",
  "title": "Evidence manifest, fingerprint, and resumable scan contract",
  "status": "closed",
  "milestone": "RAGOPS-M1",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0101"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_full_roster_convergence_loop.py",
    "pipelines/sanguo-rag/run_vector_ingestion_gate.py",
    "data/sanguo/policies"
  ],
  "acceptance": [
    "manifest ?喳?? inputFingerprint?ileCount?iles?ha256?rtifactUri?pdatedAt?chemaVersion??,
    "manifest ?質???bodyStart/bodyEnd telemetry ??body-boundary residual proposal 撘??,
    "resume 銝?閬?頛芣?????artifact tree嚗蒂?賢皜祉撩瑼?銴? hash mismatch??,
    "manifest schema ??smoke fixture ??regression check??
  ],
  "deliverables": [
    "evidence manifest schema",
    "resume/fingerprint validation fixture"
  ],
  "tags": [
    "sanguo-rag",
    "manifest",
    "resume",
    "telemetry"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke-test-6-pass | 霈: ?Ｗ evidence-manifest schema/fixture + evidence_manifest.py 撽?璅∠? + 6 ??resume regression 皜祈岫嚗 missing/duplicate/hash mismatch嚗 ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0102.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M1 Artifact lake ??manifest/resume 憟?"
  },
  "owner": "codex"
}
```

