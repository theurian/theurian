# Milestone 7 dogfooding, the dev7 corpus: 82 items through the released package

Theurian now holds 24 of its own ADRs and 2 work logs as committed knowledge,
and 56 operator handoff notes as local-only knowledge that never enters a
commit. Every one of the 82 was seeded with the **released** `0.1.0.dev7` wheel
— no dev checkout took part in a write — and every migration sets its governance
explicitly instead of inheriting the loader's defaults. That is the thing the
first slice could not do, and the thing it recorded a decision to wait for.

Everything below was measured on **2026-08-19**. The corpus is committed as
`a5af66d` and its guard as `52bf3f5`; every body is a verbatim copy of a `docs/`
file at `2a98d4c8963cdf46cc6169e43ac7add039745342`, which every `sourceAnchor`
pins. Every command set `HOME`, `THEURIAN_DATA_DIR`, `UV_TOOL_DIR` and
`UV_CACHE_DIR` to scratch directories **in the same command** that ran the CLI.

The first slice's log —
[2026-08-18](2026-08-18-milestone-7-dogfooding-first-slice.md) — records a
different corpus, which this one supersedes. Its measurements stand as the dated
record of that run; its state hash and its three migrations are not reachable
from this branch.

## What ran

| Command | Result |
| :-- | :-- |
| `uv tool install --python 3.13 'theurian[daemon]'` | `0.1.0.dev7` from PyPI, released 2026-08-19 13:44 UTC |
| `theurian init` | already initialised; the rerun reports `gitignoreUpdated: false` and changes nothing |
| `theurian propose` ×82 | 24 ADRs, 2 work logs, 56 operator handoff notes from the Obsidian vault |
| `theurian propose accept` ×82 | each after an orchestrator review of the generated migration |
| `theurian migrate validate` | `valid: true`, 82 migrations |
| `theurian migrate apply` | applied |
| `theurian index build` | 1113 chunks, `published: true` |
| `theurian index status` | `stale: false` |

A proposal is a draft until somebody reads it (ADR-0013), so the review between
`propose` and `propose accept` is not ceremony: it is where the generated
migration's metadata, its `contentSha256` and its source anchor are checked
against the document being copied. Eighty-two of them, one at a time.

