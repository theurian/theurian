---
description: Diagnose Theurian problems and suggest fixes. Never repairs automatically.
allowed-tools: Bash(theurian:*), Bash(curl:*), Read
---

# /theurian:doctor

Diagnose Theurian and report findings with remedies.

## What to do

```sh
theurian doctor --json
```

Group the findings by severity and, for each, show what was observed, why it
matters, and the exact command that fixes it.

## Rules

- **Never run a repair automatically.** Doctor exists precisely for situations
  where the safe action is not obvious, and an automatic repair of a suspected
  corruption is how data gets lost. Print the command; let the user run it.
- Never delete anything.
- If a finding involves the authentication token, do not print the token or any
  part of it. `theurian doctor --json` redacts by default; do not disable that.
- If the user asks for a report to paste into a public issue, use
  `theurian doctor --report --json`, which redacts knowledge bodies and
  credentials, and remind them to review it before posting.
