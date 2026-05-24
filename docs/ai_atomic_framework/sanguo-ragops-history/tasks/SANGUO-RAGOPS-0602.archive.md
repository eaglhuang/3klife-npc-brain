---
task_id: 
SANGUO-RAGOPS-0602
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M6
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0602.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0602.json
---

# Convergence loop repository write seam

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0602.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0602",
  "title": "Convergence loop repository write seam",
  "status": "closed",
  "milestone": "RAGOPS-M6",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0601",
    "SANGUO-RAGOPS-0202"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_full_roster_convergence_loop.py",
    "pipelines/sanguo-rag/evidence_repository.py",
    "pipelines/sanguo-rag/run_full_roster_convergence_loop_repository_smoke_test.py"
  ],
  "acceptance": [
    "Introduce a narrow writer seam in run_full_roster_convergence_loop.py that can send approved artifact rows to EvidenceRepository only when the M6 opt-in contract enables it.",
    "Default execution remains JSONL canonical and behaviorally identical to the pre-M6 runner; no PostgreSQL writes occur unless explicit opt-in settings are present.",
    "All no-write and dry-run paths must produce zero repository side effects while still emitting a WriteResult-style preview ledger.",
    "Repository errors must be captured in an error ledger and must not partially mutate canonical outputs; retry/backoff behavior must reuse the existing repository adapter policy."
  ],
  "deliverables": [
    "repository writer seam implementation",
    "no-write preview ledger",
    "repository error ledger",
    "smoke fixture proving default canonical behavior is unchanged"
  ],
  "tags": [
    "sanguo-rag",
    "convergence-loop",
    "repository",
    "jsonl",
    "postgres"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke 6/6 pass | 霈: ?啣? convergence_evidence_seam.py (ConvergenceRepoSeam, disabled by default)嚗 run_full_roster_convergence_loop.py 銝剜釣??_build_convergence_repo_seam()?rite_round()?rite_run_summary()?lose()嚗憓?run_full_roster_convergence_loop_repository_smoke_test.py 6 tests pass | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0602.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M6 convergence loop evidence repository opt-in integration"
  },
  "owner": "codex"
}
```

