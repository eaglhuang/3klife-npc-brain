---
doc_id: doc_agentskill_0065
name: sanguo-autopilot-marathon
description: 'Sanguo autopilot marathon skill. Use for: 一鍵跑完、全自動長跑、top50 每位人物完整度 95%、來源耗盡、checkpoint/resume、autopilot marathon、marathon controller、長跑總控器、一次啟動多輪 round。'
argument-hint: '可指定 --plan-only、--dry-run、--resume、--max-rounds、--top-n、--target-completeness、--top-source-refs、是否允許 overlay apply。'
---

# Sanguo Autopilot Marathon

這個 skill 用來把三國資料管線從「單次 round 自動化」推進成「可 checkpoint / resume 的長跑總控器」。

Unity 對照：這比較像一個長跑版的 Editor automation controller，不是單一 importer。底層 extractor 已經存在時，skill 只負責把既有 pipeline 組成可反覆執行、可停可續、可產生 stop evidence 的 orchestration layer。

## When to Use

- 使用者說「一鍵跑完」、「全自動跑到停」、「跑到 top50 每位人物 95%」。
- 使用者要從現有 autopilot、source-cold、alias resolution、repair backlog、readiness estimator 組成一條長跑流程。
- 使用者要求 checkpoint / resume、progress report、stop reason、round history。
- 使用者要的是 orchestration，不是重寫 extractor。

## Core Principle

1. Orchestration first：只做總控，不重寫 extractor。
2. Canonical stays frozen：預設不碰 canonical，維持 `canonicalWrites=false`。
3. Stop by evidence：停止條件以 top-N 完整度與來源耗盡證據為準，不靠平均值猜停。
4. Resume is mandatory：每輪都要能從 `marathon-state.json` 續跑。

## Default Controller

主要入口是 [pipelines/sanguo-rag/run_sanguo_autopilot_marathon.py](pipelines/sanguo-rag/run_sanguo_autopilot_marathon.py)。

薄的 Node 一鍵入口是 [scripts/run_sanguo_autopilot_marathon.js](scripts/run_sanguo_autopilot_marathon.js)，它會預設補上：

- `--resume`
- `--overwrite`
- `--advance-source-ref-window`
- `--max-rounds 5`
- `--allow-apply`
- `--apply-bucket propose-lane`

目前 controller 會在使用預設 source-event-packets 時，自動 union local/codex-smoke 的同類 packet，輸出 extended pack；source-ref window 也會依前一輪可用 budget 走 adaptive cyclic step，避免直接滑出可選範圍。

建議使用順序：

1. `--plan-only`：只產生控制器計畫與 state / progress 骨架，不呼叫 autopilot。
2. `--dry-run --max-rounds 1`：先跑一輪 read-only rehearsal，確認 round / progress / stop evidence。
3. `--resume`：正式長跑時從上次 state 接續。
4. `--allow-apply`：只有在明確要產 staged overlay 時才開，且仍保留 canonical freeze。

## Procedure

### 1. 先建立 marathon state

控制器會初始化：

- `local/sanguo-autopilot-marathon/marathon-state.json`
- `local/sanguo-autopilot-marathon/progress-current.md`
- `local/sanguo-autopilot-marathon/progress-history.jsonl`
- `local/sanguo-autopilot-marathon/stop-evidence.json`

### 2. 每輪先做 readiness gate

每輪會讀 `artifacts/data-pipeline/sanguo-rag/extracted/core-person-progress/current.json`，計算：

- top N 人物 ready count
- min / avg completion percent
- lowest blockers

### 3. 每輪呼叫既有 autopilot

長跑控制器會把每輪 queue 與 source-event-packets 丟給現有 autopilot，並保留：

- decision buckets
- review-handoff
- skill-review-pairs
- round summary

若預設 source-event-packets 仍不足，controller 會先把 local/codex-smoke 的同類 source-event-packets 併成 extended pack，再繼續跑後續 round。

### 4. 每輪更新停止候選

停止條件包含：

- top-N 人物全部達標
- source signature 連續多輪重複，視為來源耗盡
- 到達 max rounds
- 使用者要求的 error stop

## Expected Outputs

長跑結束時至少要有：

1. `marathon-state.json`
2. `progress-current.md`
3. `progress-history.jsonl`
4. `stop-evidence.json`
5. 至少一輪 round 目錄與 round-record

## Guardrails

- 不要把這個 skill 當成 extractor 重寫器。
- 不要把 reviewer / alias / source-cold / repair flow 改成 canonical 寫入。
- 不要把平均分數當成 top50 完成門檻。
- 如果使用者只要試跑，先用 `--plan-only` 或 `--dry-run --max-rounds 1`。
