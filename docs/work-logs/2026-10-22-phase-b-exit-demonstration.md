# Phase B exit demonstration: propose → PR → merge → apply from a plain MCP client

This is the run record for the Phase B *Exit criteria* row of
[`docs/roadmap.md`](../roadmap.md) (§7), which asks for "a demonstration of
propose → PR → merge → apply from both Claude Code and Codex (or any plain MCP
client)". **The demonstration is this dated run.**
`tests/e2e/test_phase_b_exit_demonstration.py` is its in-repository port and
keeps it true; the last section states what that test holds and what it does
not. The row's other half, a `KnowledgeCandidate` generated from an ingested
review thread, was met by slice B5
([ADR-0033](../adr/0033-knowledge-candidate-generation.md)) and is not
re-measured here.

## Anchor

| | |
| :-- | :-- |
| Commit | `61dfe00e`, `origin/main` when the run was made (reachable from `origin/main`) |
| Date | 2026-10-22, local |
| Window | 2026-10-21T23:49:51Z to 2026-10-21T23:49:57Z, UTC, from the brackets below |
| Machine | the development machine whose resident dogfood daemon holds port 7419 (pid 1002, launchd label `dev.theurian.daemon`); no model or OS version was recorded |
| CLI | the virtualenv CLI of a detached worktree at `61dfe00e`, reporting `version 0.5.3`, as do `initialize`'s `serverInfo` and `system.capabilities`; the code under test is the commit above |
| Client | a plain Streamable HTTP MCP client on the standard library's `http.client`, `clientInfo.name` `plain-client` |

## Fences as run

- Every CLI call used the absolute virtualenv binary, with `cwd=` and the
  environment set in the same `subprocess.run`: `HOME`, `THEURIAN_DATA_DIR`,
  `UV_TOOL_DIR`, `UV_CACHE_DIR` and `GIT_CEILING_DIRECTORIES` all under one
  scratch directory, `phaseb/`.
- The daemon was `theurian daemon start --foreground --port 7420`, started as a
  child process (pid 3214) and stopped by SIGTERM to that pid (return code
  -15).
- `setup` ran only as `setup --dry-run --port 7420`. No `uninstall`, and no
  real `setup`.
- The PR leg used a local bare remote, `phaseb/remote.git`: a branch push, a
  local `git merge --no-ff`, and a push of `main`. No GitHub repository was
  created or contacted.

Brackets, pasted from the record:

```
== BEFORE 2026-10-21T23:49:51Z
7420 rc=1                                   # nothing listening
python3.1 1002 utchy  TCP 127.0.0.1:7419 (LISTEN)   # resident dogfood daemon
1002	0	dev.theurian.daemon                 # resident launchd entry
== AFTER 2026-10-21T23:49:57Z
7420 rc=1                                   # dev daemon gone; ps -p 3214 rc=1
python3.1 1002 utchy  TCP 127.0.0.1:7419 (LISTEN)   # unchanged
1002	0	dev.theurian.daemon                 # unchanged
```

`git status --short` in the main checkout and in the worktree was unchanged
afterwards (`?? .serena/`, pre-existing, in the main checkout only). The real
CLI ran against the scratch directory and registered nothing with the service
manager.

## Steps

