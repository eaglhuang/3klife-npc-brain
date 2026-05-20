# SANGUO-AUTO-0802 Handoff Summary

生成時間：2026-05-20

## 已完成任務 (done)

| 卡號 | 里程碑 | 狀態 | 主要產出 |
|------|--------|------|---------|
| SANGUO-AUTO-0001 | M0 | done | 三國資料管線低人工自動化計畫書.md |
| SANGUO-AUTO-0002 | M0 | done | ATM task card format confirmed |
| SANGUO-AUTO-0101 | M1 | done | fixtures/baseline-manifest.schema.json |
| SANGUO-AUTO-0102 | M1 | done | fixtures/seed-card/fixture-schema.json |
| SANGUO-AUTO-0103 | M1 | done | fixtures/harness-gap-report.json |
| SANGUO-AUTO-0201 | M2 | done | config/anchor-corpus-registry.json + anchor_corpus_registry.py |
| SANGUO-AUTO-0202 | M2 | done | anchor_passage_index_builder.py |
| SANGUO-AUTO-0203 | M2 | done | fixtures/anchor-retrieval-smoke.json |
| SANGUO-AUTO-0301 | M3 | done | verify_seed_against_anchor_corpus.py |
| SANGUO-AUTO-0302 | M3 | done | score_external_evidence_seeds.py anchor integration |
| SANGUO-AUTO-0303 | M3 | done | promote_seed_to_evidence_card.py anchor schema |
| SANGUO-AUTO-0304 | M3 | done | contradiction/unverified gate |
| SANGUO-AUTO-0401 | M4 | done | anchorCorroborationScore in scoreboard |
| SANGUO-AUTO-0402 | M4 | done | 4 isolation invariants in harness |
| SANGUO-AUTO-0403 | M4 | done | Bayesian smoothing in seed scoring |
| SANGUO-AUTO-0501 | M5 | done | propose_alias_from_observed.py |
| SANGUO-AUTO-0502 | M5 | done | alias_sandbox_verifier.py |
| SANGUO-AUTO-0503 | M5 | done | manual_seed_auto_mirror.py |
| SANGUO-AUTO-0504 | M5 | done | noise_source_proposal.py |
| SANGUO-AUTO-0601 | M6 | done | run_source_cold_evidence_discovery.py |
| SANGUO-AUTO-0602 | M6 | done | run_rumination_lane.py |
| SANGUO-AUTO-0603 | M6 | done | round ledger + stop conditions in convergence loop |
| SANGUO-AUTO-0701 | M7 | done | pilot-acceptance-criteria.json (needs Docker + data for actual run) |
| SANGUO-AUTO-0702 | M7 | done | full roster framework ready (needs pilot pass first) |
| SANGUO-AUTO-0801 | M8 | done | governance-runbook.zh-TW.md updated |
| SANGUO-AUTO-0802 | M8 | done | this handoff package |

## 下一步

1. 安裝所有依賴並啟動 Docker 環境
2. 建立 anchor corpus 文本目錄（正史/演義文本）並執行 anchor_passage_index_builder.py
3. 對既有 seed JSONL 執行 verify_seed_against_anchor_corpus.py
4. 執行 top-50 pilot：`python -B pipelines/sanguo-rag/run_full_roster_convergence_loop.py --run-id low-auto-pilot-50 --top 50`
5. 通過後執行 top-500 full roster

## 治理注意事項

- 所有新 artifact 預設 canonicalWrites=false
- anchor locator 只能在 supportingLocators/supportingTextHashes，不得回填外部 card
- 降級先進 staging，不直接 canonical apply
- 修改 siteReliabilityMultiplier 需人工 gate
