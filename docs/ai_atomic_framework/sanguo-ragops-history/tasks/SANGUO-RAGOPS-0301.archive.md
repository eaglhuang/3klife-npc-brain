---
task_id: 
SANGUO-RAGOPS-0301
task_kind: archive-only
ledger_status: removed_from_atm_ledger
original_status: 
closed
owner: codex
milestone: RAGOPS-M3
priority: P0
archive_json: archive-json/SANGUO-RAGOPS-0301.json
original_evidence_path: .atm/history/evidence/SANGUO-RAGOPS-0301.json
---

# Evidence vector record schema and exporter

## 說明

這是一張歷史 SANGUO-RAGOPS 任務卡。它已從 `.atm/history/tasks/` 正式 ledger 移出，改存為 docs archive task card，避免繼續干擾目前 ATM 治理流程。

## 原始 JSON 檔

- archive-json/SANGUO-RAGOPS-0301.json

## 原始內容快照

```json
{
  "schemaVersion": "atm.workItem.v0.2",
  "workItemId": "SANGUO-RAGOPS-0301",
  "title": "Evidence vector record schema and exporter",
  "status": "closed",
  "milestone": "RAGOPS-M3",
  "priority": "P0",
  "dependencies": [
    "SANGUO-RAGOPS-0102",
    "SANGUO-RAGOPS-0201"
  ],
  "scope": [
    "pipelines/sanguo-rag/export_vector_records.py",
    "pipelines/sanguo-rag/run_vector_ingestion_gate.py",
    "data/sanguo/policies/policy-vector-ingestion-hardening.json"
  ],
  "acceptance": [
    "摰儔 evidence vector record schema嚗??anchor_passage ??evidence_card??,
    "?身銝撓??raw seed嚗??皜???provenance 憟???retrieval-ready chunks ?航撓?箝?,
    "metadata ? recordType?unId?ourceId?ourceFamily?ourceLayer?eneralIds?ocator?extHash?nchorVerdict?anonicalWrites?ayloadUri??,
    "vector export ?臬? JSONL manifest ??PostgreSQL mirror 霈??銝撓??deterministic sha256??
  ],
  "deliverables": [
    "evidence vector exporter",
    "vector record schema fixture",
    "deterministic export report"
  ],
  "tags": [
    "sanguo-rag",
    "vector",
    "anchor",
    "evidence-card"
  ],
  "notes": "2026-05-21 | ??? closed | 撽?: smoke-test-6-tests-13-pass | 霈: ? evidence vector schema + exporter嚗nchor_passage + evidence_card嚗?raw seed 銝撓?箝eviewStatus 蝭拚?eterministic sha256嚗eterministic export report fixture | ?餃?: none",
  "evidencePath": ".atm/history/evidence/SANGUO-RAGOPS-0301.json",
  "source": {
    "planPath": "?辣/銝?RAG霅?鞈??Ｙ?PostgreSQL?????閮.md",
    "sectionTitle": "M3 Evidence vector export ??smoke namespace ingestion"
  },
  "owner": "codex"
}
```

