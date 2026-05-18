<!-- doc_id: doc_server_pipeline_0062 -->
# NPC-brain / Sanguo-RAG Phase 50 Plan: Primary Canon Defaults And Refresh Hardening

## Summary

Phase 49 proved the 30-page primary-text relationship refinement path, but the main progress estimators still defaulted to the legacy source-grounded relationship evidence. That meant the 54.71% knowledge-completion result and 80.77% core-person completion result were only visible when the caller manually passed the primary-canon run artifacts.

Phase 50 makes the primary-canon outputs first-class defaults. Completion estimators now auto-detect the latest `primary-canon-relationship-backbone/primary-text-*` run and use its merged relationship evidence, event-question seeds, and source-event packets. The relationship claim graph builder also includes primary-canon overlay edges and evidence cards in its default discovery set.

## Key Changes

- Added `primary_canon_inputs.py` as the shared latest-run resolver for primary-canon relationship evidence, event-question seeds, and source-event packets.
- `estimate_knowledge_completion.py` now defaults to latest primary-canon artifacts when present, with `--no-primary-canon-defaults` available for legacy-only comparisons.
- `estimate_core_person_completion.py` now uses the same primary-canon defaults and records the selected run under `inputs.primaryCanonDefaults`.
- `build_relationship_claim_graph.py` now resolves the standalone repo root via `repo_layout.resolve_repo_root`, discovers primary-canon relationship overlay edges/cards by default, and can merge the latest primary-canon claim snapshot as supplemental already-validated claims.
- `run_relationship_claim_graph_refresh.py` now starts correctly in the standalone repo after the root-layout migration.
- `audit_pipeline_inventory.py` now uses the already-resolved standalone repo root directly.

## Expected Progress Effect

- Default knowledge completion should surface the Phase 49 primary-text result: `54.71%` instead of the old legacy-only `51.50%`.
- Default core-person completion should surface the primary-canon relationship boost: `80.77%` instead of the old legacy-only `67.75%`.
- Default relationship claim graph refresh should include primary-canon overlay inputs and the latest supplemental claim snapshot; in the standalone smoke run this preserves the Phase 49 A-canon baseline and raises the rebuild projection to `490` A-canon claims.

## Validation Plan

- `python -m py_compile` for touched Python scripts.
- `build_relationship_claim_graph.py --help` and `run_relationship_claim_graph_refresh.py --help`.
- Rebuild relationship claim graph into a local smoke output and confirm A-canon projection is at least the Phase 49 `481` baseline.
- Re-run knowledge/core completion into `current` and confirm primary-canon defaults are selected.
- `validate_sanguo_governance.py --dry-run-report`.
- `git diff --check`.
- UTF-8 / BOM / U+FFFD / repeated-question-mark mojibake guard on touched text files.

## Next Step

The next major growth round remains full-book primary ingestion for Sanguozhi, Houhanshu, Zizhitongjian, and Sanguo Yanyi. Phase 50 makes that scale-up safer because any future primary-canon run will automatically feed the main progress dashboards without bespoke command-line wiring.
