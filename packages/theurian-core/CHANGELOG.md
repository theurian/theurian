# Changelog — Theurian Core

All notable changes to the **`theurian` Python package** are documented here.
The Claude Code plugin has its own changelog at
[`plugins/claude-code/CHANGELOG.md`](../../plugins/claude-code/CHANGELOG.md);
the two version and release independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, a MINOR bump may change the protocol. Post-1.0, only a MAJOR may.

## [Unreleased]

### Added

- **`knowledge.search` takes an optional `asOf`** (RFC 3339, explicit UTC
  offset required), pinning results to FR-R1's validity-window axis at that
  moment ([#63](https://github.com/theurian/theurian/issues/63) phase 2 — the
  milestone advances, the issue stays open for the four remaining axes).
  Additive: omitting `asOf` filters on nothing more than before this parameter
  existed, on both answer paths. A permanent default filter was considered and
  rejected, because it would make the published `freshness.isWithinValidity`
  field constant-`true` on a healthy index and give the ranked path a
  stale-index statistics residual with no way to turn off (see T-17a in
  [the threat model](../../docs/security/threat-model.md)). `asOf` is a
  refinement rather than a withholding: everything one call excludes is
  returned to the same caller by the identical query with the parameter
  omitted, so it opens none of the disclosure guarantees this project holds
  for a document a caller may not read. `knowledge.get` deliberately does not
  take it — see its docstring for why "not found" would be a worse answer than
  `isWithinValidity: false`. An unparseable `asOf` is a clean tool error.

## [0.1.0.dev1] - 2026-08-09

**If you installed `theurian` before today, upgrade.** `0.1.0.dev0` was the only
published version, and everything below was fixed in the repository without
reaching anyone who had run the command the shipped surfaces name
([#83](https://github.com/theurian/theurian/issues/83)). On `0.1.0.dev0`, an
install without the `daemon` extra gives you `theurian daemon start` raising
`ModuleNotFoundError` as a rendered traceback; `theurian daemon status` — which
the Claude Code plugin's `SessionStart` hook runs on **every session** —
printing a Rich traceback into that session; and a `theurian setup` that runs to
`DEGRADED` and leaves an env file, an OS service unit and an MCP connection
entry behind, pointing at a service that fails on every start.

```sh
uv tool install --python 3.13 --force 'theurian[daemon]==0.1.0.dev1'
# or: pipx install --python 3.13 --force 'theurian[daemon]==0.1.0.dev1'
```

The extra is the part that is easy to lose. `uv tool upgrade` and `pipx upgrade`
both re-resolve the spec they recorded, so an installation that recorded no
extras stays bare across an upgrade — it will carry these fixes and still not be
able to serve. What changes is that it now says so, by name and with the command,
instead of crashing. `--force` is what makes the line above repair an existing
installation rather than report success and change nothing; measured for pipx
against 1.16.6, and harmless for uv, which re-resolves in place regardless.

Nothing here changes the wire contract: `protocolVersion` stays `theurian/v1`,
no MCP tool's request or response shape moves, and no canonical state or index
needs rebuilding.

**This is a Core release only.** The Claude Code plugin versions and releases
independently (ADR-0001) and is unchanged at `0.1.0`; fixes to its shell hooks
that landed alongside these are named below where they bear on a claim, but they
are not delivered by this wheel.

### Changed

- **`--help` is plain text now: no panels, no colour.** Fixing the entry below
  meant turning Typer's Rich formatting off at the root app
  (`rich_markup_mode=None`), and that setting is what draws the rounded boxes
  around `Options` and styles the option names. Every `theurian … --help` falls
  back to Click's formatter and prints the docstring as written. Measured on a
  real terminal, `theurian setup --help` before and after:

  | | ANSI escape sequences | box-drawing characters |
  | :-- | --: | --: |
  | before | 134 | 187 |
  | after | 0 | 0 |

  This is cosmetic and it is the whole cost of the fix. It is named here because
  it is the change a user notices without being told, and a release page is
  where they will come looking for the reason.

### Fixed

- **`theurian setup --help` printed an install command with the `daemon` extra
  deleted from it** ([#99](https://github.com/theurian/theurian/pull/99)). The
  docstring says `uv tool install 'theurian[daemon]'`; what reached the terminal
  was `uv tool install 'theurian'`, one line above the sentence explaining that
  the extra is what gives `theurian daemon start` a server to run. So the
  surface that exists to keep a user out of the bare install told them to make
  one, and contradicted itself in the same paragraph.

  Typer parses help strings as Rich markup, and Rich reads `[daemon]` as a style
  tag. Measured against `rich` 15.0.0:

  ```
  'uv tool install 'theurian[daemon]''  ->  "uv tool install 'theurian'"
  'run it from [/usr/bin]'              ->  MarkupError: closing tag '[/usr/bin]'
                                            doesn't match any open tag
  ```

  The second row is the same defect one step worse — a path in square brackets
  takes `--help` from wrong to absent — and it is why the fix is not a smarter
  docstring.

  **The fix leaves markup rather than escaping the bracket**, and that
  distinction is the finding. Escaping to `theurian\[daemon]` was tried and
  reverted: `TYPER_USE_RICH=0` is a documented setting that formats through
  Click instead, and there the escape survives to the user as a literal
  backslash — `uv tool install 'theurian\[daemon]'`, which is not an installable
  requirement. **No single docstring is correct in both modes while markup is
  on**, so the escape did not remove the defect, it moved it between modes and
  broke a path that had been correct. `rich_markup_mode=None` takes the same
  Click path under both settings, which makes the source text the printed text
  everywhere.

  This was introduced by the entry below, not inherited: before it, the
  docstring named the bare command and printed the bare command, wrongly but
  consistently.

- **`uv tool install theurian` installed a Theurian whose daemon could not
  start.** `uvicorn` and the MCP SDK live in the `daemon` extra, so the next step
  of the documented flow ended in
  `ModuleNotFoundError: No module named 'uvicorn'` and a rendered traceback
  ([#78](https://github.com/theurian/theurian/issues/78)). The packaging split is
  kept — a CI image running only `theurian migrate` should not carry a web server
  (ADR-0014) — and the three faces of the defect are fixed instead:

  - `theurian daemon start` reports which extra is missing and the command that
    installs it, on the `--json` channel as well. The guard reads
    `ModuleNotFoundError.name` and re-raises anything else, so a broken Theurian
    is never answered with "reinstall the package that holds the broken file".
  - `theurian daemon status` no longer needs the extra at all. It imported the
    lock file's *name* from the module that starts the web server, so a bare
    install printed a traceback into every Claude Code session — that command is
    what the `SessionStart` hook runs. `LOCK_FILENAME` now lives in
    `theurian/daemon/instance.py`, beside the lock it names.
  - `core-present` reported `satisfied` for an install that cannot run a daemon,
    and setup went on to write an env file, an OS service unit and an MCP
    connection entry, ending `degraded` with a registered service that fails on
    every start. The step now reports `conflicting`, which aborts the run before
    anything is created.

- **Every surface that tells a user how to install Core now names the extra**:
  `uv tool install 'theurian[daemon]'` / `pipx install 'theurian[daemon]'`. That
  includes the two that execute — the `core-missing` compatibility remedy and the
  `core-present` step detail — which read one constant,
  `theurian.domain.extras.DAEMON_INSTALLERS`, so the two answers cannot drift.
  The quoting is required, not stylistic: unquoted, the bracket is a glob under
  zsh.

  `[daemon]` rather than `[all]`, measured: `sqlite_vec` and `opentelemetry` are
  imported nowhere in `src/`, so `[all]` differs by 12 distributions and by no
  behaviour.

  **Two surfaces still name the bare command, and this release page is one of
  them.** The entry as written under `[Unreleased]` named four, deferred to four
  then-open pull requests, and said re-deriving the count belonged to whichever
  landed last. All four have landed, so it is re-derived here. `git grep -l` for
  `uv tool install theurian` / `pipx install theurian` returns 15 files at this
  tag; `README.md` and `docs/security/threat-model.md` are no longer among the
  surfaces that *instruct* it, and two are:

  - `.github/workflows/release-core.yml`, which writes
    `Install: uv tool install theurian==<version>` into every GitHub release
    body — including this one. **If you arrived from that line, add the extra**:
    `uv tool install 'theurian[daemon]'`.
  - `docs/contributing/release.md`, whose post-release verification step
    installs bare. That one is read by maintainers, not by users.

  Both are release tooling rather than install advice, and both are tracked with
  the rest of the release gate in
  [#39](https://github.com/theurian/theurian/issues/39). Naming them on the page
  that carries the defect is the mitigation available without changing the
  workflow inside the release it publishes.

- **The `core-too-old` remedy named a subcommand that does not exist.** It read
  "Upgrade Core with `theurian upgrade`, or run /theurian:upgrade"; `upgrade` has
  never been registered in `cli/main.py`, so `theurian upgrade` exits 2 with
  `No such command` and `/theurian:upgrade` failed the same way
  ([#42](https://github.com/theurian/theurian/issues/42)). It now reads
  `uv tool upgrade theurian` / `pipx upgrade theurian`, from a single
  `CORE_UPGRADERS` constant.

  `CORE_MISSING` is the only outcome `cli.main.compat_check` cannot produce,
  because it always passes a parsed version. `core-too-old`, `core-too-new` and
  `protocol-mismatch` all reach production and all exit 3, so this is a remedy a
  user can really be handed. The plugin's `SessionStart` hook prints the whole
  verdict to stderr on every session that hits it, so the remedy that could not
  be followed was the one most likely to be read.

  **That last sentence is true only from
  [#90](https://github.com/theurian/theurian/pull/90), which is a plugin fix and
  is therefore not in this wheel.** `lib.sh` opened `set -euo pipefail` and
  `session-start.sh` sources it, so a bare assignment whose command exits 3
  aborted the hook before it printed anything. Measured against both revisions:
  `set -euo pipefail` gives exit 3 and no output at all, `set -uo pipefail` gives
  the warning, the verdict and exit 0. The dependency is named because the
  remedy corrected here is only ever *seen* on a plugin that carries that fix;
  it reaches users through a plugin release, not through this one.

  **It is not reachable in the shipped configuration, and this release does not
  make it reachable.** The plugin's declared floor is `0.1.0-dev.0`, which is the
  lowest `0.1.0` any Core can report, so no released Core is below it:
  `0.1.0.dev0` renders as `0.1.0-dev.0` and this release renders as
  `0.1.0-dev.1`. Measured against the declaration the plugin ships:

  ```console
  $ theurian compat check --plugin-version 0.1.0 --core-minimum 0.1.0-dev.0 \
      --core-maximum-exclusive 0.2.0 --protocol-version theurian/v1 --json
  { "outcome": "compatible", "coreVersion": "0.1.0-dev.1", … }
  $ echo $?
  0
  ```

  What makes `core-too-old` reachable is raising `coreCompatibility.minimum` in
  the plugin's `compatibility.yaml` — a plugin release, not a Core one. The
  entry as written under `[Unreleased]` said "the first Core release that moves
  either number makes it reachable"; moving Core's version *up* moves it further
  above the floor, and cutting this release is what falsified the sentence. The
  remedy is corrected before anyone can be handed it, which is the order this
  wants.

  **Delegation is the decision, not an omission.** A real `theurian upgrade`
  would make Theurian the thing that fetches its own wheel, and with it T-16's
  install-time verification; that is a larger commitment than a remedy string and
  is deliberately not taken. The remedy names no extra because both installers
  re-resolve the spec they recorded, so an install carrying `[daemon]` keeps it
  across an upgrade and a bare one stays bare. Measured against the real
  distribution, which settles it without any upgrade:
  `uv tool install 'theurian==0.1.0.dev0'` records no extras and has no `mcp`,
  `uvicorn`, `watchfiles` or `starlette`; `'theurian[daemon]==0.1.0.dev0'`
  records `extras = ["daemon"]` and has all four. Naming the extra would imply
  that upgrading repairs a bare install; it does not, and that user needs
  `uv tool install 'theurian[daemon]'`.

  The upgrade path was measured with `black`, because when this was written
  `theurian` had exactly one release and so could not be upgraded at all. This
  release is the second, and it is the first that can be. **uv installs the
  newest version its spec allows**, so
  `uv tool install 'black[d]==24.1.0'` followed by `uv tool upgrade black`
  reports `Nothing to upgrade`; dropping the `==` pin from `uv-receipt.toml` is
  what stands in for time passing. An earlier version of this entry omitted that
  step, which made the procedure it recorded a no-op — the observation was real,
  the published recipe was not. With the step: both receipts go
  `24.1.0 -> 26.5.1`, `aiohttp` absent throughout for `black` and present
  throughout for `black[d]`, each receipt keeping what it recorded. pipx 1.16.6
  drops the pin itself (`upgrading black from spec 'black[d]'`) and ran with
  `--backend pip`, its default backend requiring uv>=0.9.17 against this
  machine's 0.7.2.

## [0.1.0.dev0] - 2026-08-07

A development release, published to claim the `theurian` name on PyPI. Until
this, the name was unregistered while `theurian setup` and the plugin's
SessionStart hook both told a user whose machine has no Core to run
`uv tool install theurian` — a command that could not work, and that would have
installed somebody else's package had the name been taken first.

Everything below happened before Theurian had released anything at all:
Milestones 0 through 5, then the two groups that follow, which landed after
Milestone 5 and before the tag. The breaking changes named in it broke nothing
that had shipped, and the `#### Known limitations` sections are where this says
what it does not do.

**The first two groups reached this section after the release.** They were
written under `[Unreleased]`, and the move that
[`release.md` §2](../../docs/contributing/release.md) asks for was not made
before `core-v0.1.0.dev0` was pushed — so the published sdist carries a changelog
filing its own contents as unreleased, and the release body generated from this
section does not mention them. The release workflow's changelog guard did not
catch it: it checks that a section for the version exists and is not empty, not
that `[Unreleased]` is.

**The wheel carries no changelog**, so this reached the sdist and the release
page rather than an install: `[tool.hatch.build.targets.sdist]` lists
`CHANGELOG.md` and the wheel target ships `src/theurian` only. `uv tool install`
and `pip install` take the wheel, which means the reader most exposed to the
error is the one reading the release notes to decide whether to upgrade.

### Milestone 6 — the index lifecycle

#### Added

- **An API to purge a build: `IndexStore.derive_purged`** (ADR-0024). Given a
  published build and a set of withdrawn revisions, it copies the build with
  `sqlite3.Connection.backup`, deletes those revisions from the copy, restamps
  `index_metadata` for the new build, verifies the result, and produces a new
  file fit to publish. The published file is never written to, so a search
  reading it is unaffected.

  **Not yet wired to withdrawal.** The automatic trigger — a purge fired whenever
  a revision is retired, superseded or rejected — is ADR-0024 decision 5, and it
  has no caller in this release: `derive_purged` is invoked by tests only. It is
  the next slice and closes [#15](https://github.com/theurian/theurian/issues/15);
  this release *advances* #15 by landing the mechanism and the schema the trigger
  will use.

  Measured on a real 400-document index with embeddings: 2,732 chunks to 1,229,
  1,503 rows removed in 847 ms, and the purged build answers **identically** to
  an index that never held the withdrawn documents — chunk ids and BM25 scores to
  ten decimals, on both the word index and the trigram index — while a stale
  control differs on every query.

  `shutil.copyfile` and `VACUUM INTO` are both rejected, and ADR-0024 records
  why: the first drops the `-wal` sidecar, and the second rests on rowid
  stability SQLite documents as *not* guaranteed for tables without an INTEGER
  PRIMARY KEY, which `chunks` is — while both FTS5 tables are external-content
  keyed on `chunks.rowid`.

- **Withdrawal is transitive over derived content.** A row built from a withdrawn
  chunk holds that chunk's content: a purge can delete a passage, and cannot
  delete a sentence out of a summary of it. `chunk_derivation` records the
  provenance and the purge walks it transitively, so a summary, a summary of that
  summary, and a node with mixed provenance all go with the withdrawal. A derived
  row whose provenance cannot be resolved is deleted rather than kept.

- **`theurian index gc` reclaims superseded index builds.** Named by ADR-0007,
  ADR-0016 and ADR-0017 since Milestone 1 and never implemented until now. It
  deletes builds the published pointer does not name, and refuses to touch four
  things: the published build, any build whose id sorts above it (a build that
  has not published yet), anything under a `.building` suffix (a writer still in
  progress, or a crash's leftovers, which it reports as `strandedBuilding`), and
  everything, when the pointer cannot be read. `--dry-run` reports the plan
  without deleting.

#### Changed

- **Publishing an index build no longer reclaims the build it replaced**
  (ADR-0024 point 6). The old file stays on disk until `theurian index gc` runs.
  Reaping at publish is what made ADR-0022's "the previous build is not deleted"
  false, and measured against a concurrent reader it cost 2,627 errors against 40
  answered searches in 1.5 seconds. Builds now accumulate — ten publishes leave
  ten files — which is the cost that makes `index gc` load-bearing rather than a
  tidy-up.

- **A search holds one read connection for the whole request** (ADR-0024 point
  7), and index files are opened `mode=ro`. Together these let a request survive
  a `theurian index gc` that unlinks its build mid-request: the held descriptor
  keeps the file readable on POSIX, and `mode=ro` stops a read of a reaped path
  from conjuring an empty database where the build was. Measured, one request of
  four index reads with the unlink after the first: 4 of 4 answered with the
  session held, against 1 of 4 without.

#### Changed — BREAKING

- **`INDEX_SCHEMA_VERSION` 2 → 3**, adding `chunks.derived` and
  `chunk_derivation`. **Every existing index reports `index-schema-mismatch` and
  falls back to the substring scan until `theurian index build` runs.** That is
  the designed response to an index schema change and not a regression: the index
  is derived and disposable, and ADR-0022 point 3 exists so that a schema change
  costs an index rebuild and nothing else — no canonical `SCHEMA_VERSION` bump,
  no state hash change, no canonical database invalidated. `theurian index
  status` reports the mismatch and names the command.

  The two new columns are for RAPTOR (ADR-0008), which does not exist yet. They
  land ahead of it because withdrawal has to be transitive from the first build
  that has anything to be transitive over — designing the purge after summary
  nodes ship means designing it twice, the second time under pressure from a
  feature already in use.


- **`IndexStore`'s three search methods return `RetrieverPage`, not
  `tuple[Ranked, ...]`** ([#16](https://github.com/theurian/theurian/issues/16)).
  The page carries the rows and an `exhausted` flag, which the depth loop reads
  instead of inferring exhaustion from a row count. No MCP schema impact:
  `src/theurian/mcp/` never calls a `search_*` method, so the outward breaking
  cost is zero — the break is to the port, its one adapter, and the six
  test-side implementations `rg "def search_lexical" packages/theurian-core`
  finds: `_ScriptedIndex`, `_CountingIndex`, `_NeverFinished`, `_TwoOpinions`,
  `_TwoRankings`, and `_CountedStore`, across five files. Four of them answer
  through the new `fakes.pages` helper; `_CountedStore` delegates to the real
  store and `_NeverFinished` builds a page the helper deliberately cannot.

  One expression used to read three different `limit` semantics off one number —
  a ceiling in `search_lexical`, a floor in `search_substring`, absent in
  `search_dense` — and the port's docstrings stood in for a type. An adapter that
  capped its output above `limit` without that cap being exhaustive satisfied
  every word of them and cost the caller rows it never learned it lost.
  `exhausted` may be `True` only when the implementation has verified there is
  nothing further; the SQLite adapter fetches `limit + 1` and reports whether the
  extra row arrived, then drops it, so `limit` stays a true ceiling.

- **`SqliteIndexStore._scan_cache` is deleted**, as its own docstring
  instructed. It was a security mitigation rather than an optimisation: it made
  the second call to the scan below the trigram floor cost no second pass over
  the corpus, where the second call itself was a step function of how many rows
  the canonical store had withheld. There is no second call now — that branch has
  read and scored everything by the time it returns, so it reports itself
  exhausted on its first. Measured against a real 400-document index with a
  two-character CJK query: one port call at 0, 49, 50, 51 and 99 withheld rows,
  where 51 and 99 cost two before.

  The cache also required a fresh `SqliteIndexStore` per search, because a
  pooled one would have leaked one caller's withheld-row count into another
  caller's latency. That requirement went with it, replaced by something
  smaller and checkable: the store holds no per-instance state at all.

- **`tests/integration/test_scan_cache.py` becomes `test_scan_exhaustion.py`.**
  Its cross-request test was deleted rather than moved — with no cache, two
  requests cost two scans whatever happens, so it would have sat in the suite
  green and guarding nothing.

#### Known limitations

- **The timing residual on the truncating retrievers is unchanged, and #16 does
  not close it.** A first pass in which too many rows were withheld still has to
  fetch deeper to keep fifty visible rows, which follows from the definition of
  the depth loop rather than from any defect in it: measured, `search_lexical`
  and the trigram lookup still make two calls at 51 withheld rows and one at 50.
  Only an index that no longer holds withdrawn rows removes it
  ([#15](https://github.com/theurian/theurian/issues/15)).

### Changed after Milestone 5

- `theurian setup` and `theurian doctor` now explain the `artifact-integrity`
  step's `not-applicable` as a property of Theurian rather than of the world.
  The old wording denied that any record existed to check against, and promised
  verification at the first tagged release. Both held only until a `core-v*` tag
  was cut, and `core-v0.1.0.dev0` cut it: from that moment the first of them
  would have told every user not to bother checking a file published on that
  very release page, which is the only mitigation available while the control is
  unimplemented. The step still reports `not-applicable` and still verifies
  nothing; it now says that Theurian does not verify the artifact it is running
  from, which holds on both sides of a tag. Checking a download against the
  checksums published with it remains a manual step
  ([#39](https://github.com/theurian/theurian/issues/39), T-16).

  *The superseded sentences are deliberately not reproduced here.* A version's
  section is published verbatim as the GitHub release body, a short distance
  above a line stating that every artifact below is covered by `SHA256SUMS` —
  which is the defect this entry records, and a changelog is no place to
  reintroduce it. That did not happen for this entry, because it was still under
  `[Unreleased]` when the tag was pushed; the published release body for
  `core-v0.1.0.dev0` does not contain it.

### Fixed after Milestone 5

- **A `minimum` did not bound anything, and neither did a `maximumExclusive`.**
  `theurian compat check` translates Core's PEP 440 version into SemVer before
  comparing it against a plugin's declared range, and the translation put
  versions in the wrong order. Declaring `minimum: 0.1.0-dev.0` — the floor this
  repository's own plugin ships, and the one the documentation recommends for an
  unreleased Core — accepted `0.1.0.dev1`, refused `0.1.0a1`, `0.1.0a2` and
  `0.1.0b1`, then accepted `0.1.0rc1` and `0.1.0` again. A floor with a hole in
  the middle is not a stricter floor; it is a floor that means nothing.

  Two rules disagree between the ecosystems, and both were live. PEP 440 sorts
  `.devN` below every pre-release phase, while SemVer §11.4.2 compares the phase
  words as ASCII and puts `dev` between `beta` and `rc`. PEP 440 sorts
  `0.2.0a1.dev1` below `0.2.0a1`, while SemVer §11.4.4 ranks the longer
  identifier list higher. Over the release train the tests now enumerate — 40
  versions of one release, so 780 ordered pairs — 99 came out backwards.

  `maximumExclusive` is the same comparison read from the other end and failed
  the same way: a ceiling of `0.1.0-alpha.1` refused `0.1.0.dev0`, which is
  *below* it, and accepted `0.1.0a0`, which is above.

  Both bounds are now ordered by Core's release train — `dev` < `alpha` <
  `beta` < `rc` < final, with a development build below the pre-release it
  precedes — applied to the declaration's bounds and to Core's own version
  alike. Declarations keep their existing spelling and verdicts keep printing
  it; neither `compatibility.yaml` nor the published schema changes.

  **Not breaking, measured rather than argued.** Every `minimum`/
  `maximumExclusive` pair this repository declares was resolved against 200
  versions spanning five releases, under the old comparison and the new one. No
  pair changes verdict against the Core that ships (`0.1.0.dev0`), and the only
  pair whose meaning changes at all is `0.1.0-dev.0`/`0.2.0`, where all 24
  changes run `core-too-old` → `compatible`: versions that were wrongly refused
  are now accepted, and nothing that was accepted is now refused.

  What *can* move the restrictive way is a bound that names a pre-release phase.
  A ceiling of `0.1.0-dev.0` stops accepting every `0.1.0` alpha — correctly,
  because those are newer than it. If you maintain a client whose `minimum` or
  `maximumExclusive` carries a `-dev`, `-alpha`, `-beta` or `-rc` segment,
  re-read it against the ordering above. A bound with no pre-release segment is
  unaffected.
- **A PEP 440 development segment carrying no number was dropped whole.** PEP
  440 makes that number optional and defaults it to 0, but the parser decided
  the segment was *present* by asking whether its number was — so `0.2.0.dev`
  parsed as `0.2.0`, a development build read as the finished release it
  precedes. That is the failure this translation exists to prevent, inverted:
  rather than being told Core was missing, a client would have been told it had
  shipped.

---

### Milestone 5 — hybrid retrieval

#### Added

- **Reciprocal Rank Fusion** over three retrievers (FR-R2): a word index, a
  trigram substring index, and — opt-in — a dense one. Fusion uses *ranks*,
  never scores. BM25 and cosine similarity are not comparable quantities, and
  neither are two BM25 scores computed over different token spaces; normalising
  any of them onto one scale needs assumptions about their distributions that do
  not survive a change of corpus, tokenizer, or embedding model (ADR-0021).
- **A trigram index beside the word index**, which is what makes languages
  without word spacing searchable at all. `unicode61` splits on whitespace and
  punctuation only, so `署名付きトークンを持つ` is one token and `トークン`
  matched nothing. Both indexes feed the fusion as separate retrievers; the
  trigram one is not a replacement, because trigrams are worse at the exact
  identifiers engineering queries are mostly made of — a trigram search for
  `cat` matches `concatenate`. (ADR-0023)
- **Document chunking** on structure first and length second — headings, then
  paragraphs, then sentences, then words, then a hard character cut as the
  backstop that always terminates.
- **A retrieval index in its own SQLite file**: FTS5 for terms, an exact vector
  scan for the rest. Separate from the canonical store on purpose — the
  canonical `SCHEMA_VERSION` is an input to the state hash (ADR-0017), so
  co-locating them would make every index change invalidate every canonical
  state.
- **A default embedding provider** that is deterministic, local, and needs no
  API key: hashed character trigrams. It is **not a semantic model, does not
  claim to be, and is no longer on by default** — see the breaking change below.
- **Diversification and token budgeting** (FR-R4). At most N chunks per item, so
  one long document cannot take every slot; packing strictly in rank order,
  never a knapsack fill that would trade relevance for a number the caller
  cannot see.
- **`theurian index build` and `theurian index status`.** Status reports three
  hashes — what the knowledge *is*, what the database *holds*, and what the
  index was *built from* — because all three can differ, and comparing only the
  last two calls an index fresh exactly when someone most needs to be told
  otherwise.
- **`theurian project register --project-id <id>`**, which is how a directory
  name collision is broken. See the breaking change below.
- **`StateDatabaseUnreadableError`**, the one error every read of the canonical
  state database answers with when the file cannot be interpreted. It carries
  the failing exception's **type** and never its message, because every
  converter the store reaches for quotes the value it would not accept:
  `datetime.fromisoformat` quotes the string, each enum quotes the member it
  could not find, and every domain value object renders its argument with `!r`.
  See Security, below, for what that used to cost.

  It lives in `theurian.infrastructure.sqlite.connection`, not beside the store
  that raises it most — opening a connection interprets the file too, and
  `write_transaction` opens one without going through the store at all. The real
  exception travels on `__cause__` for whoever debugs it; every CLI path that
  would have rendered that cause to a terminal is converted (see Changed).

#### Changed

- **BREAKING — `knowledge.search` response shape.** The flat `note` string is
  replaced by a structured `retrieval` object carrying `mode`, `indexed`,
  `stale`, `staleAgainst`, `indexesUnapproved`, `indexBuildId`,
  `embeddingModel`, `fallbackReason`, `snapshotId`, `usedTokens`,
  `droppedForBudget`, and `note`. Each hit gains `foundBy` (which retrievers
  surfaced it) and `fusedScore`. A ranking nobody can explain is a ranking
  nobody can debug.

  `snapshotId` is FR-R5's provenance realised once per response rather than
  once per hit: every hit in one answer is resolved through one canonical
  connection, so a per-hit copy would repeat one string. It is byte-identical
  to `knowledge.status.stateHash`, so a caller holding one can compare it
  against the other without a second call, and it is query-independent by
  construction — which is what makes it safe to publish at all (see Security).

  **The shape is now the same on both answer paths.** The ranked path and the
  unranked fallback used to publish different key sets — `stale`,
  `staleAgainst`, `indexBuildId`, and `embeddingModel` only on the ranked one,
  `fallbackReason` only on the fallback — which let a client branch on key
  *presence* rather than on a value. Every key now appears on both responses;
  one that does not apply to a given path carries `null` rather than being
  omitted.

  `retrieval.mode` takes five values: `substring` for the unranked fallback,
  then `lexical`, `dense`, `hybrid`, or `none` on the ranked path, according to
  which retrievers actually contributed a result that survived the canonical
  re-check. `none` is new: an empty result set used to report `lexical`,
  indistinguishable from "the word index answered and found nothing" — exactly
  what a v1 index missing its trigram table, or an embedder whose vectors do
  not match the corpus, produces.

  When the answer came from the unranked fallback, `retrieval.fallbackReason`
  says which of seven things happened — `no-index`, `index-pointer-invalid`,
  `index-file-missing`, `index-schema-mismatch`, `index-unreadable`,
  `index-project-mismatch`, or `unapproved-not-indexed`. All seven used to
  produce the same sentence, "no retrieval index has been built for this
  project", which is true of exactly one of them; the rest told a user to run a
  command they had already run and said nothing about the one that would have
  helped.

  **BREAKING — `withheldSuperseded` is removed** from the `retrieval` object.
  It was a per-query count of matches the caller was not allowed to see. See
  Security, below: it turned out to be a side channel, not a courtesy.

- **BREAKING — the index schema is version 2; existing indexes must be rebuilt.**
  The trigram table is new, and `INDEX_SCHEMA_VERSION` went 1 → 2 with it. Run
  `theurian index build`. Nothing canonical is affected: the index is derived and
  disposable, and this is the lifecycle separation ADR-0022 exists for, exercised
  for the first time.

  A version-1 index is detected rather than silently losing its trigram half.
  `SqliteIndexStore.is_searchable` compares the stored schema version against
  the one this build expects before any query runs; a mismatch is reported as
  `retrieval.fallbackReason: "index-schema-mismatch"` and `indexed: false`,
  with an unranked substring scan still answering the question underneath it.
  This landed in Milestone 5, not Milestone 6 as ADR-0022 and ADR-0023 said —
  the check shipped later in the same milestone, after those ADRs were
  written, and the ADRs were not updated to match until now.

- **BREAKING — `theurian index build` refuses to publish an index with zero
  chunks when the canonical state holds knowledge.** Publishing it used to put
  a correct-looking empty index in place — every later search answers
  `count: 0` with `indexed: true`, and `theurian index status` reports nothing
  to do, which is the exact shape a project-id mismatch takes. The build now
  exits 1 and names every project id the canonical store actually holds
  knowledge under.

- **BREAKING — `theurian index status` gains `projectId`, `indexProjectId`, and
  `orphaned`; `active-index.json` now records `projectId`.** Every chunk is
  stamped with the project id that built it, so an index built for a different
  id answers every query with nothing while still reporting `indexed: true`.
  A pointer written before this field existed cannot be checked, so it is
  treated as `orphaned` too — deliberately, because the command exists to avoid
  asserting a freshness it has not established, and a pointer that predates the
  check has none to assert. `knowledge.search` reports the same class of
  mismatch as `retrieval.fallbackReason: "index-project-mismatch"`.

- **BREAKING — dense retrieval is off by default.** `SearchRequest.use_dense` and
  the MCP parameter `useDense` both default to `false`, so a healthy default
  search now reports `retrieval.mode: "lexical"` rather than `"hybrid"`.

  This is measured, not cautious. Against a real corpus, **91% of unrelated
  natural-language questions cleared the bundled embedder's similarity floor**,
  while the lowest genuinely related query scored below the unrelated median. The
  distributions overlap; no threshold separates them, because what the embedder
  measures is English surface-form overlap and not topical relevance. The floor
  in the code was calibrated against random strings, which turned out to be the
  easy case and the wrong population to calibrate on.

  The retriever is kept and made opt-in rather than deleted, so the code path
  stays exercised and works the day a real model is configured through the same
  port (ADR-0009). `theurian index build` still writes embeddings unless
  `--no-embeddings` is passed, so opting in needs no rebuild.

- **BREAKING — a project id already registered to another root is refused.**
  `ProjectRegistry.register` used to overwrite. Ids default to the directory
  name, directory names repeat, and registering `team-two/api` therefore
  re-pointed the id `api` at the newer root — after which an agent working in
  `team-one` that asked for `api` was served `team-two`'s knowledge, with no
  error and nothing in the answer naming the repository (SEC-13). Registration
  that used to succeed now exits 1 and names the conflict. Break it with
  `theurian project register --project-id <id>`. Choosing a suffix automatically
  would have been worse: an already-configured agent keeps naming `api` and would
  silently follow the id to whichever project kept it.

  **BREAKING, and its mirror.** The same refusal now also applies the other way
  round: a root that already has an id being registered under a *second* one.
  An id is stamped into every canonical row and index chunk at the moment it is
  written, and `migrate apply` is idempotent, so using `--project-id` to rename
  an already-registered project produced a second, empty project rather than a
  renamed one — every search under the new id answered `count: 0` with
  `indexed: true`, and `theurian index status` reported nothing to do. To
  rename a project: `theurian project unregister <old-id>`, register the new
  id, delete `.theurian/state/`, then `theurian migrate apply` and `theurian
  index build`.

- **Project id resolution order changed** to: explicit `--project-id`, then the
  registry keyed by *root path*, then the directory name. Without the middle
  step, a project registered under a disambiguated id would still be addressed by
  the colliding default on its own command line — the CLI writing to one project
  while every agent reads the other.

- **BREAKING — the project registry validates every entry as it reads it, and a
  question keyed by root path refuses while any entry is unreadable.**
  `ProjectRegistry.load` used to return whatever `json.loads` produced, so an
  entry that was not a registration reached its caller unchecked. Three
  consequences, each reproduced against the previous behaviour before being
  written here:

  | Registry contents | Was | Is |
  | :-- | :-- | :-- |
  | an entry naming no `rootPath` | `project list` reported it as an ordinary project, `count: 2` | skipped by `load`; `count: 1`, and the id appears under `unreadable` |
  | an entry naming no `rootPath` | `id_for_root` matched it against **every** directory | every root-keyed question refuses |
  | a JSON array, or truncated JSON | `AttributeError: 'list' object has no attribute 'items'`, as a Rich traceback | reported as `{error, remedy}` with exit 1 |

  The middle row is the one that mattered. `Path("").resolve()` is the *calling
  process's* current working directory, so `entry.get("rootPath", "")` made an
  entry with no root claim whichever directory the command happened to run from:
  `id_for_root` returned that id for a completely unrelated repository, and
  `resolve_context` then addressed that repository's knowledge under it
  (SEC-13). An empty `rootPath` is now rejected as firmly as a missing key.

  A malformed entry invalidates only itself. It still holds its id, so `register`
  refuses to reclaim it — with its own remedy, rather than a collision message
  that assumes a readable `rootPath` to report — and `unregister` can remove it.
  Both read the raw file rather than `load`'s validated subset, or registering
  one id would erase a different id's broken entry as a side effect of an
  unrelated write.

  **`ProjectRegistry.ids_for_root` raises rather than answering "not
  registered", and that is the breaking half.** An entry is skipped exactly
  because it names no root, so "is that entry this directory's registration?"
  has no answer: the field that would settle it is the field that is missing.
  Answering `()` sends `resolve_context` to `derive_project_id`, which addresses
  the directory by its *name* — the id that may already belong to the project it
  collided with, which is the misrouting the collision refusal above exists to
  prevent.

  The blast radius is asymmetric on purpose. Questions keyed by an **id** keep
  working: `theurian project list`, `theurian project unregister` (the remedy),
  the setup registry scan, and every MCP tool — so one hand-edited line does not
  stop a daemon that serves every other project on the machine. Questions keyed
  by a **root path** refuse: resolving the project for the working directory,
  and `theurian project register`, which asks the same question to enforce "one
  root, one id" and therefore stops machine-wide until the entry is removed.
  That last part is accepted rather than special-cased — the plain `register`
  form resolves its context from the working directory and would refuse there
  regardless, so an exception would reach only the `--project-id` form, in
  exchange for a safety argument harder to check than the refusal it removes.

- **`theurian project list` gains an always-present `unreadable` field**, naming
  the ids whose entries `load` skipped, plus a `remedy` when it is non-empty. It
  is emitted even when empty, because a consumer that has to branch on whether a
  key is present will eventually forget to. Reported here rather than only where
  it breaks something: this is the command every other surface sends a user to,
  and the id it now prints is the argument `theurian project unregister` needs.
  A registry that cannot be partitioned into entries at all — truncated JSON, a
  JSON array, arbitrary bytes — is now reported by this command rather than
  escaping as a traceback, which mattered most here because this was the one
  place the "delete it and re-register" remedy never reached.

- **BREAKING — the `project.list` MCP tool gains two required response fields,
  `unreadable` and `remedy`, and its output is published as a schema for the
  first time.** Adding a required property is a breaking change under
  `schemas/README.md`'s compatibility rules, and it is named as one here even
  though a client that ignores unknown keys will not notice: the rule is about
  what the contract *permits*, and a response missing either key is now invalid.

  Both are always present — `remedy` carries `null` when `unreadable` is empty —
  because emitting a key only when it applies makes "nothing is unreadable"
  indistinguishable from "this daemon predates the field". `count` sizes
  `projects` alone and excludes the unreadable ids, since an entry naming no root
  path can be queried by nothing.

  `schemas/mcp/project-list-response.schema.json` is the contract, published
  after three milestones in which `project.list` was the tool an agent calls to
  find out what this daemon can answer for and the only one whose shape was not
  written down.

  **`projects` and `unreadable` are two reads of one file, not a partition of
  one snapshot.** `load()` and `unreadable_ids()` open the registry
  independently, so a registration landing between them can leave an id in both
  lists or in neither. Stated as it is rather than as the cleaner guarantee it
  resembles: a caller must not compute the size of the registry file by adding
  the two, and must not treat membership of one as proof of absence from the
  other. The fix is a single-snapshot read in `ProjectRegistry`, which is not
  written and not scheduled.

  **This wire change turned nothing red, and that is the finding.** Every test
  covering the MCP tools, the schemas and the wire contract — 186 of them when
  the fields were added, 189 now — was green before and after, because no
  assertion anywhere in the repository pins `project.list`'s response shape. The
  tool's own test reads `count` and one `projectId` and never looks at the key
  set. It is the same gap that let
  `knowledge.search`'s response shape change with the whole suite passing; that
  one was closed with a conformance test this milestone and this one was still
  open until the schema was written. The guarantee now rests on the schema —
  and therefore on the schema being *checked against a real response*, which is
  the rule stated in `schemas/README.md` and the work item it names.

  **The schema deliberately puts no pattern on `projects[].projectId`, and that
  was settled by measurement rather than by transcription.** Nothing validates
  registry *keys*: `load()` reads only `rootPath`, so a hand-edited
  `{"Not An Id": {"rootPath": "/valid"}}` loads and `project.list` publishes
  `projectId: "Not An Id"`. Ids that Theurian creates are lowercase kebab-case
  and `ProjectId` enforces that, but a registry key is not a `ProjectId`.
  Constraining the published field to the slug pattern would have produced a
  schema that rejects the product's own output — which is exactly the defect
  this milestone shipped in round one and corrected in
  `knowledge/retrieval-result.schema.json` (see Fixed, below).

- **`protocolVersion` stays `theurian/v1`, and that is a decision rather than an
  omission.** Milestone 5 makes several breaking wire changes — the
  `knowledge.search` response reshape, the removal of `withheldSuperseded`, and
  the two required fields above — and none of them bumps it. No published
  version of Core has ever lacked them, so no plugin can be pinned to a v1 that
  lacks these fields, and bumping would publish a `theurian/v2` whose v1 was
  never shipped. The version's unit
  is a released protocol; what protects an integrator is this changelog, which
  names each break. `protocolVersion` bumps on the first breaking change *after*
  the version that first carries `theurian/v1` — Milestone 5's set is the
  content of v1, not a departure from it. Recorded here because a reader who
  finds three breaking wire changes and an unchanged protocol version is
  entitled to know which of the two is the mistake.

- **Every project-scoped MCP tool now tells "not registered" from "registered
  and unreadable".** The two need opposite remedies and used to share one
  message, which sent half its readers into a loop: `theurian project register`,
  be told the id is already in use, read the same advice again. An id whose entry
  cannot be parsed is now named as such and pointed at `theurian project
  unregister <id>` first. The `Registered:` list is assembled from the entries
  that loaded, so the skipped ids are named beside it rather than merged into it
  — merged, they would inherit the `register` remedy that cannot work; omitted, a
  user comparing the answer against their own registry file finds a project
  missing from both the list and the explanation.

- **BREAKING — `theurian project status --json` reports `registered: null`, a
  third value for what was a boolean**, and gains an always-present `unreadable`
  list. `null` means "cannot be told": the registry holds an entry that names no
  root path, and this directory is inside a Git repository, so `false` would be
  the same guess `ids_for_root` refuses to make. A plain "not inside a Git
  repository" keeps its honest `false` rather than being dragged into an
  ambiguity it cannot be about. The command still exits 0 and carries the remedy
  in the payload, because a confused user reaches for `project status` first and
  a report with no way out is what it used to give them. A consumer treating
  `registered` as a boolean now sees `null` where it previously saw `false`.

- **`theurian setup` reports a registry it cannot read as `conflicting` rather
  than as a missing registration.** The project-registration step asked the
  registry for entries and scanned them by root path, which silently skipped an
  unreadable one and reported `MISSING` beside a remedy that cannot work —
  registering is refused while an entry that might hold this root's id is
  unreadable. It now asks the same question `ids_for_root` answers, and reports
  the impossibility on the first screen a person reads when something is broken.

- **BREAKING — `maxTokens` now pays for the whole response, not only the
  results, so the same budget returns fewer of them.** `projectId`, the echoed
  `query`, `count` and the entire `retrieval` block — the `note` above all,
  which is a paragraph of prose — travel with every answer and were charged to
  nobody. Measured at 138 to 171 tokens of fixed overhead on a ten-document
  project: a caller asking for 2,000 was sent 2,030 and told the answer had
  cost 1,860. The envelope is now reserved from the budget before any result is
  packed, so on that same project the default `maxTokens=2000` returns **9
  results with `droppedForBudget: 1`** where it used to return 10 and overshoot.
  `usedTokens` still means what it meant — what `results` cost — rather than
  quietly becoming a different number; charging honestly for the envelope did
  not have to wait for a wire-contract change. A budget smaller than the
  envelope still returns one result rather than none.

- **BREAKING — `SearchRequest` has no `limit`, and `substring_answer` and
  `hybrid_answer` require the caller's `ActiveState`.** Both are Milestone 5
  APIs changed within Milestone 5, so no released version is affected; they are
  named here because this changelog is what anyone integrating against Core is
  reading.

  `SearchRequest(query=..., limit=10)` no longer type-checks. `limit` and the
  token budget both moved to `ResultRequest`, which is applied on the far side
  of the canonical gate. Applying `limit` to *candidates* let a withheld
  document consume a result slot — see Security, below — and a field that
  cannot be set cannot be applied in the wrong order. What is left on
  `SearchRequest` bounds the work rather than the answer: `CANDIDATE_DEPTH` per
  retriever, and `per_item` per document.

  `substring_answer(database, *, project_id=...)` becomes
  `substring_answer(database, *, state=..., project_id=...)`, and `hybrid_answer`
  takes the same new parameter in place of reading the active state itself.
  Both publish `retrieval.snapshotId`, and resolving it here rather than
  receiving it is the read that can disagree with the one that chose the
  database: a pointer replaced by `migrate apply` mid-request makes the field
  name a state the results did not come from, and a pointer deleted mid-request
  makes it `null`. The caller passes down the state it already resolved to
  choose the database, so `snapshotId` names that state and is never empty.

- **BREAKING — retrieval takes a visibility, because the canonical gate moved
  inside the ranking.** Again Milestone 5 APIs changed within Milestone 5, so no
  released version is affected. No `knowledge.search` response field changes:
  this is the application layer and the `IndexStore` port only.

  | Was | Is |
  | :-- | :-- |
  | `RetrievalService.search(request)` | `search(request, visible)`, taking a `Visibility` — with **no default** |
  | `ResultGate.admit(request)`, with candidates and passages carried on `ResultRequest` | `admit(request, source)`, where `source` is `Callable[[Visibility], SearchOutcome]`; `ResultRequest` has neither field |
  | `SearchOutcome.embedding_model` | `RetrievalService.embedding_model(use_dense=...)` |
  | `IndexStore.search_dense(vector, *, project_id, limit, include_unapproved)` | the same without `limit`; it returns its whole ranking |
  | `IndexStore.token_sizes(chunk_ids, *, project_id)` | removed |
  | `theurian.mcp.results.may_surface` | `theurian.domain.enums.may_surface` |
  | — | `CanonicalReadSession.get_item` is now part of the protocol |

  A caller of `search` has to name whose view it is ranking for. "Everything is
  visible" is the assumption this milestone chased through five separate fields
  (see Security, below), and a default parameter is how it comes back — so there
  is none, and a test that wants an ungated ranking says so at the call site.
  `admit` takes a *source* of candidates rather than a finished list for the same
  reason: there is nowhere to put a list that was ranked without a visibility.

  The rest follow from the same move. `embeddingModel` left the search outcome
  because it was the same value for every query against one index, and a value
  answerable without a query cannot be made to vary with one. `search_dense` lost
  its `limit` because an exact vector scan scores every embedding whatever it is
  asked for — the parameter bounded the output while appearing to bound the work,
  which would have misled the caller that now re-asks at greater depth.
  `token_sizes` went with the budget that used it: pricing a retrieved chunk
  charges for text the canonical store may still withdraw. `get_item` is on the
  read protocol because clearing a ranked row needs the *item*'s status now, not
  the status the index recorded at build time.

- **BREAKING — a damaged canonical state database is reported as
  `{"error", "remedy"}`, and `theurian migrate status` and `theurian migrate
  apply` exit 4 rather than 1.** These paths used to leave the command through a
  Rich traceback: exit 1, an empty stdout where `--json` promises a document
  (CP-2), and — because a traceback renders `__cause__` one line below the
  exception — the corrupted cell printed to the operator anyway, undoing the
  withholding described under Security.

  | Command, over | Was | Is |
  | :-- | :-- | :-- |
  | `migrate status`, `migrate apply` — a damaged cell | exit 1, a traceback quoting the cell | exit 4, `{"error", "remedy"}` |
  | `migrate apply` — a real immutability violation, **healthy** database | exit 1, a traceback | exit 4, `{"error", "remedy"}` |
  | `index build` — zero chunks, canonical store unreadable | exit 1, a traceback quoting the cell | exit 1, `{"error", "remedy"}` |

  A caller branching on exit 1 has to branch on 4; a caller parsing `--json` is
  handed a payload where it used to be handed nothing. Failures print to stderr,
  as every other failure in this CLI does. Measured across `migrate status` and
  `migrate apply` over `migration_history.migration_id`,
  `migration_history.checksum` and `schema_metadata.schema_version` — six
  (command, column) positions, all six now exit 4 carrying both keys and none of
  the cell. The immutability row keeps its own remedy, "Fix the migration set,
  then retry", because it is the caller's migration set that is wrong and not
  the file.

  The `except` is over `TheurianError` rather than over the types known to
  arrive today, because a guard's promise reaches only as far as the exception is
  caught. It wraps `write_transaction` itself rather than the body of the `with`:
  opening a connection interprets the file, so `schema_metadata.schema_version`
  raises before the body runs. The remedy is chosen per family — a file this
  build cannot interpret, another process holding the write lock, a migration set
  the store refused — and the one that deletes something is the one that is never
  the default.

  `index build`'s row is a second read session, opened after the build to ask the
  canonical store whether indexing nothing was correct, over rows the build never
  reads. It is reached only when the build indexed zero chunks, which no fixture
  produced; measured on a project whose only knowledge is `draft`, with
  `projects.registered_at` or `projects.root_path` overwritten. The partially
  built index file goes with the refusal, matching every other branch that
  declines to publish — a file left behind is one a later `index status` finds
  and believes.

- `knowledge.search` gains a `maxTokens` parameter (FR-R4).
- Searching a project with no index falls back to the previous substring scan
  and says so, rather than returning nothing — which would read as "we have no
  such decision" rather than "ask me again in a moment".
- The substring fallback now honours `maxTokens` as well. FR-R4 is a promise
  about every answer, and this path ignored it: fifty results carrying their
  provenance and trust labels are several thousand tokens handed to a caller who
  asked for five hundred.
- The relevance floor on the lexical retriever was removed, because it was dead
  code. A review reported that BM25 returns "exactly 0.0000" when the only
  matching terms appear in every row, and proposed excluding those hits.
  Measured, SQLite returns `-1.375e-06` for that case — the `0.0000` was a
  printed rounding — so the threshold excluded nothing while claiming to be a
  floor. Separating "matched only common words" from "matched weakly" needs a
  per-term IDF test, which is recorded as an outstanding gap rather than papered
  over.

#### Fixed

- Japanese documents were indexed as a single chunk. Japanese puts no space
  after a full stop, so the sentence pattern matched nothing and the word
  fallback had no spaces to split on either. Found by running it, not by
  reading it.
- `theurian index build` reported "no built knowledge state" on a project that
  had one: `_require_project` returns the state *database* as its second value,
  and the new code treated it as the repository root.
- `search_dense` leaked a raw `sqlite3.OperationalError` — "no such table:
  embeddings" — when an index's `embeddings` table was missing, which defeated
  `hybrid_answer`'s guarantee to never answer from a broken index for
  `useDense=true`. Wrapped in `IndexUnreadableError`, like the other two
  retrievers, and reported as an ordinary fallback.
- A query containing a NUL byte or a lone unpaired surrogate reached the agent
  as a tool failure instead of a search result: SQLite rejects the first as an
  unterminated string, and the Python driver raises `UnicodeEncodeError` on the
  second *before* SQLite is even called, so no `except sqlite3.OperationalError`
  could catch either. Both are now dropped as untransportable terms — the
  treatment punctuation already got — so `auth token\x00` still searches for
  `auth`. Separately, a 20,000,000-character query was accepted and echoed back
  verbatim, a 20 MB response to one search; `knowledge.search` now bounds and
  normalises the query once, at the MCP boundary, before both searching and
  echoing it.
- A corrupt `active-index.json` — truncated, a JSON array, an object with no
  `indexBuildId` — was treated identically to no index ever having been built,
  sending a user who had already run `theurian index build` back through the
  same command a second time. The two are now distinguished
  (`fallbackReason: "index-pointer-invalid"` vs. `"no-index"`).
- A query with more than 64 distinct terms kept the first 64 in the order the
  caller typed them, which for a natural-language question discards the noun
  it was about — "how do we handle the ..." front-loads its least selective
  words. The limit now keeps the 64 *longest* terms, a tokenizer-free proxy for
  selectivity.
- **`theurian index build` made search strictly worse than having no index at
  all, for the most common noun length in Japanese.** A trigram index has no
  gram for a term shorter than three characters, so 認証, 決済, 監査 and 契約 —
  two characters each — returned results before a build and `count: 0,
  indexed: true` after one, with no `fallbackReason` to explain it. An agent
  reads that as "this team has made no such decision". A query whose terms are
  *all* below the floor is now answered by a scoped `LIKE` scan over the same
  rows, under the same project and status filters, and a single character is
  admitted when it is a letter of a script written without word boundaries
  (`鍵` is a noun; `e` is a letter the word index already answers as a word).
  The scan is ranked, by how many characters of the query each chunk accounts
  for — under a `LIMIT` the ordering key is the selection key, so ordering by
  `chunk_id` would have made the *oldest* matches the only reachable ones. A
  lone punctuation character is deliberately declined rather than answered:
  `。` is in every Japanese paragraph, and matching it means reading the whole
  corpus to return "the fifty the sort favoured". (ADR-0023)
- **The published schema disagreed with the product it describes, twice, in the
  same way — found by comparing the schema against the domain's own
  validators, not by testing output.** `knowledge/retrieval-result.schema.json`
  required at least one `sourceAnchors` entry. INV-8 permits a revision to
  carry no source anchor when it declares itself `authored-in-theurian`, so
  every result for knowledge written inside Theurian violated the schema
  Theurian publishes, on both answer paths. No `protocolVersion` bump: no
  response ever carried a different shape, and no schema-validating client
  could have been working against such a document. An integrator who wrote a
  non-empty check from the schema rather than from the product has work to do.
  Separately, `itemId` had no `maxLength` in the schema while the domain has
  rejected one over 200 characters since it was introduced; the schema now
  states the same bound.
- **The disclosure fix below silently disabled the FR-K5 check for six
  commands, and this branch is where that was found.** Recorded even though it
  never shipped, because it is the most instructive thing that happened here:
  `StateDatabaseUnreadableError` descends from `TheurianError`, and
  `_verify_history` swallowed every `TheurianError` raised while reading the
  *previously active* state database — on the correct grounds that a state
  written at another schema version is not evidence about this one (ADR-0017).
  The new error fell into the same `except`.

  So a **tampered applied migration**, which is what FR-K5 and ADR-0005 exist to
  catch, was reported as a clean history with exit 0 where a healthy database
  refuses it with exit 4. The check is reached from `_require_project`, so the
  silence covered `migrate status`, `migrate apply`, `migrate validate`,
  `index build`, `index status` and `ingest`. `SchemaVersionMismatchError` keeps
  the early return, because that is the case the comment describes; a database
  this build cannot read is neither evidence of tampering nor evidence of its
  absence, and now exits 4 naming the check that could not be performed and what
  rebuilding costs — the rebuilt history records the files as they are now, so an
  edit made before that point stops being detectable.
- **A damaged `content_sha256` cell was diagnosed as a rewritten revision.**
  `append_revision` compares the stored hash against the caller's, and two states
  produce the same mismatch: an author rewriting a revision, which is INV-1, and
  a cell that is not a digest at all. The second was answered with `Revisions are
  immutable; write a new revision instead` — a remedy that appends a duplicate
  into a database that is already damaged. The comparison stays a comparison of
  opaque strings; only the question of *why* the two differ is an interpretation,
  and it is now asked only on the branch that has already decided they do.

#### Security

- **`theurian doctor --report` published values Theurian had only read, inside a
  payload that said `redacted: true`** (SEC-6, O-3). Redaction was a substitution
  of the paths the local `SetupContext` holds, which by construction cannot
  reach a string that came from another file, another process, or an exception —
  and five setup steps put exactly such strings into the `detail` a report
  carries. The one that matters is the MCP entry: `mcp-connection` renders the
  installed entry so the user can decide whether the run may proceed around it,
  and an entry configured with a literal `Authorization: Bearer <token>` instead
  of `${THEURIAN_MCP_TOKEN}` is both the state that makes that step conflict and
  the state that gives someone a reason to publish the report. Measured in the
  shipped default configuration, with no flags: the token was in the output.

  Theurian never writes such an entry (SEC-5) — it is what it finds. The same
  route ran through a service unit's `EnvironmentVariables` / `Environment=`
  lines, another daemon's `dataDir` from `/health`, the ids of other
  repositories in the project registry, and the message of any exception a probe
  raised.

  Redaction now has a second half that runs before substitution rather than
  after: `SetupContext.for_publication`, set by the composition root when
  `--report` is passed, makes each of those steps withhold what it did not
  author. What is published is which fields differ, a count of unreadable
  registry entries, `<another data directory>`, and an exception's type. Plain
  `theurian doctor` is unchanged and still prints everything, because it is read
  by the person who has to act on it. Asserted on the values themselves in
  `tests/integration/test_setup_report_withholding.py` — a test that only checked
  the path anchors passed before this fix and after it.

  **A field *name* is a value too, and that took a second pass to see.** The
  first fix published the names of the differing fields on the reasoning that a
  name is schema. It is not, unless Theurian defined it: the names came from a
  union with the installed file, so what got published was whatever string sat in
  key position in somebody else's. A systemd continuation line is the *value* of
  the directive above it, and parsed alone its left-hand side became a directive
  name — a bearer token, published as a field name inside the sentence promising
  the values were withheld. `DifferingFields` now intersects with the names
  Theurian's own renderer produces and counts the rest, which holds without
  depending on a parser being right about a third party's file format.

  **And "the names Theurian's own renderer produces" had to stop being asked of
  the renderer.** The vocabulary was computed by re-parsing `render()`'s output,
  on the argument that a name Theurian writes cannot be a value it read. True of
  `plistlib` and of a dict literal; not true of an f-string over a line-oriented
  format. `SystemdUserManager.render` interpolates the data directory and the
  executable, so a line break in either added a directive of the caller's
  choosing to the "authored" set — and a name present only in the *installed*
  unit was then published. Two faces of one root cause: the write side rendered
  that injected directive into the user's unit file at all three interpolation
  points. The vocabulary is now a stated constant, and a line break in an
  interpolated value is refused rather than escaped, because systemd has no
  escape that makes one part of a value. Not reachable in the shipped default
  configuration — `THEURIAN_DATA_DIR` had to contain a newline.

  **The two halves cannot be used apart.** `_redacted` refuses a payload from a
  context that did not ask for publication, because stamping `redacted: true` on
  a run that did not withhold reproduces the original defect exactly — and
  `tests/integration/test_setup_report_withholding.py` sweeps *every* step in
  `STEPS` with a seeded sentinel rather than testing the routes that were known
  to be broken, after a one-line addition to an unrelated step reopened the class
  with the whole suite green.

  **`SECURITY.md` and `docs/security/local-mcp.md` said this could not happen**
  ("no credential value … enters that payload for it to remove"), which is what
  told a reader the output was safe to paste. Both now describe the two
  mechanisms and what review is still the reader's. `docs/adr/0011`,
  `CONTRIBUTING.md`, `docs/architecture/requirements-analysis.md` and the bug
  report template carried the same claim. The plugin's `/theurian:doctor` command
  said plain `theurian doctor --json` "redacts by default" — it never has, and
  now says so.

- **A corrupted cell in the canonical state database was published to MCP
  callers verbatim** (SEC-13). `SqliteCanonicalStore` handed the bytes it could
  not interpret straight to the tool result: overwriting `created_at`,
  `valid_from`, `content_type` or `status` came back as `Error executing tool
  knowledge.get: Invalid isoformat string: '<the cell>'`, eight of eight across
  `knowledge.get` and `knowledge.search`, against a control on an intact database
  that raised nothing. Swept one cell at a time across the whole schema, the
  damage reached an MCP client from **60 (column, tool) positions** on
  `67a792c`, and `theurian index build` published the same cell as an unhandled
  `ValueError`.

  That store holds *every* revision — `draft` and `rejected` alongside `approved`
  (ADR-0006) — so a cell it fails to interpret carries bytes the caller may not
  read, and the retrieval gate never sees them: an exception raised while a row
  is being interpreted goes around the gate entirely. This was on file as
  [#18](https://github.com/theurian/theurian/issues/18), *a corrupt state
  database reaches the caller with no remedy*, and stood under Known limitations
  below through this milestone. That reading was wrong. The missing remedy was
  the smaller half of it; the defect is an information disclosure.

  Every line that turns a stored cell into a value now runs inside a guard that
  answers with `StateDatabaseUnreadableError` (see Added), whose detail is the
  failing exception's type and never its message. The block is entered by the two
  functions that are the only way this class reads, so a read added later cannot
  forget the convention. **The type name is the whole detail for `sqlite3`'s own
  errors too**, which is a narrower rule than the index store's: damaging one
  `sqlite_master.sql` cell gives `malformed database schema
  (payroll_secret_band_l7) - incomplete input` on SQLite 3.51.2 — a name read
  straight out of the file — so passing `str(exc)` through keeps a case analysis
  over SQLite's error catalogue that a later release can invalidate.

  Two cells travelled as *data* rather than inside an exception message, and both
  are converted on the way out. `migration_history.checksum` was returned as a
  plain string and rendered into `MigrationChecksumMismatchError`, so
  `theurian migrate status --json` answered `Migration 01K1… was applied with
  checksum <the cell> but the file on disk hashes to …`. And INV-3's refusal on a
  tampered body named `content_sha256.short` together with the hash of the stored
  body — a 12-character confirmation oracle over a revision the caller may not be
  entitled to read. The invariant check is unchanged; what it publishes is not.

  **The remedy discards the retrieval index, and does not rebuild it.** It reads
  "delete `.theurian/state/` and run `theurian migrate apply`", and the index
  lives under `.theurian/state/` as well, so following it literally takes the
  index with the canonical state while `migrate apply` restores only the latter.
  Run for real: `migrate apply` reports `databaseCreated: true` and the knowledge
  is back, so nothing authored is lost; `theurian index status` then reports
  `built: false` with a remedy of its own — run `theurian index build` — and
  `knowledge.search` answers from the unranked substring scan with
  `retrieval.fallbackReason: "no-index"`. The degradation announces itself at
  both surfaces, but a project that was ranked stays unranked until `theurian
  index build` runs.

- **`knowledge.get` was not gated on status** (SEC-13). Closing every path
  through `knowledge.search` achieved nothing while this stood open: a caller
  read an approved item, took a `targetItemId` off one of its relations, and
  fetched the withheld body in one further call. No flag, no guessing. A
  rejected revision is where the secret that caused the rejection still lives.
  Both the item and its relations are now limited to surfaceable statuses by the
  same authority search uses.
- The refusal for a withheld item is byte-identical to the one for an item that
  does not exist, so the error cannot be used to confirm that a retired item
  exists at a given id.
- A stale index no longer resurrects retired knowledge or superseded revisions.
  Status and current-revision are both re-checked against the canonical store on
  *both* the default and the `includeUnapproved` path, so a stale index returns
  fewer results rather than wrong ones.
- **The token budget was priced on candidates before the canonical re-check
  withdrew them, which turned `usedTokens` into a truth oracle for content the
  caller had just been refused** (SEC-13, T-15). A retired or superseded
  revision that matched the query still spent its share of the budget before
  being dropped from `results`, so `count: 0, results: [], usedTokens: 46`
  said "something matched and may not be read" — and because the trigram
  retriever matches any substring of three characters or more, that statement
  supports sequential extraction, not just existence detection: guess a
  character, ask, keep it if the number moves. Measured on this code, 257
  ordinary `knowledge.search` calls — no `includeUnapproved`, no privileges —
  recovered a 20-character credential from a document whose superseding
  revision had redacted it. The only precondition was an index older than the
  redaction, which is the normal state between `migrate apply` and `index
  build`: the window opened by *performing* the redaction was the window the
  plaintext could be read back through.

  `retrieval.mode` had the same defect in a different field: it was derived
  from the rankings fusion produced, which still held candidates the canonical
  store went on to withhold, rather than from the results the caller actually
  received. Both are now computed from `results` after resolution — never from
  a candidate that did not survive it. `withheldSuperseded` is removed for the
  same reason (see Changed, above). Verified by comparing a query that matches
  only withheld content against a query that matches nothing at all, field by
  field: no key differs.

- **The bullet above is one face of five, and recording it as one defect with
  five faces is the finding** (SEC-13, T-15, T-17). Four more fields carried the
  same oracle. They are listed together because each fix closed the one in front
  of it and left a sibling: every one of them moved a *quantity* past the
  canonical gate while the gate itself stayed after the ranking.

  | Face | What was computed before the gate |
  | :-- | :-- |
  | `usedTokens` | the token budget, priced on candidates |
  | `count` | `limit`, truncating candidates |
  | `fusedScore` | the RRF ranks |
  | `CANDIDATE_DEPTH` | the rows *fetched* from each retriever |
  | the excerpt | `diversify` choosing which chunk of a document to publish |

  Two of them supported full extraction with no flags and no privileges: 203
  ordinary `knowledge.search` calls recovered a 16-character credential through
  `count`, and 442 recovered one through the candidate depth at the **default**
  token budget with no parameter set. 203 is the figure to plan against, because
  an attacker picks whichever implementation is cheaper. `fusedScore` moved every
  published score by a rank —
  `[0.032787, 0.032258, 0.031746, 0.031250]` became
  `[0.032258, 0.031746, 0.031250, 0.030769]` — and the excerpt moved *which
  paragraph* of a visible document was published, in 9.1% of 20,000 random rank
  arrangements.

  **What closed it was not a sixth patch.** The gate moved inside the ranking:
  `RetrievalService.search` takes a `Visibility` and ranks only rows it has
  cleared, so fusion, diversification, `limit` and the budget all see exactly the
  rows an index that never held the withheld documents would have offered. The
  fix this entry used to describe — re-fusing the survivors after filtering — is
  gone with the function that did it, because ranks are never computed over
  withheld rows now and there is nothing left to repair. Retrievers are read deeper
  instead: 100 rows, then twice as many, until 50 *visible* rows exist or the
  retriever returns fewer rows than it was asked for. On a Japanese corpus this
  mattered most, and needed no setup: `unicode61` cannot segment CJK, so the
  trigram retriever's fifty slots are the whole candidate list.

  Verified by comparing one query against two corpora — one whose index holds a
  document the caller may not read, one that never held it — rather than two
  queries against one corpus, which is what the three earlier rounds did and is
  only ever as wide as the fields those two queries happen to move. Every
  published value is equal, at every `limit` up to 50 and at the default budget,
  with and without `useDense`, **in English and in Japanese**.

  **Both corpora are load-bearing, in opposite directions.** The depth loop is
  read once for the word index and once for the trigram retriever, and removing
  it from one is a different mutation from removing it from the other. Taking it
  off the trigram retriever, English notices only at `maxTokens=32,000` while
  Japanese also notices at `limit=50` at the default budget, through
  `droppedForBudget` — the field the attack used. Taking it off the word index,
  English fails four cases and **Japanese fails none**: the Japanese word index
  returns one row against this crowd, so its loop has nothing to skip and the
  displacement is unobservable. Deleting either corpus leaves one of the two
  loops unguarded with a green suite.

  Timing remains a stated residual, and closing the content channel **widened**
  it before a mitigation narrowed it again: with a first pass of exactly 50, a
  single withheld row forced a second query, and a single call classified the two
  cases correctly 91.6% of the time. Reading 100 rows first moves that threshold
  from "one withheld row matched" to "fifty did" and brings it back to +3.0% /
  63.0%, against 62.1% for a pipeline with no depth loop at all. It is a
  mitigation, not a proof, and what guards it is a count of retriever reads per
  request rather than a clock — no wall-clock assertion runs in CI, so a change
  that made each read more expensive in proportion to what was withheld would go
  unnoticed.

  Those separations are the trigram-lookup branch, and the branch below the
  trigram floor was two orders worse before it was fixed in this same milestone:
  a `LIMIT` bounded nothing there, so every doubling re-scanned the corpus —
  +86% on a plain CJK noun, +101% on the worst legal query, and a six-pass worst
  case of 3.06 s against the 43 ms recorded for the lookup. **That branch now
  scans the corpus once whatever the canonical store withheld** — `scan_statement`
  dropped its `LIMIT` and the loop's exit test became `!=`, so a retriever that
  never truncates is not asked twice; 3.06 s → 0.64 s, and 0.65 s with the whole
  corpus retired.

  **Once *scanned*, not once *called*, and the two were reported as one until
  review round five.** A ranking that totals exactly `FIRST_PASS_DEPTH` rows is
  indistinguishable from a truncated one, so the loop asks again: the scan port
  is called once at 50 withheld rows and twice at 51. What holds that second call
  to no further pass over the corpus is `SqliteIndexStore._scan_cache`, a
  memoisation for this one gap — deleted when `IndexStore` states its own
  exhaustion, filed as [#16](https://github.com/theurian/theurian/issues/16).
  The earlier "one pass from 0 to 5,999 withheld rows" is not wrong, only
  narrower than the sentence it supported: a 6,000-row ranking never lands on the
  coincidence.

  The trigram lookup keeps the loop outright: 1 pass at 50 withheld rows and 2 at
  51, costing +12.8 ms (+15% of a request), down from +64.3 ms. **What is left on
  both branches is closed by an argument rather than a further mitigation**, and
  it is the duration face of the BM25 entry below rather than a finding of its
  own: a ranking the visibility has not yet judged still contains the withheld
  rows, so any work proportional to its length moves with how many there are.
  That is the extra fetch needed to secure `CANDIDATE_DEPTH` visible rows from a
  retriever that is not exhausted, and — with the pass count held at one — the
  canonical read `CanonicalVisibility.cleared` makes for every row of the
  ranking. Both follow from the definition of the loop and not from a defect in
  it. No exhaustion signal removes them and no cache removes them; only an index
  that no longer holds withdrawn rows does
  ([#15](https://github.com/theurian/theurian/issues/15), Milestone 6). See T-17
  in the threat model for the measurements, the five things that would falsify
  that argument, and their evidence grade.

  **The canonical-read half was found in review round six, and two claims are
  retracted with it.** T-17 said this branch's timing channel was "closed
  outright" and that walking the whole ranking is what keeps the canonical read
  count off the withheld count. Dropping the `LIMIT` closed the *pass count*; the
  read count is `len(ranked)`, so 10 visible rows cost 10 canonical reads with
  nothing withheld and 210 with 200 withheld, in one pass, at about 15 µs each and
  bounded by nothing on a branch whose statement has no `LIMIT`. Round four
  replaced a bounded 6× multiplier with an unbounded linear term rather than
  removing the channel, and the published residual of +0.35 ms / 63.0% is the
  lookup's pass-count edge rather than a bound over T-17 as a whole.

#### Known limitations

- The default embedder is lexical in vector form, and off by default for the
  reason given above. Semantic retrieval needs a real model, which plugs in
  through the `EmbeddingProvider` port without touching anything else
  (ADR-0003, ADR-0009). Because it stays opt-in, FR-R2 is only partly
  discharged: the fusion is real and both retrievers exist, but a healthy
  default search never runs the dense one.
- The relevance floor removed above (Changed) leaves a query whose terms all
  appear in every document still ranking. Separating "matched weakly" from
  "matched only common words" needs a per-term IDF test, not a score
  threshold (ADR-0021, Milestone 6).
- Two candidates ranked `(i, j)` and `(j, i)` by two retrievers score exactly
  equal under Reciprocal Rank Fusion, so the `chunk_id` tie-break — revision
  creation order — decides between them instead of relevance. Measured at
  9%–16% of adjacent top-10 pairs, depending on corpus, over a 30-document,
  15-query test corpus. A relevance-based tie-break needs a per-retriever
  weighting decision (ADR-0021, Milestone 6).
- A query mixing a short term (one or two characters) with a longer one (three
  or more) still drops the short term from the trigram retriever entirely,
  because the trigram expression is then non-empty and the floor that rescues
  an all-short query never fires. `認証 トークン` searches only for `トークン` on
  this retriever (ADR-0023, Milestone 6).
- The scan below the trigram floor orders by occurrences weighted by term
  length, which is a proxy for relevance and not IDF: a chunk that repeats one
  term many times can outrank a chunk that covers two. The same per-term IDF
  work closes this and the mixed-length residual above (ADR-0023, Milestone 6).
- **A query with more than eight terms below the trigram floor searches only its
  first eight**, and *first* means first typed. Both the match and the order come
  from that one slice, so a term past it is honestly absent rather than present
  but unrankable. Eight is a cost bound: each term is a `LIKE` and an occurrence
  count over every row, so the worst legal query runs 0.81s at four terms, 1.67s
  at eight and 4.25s unbounded, on 20,000 chunks — the multi-second, GIL-holding
  query SEC-8 exists to keep out of a daemon shared by every project. The
  longest-first ordering that decides *which* terms survive elsewhere is a no-op
  here, because every term on this path is one or two characters; picking the
  selective one out of `認証 決済 監査 契約` needs corpus statistics this
  retriever does not have (ADR-0023, Milestone 6).
- Case matching is asymmetric across the trigram floor: SQLite's `LIKE` folds
  ASCII only while the trigram tokenizer folds all of Unicode, so a two-letter
  Greek query is case-sensitive and the same word with one letter more is not.
  Japanese and Chinese are caseless, so the scripts the floor exists for are
  unaffected; a Greek or Cyrillic corpus would need the `icu` tokenizer
  (ADR-0023).
- `mode: "substring"` names two different things on the wire: the unranked
  canonical scan reported at the top level of `retrieval`, and the trigram
  retriever named inside a ranked hit's `foundBy`. Left as one published value
  rather than renamed, and documented at both call sites in `mcp/search.py`.
- A hit's `foundBy` names which retrievers ranked it, not at which position each
  did. The positions exist on the fused candidate and are what `fusedScore` is
  computed from; publishing them would be a schema change (ADR-0021).
- **On a stale index, BM25's collection statistics count documents the canonical
  store will withhold, and the visible order moves with them.** This entry twice
  called part of the effect harmless, and both bounds were false. It first said
  the statistics "do not vary with what a query matched"; FTS5's `bm25` weights
  each phrase by `idf = log((N - nHit + 0.5) / (nHit + 0.5))`, where `nHit`
  counts the rows matching *that phrase* — so a withheld row containing one of
  the query's terms reweights the **visible** rows against each other. It then
  said the remaining statistics were harmless because query-independent. They are
  query-independent, and that is not the same claim.

  Two channels, and they differ in what a caller can do with them:

  - **`idf`, via `nHit`** — query-dependent, so a probe steers it, and bounded by
    `tf`: a term that does not also occur in visible content leaves every visible
    row at `tf = 0` and reads back nothing. It is an oracle for whether a
    withheld document contains a term already in the visible vocabulary, not the
    character-at-a-time extraction T-17 describes — still disclosure on a corpus
    of incident notes or rejected-review rationales.
  - **`avgdl`, and more weakly `N`** — query-independent, so no probe can make
    them answer a question, but **unconditional**: no shared vocabulary is
    needed. BM25's length norm `k1 * (1 - b + b * D / avgdl)` is a function of
    each row's own `D`, so it is not a common factor across rows and moving
    `avgdl` does not preserve an order. Measured on withheld rows sharing no term
    with the query, with each phrase's `nHit` asserted identical in both indexes:
    1,218 configurations reorder two visible rows.

  So the published `fusedScore`, the hit order and — because `knowledge.search`
  always asks for one chunk per document — which paragraph is returned as
  `excerpt` can all move for *any* withheld content while the index is stale,
  whatever that content says. The gate removes rows from the result; it does not
  remove them from the statistics the survivors are scored against. What an
  attacker can read back out is the `idf` channel and nothing more.

  **Accepted for this milestone rather than deferred without a decision**, and
  re-taken in review round five against the corrected text rather than carried
  forward on the old one. The argument is in T-17a: purging withheld chunks from
  the derived index on read means a read path writing to a derived artifact, and
  Milestone 6 settles blue/green index builds, so building it now means building
  it twice. The window is the stale window and the root fix is eliminating it,
  not correcting statistics inside it. `theurian index build` closes it today.
  Tracked at HIGH against Milestone 6 as
  [#15](https://github.com/theurian/theurian/issues/15), disclosed in
  `SECURITY.md` and the README. Both channels are pinned by tests that assert the
  leak is *present*, so its scope cannot grow unnoticed and closing the window in
  Milestone 6 turns them red — `test_a_withheld_document_can_still_reorder_the_visible_ones`
  for the `idf` channel, and
  `test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones`
  for the `avgdl` one.
- A search running while `theurian index build` publishes is not protected. The
  new build reaps the old file immediately, and the retrieval store holds no
  open handle between queries, so such a search falls back to the substring scan.
  Blue/green index builds in Milestone 6 are what fix this properly; ADR-0022's
  original promise that the previous build survives has been withdrawn rather
  than delivered.
- **`knowledge.status` is not covered by the equality above.** The withheld half
  of a project cannot be read out of `itemCount` or `itemsByStatus`, which
  exclude `deprecated`, `superseded` and `rejected` and report a total that is
  the sum of what they do report. Two other values move: `stateHash`, which
  covers the whole working tree by design (ADR-0016) and is query-independent —
  the same property that makes `snapshotId` safe to publish — and
  `appliedMigrations`, which counts migration *files*, so it increments for a
  migration that creates only withheld items. Measured on two projects differing
  by exactly one rejected item: `appliedMigrations` 1 against 2. It names no
  status, id or body, no request parameter reaches it, and `stateHash` already
  distinguishes anything it does. Accepted for this milestone with the argument
  in T-17 and filed against Milestone 6 as
  [#19](https://github.com/theurian/theurian/issues/19), because every remedy is
  a change to a published tool that has no response schema to change with it.
- **An unregistered `projectId` is echoed back into the error unbounded.** Two
  million characters in produce a two-million-character message, against 2,000
  for an over-long `query` (clamped by `MAX_QUERY_CHARS`) and 185 for an
  over-long `itemId` (which reports its length instead of itself). Nothing is
  disclosed — the caller receives bytes it sent — but the reader of the error
  pays for them. Recorded under T-6 and filed as
  [#17](https://github.com/theurian/theurian/issues/17); the bound is trivial and
  where it goes decides which of three tools change their error text.
- **A damaged cell can make `knowledge.search` and `knowledge.status` answer
  *successfully* with less than the database holds.** The byte-interpretation
  guard under Security does not cover this and cannot: the corrupted column is
  part of the item → revision pointer chain, so the lookup misses and **no value
  is ever converted**, while that guard's whole key is whether a line interprets
  bytes that came out of the file. A caller is told `count: 0` with
  `stale: false`, or `itemCount: 0`, and has no way to tell either from a project
  that genuinely holds nothing — `knowledge.status` contradicts itself inside one
  body, reporting one applied migration beside zero items. Four positions, held
  as an exact set rather than as an allowance, so the reach cannot grow in
  silence and each entry disappears the moment its surface starts refusing
  instead:

  | Tool | Table | Column |
  | :-- | :-- | :-- |
  | `knowledge.search` | `knowledge_items` | `item_id` |
  | `knowledge.search` | `knowledge_items` | `project_id` |
  | `knowledge.status` | `knowledge_items` | `project_id` |
  | `knowledge.status` | `migration_history` | `project_id` |

  The same cause has a second face, and it is the same defect rather than a
  second one: `knowledge.get`'s two id-resolution refusals — `'<itemId>' is not
  present in project '<projectId>'.` and `'<itemId>' points at a missing
  revision.` — **report damage as absence**, at four (tool, table, column)
  positions of their own. A caller is told the item does not exist when its row
  is in fact unreadable. Both faces fire before any cell is interpreted, so
  neither message contains anything but the caller's own arguments: this is
  wrong knowledge, not disclosure. Milestone 6, tracked as
  [#30](https://github.com/theurian/theurian/issues/30); closing it is a change
  to the retrieval gate, `knowledge.status` and `knowledge.get`, not to the
  store the fix above changed.
- **`knowledge.get` and `knowledge.status` publish no response schema.**
  `schemas/mcp/` describes `knowledge.search`, `project.list` and the tool
  context; two of the five tools have nothing a client in another language can
  validate against, and nowhere for the decisions above to land.
  [#20](https://github.com/theurian/theurian/issues/20), which also collects the
  review rounds' remaining LOW findings — three docstrings that overstate what
  the code beside them does, a stale line-number citation, a mutable module-level
  `SAFETY` dict, and a relation gate that publishes an id `knowledge.get` refuses.
- Scope filtering is not implemented. `sensitivity`, `trust_level`, and
  `namespace` are carried on every chunk and read by no query; `namespace` is not
  even populated. Milestone 6.
- RAPTOR summary nodes (FR-R3) and reranking arrive in Milestone 6.

---

### Fixed after Milestone 4

- **`theurian doctor --report` did not redact the repository on any machine
  where the checkout lives inside the home directory** — which is most of them
  (O-3). `_redacted` substitutes plain substrings and replaced `$HOME` with `~`
  first, so by the time the `<repository>` substitution ran its needle was no
  longer in the string. Same command, two layouts:

  ```
  repository beside HOME   <repository>/.theurian is missing migrations, knowledge, state.
  repository under HOME    /private~/work/api/.theurian is missing migrations, knowledge, state.
  ```

  The second publishes the checkout's path relative to home into output meant
  for a public issue, and the `/private~` is a second fault arriving with the
  first: `context.home` is whatever `$HOME` says while the repository root is
  `Path.cwd().resolve()`, so on an account whose home is a symlink the
  unresolved anchor matched *inside* the resolved path.

  Anchors are now built in both spellings and applied longest first. The rest of
  the class goes with it, since the root cause is naive substitution over
  incomplete anchors rather than one ordering:

  - **Only the default data directory is still legible as `~/.theurian`.** The
    exemption used to cover every path under `$HOME`, on an argument about the
    default alone, so `THEURIAN_DATA_DIR=$HOME/clients/<name>/store` was
    published in full — `~` is anonymous, and the directory it sits in is what
    identifies someone. Anything the operator chose is now `<data directory>`
    wherever it points. This is also what stopped `~/work/api/.theurian-data`
    from disclosing the checkout's path relative to home when `$HOME` is a
    symlink and the data directory sits inside the repository.
  - **The executable is redacted to `<executable>`**, since it is routinely a
    virtualenv under a project directory. The install location is given up
    deliberately; `platform` and `version` are still published.
  - **The setup steps stop naming the repository by its bare directory name**,
    which no path anchor can catch without corrupting unrelated prose.

- **`theurian setup` reported files as changed that it never touched, and
  journalled them as applied.** Three steps — `project-registered`,
  `project-layout` and `gitignore` — report what `theurian project register` and
  `theurian init` would do, and setup performs neither. Their probes reported
  `missing`, the runner recorded them `changed`, and five paths landed in
  `changedPaths` with an `applied` line in the setup journal apiece. All five
  were absent from the disk when the run ended, and a second run named the same
  five having written nothing — so the report did not describe the idempotence
  setup actually has (FR-L2). `setup --dry-run --json` offered the same five
  under `steps[].paths`, which is what the user is shown before consenting.

  **Published JSON changes** for those three steps: `outcome` is now `unchanged`
  rather than `changed`, `paths` is now `[]`, and they no longer contribute to
  `changedPaths`. What they report does not shrink — the `missing` status stays,
  `action` still names the command that fixes it, and the run still ends
  `degraded` with a warning for each. The two locations that only `paths` had
  been carrying moved into `summary`: `project-registered` names the registry
  file, `gitignore` names the `.gitignore` it checked.

  The rule is now the runner's rather than each probe's — a step declared with no
  action has its `paths` dropped centrally, the same way criticality is already
  taken from the step definition instead of from the probe. Two mutations had
  restored the defect from a single probe arm while the whole suite stayed green.

- **`theurian auth rotate` did not exist**, while three user-facing messages told
  people to run it — including the one shown when a token is found readable by
  other users. A remedy that errors out is worse than no remedy, because it is
  shown at the moment a credential has already been exposed. Rotation now
  replaces the token, rewrites the env file that names its location, and
  restarts the daemon: the daemon reads its token once at startup, so writing a
  new file alone leaves every client getting a 401 with no visible cause.
- **`theurian daemon stop`** now exists. Milestone 3 omitted it deliberately —
  the service manager owns the lifecycle and a PID-based stop would contradict
  the reason this design uses an advisory lock. Milestone 4 made `daemon start`
  delegate to that manager, which left the absence of `stop` an arbitrary
  asymmetry rather than a principled one. It asks the service manager, and
  refuses rather than guessing when none is registered.

### Milestone 4 — setup, service adapters, and doctor

#### Added

- **`theurian setup`**, an idempotent plan-then-apply state machine over the
  eighteen steps of the specification. It probes everything, shows what it would
  do, applies only that, and then probes everything again — so the report states
  what *is*, not what the apply functions believe they did. Running it twice
  changes nothing (FR-L1, FR-L2).
- **`--dry-run`** is the same code path with the apply skipped, so what the user
  is shown cannot drift from what runs.
- **User-scoped service adapters** for macOS LaunchAgent and Linux systemd user
  units. Never a LaunchDaemon and never a system unit: those need administrator
  rights and would run Theurian as root or a service account to read one user's
  home directory.
- **`theurian doctor`**, read-only by design, and **`doctor --report`**, which
  redacts the home directory, the token path, and repository paths by default
  because its output is what people paste into public issues (O-3).
- **`theurian uninstall`**, which removes the OS service and the MCP entry
  independently and never touches approved knowledge (FR-L5).
- **MCP connection installation** into Claude Code at user scope, carrying
  `${THEURIAN_MCP_TOKEN}` rather than a literal token (SEC-5).

#### Changed

- Theurian **reads** `~/.claude.json` and delegates every **write** to
  `claude mcp add` / `claude mcp remove`. That file is Claude Code's live state,
  not a configuration file Theurian has any business reformatting, and Claude
  Code may be writing to it concurrently. See the amendment to ADR-0012.
- `theurian daemon start` without `--foreground` now asks the service manager
  to start the daemon rather than refusing. Theurian never daemonises itself:
  launchd and systemd already do supervision, restart-on-failure, and log
  redirection, and a hand-rolled double-fork would be a second, worse
  implementation of all three. Starting an *unregistered* service is refused
  rather than improvised — a hook may resume a service the user approved, but it
  must never be the thing that installs one (FR-L3).
- `theurian daemon status` now distinguishes `not-installed` from
  `installed-stopped` by asking the service manager. The SessionStart hook
  branches on exactly this: one means a user-approved service may be resumed,
  the other means send the user to `/theurian:setup` and install nothing.

#### Fixed

- Setup reported `degraded` on almost every successful install. The verification
  pass re-probed the daemon's health microseconds after the start command
  returned, long before it had bound its port. Starting a service and having it
  answer are separate events, and setup now waits for the second one.

#### Known limitations

- Artifact integrity verification is reported as *not applicable* rather than
  satisfied: setup never obtains Core, so it holds no artifact to hash, and a
  step claiming success without checking anything would be a false assurance
  about supply chain integrity (T-16).
- Rollback is a journal, not an undo. Every apply is a create-or-tighten, so a
  critical failure stops and reports where it stopped rather than deleting a
  token another session may already be using.

---

### Milestone 3 — the single MCP daemon

#### Added

- **One daemon per user per machine**, serving MCP over Streamable HTTP at
  `http://127.0.0.1:7419/mcp`. Ten subagents cost one process, one writer, and
  one warm index rather than ten of each (ADR-0002).
- **Single-instance enforcement** through three independent mechanisms, because
  each alone has a known failure mode: an advisory `flock`, a port probe, and a
  startup handshake that reports version and data directory. A losing starter
  exits 0 after confirming the winner is healthy; it never kills the winner and
  never repairs data. A daemon serving a *different* data directory is a
  conflict, not something to reuse — reusing it would answer every query from
  the wrong knowledge base.
- **Local authentication.** A 256-bit token in a 0600 file inside a 0700
  directory, compared in constant time. `/health` stays unauthenticated so the
  SessionStart hook and the instance probe need no credential (ADR-0011).
- **Five read-only MCP tools**: `knowledge.search`, `knowledge.get`,
  `knowledge.status`, `project.list`, and `system.capabilities`. No write-intent
  tool exists at all — not behind a flag, not behind a permission (ADR-0013).
- **Explicit project context.** Every project-scoped tool requires `projectId`.
  There is no "last used project", because with many agents sharing one daemon
  an implicit default resolves one agent's query against another's project.
- **Trust labelling on every result**: `contentClassification:
  untrusted-knowledge`, `mayContainInstructions: true`, `executable: false`,
  plus source anchors and freshness. Knowledge bodies contain imperative
  sentences because they describe rules; the labels say so explicitly (SEC-15).
- **`theurian daemon start --foreground` and `theurian daemon status`**, both
  with `--json`.

#### Security

- The daemon refuses to bind anything but loopback. A networked deployment needs
  TLS, OAuth 2.1, audience validation, and tenant isolation; shipping half of
  them would be worse than shipping none (SEC-1).
- Origin and Host validation, so a page the user visits cannot reach the MCP
  endpoint by resolving a hostname to 127.0.0.1 (SEC-2, T-2).
- Access logging is off. Every request carries an `Authorization` header, and an
  access log is the easiest place for one to escape its 0600 file (SEC-6).
- A token file readable by other users is refused rather than quietly repaired:
  a credential others could already read is not a credential (SEC-4).
- An unregistered `projectId` returns an error naming what *is* registered,
  never another project's knowledge (SEC-13).

#### Known limitations

- `theurian daemon start` supports `--foreground` only. Detaching belongs to the
  user's service manager — a LaunchAgent or a systemd unit — which arrives with
  `/theurian:setup` in Milestone 4. There is deliberately no `daemon stop`:
  the lifecycle owner is the service manager, and a PID-based stop would
  contradict the reason the design uses an advisory lock rather than a PID file.
- `knowledge.search` matches substrings. Ranked hybrid retrieval arrives in
  Milestone 5; the *result shape* is already the published one, so callers
  written now keep working.

---

### Milestone 2 — source ingestion

#### Added

- **Parsers** for Markdown, YAML, JSON, OpenAPI, AsyncAPI, and JSON Schema.
  Structured sources keep their structure: an OpenAPI document yields an index
  of operations, parameters, responses, and schemas, which is what specification
  coverage will read in Milestone 8.
- **Deterministic text projection** so lexical search can reach structured
  content. Renders `outcomes.failure.code: CANCELLATION_NOT_ALLOWED` rather than
  a bare value dump, and is byte-identical across processes and machines.
- **Front matter handling**: parsed, preserved as searchable data, and never
  permitted to govern. A `status: approved` in front matter is ignored *and
  reported*, because a silent ignore is exactly the case where an author
  believes something is approved and it is not.
- **Media-type detection** that prefers content over extension. An OpenAPI
  document is conventionally `openapi.yaml`, and treating it as plain YAML would
  discard the operation index — a loss that would only surface milestones later
  as a query returning nothing.
- **`theurian ingest`**, with per-document failure isolation and an incremental
  path: an unchanged file costs one hash, not a reparse.
- External `$ref` targets are recorded, never fetched (SEC-10, T-7).

#### Fixed

- Markdown files with front matter were reparsed on every run. The manifest
  stored the *body* hash while the early exit compared the *source* hash, and
  those differ for exactly such a file. The two are now distinct fields with
  distinct purposes.
- OpenAPI documents serialised as JSON were not detected, because the sniff
  assumed a YAML line start and `{"swagger": ...}` never matched. They fell
  through to the generic JSON parser and silently lost their operation index.
- The blank line after a front matter block stayed in the body, so identical
  prose hashed differently depending on whether the file carried front matter.

---

### Milestone 1 — local canonical store

#### Added

- **Knowledge migration engine.** Applies the fourteen YAML operations
  transactionally, with `expectedRevision` optimistic concurrency, deterministic
  topological ordering, cycle detection that names the actual cycle, and
  idempotent re-application.
- **SQLite canonical store.** WAL, `foreign_keys=ON`, immutable revisions with no
  update path, alias resolution, bidirectional relation traversal, and a
  migration history that records checksums.
- **State hashing.** Content-addresses a whole canonical state so a database
  file's name describes its contents. Covered by a committed golden vector and a
  cross-process test that would catch a `PYTHONHASHSEED` dependency.
- **Single-writer guarantee.** An OS advisory lock serialises concurrent writers,
  behind an interface a daemon-owned queue can replace without touching
  application code.
- **CLI**: `theurian init`, `project register|unregister|list|status`, and
  `migrate status|validate|apply`, all with `--json`.
- **Deterministic fakes** for `Clock`, `IdGenerator`, and the migration writer.

#### Fixed

- The published JSON Schemas were not packaged into the wheel, so an installed
  `theurian` could not validate a migration at all. A build hook now ships them
  and an e2e test asserts an installed build can read a migration.
- Editing an already-applied migration was not detected. Editing one changes the
  state hash, which routed the next command to a fresh empty database where
  nothing looked wrong; the check now runs against the previously active state
  as well. See the amendment to ADR-0016.
- Re-registering a project reported a change every time, because the
  registration timestamp was refreshed. It now records when the project was
  *first* registered, restoring the idempotence FR-L2 requires.
- A read connection leaked when verifying migration history.

#### Security

- `contentFile` paths are resolved with symlinks followed before the containment
  check, so both `../` traversal and symlink escape are refused (SEC-7, T-4, T-5).
- YAML loading no longer coerces timestamps to `datetime`, which had made valid
  migrations fail their own schema validation.

---

### Milestone 0 — architecture and OSS foundation

### Added

- **Domain model.** Immutable `KnowledgeRevision` under a mutable
  `KnowledgeItem` pointer; typed relations, aliases, source anchors, and
  evidence; specifications with their structured form preserved; traceability
  edges and per-change-type policy; review events, threads, comments,
  resolutions, promotion gates, and knowledge candidates.
- **Ten enforced invariants**, including content-hash verification, immutable
  revisions, half-open validity windows, and mandatory source attribution.
- **Fourteen ports** as `Protocol`s, including `Clock` and `IdGenerator` — both
  ports because time and identifiers are inputs to the state hash, and without
  controlling them the reproducibility guarantee is not assertable.
- **Scope isolation primitives.** A RAPTOR tree's identity is
  `(project, tenant, sensitivity, acl_group, namespace)`, so a summary spanning
  two sensitivity levels has no tree to belong to.
- **Path containment and input limits.** `realpath` resolution before
  containment checks, symlink-escape rejection on intermediate components as
  well as final targets, size and depth caps, and permission checks for
  credential files.
- **Compatibility resolution**, including SemVer §11 ordering and a PEP 440 →
  SemVer translation so Core's own development versions resolve correctly.
- **CLI**: `theurian version --json` and `theurian compat check`, with exit code
  3 reserved for a compatibility mismatch.
- **Public JSON Schemas** for the migration format, MCP tool context, retrieval
  results, project configuration, the CLI version contract, and plugin
  compatibility metadata.
- **275 tests** covering domain invariants, layering, scope isolation, path
  security, schemas, the plugin boundary, and the CLI contract. All run offline.

### Security

- Retrieval results are structurally prevented from being marked executable.
- Every result requires at least one source anchor.
- Migration `contentFile` paths are rejected at both schema and runtime level if
  they escape the project root.

[Unreleased]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev1...main
[0.1.0.dev1]: https://github.com/theurian/theurian/compare/core-v0.1.0.dev0...core-v0.1.0.dev1
[0.1.0.dev0]: https://github.com/theurian/theurian/releases/tag/core-v0.1.0.dev0
