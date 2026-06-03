---
name: atm-captain-dispatch-standard
description: Captain 派工規範與決策手冊。當 AI 進入隊長模式或進行派工/派任務語境時，遵照這套標準，產出可供人類直接轉貼、高度結構化、符合 token 經濟且能動態適應可用 roster 的派工單。
---

# ATM Captain Dispatch Standard

This skill defines the Project Captain dispatch standard for producing precise, safe, pasteable work orders.

## Bundled References

This skill folder is designed to travel across repositories.

- `references/project-captain-mode.md`: portable Captain behavior, token / sidecar, and role / skill boundary rules extracted from keep.
- `references/dispatch-template.md`: pasteable dispatch templates and field rules.

When entering Captain mode or writing dispatch orders, read `references/project-captain-mode.md` if role boundary, sidecar, or delegation policy matters.

## Core Goal

Dispatch is not a chat summary. It is a work contract.

Every dispatch should make the next agent understand:

- what to do;
- where to do it;
- what not to touch;
- how to report back;
- whether the work is read-only, planning, implementation, review, or closure.

## Pre-flight Routing

Before sending a work order, classify the task:

1. Small question: Captain answers directly.
2. Small read-only check: use short-lived internal mini sidecars.
3. Dependent read-only follow-up: do not pre-dispatch externally; run internally after the upstream result returns.
4. Scope audit or complex judgment: use a reviewer / audit agent.
5. Implementation, claim, commit, close, or PR: use an executor agent with strict allowed files.

## Roster Binding

Never put internal capability labels such as `MINI-RO`, `JUDGE`, or `EXEC-FAST` in the first line of an external dispatch.

Use human-recognizable roster IDs such as `001` to `007`, or `子代理-01` for internal sidecar tasks when the user explicitly asks for a pasteable subagent brief.

## Standard Dispatch Fields

Every external dispatch must include:

1. First line report format: `回報第一行必須是：代號：<roster-id>；模型：<實際使用模型>`
2. Task
3. Repo
4. Background in plain language
5. Scope / allowed files
6. Report format
7. Forbidden actions
8. One plain-language takeaway

## Context Map Rule

Context Map is risk-based, not mandatory for every small check.

Use Context Map when the task crosses repos, touches implementation, claim, close, commit, cleanup, reconciliation, release, evidence, ledger, source, artifact, or scope audit.

Context Map format:

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

## Forbidden Defaults

Unless explicitly authorized:

- no push;
- no merge;
- no rebase;
- no destructive cleanup;
- no unrelated dirty file staging;
- no hand-editing managed runtime history;
- no broad source rewrite hidden inside a small card.
