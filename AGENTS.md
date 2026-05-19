<!-- ATM ROOT ENTRY:START -->

## ATM Governance Entry

Read the repository root README.md for project context, then run:

```bash
node atm.mjs next --json
```

If the result includes an `ATM_USER_NOTICE` message or `evidence.userNotice`, show it to the user in natural language before running the returned next action.

If existing host instructions reference a missing local document, mention the missing file once and continue with the ATM route.

After an onboarding or refresh command succeeds, return to the user original request and continue the actual work.

Treat these ATM runtime files as supporting state only:

- .atm/history/tasks/BOOTSTRAP-0001.json
- .atm/runtime/profile/default.md
- .atm/history/evidence/BOOTSTRAP-0001.json

<!-- ATM ROOT ENTRY:END -->

# ATM Bootstrap Instructions

This repository uses the default ATM bootstrap pack.

Start with this line:

Read README.md if present, then run "node atm.mjs next --json" from the repository root. If the result includes `ATM_USER_NOTICE` or `evidence.userNotice`, show it to the user before executing the returned next action. Use .atm/history/tasks/BOOTSTRAP-0001.json, .atm/runtime/profile/default.md, and .atm/history/evidence/BOOTSTRAP-0001.json only as supporting runtime state.

Bootstrap files:

- Task: .atm/history/tasks/BOOTSTRAP-0001.json
- Lock: .atm/runtime/locks/BOOTSTRAP-0001.lock.json
- Profile: .atm/runtime/profile/default.md
- Project probe: .atm/runtime/project-probe.json
- Default guards: .atm/runtime/default-guards.json
- Evidence: .atm/history/evidence/BOOTSTRAP-0001.json

Operating rules:

1. Keep the host workflow as manual.
2. Treat the repository kind as generic-repository.
3. Do not invent a package manager or build step when the probe reports none.
4. Write a short evidence update before finishing the bootstrap task.