**Every accept was followed by an assertion that the migration file exists.**
That guard is aimed at [#253](https://github.com/theurian/theurian/issues/253) —
an interrupted `propose` leaves the proposal directory half-emptied with no
migration anywhere, and `propose accept` then reports that no action is needed.
The failure mode is a misdiagnosis, not a crash, so nothing surfaces it except a
check that looks. It never fired in 82 accepts.

## Two corpora, and only one of them a reader can reproduce

| Corpus | Items | Chunks | State hash |
| :-- | --: | --: | :-- |
| committed | 26 | 669 | `73cda6f9fc0df27be45a8badb25ce2c4ee620f418a9b37f974888dc6b3d80a66` |
| local superset | 82 | 1113 | `d07f2eba…` |

This is by design, and it is why both numbers are written down here rather than
in a commit message. The committed slice is what a clone contains, so 669 is the
number a reader can check. The 1113 belongs to a tree that exists on one
machine, and no reader can reach it — a work log is the right place for a
measurement nobody else can take.

**The committed slice derives standalone.** The adversarial review reproduced
`73cda6f9…` and 669 chunks four independent ways: from a `git archive` of the
tree plus `theurian init`, from a linked worktree, from a real clone, and from a
tree with every `evidence.json` deleted. Three of those move the absolute path;
one removes files that are committed beside the corpus but are not inputs to it.
That is [ADR-0016](../adr/0016-state-hash-covers-the-working-tree.md)'s decision
3 — *no absolute path, mtime, inode, hostname, or environment value enters the
hash* — measured on real data rather than on a fixture.

It says nothing about the rows, and the first slice's log already records why:
`compute_state_hash` hashes the inputs, so equal hashes mean equal migrations
and bodies, not equal output. What is new here is only that the property holds
over 26 items and four derivations instead of three items and two.

## Governance is set, not defaulted

Measured in the derived SQLite, after apply:

| Where | Items | `trustLevel` | `sensitivity` |
| :-- | --: | :-- | :-- |
| committed | 26 | `reviewed` | `public` |
| local only | 50 | `inferred` | `internal` |
| local only | 6 | `inferred` | `confidential` |

**Six items across five note numbers.** The `confidential` set is the vault's
disclosure-handling notes — the ones whose subject is an embargo rather than a
decision — numbered 44 to 48. Number 48 exists as two files, an English
`2026-08-16-48-dev4-record-closure-and-m7-handoff.md` and a Japanese
`2026-08-16-48-record-closure-and-m7-handoff.md`. That is a historical numbering
collision the vault keeps deliberately, and both files were seeded, so five
numbers produce six items. Nothing in the set is committed, and nothing in it is
`reviewed`: a handoff note is what one session told the next, which is exactly
what `inferred` means.

`--trust-level`, `--sensitivity`, `--scope-path` and `--label` are
[#249](https://github.com/theurian/theurian/issues/249)'s options, and they
shipped in dev7. **This discharges the first slice's recorded decision** — *the
remaining 21 ADRs are not seeded until #249 ships those options*. It was
discharged by a release, not by a workaround: no migration in this corpus was
hand-edited, where the first slice had to hand-edit all three of its own to
correct `unverified`/`internal` on approved public content.

## What carries a scope, and what deliberately does not

The 24 ADRs are scoped to the tree each one governs:

| `scope.paths` | ADRs |
| :-- | :-- |
| `**` | 0001, 0009, 0014, 0015 |
| `plugins/**` | 0012 |
| `packages/theurian-core/**` | the remaining 19 |

Labels are `adr` plus `adr-NNNN`, so a query can reach one decision or all of
them. The two work logs carry `work-log`, one of them `disclosure` as well, and
the 56 notes carry `handoff-note` plus `superseded` where that applies.

**The work logs and the notes carry no scope at all, and that is a decision, not
an omission.** They govern process rather than source: no glob over
`packages/` or `plugins/` says anything true about a record of what four review
rounds found. Scoping them to `**` would be the lazy answer and the wrong one,
because it claims relevance to every file in the repository.

The distinction is worth recording precisely because it is not observable today.
`scope_paths` is written by the loader, stored by the SQLite store and read back
into `RevisionMetadata` — and nothing anywhere matches it against a path. The
production tree contains no glob matcher: `fnmatch`, `PurePath.match` and
`pathspec` appear in it zero times. So `()` and `('**',)` behave identically
right now and will stop doing so the day a matcher exists, which is the day this
corpus needs to already be right.

## Who wrote the proposals

Every `evidence.json` in the corpus records `model: claude-fable-5`. That is the
real identifier of the model that drafted these proposals (Claude Fable 5), the
same check the first slice's log recorded for its three. Both reviews asked for
it to be re-confirmed rather than carried forward from that log, and it was,
against the running session.

## The private/public boundary, and what actually holds it

Fifty-six of the 82 items must never be committed. What keeps them out is a
glob fence in `.git/info/exclude`:

```text
# >>> local-knowledge (private-by-default; NEVER commit) >>>
.theurian/knowledge/**
.theurian/migrations/*.yaml
.theurian/proposals/**
# <<< local-knowledge <<<
```

The shape is private-by-default: an untracked addition under those paths is
invisible to `git add -A` and to `git status`, tracked files are unaffected, and
publishing a *new* item takes a deliberate `git add -f`. A fence that listed the
private notes instead would have to be edited every time one arrived, and the
one time it was not edited is the commit that leaks.

Three residuals, all recorded as
[#265](https://github.com/theurian/theurian/issues/265):

- **A clone inherits no fence.** `.git/info/exclude` is machine-local. It does
  reach this machine's linked worktrees, because they share the common Git
  directory, and it reaches nothing past that.
- **`git clean -xdf` deletes all 56 local notes**, precisely because they are
  invisible to Git. They are recoverable — the Obsidian vault is their source
  and Theurian holds a copy, not the original — but the recovery is manual.
- **The only repository-side assert is the guard test**, `52bf3f5`. It is what a
  clone gets: 13 tests reading nothing but what Git ships, holding that every
  committed revision is exactly `public`/`reviewed`/`approved`, that every pin
  resolves to a tracked body whose bytes hash to the declared `contentSha256`
  and match the blob at its own anchor commit, and that the managed `.gitignore`
  block is present exactly once with exactly the patterns `init` writes. A stray
  `git add -f` of an `internal` or `confidential` item goes RED in CI.

That test has one known gap, recorded in its own skip message rather than left
to be discovered: the byte-identity rule against the anchor commit needs the
anchor object, and `actions/checkout` defaults to `fetch-depth: 1`, so the rule
skips in CI until the job asks for `fetch-depth: 0`. In any complete clone a
missing anchor is a failure, not a skip. The workflow change is owed to
`theurian-ci` and is not in this branch.

Sensitivity is a published label, not a serving predicate
([#119](https://github.com/theurian/theurian/issues/119)), which is why the
boundary has to hold at the commit: a local-only note that reaches the
repository is a note that gets served.

## What the run found

| Issue | What |
| :-- | :-- |
| [#262](https://github.com/theurian/theurian/issues/262) | the documented-commands scan walks git-ignored files, so a local-only corpus turns the suite RED |
| [#263](https://github.com/theurian/theurian/issues/263) | nothing checks the committed corpus for drift against its `docs/` sources |
| [#264](https://github.com/theurian/theurian/issues/264) | `init`'s managed `.gitignore` block omits `.theurian/evaluations/` and `.theurian/schema/` |
| [#265](https://github.com/theurian/theurian/issues/265) | the boundary class above |

**#262 is the one that only dogfooding could find.** The scan walks every file
in the tree by design, and until this run nothing had ever put fifty-six
untracked, git-ignored markdown files inside it. The suite was green in CI and
RED on the machine that had the corpus — which is the worst arrangement, because
CI is where anybody would look. The fix is on `fix/command-scan-population` and
not in this branch.

One observation, not filed. The project-local commands write `provenance.json`
— the map from project root to the state hashes this installation built — into
`THEURIAN_DATA_DIR` (`BuildProvenance.default`,
`application/project_service.py`). Every command in this run redirected that
directory, so the record went to a scratch tree and the real one still holds
nothing for this project. That is correct behaviour and it is a note for the
resident phase: the first two commands there are `migrate apply` and
`index build` under the real data directory, because
`verify_state_provenance` refuses to serve state this installation never built
([ADR-0004](../adr/0004-sqlite-is-a-derived-artifact.md), SEC-7).

## Near-misses, recorded because an unreported one becomes a habit

- **The orchestrator's first driver run aborted on its own tree-check.** It
  compared the working tree against a baseline it had never taken, decided the
  tree was dirty and stopped. The driver's bug, not the product's; nothing was
  written, and the run was restarted after the baseline was taken.
- **Two manual CLI invocations leaned on the shell's persistent working
  directory** instead of setting it in the same command. Both landed in the
  intended directory, so these are near-misses and not two more incidents — but
  they are the class [`CLAUDE.md`](../../CLAUDE.md) records three incidents of
  under *Running the CLI on a development machine*, each to someone who had read
  the warning. That is why the rule is written as "never depend on an earlier
  `cd`" rather than as "be careful", and why a near-miss under it is worth a
  line here.

## Entrance decisions

- **No `setup`, no `uninstall`, no `daemon` command was run at any point** — not
  even `--dry-run`, because nothing in this run needed a daemon. Port 7419 was
  verified free before and after, and `launchctl list | grep theurian` was
  silent before and after. Nothing was registered with the login session's
  service manager.
- **Retrieval was deliberately not exercised.** There is no daemon on this
  machine, so `knowledge.search` over this corpus is the next phase rather than
  part of this one. Its entrance condition is the port rule in
  [`CLAUDE.md`](../../CLAUDE.md): a dev-time daemon takes `--port 7420`, and the
  "is it gone" check is two lines, because the survivor that check exists to
  catch comes from a run that forgot `--port` and therefore sits on 7419.

## Review round one

| Reviewer | CRITICAL | HIGH | MEDIUM | LOW |
| :-- | --: | --: | --: | --: |
| code | 0 | 1 | 3 | 4 |
| security | 0 | 0 | 3 | 5 |
| adversarial | 0 | 1 | 2 | 3 |

Both HIGHs were false claims rather than broken behaviour, which is this
project's most frequent defect shape:

- **A commit message stated a chunk count measured against the wrong corpus.**
  It gave the local 1113 for the committed slice, which derives 669. Fixed by
  rewording the commit, and the pair is now in the table above, where the
  difference between the two corpora is the point rather than a footnote.
- **The boundary lived only in machine-local state.** The commit message
  described the fence as though it travelled with the repository. The message
  was fixed and the gap it had papered over is #265 — the residuals listed
  above, and the guard test that answers the repository-side half of them.

The adversarial review reproduced and confirmed all five of the claims this
branch makes, and then ran nine mutations over the committed corpus — governance
flips, a removed migration, an edited body. **All nine survived.** No test in
the suite read a byte of the corpus, so committed data had entered the
repository with no owner. That finding is what `52bf3f5` answers, and it was
closed the way it was found: each of the 13 rules was verified by mutating a
scratch clone and watching that rule go RED, not by reading the patch.

The security review found no private content on any of the six channels it
examined. That is the finding worth stating plainly for a run whose whole
premise is that 56 items must not leave the machine.