| # | Step | Command or wire call | Result |
| :-- | :-- | :-- | :-- |
| s0 | setup dry-run | `theurian setup --dry-run --port 7420 --json` | rc 0; `find home data -mindepth 1` printed nothing: no files written |
| s1 | repositories | `git init --bare -b main remote.git`; `git init -b main demo`; `git remote add origin` | rc 0 |
| s2 | seed | `init --json`; a seed migration (one approved item, `architecture.auth-policy`); `project register --json`; commit; push; `migrate apply --json`; `index build --json` | all rc 0; projectId `demo`; 1 migration applied; 1 chunk published |
| s3 | daemon | `daemon start --foreground --port 7420` | pid 3214; healthy; token read from `data/auth/mcp-token` |
| s4 | handshake | `initialize` (`2025-06-18`) → `notifications/initialized` → `tools/list` → `system.capabilities` → `project.list` | 10 tools, among them `knowledge.proposeChange`, `knowledge.generateMigrationDraft` and `review.generateKnowledgeCandidate`; `writeTools: true`; version `0.5.3`; one project, `demo` |
| s5 | **propose** (wire) | `tools/call knowledge.proposeChange`: new item `architecture.retry-policy`, label `authored-in-theurian` | proposal `01M5GDY4DEZ4JG3WYF96D9F1A6`: a migration, a body and `evidence.json` under `.theurian/proposals/<id>/` |
| s6 | accept, **PR**, **merge** | `propose accept <id> --json`; `migrate validate --json`; `checkout -b proposal/<id>`; `add .theurian`; commit; `push -u origin`; `diff --stat main...HEAD`; `checkout main`; `merge --no-ff`; `push origin main` | accept rc 0, `secretFindings: []` under policy `block`; validate `valid: true`, `migrationCount: 2`; PR diff: the migration, the body and `evidence.json`, 3 files, 38 insertions; the merge commit is on the bare remote's `main` |
| s7 | **apply** | `migrate apply --json`; `index build --json`, with the daemon still running | 2 migrations applied, under a new `stateHash`; 2 chunks published |
| s8 | served (wire) | `knowledge.get`, `knowledge.search`, `knowledge.status` | item served `status: approved`, `trustLevel: unverified`, `sourceAnchors: []`, `contentClassification: untrusted-knowledge`; search `count: 1`, `stale: false`, `indexBuildId` `01M5GDY5XQH3PJE72AMM886K53` (the s7 build), so the running daemon served the new build without a restart; status `itemCount: 2`, `{approved: 2}` |
| s9 | **propose** (wire, second tool) | `tools/call knowledge.generateMigrationDraft`: `deprecateItem architecture.auth-policy` | proposal `01M5GDY5ZRPV9M1AEDFJ7H1MHR`, operations `[deprecateItem]` |
| s10 | accept, PR, merge | as s6 | rc 0 throughout; validate `valid: true`, `migrationCount: 3`; PR diff: the migration and `evidence.json`, 2 files, 19 insertions |
| s11 | apply | `migrate apply --json`; `index build --json` | 3 migrations applied; `indexPurge: {published: true, removed: 1}`; a new `stateHash`; 1 chunk published |
| s12 | refused (wire) | `knowledge.get`, with and without `includeUnapproved`; `knowledge.search`; `knowledge.status` | `get` refused both ways with `'architecture.auth-policy' is not present in project 'demo'.`; search `count: 0`; status `itemCount: 1`, `{approved: 1}`, so the retired item is unreachable, as SEC-13 designs |
| s13 | tree | `git status --short --ignored` | only `!! .theurian/runtime/` and `!! .theurian/state/` |
| s99 | daemon stop | SIGTERM to pid 3214 | return code -15 |

**Verdict: the demonstration completes end to end from a plain MCP client, for
both draft-producing tools.** No step needed a fix to complete.

## The PR leg: a local merge, and why it suffices

The row says "PR → merge". The run's PR leg is a branch pushed to a local bare
remote and merged into `main` with `git merge --no-ff`; no forge pull request
was opened. The `watchdog` agent ruled on 2026-10-22 that **the local flow is
the demonstration**. Its reasoning: Theurian observes nothing of the
review-and-merge step. The [README](../../README.md) says "nothing in the code
checks that the merge happened";
[ADR-0034](../adr/0034-migrate-apply-enforces-the-merge.md)'s check enforces
only that a migration is committed at `HEAD`; and
[T-15](../security/threat-model.md) records the merge as its residual. A bare
remote and `merge --no-ff` therefore exercise every Theurian step the row names,
and a forge pull request would add no outcome Theurian can observe. The owner
may still ask for a forge-PR variant on a scratch repository.

## The three client legs

- **Plain MCP client: run**, above. This is the row's "(or any plain MCP
  client)" clause.
- **Claude Code: the same wire calls, not run live.** Claude Code speaks the
  same Streamable HTTP MCP to the daemon, so the calls above are the calls it
  would issue. A live run was not made. The measuring session's own Claude Code
  connection to the resident daemon on 7419 answered `system.capabilities` with
  `version 0.1.0.dev18` and `writeTools: false`, and exposed no write-intent
  tool. A live leg waits on the operator's installed daemon carrying the write
  tools, which means core 0.3.0 or later
  (`packages/theurian-core/CHANGELOG.md`, `## [0.3.0]`). Pointing Claude Code at
  the dev daemon instead would mean editing `~/.claude.json`, which the fences
  forbid. This is an operator matter, not a repository gap.
