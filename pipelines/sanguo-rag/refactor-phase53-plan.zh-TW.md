# Phase 53：enemy_rival directed action cue

## 目標

這輪接續 Phase 52 的 subject-bound pair cue，聚焦在 `enemy_rival`。

問題很明確：如果直接把 `攻 / 討 / 伐 / 戰` 這類 war terms 丟進 promotion gate，雖然 A-canon 會上升，但也會把下面這些句子誤升：

- `X 跟 Y 討 Z`：Y 其實是同側，不是敵對 pair。
- `X 令 A 攻 B`：X 與 B 未必是直接 action pair。
- 評論句、書名句、後設敘述句：句中有 `討曹操檄`、`欲殺孔明`，但 pair 不一定是 claim 指向的人。

因此這輪不做「war term 放寬」，而是只吃 subject/object 明確的 directed cue。

## 本輪做法

在 [build_relationship_claim_graph.py](C:/Users/User/3klife-npc-brain/pipelines/sanguo-rag/build_relationship_claim_graph.py) 新增 `enemy_rival` 的 subject-bound evaluator：

- `enemy-direct-object`
  - 只吃 `X 殺 Y`、`X 斬 Y`、`X 誅 Y`、`X 攻 Y`、`X 夾攻 Y`、`X 來戰 Y`、`X 搦戰 Y`。
- `enemy-reciprocal-battle`
  - 只吃 `X 與 Y 交鋒 / 廝殺 / 雙戰 / 大戰`。
- `enemy-encounter-battle`
  - 只吃 `正逢 Y，三軍混戰`、`與 Y 相遇，便搦戰` 這類 encounter + battle tail。
- `enemy-passive-kill`
  - 只吃 `Y 為 X 所殺`、`Y 被 X 斬 / 誅`。

同時把 `enemy_rival` 從舊的寬鬆 legacy war regex 拔掉。也就是說：

- `enemy_rival` 不能再只靠 broad regex 通過 pair cue。
- 必須留下可解釋的 `pairRelationCue.binding`。

## Broad Type 污染防護

拔掉寬鬆 enemy override 後，另一個副作用浮出來：

- 有些原本會被誤判成 `enemy_rival` 的句子，會回流成 `ruler_subject / patron_client / mentor_student`，甚至衝進 A。

這輪因此再補了一層 `enemyContextGuard`：

- 若句子有明顯 war context。
- 但沒有通過 `enemy_rival` directed binding。
- 且目前 refined type 是 `ruler_subject / patron_client / mentor_student`。

則一律壓回 `B-history / B-romance`，不准偷換型別進 A。

這讓 `忽曹操使至，拜策為會稽太守，令起兵征討袁術` 這類句子不會從「假 enemy A」變成「假 patron A」。

## 結果

Baseline 取 Phase 52 v3：

- Claims：`2469`
- A-history：`293`
- A-romance：`213`
- A-canon：`506`
- Subject-bound pair cues：`56`

Phase 53 enemy-directed v2：

- Claims：`2709`
- A-history：`293`
- A-romance：`222`
- A-canon：`515`
- Subject-bound pair cues：`108`
- Enemy-context guard rows：`216`

相對 Phase 52 v3：

- A-canon：`506 -> 515`，增加 `+9`
- A-romance：`213 -> 222`，增加 `+9`
- A-history：持平 `293`
- Subject-bound pair cues：`56 -> 108`，增加 `+52`

新增的 `+9` A-canon 全部是 `enemy_rival`，而且都有可解釋 binding，沒有 broad type 偷升：

- `曹操 -> 高順`
  - `正逢高順，三軍混戰`
  - binding：`enemy-encounter-battle`
- `關羽 -> 曹操` / `曹操 -> 關羽`
  - `雲長欲殺曹操`
  - binding：`enemy-direct-object`
- `李傕 -> 呂布` / `呂布 -> 李傕`
  - `教殺呂布`
  - binding：`enemy-direct-object`
- `張飛 -> 呂布`
  - `要殺呂布`
  - binding：`enemy-direct-object`
- `周瑜 -> 諸葛亮`
  - `周郎欲殺孔明`
  - binding：`enemy-direct-object`
- `曹操 / 韓胤`
  - `為曹操所斬`
  - binding：`enemy-passive-kill`

## Analyzer 補強

在 [analyze_relationship_validation_pass_ratio.py](C:/Users/User/3klife-npc-brain/pipelines/sanguo-rag/analyze_relationship_validation_pass_ratio.py) 補上：

- `enemyContextGuardCount`
- `enemyContextGuardByType`
- repair queue 帶出 `enemyContextGuard`

這讓下一輪可以直接看出：

- 哪些 broad type 其實是 war-context contamination。
- 哪些 `B-romance / B-history` 需要新的 directed cue，而不是再放寬 promotion gate。

## 下一步

1. 補 `enemy_rival` 的 subject/object 變體：
   - `X 擊 Y 盔 / 中 Y 軍 / 射 Y` 這類介詞受體句。
   - `X 截住 Y 廝殺`、`X 抵敵 Y` 這類 target-first battle tail。
2. 把 `enemyContextGuard` 擴成正式 repair queue 分類，不只是一個統計旗標。
3. 之後再處理 `spouse` 的 object-bound cue，維持同一個策略：
   - 先綁 subject/object。
   - 不直接放寬 governance terms。
