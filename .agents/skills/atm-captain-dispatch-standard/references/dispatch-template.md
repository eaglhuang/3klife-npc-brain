# Captain Dispatch Template

Use this file when the user asks for a pasteable external work order.

## Minimal Template

```text
回報第一行必須是：代號：<roster-id>；模型：<實際使用模型>

任務：<TASK-ID or concise task>。

repo：
<absolute repo path>

背景白話：
<2-4 sentences: why this work exists, what changed before it, and what the key boundary is.>

請做：
1. <concrete step>
2. <concrete step>
3. <validator or read-only check>

請回報：
1. 結論：PASS / CONCERN / BLOCK
2. 實際觀察或修改
3. validators / route / evidence result
4. staged / commit / push 狀態
5. 風險或缺口
6. 下一步
7. 大白話補一句

禁止：
- 不准改 unrelated files
- 不准 stage/commit/push unless the task explicitly says so
- 不准 reset/restore/clean/stash
- 不准把 source / evidence / release / ledger 混袋

大白話補一句：
<one plain-language guardrail>
```

## Context Map Block

Use this block when scope drift risk exists:

```text
── Context Map ──
Primary（直接改 / 直接查）：
  - <path> — <why>
Secondary（可能波及，預警 scope drift）：
  - <path> — <relationship / risk>
Test Coverage：
  - <test or validator> — <what it proves>
Patterns to Follow：
  - 沿用 <reference path> 的 <style>
```

## Dependent Follow-up Rule

If a read-only follow-up must wait for another external task to finish, do not pre-dispatch it externally. Wait for the upstream report, then let Captain run the check locally or with an internal mini sidecar.
