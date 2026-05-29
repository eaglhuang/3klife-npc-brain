<!-- doc_id: doc_baihua_focus_skill_contract_0001 -->
# 白話人物中心 Skill 契約（SANGUO-BOOTSTRAP-0201）

## 目的

這份契約定義 `人物中心 relationship extraction` 的輸入、判斷步驟與輸出格式，供：

- [run_baihua_focus_relationship_runner.py](C:/Users/User/3klife-npc-brain/pipelines/sanguo-rag/run_baihua_focus_relationship_runner.py)
- 後續 reviewer / human review lane
- 未來接本機或遠端 LLM skill 的抽取流程

共同使用。

這條 lane 的核心不是「看到兩個名字就猜關係」，而是：

1. 先以一名人物為中心讀白話《三國演義》段落
2. 再從段落中找出有直接語意支持的硬關係
3. 每一條都必須附上原文句子與章回定位

所有輸出都必須維持 `canonicalWrites=false`。

## Input

### 必要欄位

1. `focusGeneralId`
   - 本次抽取的焦點人物，例如 `zhang-fei`
2. `focusNameZhTw`
   - 焦點人物繁中名稱，例如 `張飛`
3. `candidateCounterpartIds`
   - 允許配對的其他人物 ID 範圍
4. `passages[]`
   - 與焦點人物有關的白話段落或句窗
5. `allowedRelationshipTypes`
   - 允許抽取的關係型別
6. `canonicalWrites=false`

### `passages[]` 子欄位

每個 passage 至少要有：

1. `locator`
2. `chapterRef`
3. `normalizedText`
4. `personIds[]`

建議附加：

1. `windowType`
   - `sentence` 或 `passage`
2. `counterpartHits`
   - 該段同時提到哪些候選人物
3. `sourcePath`

## Output

### 最外層

1. `focusGeneralId`
2. `focusNameZhTw`
3. `relationships[]`
4. `canonicalWrites=false`

### `relationships[]` 每筆必要欄位

1. `fromId`
2. `toId`
3. `relationshipType`
4. `relationshipDirection`
   - `directed` 或 `bidirectional`
5. `timeScopeZhTw`
6. `evidenceQuoteZhTw`
7. `chapterRef`
8. `sourcePassageRef`
9. `confidence`
10. `reasonZhTw`
11. `canonicalWrites=false`

### 建議附加欄位

1. `cuePayload`
   - 若是規則先抓到的句內 cue，可附上 cue span 與 alias span
2. `sourceKind`
   - `focus-sentence`
   - `hard-spec-fallback`

## Skill 抽取步驟

skill 應依照下面順序做，不可跳步：

1. 先確認這批段落的主角是不是 `focusGeneralId`
   - 若句子主角不是焦點人物，只能當上下文，不可直接投射成焦點人物關係
2. 再確認句中另一位人物是誰
   - 必須是 `candidateCounterpartIds` 內的合法人物
3. 判斷句子是否真的在表達硬關係
   - 不是只看共現
   - 要看動詞、稱謂、親屬詞、婚配詞、主從詞、結義詞
4. 決定方向
   - 例如 `劉備任用諸葛亮` 與 `諸葛亮效力劉備` 最後都應落成 `liu-bei -> zhuge-liang` 的 `ruler_subject`
5. 附上最短且足夠支持的原文句
   - 不要用整大段，優先用真正承載關係語意的句窗
6. 若句子只有暗示、沒有明講
   - 不輸出
   - 或降成待後續 skill review / proposal

## 關係定義

### `ruler_subject`

可接受的語意：

- 任用、拜為、命令、派遣、使其領兵
- 投奔、歸附、效力、隨從、為其部下
- 明確說某人是某人麾下、帳下、部將、丞相、軍師、太守

不可接受的語意：

- 只是推薦、舉薦、談論、稱讚
- 只是兩人同場出現

### `faction_membership`

可接受的語意：

- 明確屬於某一勢力、國別、陣營
- 例如蜀、魏、吳、劉備軍、曹操軍、孫權軍

不可接受的語意：

- 只因為同句提到某主君，就直接推成同陣營

### `parent_child`

可接受的語意：

- 父、母、兒子、女兒、其子、其女
- 明確親生親屬語意

不可接受的語意：

- 義父義子
- 單純長幼尊稱

### `adoptive_parent_child`

可接受的語意：

- 義父、義子、養子、收為義子

### `spouse`

可接受的語意：

- 妻、夫人、娶、嫁、成婚、婚配

不可接受的語意：

- 後宮、女性同場、母系親族關係

### `sibling`

可接受的語意：

- 兄、弟、姊、妹、兄弟、姊妹、昆弟

不可接受的語意：

- 結義兄弟

### `sworn_sibling`

可接受的語意：

- 桃園結義
- 結拜為兄弟
- 義兄、義弟

## 強制反例守則

以下情況不得輸出關係：

1. 只有兩人共現，沒有關係語意
2. 關係語意其實掛在第三人身上
3. sourceQuote 只支撐其中一人，沒有同時支撐 pair
4. 單一句子太長，真正關係 cue 跟人物間隔太遠
5. 句子屬於旁白統計、人物清單、名單列舉，不是敘事句
6. `enemy_rival` 沒有衝突語意，不可亂升
7. `ruler_subject` 沒有明確主從 / 任用 / 效力語意，不可亂升

## 允許的保守策略

如果 skill 無法穩定判斷，可以：

1. 不輸出
2. 降低 `confidence`
3. 改寫 `reasonZhTw` 說明仍需 reviewer 驗證

但不可以：

1. 用猜的補方向
2. 因為人物很有名就直接補關係
3. 因為其他資料層已知而跳過原文支持

## 最低品質要求

1. 每筆關係都必須帶 `evidenceQuoteZhTw`
2. 每筆關係都必須帶 `sourcePassageRef`
3. 每筆關係都必須能從原文直接回看
4. 若沒有可回看的原文支持，該筆不得輸出到正式候選
