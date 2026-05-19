---
name: atm-upgrade-scan
description: Scan evidence reports and draft governed upgrade proposals.
argument-hint: "<ATM context>"
charter-invariants-injected: true
---


# ATM Upgrade Scan

First command:

```bash
node atm.mjs next --json
```

## Route Command

Use this ATM command only after the first command confirms it is the current governed route:

```bash
node atm.mjs upgrade --scan --input "$ARGUMENTS" --json
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
