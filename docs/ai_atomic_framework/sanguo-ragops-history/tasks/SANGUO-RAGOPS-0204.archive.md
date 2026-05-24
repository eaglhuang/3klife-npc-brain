---
task_id: 
SANGUO-RAGOPS-0204
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M2
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0204.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0204.json
---

# Dual-write parity gate for PostgreSQL mirror

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0204.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0204",
  "title": "Dual-write parity gate for PostgreSQL mirror",
  "status": "closed",
  "milestone": "RAGOPS-M2",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0202",
    "SANGUO-RAGOPS-0203"
  ],
  "scope": [
    "pipelines/sanguo-rag/run_full_roster_convergence_loop.py",
    "pipelines/sanguo-rag/validate_sanguo_governance.py",
    "pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py"
  ],
  "acceptance": [
    "dual-write 璅∪??臬撠? fixture ??smoke run 銝?甇亙神 JSONL ??PostgreSQL mirror??,
    "?Ｗ parity gate嚗炎??row count?ha256?anonicalWrites?rtifactUri?un/source coverage??,
    "parity 憭望???敶梢??JSONL 頛詨嚗蒂撖怠 error ledger??,
    "read path ?身隞粥 JSONL嚗??feature flag ?Ⅱ????
  ],
  "deliverables": [
    "dual-write smoke mode",
    "PostgreSQL parity gate",
    "governance regression evidence"
  ],
  "tags": [
    "sanguo-rag",
    "postgres",
    "dual-write",
    "governance"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: gate-smoke-3-tests-22-pass | 霈: ? dual_write_parity_gate嚗ow count/sha/canonicalWrites/artifactUri/run-source coverage + read-path feature flag gate嚗? 22 ??assertion嚗?頝臬??身 jsonl | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0204.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M2 PostgreSQL schema?dapter?ackfill?ual-write"
  },
  "owner": "codex"
}
```