- **Codex: not run.** `codex exec` returned 400 on the owner's account when
  last tried (recorded 2026-10-21), and it was not re-tried for this run.

## Observations, each with a disposition

| Observation | Disposition |
| :-- | :-- |
| `migrate apply` reports `databaseCreated: true` on every apply (s2, s7, s11) | By design: one state database per distinct state ([ADR-0007](../adr/0007-state-hash-partitioned-databases.md)), so each new `stateHash` gets a new database. Recorded and closed. |
| The deprecated item's `knowledge.get` says "is not present", with `includeUnapproved` too | By design: `may_surface` surfaces a deprecated item under neither value of the flag, and the refusal is byte-identical to an absent id's (SEC-13; `mcp/tools.py:2400-2403` at `61dfe00e`). Recorded and closed. |
| `evidence.json` stays in `.theurian/proposals/<id>/` after accept and rides in the PR | By design for a proposal not drafted with `--local`: accept's first next step reads "open a pull request with the proposal directory in it" (s6). A `--local` proposal gets a different step (`cli/propose_commands.py:663-676` at `61dfe00e`; [ADR-0028](../adr/0028-a-local-proposal-is-a-different-directory.md)). Recorded and closed. |
| The PR leg is a local merge on a bare remote, not a forge pull request | Ruled by the `watchdog` agent, 2026-10-22: the local flow is the demonstration (previous section). Recorded and closed. |
| The roadmap said the demonstration "is F ①'s adapter and has not run" | The plain-client run needed no install adapter: the client is configured by hand. F ① stays its own Phase F item, off this row's critical path. The roadmap row is corrected in the same change as this log. |

Nothing was filed.

## Re-check

The re-check is `tests/e2e/test_phase_b_exit_demonstration.py`, the run ported
into the suite. Its fixture builds a git repository with a bare remote and one
approved seed item, committed, pushed, applied and built, and serves it from a
real `theurian daemon start --foreground` on a free port. `HOME`, the data
directory and the git ceiling sit under the test's temporary directory, and
inherited `GIT_*` variables are stripped. Each test drives one tool over the
wire, then runs `propose accept`, commits on a branch, pushes, merges `--no-ff`
into `main`, pushes `main`, and runs `migrate apply` and `index build`.

- `test_a_proposed_item_is_served_once_a_human_merges_and_applies_it`
  (`knowledge.proposeChange`) first asserts that the proposal alone is not
  served: before the human cycle, `knowledge.get` on the proposed item errors
  and a `knowledge.search` for its text returns no results. After the cycle it
  asserts that `get` returns the item with `status: approved`, that `search`
  returns exactly that item, and that `retrieval.stale` is `false`.
- `test_a_deprecation_draft_retires_the_item_once_a_human_merges_and_applies_it`
  (`knowledge.generateMigrationDraft`, `deprecateItem`) first reads the seed
  item through `get` and `search` as a positive control. Afterwards it asserts
  that `get` errors with and without `includeUnapproved`, that `search` no
  longer returns the item, and that `knowledge.status`'s `itemCount` dropped by
  one. The withdrawal does not wait for the `index build` after the apply:
  `migrate apply` publishes a purged build in the same command (s11,
  `indexPurge` `published: true`, `removed: 1`;
  [ADR-0024](../adr/0024-a-purge-is-a-build.md) decision 5).

It does not repeat everything the run did. It runs no `setup --dry-run`,
`tools/list`, `system.capabilities`, `migrate validate` or `git diff --stat`. It
asserts that `get` errors, not the refusal's text. It also checks one thing the
run did not: before the human cycle, `get` errors and `search` returns no hit
for the proposed item.

core.yml's `e2e` job runs it with `pytest tests/e2e -m e2e` on ubuntu-latest
and macos-latest, and fails on a skip. That workflow runs only on the pushes to
`main` and the pull requests its path filter selects (Core, `schemas/`,
`tests/`, `tools/`, the workflow files and a short list of named paths), so a
pull request that touches only other documentation does not run it.
