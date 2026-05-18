# Phase 51：主線人物關係驗證通過率研究

## 目標

這輪的問題不是單純把 A-canon 數字衝高，而是要讓後續 full-book primary ingestion 可以大量驗證通過，同時不把同句共現、評論句、第三者動作誤升成主線人物關係。

主線關係驗證要拆成四個指標看：

- normalized claim pass ratio：已正規化 claim 裡有多少能成為 A-canon。
- raw extractor rejection ratio：抽取器吐出的 raw 候選有多少因缺 direct pair 而被擋下。
- near-A repairable ratio：已有 quote / locator / textHash / direct pair，只差可修 deterministic cue 的比例。
- conflict false-positive pressure：同一 pair 多事件、多時段、多關係被 pair-global conflict 誤當衝突的壓力。

## Baseline：30-page primary harvest

輸入：`local/codex-smoke/phase51-relationship-pass-study/claim-graph`

- Claims：`2469`
- A-history：`293`
- A-romance：`197`
- A-canon：`490`
- A-baseline：`39`
- Raw rejected：`34195`
- A-canon / claims：`19.85%`
- A-canon + A-baseline / claims：`21.43%`
- A-canon / raw attempts：`1.34%`
- Near-A rows：`399`
- Pair-cue repairable rows：`340`
- Pair-global type-family conflicts：`250`

最大 blocker 是 `missing_pair_relation_cue`，不是缺 quote / locator / textHash。這代表下一輪真正的放量點是「證明 quote 中的關係 cue 綁在指定 pair 上」，而不是降低 A-history / A-romance 門檻。

## Pair Cue 實驗

本輪試過把 pair-relation cue 改成直接使用 governance `rule-relationship-type-refinement.jsonl` 的可讀 terms。

寬鬆版結果：

- A-history：`342`
- A-romance：`310`
- A-canon：`652`
- A-canon / claims：`25.12%`

收緊單字 cue 後：

- A-history：`327`
- A-romance：`265`
- A-canon：`592`
- A-canon / claims：`24.08%`

這證明 deterministic cue repair 有明確放量能力：相對 baseline，A-canon 可提升約 `+102` 到 `+162`。但抽樣也看到假陽性：

- `師` 會誤吃「班師」。
- `子` 會誤吃干支日期如「壬子」。
- `臣 / 事 / 將` 會誤吃「強臣」「贈袍事」「將某物」。
- 同句第三者動作會誤綁 pair，例如「呂布召薛蘭」的句子同時提到曹操時，不能直接升成曹操與薛蘭的 patron/client。
- 評論句或比較句會造成 pair 共現，但不是人物關係，例如「曹操如董卓、李傕」一類的評論。

因此本輪不把 governance-backed pair cue 直接併入預設 promotion gate。正確做法是先把它變成 near-A repair queue，再加 subject-bound cue 檢查。

## 已落地

新增 `analyze_relationship_validation_pass_ratio.py`：

- 讀取 claim graph 的 `relationship-claims.jsonl`、`rejected-relationship-claims.jsonl`、`relationship-claim-summary.json`。
- 輸出 normalized pass ratio、raw attempt pass ratio、grade distribution。
- 列出 near-A blocker 組合。
- 產生 `pair-cue-repair-queue.jsonl`，只列出「理論上只差 pair relation cue」的候選。
- 統計 unsafe history source family，避免把非三國志 / 後漢書 / 資治通鑑來源升成 A-history。
- 統計 pair-global conflict pressure，提醒後續要改成 event/time scoped conflict。

範例：

```powershell
python pipelines\sanguo-rag\analyze_relationship_validation_pass_ratio.py `
  --claim-root local\codex-smoke\phase51-relationship-pass-study\claim-graph `
  --output-root local\codex-smoke\phase51-relationship-pass-study\pass-study-before `
  --round-id phase51-before `
  --overwrite
```

## 下一步

1. 新增 subject-bound pair cue evaluator：
   - 每個通過的 cue 要輸出 `cueTerm`、`cueSpan`、`cuePosition`、`betweenAliases`、`sourceField`。
   - authority / patron / mentor 不可只靠弱單字通過。
   - kinship 不可吃干支、年齡、排序或族群詞。

2. 把 source field 納入 gate：
   - `parent_child / spouse / sibling / mentor_student / patron_client / ruler_subject` 優先吃 `page-text-relationship`。
   - `enemy_rival / betrayal_surrender / alliance_oath` 可以吃 event / battle context，但要確認動作主體與受體是該 pair。

3. 將 conflict gate 從 pair-global 改成 event/time scoped：
   - 同一 pair 可以同時有君臣、敵對、結盟、庇護等不同時段關係。
   - conflict 應該擋「同一 source event 的互斥 type」，不是擋「同一 pair 的所有不同 type」。

4. full-book ingestion 前後都跑 analyzer：
   - ingestion 前看 baseline。
   - ingestion 後看 A-canon delta、near-A delta、unsafe source delta、conflict pressure delta。
   - 只有 pair cue repair queue 下降且 unsafe source 不上升，才算真的改善資料管線驗證能力。
