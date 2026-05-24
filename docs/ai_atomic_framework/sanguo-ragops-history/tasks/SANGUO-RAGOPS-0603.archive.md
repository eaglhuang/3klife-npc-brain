---
task_id: 
SANGUO-RAGOPS-0603
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M6
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0603.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0603.json
---

# Convergence loop evidence manifest and resume integration

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0603.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0603",
  "title": "Convergence loop evidence manifest and resume integration",
  "status": "closed",
  "milestone": "RAGOPS-M6",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0602",
    "SANGUO-RAGOPS-0102"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_full_roster_convergence_loop.py",
    "pipelines/sanguo-rag/evidence_manifest.py",
    "pipelines/sanguo-rag/evidence_manifest_smoke_test.py",
    "pipelines/sanguo-rag/fixtures/evidence-manifest.schema.json"
  ],
  "acceptance": [
    "Make convergence-loop runs emit an evidence manifest in opt-in mode with input fingerprint, artifact URIs, sha256, row counts, body-boundary telemetry references, and repository write summaries.",
    "Add resume validation that fails fast on missing artifacts, hash mismatch, run/profile mismatch, or canonicalWrites drift.",
    "Resume scan must be telemetry-driven and budgeted by policy; it must not encode source-specific or filename-specific shortcuts.",
    "Manifest integration must support no-write preview runs and must not require PostgreSQL or vector credentials."
  ],
  "deliverables": [
    "convergence-loop manifest emission",
    "resume validation path",
    "manifest smoke fixture",
    "hash mismatch and missing artifact tests"
  ],
  "tags": [
    "sanguo-rag",
    "manifest",
    "resume",
    "convergence-loop",
    "governance"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke 6/6 pass | 霈: ?啣? convergence_manifest_helper.py (build/write/scan)嚗 run_full_roster_convergence_loop.py 瘜典 --check-manifest-resume ???check_manifest_resume() startup scan?emit_convergence_manifest() ??main() ?怠偏嚗videnceManifestPath ?神??baseline manifest嚗onvergence_manifest_smoke_test.py 6/6 pass | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0603.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M6 convergence loop evidence repository opt-in integration"
  },
  "owner": "codex"
}
```

