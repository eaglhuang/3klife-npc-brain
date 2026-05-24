---
task_id: 
SANGUO-RAGOPS-0604
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M6
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0604.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0604.json
---

# Convergence loop repository parity rehearsal gate

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0604.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0604",
  "title": "Convergence loop repository parity rehearsal gate",
  "status": "closed",
  "milestone": "RAGOPS-M6",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0602",
    "SANGUO-RAGOPS-0603",
    "SANGUO-RAGOPS-0204",
    "SANGUO-RAGOPS-0401"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_full_roster_convergence_loop.py",
    "pipelines/sanguo-rag/dual_write_parity_gate.py",
    "pipelines/sanguo-rag/run_large_run_rehearsal.py",
    "pipelines/sanguo-rag/validate_sanguo_governance.py",
    "pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py"
  ],
  "acceptance": [
    "Add a rehearsal gate that can compare convergence-loop JSONL canonical outputs with repository mirror outputs using count, sha256, run/source coverage, canonicalWrites, and artifact URI parity.",
    "Cover no-write, jsonl-only, and dual-write opt-in modes with deterministic fixtures before any live source or production backend is allowed.",
    "Parity failures must produce a machine-readable error ledger and must preserve the JSONL canonical fallback path.",
    "Governance validation must assert repository opt-in isolation, manifest hash integrity, and no production credentials in repo."
  ],
  "deliverables": [
    "convergence-loop parity rehearsal runner or mode",
    "parity fixture set",
    "governance assertions",
    "error ledger schema"
  ],
  "tags": [
    "sanguo-rag",
    "parity",
    "rehearsal",
    "dual-write",
    "governance"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke 3/3 pass | 霈: ?啣? run_convergence_repo_parity_rehearsal.py (synthesises EvidenceManifest from baseline paths, runs backfill dry-run, outputs parity report)嚗onvergence_repo_parity_rehearsal_smoke_test.py 3/3 pass | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0604.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M6 convergence loop evidence repository opt-in integration"
  },
  "owner": "codex"
}
```

