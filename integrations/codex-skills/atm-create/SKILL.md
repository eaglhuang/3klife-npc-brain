---
name: atm-create
description: Create and register an atom through the provisioning facade.
argument-hint: "<ATM context>"
charter-invariants-injected: true
---


# ATM Create

First command:

```bash
node atm.mjs next --json
```

## Route Command

Use this ATM command only after the first command confirms it is the current governed route:

```bash
node atm.mjs create --bucket CORE --title "$ARGUMENTS" --dry-run --json
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
