---
task_id: 
SANGUO-RAGOPS-0601
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M6
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0601.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0601.json
---

# Convergence loop evidence repository opt-in contract

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0601.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0601",
  "title": "Convergence loop evidence repository opt-in contract",
  "status": "closed",
  "milestone": "RAGOPS-M6",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0102",
    "SANGUO-RAGOPS-0202",
    "SANGUO-RAGOPS-0401",
    "SANGUO-RAGOPS-0501"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_full_roster_convergence_loop.py",
    "pipelines/sanguo-rag/evidence_repository.py",
    "pipelines/sanguo-rag/evidence_manifest.py",
    "data/sanguo/policies",
    "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md"
  ],
  "acceptance": [
    "Define an explicit opt-in contract for routing convergence-loop evidence artifacts through EvidenceRepository and EvidenceManifest without changing default canonical JSONL behavior.",
    "Document CLI/env flags, default values, kill switch, dry-run/no-write semantics, and the exact artifact classes allowed to enter the repository seam.",
    "State that PostgreSQL and vector backends are mirrors or smoke targets until separate promotion gates are satisfied; convergence loop must not directly depend on provider-specific code.",
    "Add a machine-readable policy or contract document that downstream tasks can consume; all thresholds, modes, and destinations must be data-driven, not hardcoded in the runner."
  ],
  "deliverables": [
    "opt-in contract document or policy",
    "flag and environment variable matrix",
    "repository seam boundary definition",
    "rollback and disable semantics"
  ],
  "tags": [
    "sanguo-rag",
    "convergence-loop",
    "repository",
    "opt-in",
    "contract"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: policy JSON schema + guard list | 霈: ?啣? policy-convergence-evidence-repo.json嚗?蝢?opt-in env var?llowed tables?rite semantics?rror handling ???Ｖ?霈改?enabledByDefault=false嚗???threshold ??destination ???? | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0601.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M6 convergence loop evidence repository opt-in integration"
  },
  "owner": "codex"
}
```

