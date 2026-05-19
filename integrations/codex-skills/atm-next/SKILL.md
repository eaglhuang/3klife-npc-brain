---
name: atm-next
description: Recommend the next official ATM guidance action from current state.
argument-hint: "<ATM context>"
charter-invariants-injected: true
---


# ATM Next

First command:

```bash
node atm.mjs next --json
```

## Route Command

Use this ATM command only after the first command confirms it is the current governed route:

```bash
node atm.mjs next --json
```

## Handoff

```bash
node atm.mjs handoff summarize --task "$ARGUMENTS" --json
```

## Charter Invariants

{{CHARTER_INVARIANTS}}

## Guardrails

- Stay inside ATM CLI routing and evidence contracts.
- Do not create a parallel task model, registry, or approval flow.
- Treat any planning hint as CLI output, not as template authority.
- If an `ATM_USER_NOTICE` message or `evidence.userNotice` is present, show it to the user in natural language before executing the returned next action.
- After an onboarding or refresh command succeeds, return to the user original request and continue the actual work.
