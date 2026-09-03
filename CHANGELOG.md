# Changelog

This repository ships **two independently versioned artifacts**
([ADR-0001](docs/adr/0001-monorepo-with-independent-artifacts.md)). Their
changelogs are the ones you want:

- **Theurian Core** — [`packages/theurian-core/CHANGELOG.md`](packages/theurian-core/CHANGELOG.md)
- **Claude Code plugin** — [`plugins/claude-code/CHANGELOG.md`](plugins/claude-code/CHANGELOG.md)

This file records repository-level events only: governance changes, licence
changes, and milestone completions.

## Repository history

### 2026-08-20 — Forward planning moved from milestones to phases

[`docs/roadmap.md`](docs/roadmap.md) was adopted as the plan of record, and the
README's forward-looking milestone rows were retired in favour of its phases.
The numbering had stopped being trustworthy: ADR-0013 records `theurian propose`
as landing in Milestone 7 while the README listed Milestone 7 as `planned`, and
the definition of that milestone differed between documents. Milestones 0–6 stay
in the README as shipped history.

Recorded here because it changes how work is planned and announced. It is not a
milestone completion, and it is not comparable to the entries below — and the
entries below are not a complete set: milestones 1 through 6 are missing from
this file, an inconsistency the roadmap's own appendix records against Phase 0.

### 2026-08-01 — Milestone 0 complete

Architecture and OSS foundation.

- Requirements, non-functional, security, and OSS requirements catalogued with
  stable identifiers referenced from ADRs and tests.
- Fifteen ADRs, each with rejected alternatives and an enforcement mechanism.
- Domain model with ten enforced invariants; fourteen ports as `Protocol`s.
  *(What Milestone 0 shipped. The set has grown since;
  [ADR-0003](docs/adr/0003-ports-and-adapters.md) point 5's Milestone 7
  amendment names `ALL_PORTS` as the register and carries the current count.)*
- Public JSON Schemas as the Core/plugin contract, co-owned in CODEOWNERS.
- Claude Code plugin skeleton: twelve commands, a bounded `SessionStart` hook,
  and a connection template that setup installs rather than the plugin
  auto-registering.
- Threat model v1: four trust boundaries, sixteen enumerated threats.
- Path-filtered CI: quality, tests, an offline run with the network blocked,
  packaging, CodeQL, dependency review, licence scan, SBOM, secret scan,
  Conventional Commits, DCO, and documentation link checking.
- 275 tests, 82% coverage, strict type checking, all offline.

### 2026-08-01 — Project started

Apache-2.0, DCO, maintainer-led governance.

## Compatibility matrix

| Plugin | Core | Protocol |
| :-- | :-- | :-- |
| 0.1.x | ≥ 0.1.0-dev.0, < 0.2.0 | `theurian/v1` |
