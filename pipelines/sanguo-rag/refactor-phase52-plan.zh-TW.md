# Phase 52：subject-bound pair cue

## 目標

這輪接續 Phase 51 的結論：主線人物關係要大量通過驗證，不能直接把 governance relationship terms 併進 promotion gate。正確方向是建立 subject-bound pair cue，也就是每一個可升級的關係 cue 都要能說明：

- cue 綁在哪一對人物 alias 上。
- cue 在 quote 的哪個 span。
- cue 屬於哪個 relationship type。
- 該 binding 是「人物之間」、「名單後方」、「同句結義」、「兄弟排行」等哪一種可解釋模式。

這和 Unity 裡事件碰撞判定的精神接近：不能因為場景裡有 `Enemy` tag 就判定某兩個 Collider 互相命中，必須有 contact pair 或 raycast hit 的主體/受體。

## 本輪做法

在 `build_relationship_claim_graph.py` 新增 `subject-bound-pair-cue-v1` payload。通過時 claim 會保留：

- `pairRelationCue.evaluator`
- `relationshipType`
- `binding`
- `cueTerm`
- `cueSpan`
- `fromAlias / toAlias`
- `fromAliasSpan / toAliasSpan`
- `snippet`

第一層仍使用保守 generic pair cue：

- alias A / alias B 之間有非弱詞 cue。
- 少數安全 type 可接受人物名單後方短距 cue。
- 單字弱詞如 `師 / 子 / 臣 / 事 / 將 / 戰` 不可單獨通過。

第二層只補高 precision kinship pattern：

- `sworn_sibling`：同句有 pair alias，且同句有 `桃園 / 結義 / 結為兄弟 / 異姓 / 焚香再拜 / 說誓 / 同心協力` 等結義 cue。
- `sibling` possessive：`A 之弟 B`、`A 之兄 B`、`族兄 / 族弟 / 從兄 / 從弟`。
- `sibling` group title：`曹氏兄弟曹仁，曹洪` 這類 title-before-listed-pair。
- `sibling` rank sentence：`拜玄德為兄，關羽次之，張飛為弟` 這類同句排行。

目前沒有放寬 `ruler_subject / patron_client / mentor_student`。這些類型的假陽性壓力仍高，例如 `班師`、干支 `壬子`、評論句、第三者召喚/舉薦等，必須等 subject/object pattern 更成熟後再動。

## 結果

Baseline 取 Phase 51 的 30-page primary harvest：

- Claims：`2469`
- A-history：`293`
- A-romance：`197`
- A-canon：`490`
- A-baseline：`39`
- Near-A rows：`399`
- Pair-cue repairable rows：`340`

Phase 52 subject-bound v3：

- Claims：`2469`
- A-history：`293`
- A-romance：`213`
- A-canon：`506`
- A-baseline：`39`
- Near-A rows：`383`
- Pair-cue repairable rows：`324`
- Subject-bound pair cues：`56`
- A-canon / claims：`20.49%`
- A-canon + A-baseline / claims：`22.07%`

相對 Phase 51 baseline：

- A-canon：`490 -> 506`，增加 `+16`
- A-romance：`197 -> 213`，增加 `+16`
- Pair-cue repairable rows：`340 -> 324`，下降 `-16`

相對第一版 subject-bound v2：

- A-canon：`491 -> 506`，增加 `+15`
- A-romance：`198 -> 213`，增加 `+15`
- Subject-bound pair cues：`33 -> 56`

新增 A-canon 抽樣重點：

- `劉備 / 關羽 / 張飛`：桃園、焚香再拜、異姓、結為兄弟。
- `劉備 / 關羽 / 張飛`：拜玄德為兄、關羽次之、張飛為弟。
- `曹仁 / 曹洪`：曹氏兄弟曹仁，曹洪。
- `袁紹 / 袁術`：袁紹之弟袁術。

這些新增都能從 `pairRelationCue.binding` 看出為什麼通過，沒有靠全域 governance terms 放寬。

## Analyzer 補強

`analyze_relationship_validation_pass_ratio.py` 新增：

- `subjectBoundPairCueCount`
- `pairRelationCueBindingCounts`
- repair queue 帶出 `quote`、`promotionTrace`、`pairRelationCue`

這讓下一輪可以直接從 near-A repair queue 判斷「缺的是哪種 subject-bound evaluator」，不用回頭人工 join claim graph。

## 下一步

1. 補 enemy/rival 的 directed action cue，但只接受 `X 殺/斬/誅 Y`、`X 與 Y 交鋒/廝殺`、`正逢 Y 三軍混戰` 這類主體/受體明確的 pattern。不可接受 `X 跟 Y 討 Z`。
2. 補 spouse 的 object-bound cue，只接受 `X 妻 Y`、`Y 嫁與 X`、`X 娶/納 Y` 等能綁配偶 alias 的句型。不可因句中有 `妻小 / 夫人` 就升任意共現 pair。
3. ruler/patron/mentor 先維持 repair queue，不進 gate；下一輪要先建 `verb subject -> object` pattern，再處理 `命 / 使 / 令 / 薦 / 拜 / 問 / 教`。
4. conflict gate 仍需從 pair-global 改成 event/time scoped，避免同一 pair 在不同章回的君臣、敵對、結盟互相抵消。
