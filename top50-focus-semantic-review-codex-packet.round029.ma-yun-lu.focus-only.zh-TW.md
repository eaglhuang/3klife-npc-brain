# Codex 句級關係語意審查包

- 產生時間：`2026-05-26T16:07:38+00:00`
- 使用 skill：`integrations/codex-skills/sanguo-relationship-semantic-review/SKILL.md`
- 待審句子數：`4`
- 輸出 JSONL：`artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review/codex-relationship-semantic-reviewed-cache.jsonl`
- 原則：只根據原文句子判斷，不憑記憶補事實；不確定就寫 `not_enough_context`。
- 原則：`canonicalWrites=false`；這只是 evidence/proposal，不直接寫正式關係白名單。

## 輸出要求

請依 `sanguo-relationship-semantic-review` skill，為每個 `entries[]` 產生一行 reviewed cache JSONL。

## 待審項目摘要

### 1. `relsem.95a24be4c5280175bc656921`

- 原文：馬雲騄在小說中的丈夫是趙雲。
- 候選：spouse:ma-yun-lu->zhao-yun

### 2. `relsem.2fc0e6762a4da78274139eb5`

- 原文：馬雲騄是馬超之妹，後與趙雲成婚。
- 候選：spouse:ma-yun-lu->zhao-yun

### 3. `relsem.f1c1a6207f786057ec24c142`

- 原文：馬雲騄是馬騰之女，與趙雲成婚。
- 候選：spouse:ma-yun-lu->zhao-yun

### 4. `relsem.aefc414a1d1e0d565398b823`

- 原文：諸葛亮為趙雲做媒人，迎娶了馬超智勇雙全的妹妹馬雲騄，令其雙雙建功立業。
- 候選：sibling:ma-chao->ma-yun-lu
