# Phase 54 - Pair Cue Governance Externalization

## 背景

本階段承接 Phase 52/53 的 subject-bound pair cue 與 directed enemy cue，收斂一個新治理原則：`build_relationship_claim_graph.py` 不應再內嵌 pair cue 的 domain 詞庫、regex、guard 詞與數值門檻。

## 本輪調整

- 新增 `data/sanguo/rules/rule-relationship-claim-pair-cues.jsonl`，集中承接 pair cue 的關係詞、guard、regex pattern、connector 與 span/window limit。
- 新增 `relationship_claim_pair_cues.py`，以 lazy-load 方式載入 claim graph pair cue 規則。
- `build_relationship_claim_graph.py` 改為只保留判斷流程，所有 pair cue 的 domain data 與條件集合都改由治理規則提供。
- `validate_sanguo_governance.py` 新增 `relationshipClaimPairCueRuleCount` 與 `relationshipClaimPairCueValueCount`，確保新規則檔會進入治理驗證。

## 為什麼這樣做

- 避免下一輪再把詞庫、條件、閾值偷偷長回腳本。
- 讓 pair cue precision / recall 的調整能走治理 diff，而不是混在演算法改動裡。
- 降低「看似只是小修 regex，實際上改壞 promotion gate」的追蹤成本。

## 後續原則

- 新增 pair cue 或 legacy fallback cue 時，先改 governance rule，再改 consumer。
- 若條件屬於可調 domain 規則，而不是純演算法控制流程，就不得直接寫死在 `build_relationship_claim_graph.py`。
- `docs/keep.summary.md` 目前仍不存在；後續若要把這條升成跨模組共識，應補進 keep 系列文件。
