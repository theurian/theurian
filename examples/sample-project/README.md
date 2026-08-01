# Sample project

A minimal but complete `.theurian/` directory, so you can see the shape of a real
knowledge base without writing one first (OSS-13).

## What is here

```text
.theurian/
├── config.yaml                          project configuration, no secrets
├── knowledge/
│   ├── architecture/auth-policy.md      an approved architecture decision
│   └── domain/order-cancellation.md     a rule generalized from a review
├── specifications/
│   └── order-cancellation.yaml          a spec that keeps its structure
└── migrations/
    ├── 01K1ABCXYZ...-add-auth-policy.yaml
    └── 01K1DEFABC...-add-order-cancellation.yaml
```

## What to notice

**Content and state are separate.** The Markdown files hold the knowledge. The
migrations hold status, ownership, sensitivity, and validity. Neither duplicates
the other, so neither can go stale relative to the other.

**The specification stays structured.** `order-cancellation.yaml` has
`preconditions`, `rules`, and `outcomes` as data. That is what makes
`spec.getCoverage` able to ask "which outcomes have tests?".

**Review evidence is attached, not quoted.** The second migration records the
review thread as evidence for the rule. The rule is a generalization a human
approved; the thread is the reason to believe it.

**`dependsOn` orders the migrations.** The second depends on the first, so
applying them to an empty database always produces the same state.

**Nothing here is derived.** No SQLite, no index, no embeddings. Those are
rebuilt from exactly these files (ADR-0004).

## Trying it

> Requires Milestone 1. Today, `theurian version --json` works and the rest is
> the contract these files are written against.

```sh
theurian project register examples/sample-project
theurian migrate validate --json
theurian migrate apply --json
theurian index build --json
```
