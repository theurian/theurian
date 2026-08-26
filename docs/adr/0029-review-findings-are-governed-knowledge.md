# ADR-0029: Review findings are governed knowledge, ingested from commit trailers

- Status: proposed
- Date: 2026-08-26
- Deciders: Theurian maintainers
- Requirements: FR-S1, FR-S3, FR-V1, FR-V2, FR-V3, FR-V5, FR-V6, FR-K10, FR-T6,
  SEC-15, SEC-16, T-3, R-4
- Decision recorded in
  [#368](https://github.com/theurian/theurian/issues/368), the design-first step
  of turning the manual review-knowledge loop adopted 2026-08-25 into a product
  capability
- Situates against [ADR-0010](0010-three-layer-knowledge-model.md) (which of the
  three layers each artifact occupies), [ADR-0013](0013-ai-writes-produce-proposals.md)
  (the taxonomy corpus items land via propose→guard→accept, not by fiat),
  [ADR-0019](0019-front-matter-is-data-not-governance.md) (the derived labels are
  data, not governance), [ADR-0026](0026-evidence-plane-not-control-plane.md) (a
  served finding is evidence, it gates nothing), and
  [ADR-0027](0027-accept-validates-before-it-moves.md) (the accept path whose
  review rounds emitted the trailers this ADR ingests)

**This ADR records a decision and ships no code.** The parser
([#200](https://github.com/theurian/theurian/issues/200)), the family-taxonomy
corpus items, the recurrence query, and the serving path are all deferred to the
implementation lanes named in *Compliance*. Nothing here modifies
`SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`, any `*.schema.json`, or any `*.py`; the
diff is confined to `docs/`.

**Every count in this ADR was measured on 2026-08-26 against `main` @ `e39572c`,
and each measurement names the command that produced it.** The advisory census in
the appendix is dated and explicitly non-normative; nothing else in the document
depends on the number of advisories that happen to be published on that date.

## Context

On 2026-08-25 the project adopted a **manual** loop for turning review findings
into durable knowledge, and it is now live in three pieces:

1. **`Review-Finding:` commit trailers.** A commit that fixes a review finding
   records it as history in a machine-parseable trailer
   (`Review-Finding: <reviewer> <SEVERITY> — <one-line finding>`), so that
   `git log --grep 'Review-Finding:'` reconstructs the review history.
2. **Brief-time family enumeration.** Before a change is assigned, the brief
   names the observable families that apply, so the reviewer spends its mandate
   on the family nobody enumerated.
3. **Recurrence burn-in.** When the same specialist is caught on the same family
   twice, that family is written into the implementing specialist's agent
   definition, so every future instance is born knowing it.

Each of these three maps onto a product surface Theurian already has requirements
for:

- The standing **observable-family table** → **governed corpus items served by
  search**. FR-V6 already scopes rendered Markdown views of reviews as derived
  artifacts.
- The **trailers** → a **git-history ingestion source**, parsed into structured
  finding records. FR-S1 names "Git commit metadata" as an ingestable source,
  distinct from its "GitHub pull requests, reviews" arm.
- **Family enumeration at assignment** → **retrieval at assignment time** — a
  query that surfaces the prior findings for the specialist and family a brief
  touches.
- **"Caught twice → burn in"** → a **recurrence query** over the ingested
  findings, keyed on (family, specialist).

This is a narrower, git-history-native instance of the review-ingestion family
already specified. FR-V1 asks for structured review records; FR-V2 classifies
review comments into categories; FR-V3 promotes a review thread to a
`KnowledgeCandidate` through a promotion gate; FR-V5 requires raw reviews to
ingest even when LLM candidate generation fails. **The trailer is a
pre-classified, human-authored review record.** Its reviewer and severity are
already a closed vocabulary, and a human wrote its one-line finding into a
commit; it needs no LLM promotion gate to become a structured record. So the
trailer source is the FR-V family's floor — the record that ingests with no model
in the loop, which is exactly the FR-V5 guarantee stated for a source that was
born structured.

The two arms of FR-S1 differ in trust and in when they exist. The
**Git-commit-metadata arm** is a local read of `git log` that works on any clone,
today, with no network and no token. The **GitHub-API arm** (threads, inline
comments, resolution state, CI results, LLM candidate generation) is broader,
needs credentials, and is not this ADR's to design — it is the rest of FR-V. This
ADR bounds itself to the trailer / git-history source and cross-references FR-V
for the rest.

One structural fact makes the trailer source buildable now and the GitHub-API arm
later: the first Git-commit parser does not exist.
`docs/architecture/source-normalization.md` lists a `Git commit` row (subject and
body → author, tree, parents, changed paths) and a `Git diff` row, but
[#200](https://github.com/theurian/theurian/issues/200) records that neither
parser is built. **This ADR is the concrete driver for the first one.**

## Decision

### 1. A review finding is a canonical record parsed from a commit trailer

A `Review-Finding:` trailer is ingested into a **finding record** with the fields
below. The mapping from the trailer to the record is total: every element the
trailer grammar carries maps to a named field, and the two fields the trailer
does **not** carry are marked *derived* rather than left blank.

| Field | Value | Source | Trust |
| :-- | :-- | :-- | :-- |
| `reviewer` | one of `{code-review, security, adversarial}` | trailer token 1 | closed vocabulary, parser-validated |
| `severity` | one of `{CRITICAL, HIGH, MEDIUM, LOW}` | trailer token 2 | closed vocabulary, parser-validated |
| `findingText` | the free-text remainder after the ` — ` separator | trailer text | **untrusted** authored content (decision 3) |
| `commitSha` | the commit the trailer was parsed from | `SourceAnchor.commitSha` (FR-S3) | trusted Git metadata |
| `provider` | `git` | `SourceAnchor.provider` (FR-S3) | fixed |
| `pullRequest` | the PR number the fix merged under | the **trailing** `(#N)` on the squash-merge subject (see the note below), cross-checked against the GitHub merge API when the API arm is present | trusted Git/GitHub metadata |
| `date` | the commit date | Git commit metadata | trusted Git metadata |
| `family` | one member of the observable-family taxonomy, or a residual `unclassified` | **derived** by classification (FR-V2), *not parsed from `findingText`* | derived label (decision 4) |
| `specialist` | one owner from the work-ownership map | **derived** from the fixing commit's changed-file set intersected with the ownership map, *not parsed from `findingText`* | derived label |

**`pullRequest` is the *trailing* `(#N)`, byte-precisely.** GitHub appends the PR
number as a trailing `(#N)` when it squash-merges, so the PR number is the **last**
`(#N)` on the subject line, not the first. This distinction is load-bearing on
this repo's real history: measured 2026-08-26, **6 of the last 40 subjects on
`main` carry two `(#N)`** (`git log origin/main --format='%s' -40 | grep -cE
'\(#[0-9]+\).*\(#[0-9]+\)'` → 6), for example
`fix(security): scan what accept lands … (#349) (#363)`, where `#349` is the
*issue* reference and the trailing `#363` is the PR. A naive first-match would
extract the issue number as `pullRequest`. The git-native rule is therefore **the
trailing token**; the GitHub-API arm, when present, cross-checks it against the
merge metadata.

**`family` and `specialist` are derived, not parsed, and that is the load-bearing
choice.** The one-line finding text does not carry them — a reviewer writes
"byte-identical body accepted under a second item id", not "family=X
specialist=Y" — so a parser that tried to read them from the text would be
inventing them. They are computed: `family` by classifying the finding (FR-V2's
mechanism, against the taxonomy of decision 4), `specialist` by mapping the fix
commit's changed paths onto the work-ownership map. Both derivations are
best-effort: a fix touching two owners' files yields two candidate specialists,
and a finding outside the disclosure-shaped taxonomy takes the residual `family`.
The recurrence query (decision 5) is designed to tolerate that, not to assume the
labels are exact.

Per **FR-K10**, a finding record carries a typed relation to the commit that
recorded it (`recorded-in`) and, through that commit, to the pull request and the
issue it fixed. FR-K10's "typed relations between knowledge, specs, reviews,
code, and tests" is what lets a served finding be followed back to the code it
concerns and the review it came from.

**Worked example — the mapping applied to three real trailers**, chosen because
they span all three reviewer tokens and both a code fix and a docs change
(commits and PR numbers verified 2026-08-26 with
`git log origin/main --grep '<phrase>'`):

Trailer A —
`Review-Finding: adversarial HIGH — a trailing-newline identifier escapes the refusal tuple and splits doctor from migrate validate`

| Field | Value |
| :-- | :-- |
| `reviewer` | `adversarial` |
| `severity` | `HIGH` |
| `findingText` | `a trailing-newline identifier escapes the refusal tuple and splits doctor from migrate validate` (untrusted) |
| `commitSha` | `dd4b991` |
| `pullRequest` | `#364` |
| `date` | `2026-08-26` |
| `family` (derived) | *An error that fires for one input and not another* — the finding is about a refusal behaving differently on a trailing-newline input |
| `specialist` (derived) | *candidate set* `{theurian-python, theurian-tests, theurian-docs}` — `dd4b991` changed files under production Python, tests, and an ADR (plus a plugin doc outside the current map), so the changed-file ∩ ownership-map rule yields at least three owners; the design keeps the candidates rather than guess which the finding is against (decision 1, *What this does not close* item 7) |

Trailer B —
`Review-Finding: security HIGH — satisfied summary claims reachability the mode bits do not establish`

| Field | Value |
| :-- | :-- |
| `reviewer` | `security` |
| `severity` | `HIGH` |
| `findingText` | `satisfied summary claims reachability the mode bits do not establish` (untrusted) |
| `commitSha` | `dd4b991` |
| `pullRequest` | `#364` |
| `date` | `2026-08-26` |
| `family` (derived) | *A published field* — the `satisfied` summary is a published value asserting a property it does not establish |
| `specialist` (derived) | *candidate set* `{theurian-python, theurian-tests, theurian-docs}` — same commit as Trailer A (`dd4b991`), so the same multi-owner changed-file set yields the same candidate set; no single attribution |

Trailer C —
`Review-Finding: code-review HIGH — commit-at-green bullet contradicted push-at-first-green`

| Field | Value |
| :-- | :-- |
| `reviewer` | `code-review` |
| `severity` | `HIGH` |
| `findingText` | `commit-at-green bullet contradicted push-at-first-green` (untrusted) |
| `commitSha` | `6c3019c` |
| `pullRequest` | `#369` |
| `date` | `2026-08-26` |
| `family` (derived) | `unclassified` — a prose self-contradiction is not a disclosure-shaped observable; it maps to no member of the current taxonomy, which is why decision 4 mandates a residual value |
| `specialist` (derived) | `unclassified` (residual) — `6c3019c` changed only `.claude/agents/theurian-*-review.md` (reviewer definitions) and `CLAUDE.md` (orchestration), none of which is owned by an *implementer* specialist — the map's only reviewer row (*Review → the three reviewers*) names reviewers, not an implementer owner the recurrence query keys on — so the changed-file ∩ ownership-map intersection over implementer owners is **empty** → residual, exactly as `family` took `unclassified` above |

Trailer C is the honest residual case for **both** derived labels, and it is the
one that shows the rule producing its own output rather than a hand-assigned one.
Its `family` is `unclassified` because the observable-family table is a
*disclosure* taxonomy and a prose self-contradiction fits none of its members. Its
`specialist` is `unclassified` because the fixing commit changed only
reviewer-agent definitions and `CLAUDE.md`, which no row of the current
work-ownership map owns — the map's reviewer row (*Review → the three reviewers*)
is not an implementer owner the recurrence query keys on — so the changed-file ∩
ownership-map intersection over implementer owners is empty. Both derivations are
best-effort, and Trailer C is where each honestly
returns its residual instead of force-fitting a label: `family` is not pushed into
a disclosure box, and `specialist` is not guessed from a file the map does not
cover. (That the ownership map does not yet cover orchestration files and reviewer
definitions is a fair observation; extending it is an implementation question, not
designed here.) The input population the parser must handle is
`git log origin/main --format=%b | grep 'Review-Finding:'` → **28 lines**
(measured 2026-08-26).

### 2. The trailer is a wire contract, and it is already in use

The `Review-Finding:` trailer grammar is **normative**:

```
Review-Finding: <reviewer> SP <SEVERITY> SP "—" SP <one-line finding>

<reviewer>          ::= "code-review" | "security" | "adversarial"
<SEVERITY>          ::= "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
<separator>         ::= " — "   ; SPACE, EM DASH (U+2014), SPACE
<one-line finding>  ::= opaque free text to end of line
```

`reviewer` is the observed closed set of the three review agents; `severity` is
the four-value severity scale; the separator is a spaced em dash; the remainder
is opaque free text (decision 3 says how it is trusted).

**The format is already being emitted, so a change to it is breaking.** Measured
2026-08-26 on `main` @ `e39572c`:

| Measurement | Command | Result |
| :-- | :-- | :-- |
| Trailer lines already emitted | `git log origin/main --format=%b \| grep -c 'Review-Finding:'` | **28** |
| Commits carrying at least one | `git log origin/main --grep 'Review-Finding:' --oneline \| wc -l` | **5** (`e39572c` #377, `dd4b991` #364, `fa7fef6` #374, `f318b24` #370, `6c3019c` #369) |

An incompatible grammar change — renaming the key, changing the separator,
widening the reviewer or severity vocabulary in a way the parser cannot read
old-style — **strands the 28 lines already committed**, because they are frozen
in Git history and cannot be rewritten without rewriting signed history. Any such
change is therefore a **breaking change** that must state the old shape and the
new one and carry a migration for the emitted population, exactly as a schema
break would. This is why the grammar is pinned here as a contract and not left to
the parser's convenience.

### 3. The finding text is untrusted content; only the two tokens are governed

The trailer's `<one-line finding>` is **authored commit text, and therefore
untrusted content** in the sense SEC-15, SEC-16 and T-3 define. It is written by
a reviewer, but the ingestion path cannot distinguish a reviewer's summary from
any other free text a commit body carries, and T-3 grades exactly this — an agent
acting on instructions embedded in ingested content — as High.

- **When a finding's `findingText` is served, it SHALL carry the untrusted-content
  safety triple** — `contentClassification: untrusted-knowledge`,
  `mayContainInstructions: true`, `executable: false` — the same triple SEC-15
  puts on every retrieval result and T-3 relies on. The design SHALL NOT treat
  `findingText` as governed or trusted metadata.
- **Only `reviewer` and `severity` are a governed, closed vocabulary the parser
  validates.** A trailer whose first token is not one of the three reviewers, or
  whose second is not one of the four severities, is a malformed trailer, not a
  new value. Everything after the separator is opaque and rides under the safety
  triple.

This keeps the trust boundary where the risk register puts it. **R-4** ("prompt
injection through ingested knowledge", `docs/architecture/requirements-analysis.md`
risk register) and **T-3** (`docs/security/threat-model.md` §T-3) both hold that
imperative text inside ingested content is data, never an instruction; the safety
triple is the control both name. The finding text is ingested content, so it
inherits that control rather than an exemption.

### 4. The family taxonomy becomes governed corpus items

The standing observable-family table — the disclosure-family table used in review
rounds, currently in `CLAUDE.md` — is **mirrored into governed corpus items**:
one knowledge item per family, served by `knowledge.search`, so a brief can
retrieve the family and its worked example at assignment time instead of a human
reading the table from an orchestration file.

- Those corpus items land through **propose → guard → accept per ADR-0013**, not
  by fiat. AI writes produce proposals; a human accepts them as migrations. The
  taxonomy is knowledge like any other, and it enters the store the way all
  knowledge does. **The family and specialist labels on a finding are data, not
  governance (ADR-0019):** they describe a finding, and they gate nothing — no
  assignment is blocked and no merge is held by a label's value.
- **Layer placement (ADR-0010):**

  | Artifact | ADR-0010 layer | Why |
  | :-- | :-- | :-- |
  | The `Review-Finding:` trailer in the commit | **Source** | bytes as authored — Git commit metadata |
  | The parsed finding record | **Canonical** | the record of truth, normalized, carrying a `SourceAnchor` (FR-S3) |
  | The family-taxonomy knowledge items | **Canonical** | governed knowledge, landed via ADR-0013 |
  | A served search result or a rendered Markdown view | **Index / derived** | rebuildable, never authoritative — FR-V6 scopes Markdown views as derived artifacts only |

  Both governed artifacts this decision names — the family-taxonomy items and the
  parsed finding records — occupy the **Canonical** layer.

**Why the finding record enters Canonical without a propose → accept gate, while
the taxonomy items go through one.** FR-V4 forbids auto-approving a *candidate*
("approval is a human act recorded as a migration",
`docs/architecture/requirements-analysis.md`), and FR-I3 routes *AI writes* to
proposal files rather than into approved state. Neither governs the finding
record. The record is a normalized read of **human-authored, signed git-commit
metadata that already exists in history** — it is not an AI write (no model
produces it; FR-I3's subject is AI writes) and not an LLM `KnowledgeCandidate`
awaiting human promotion (FR-V4/FR-V3's subject is model-generated candidates).
Ingesting it is the same class of act as any FR-S1 source ingestion: a read of an
existing source into the Canonical record of truth, carrying a `SourceAnchor`
(FR-S3). The human accept gate already fired — when the maintainer authored and
signed the commit whose trailer is now read — so FR-V4/FR-I3's approval gate does
not apply a second time. The **family-taxonomy items** are the opposite case:
they are *new* authored knowledge, so they land through propose → guard → accept
per ADR-0013, as the first bullet of this decision states.

The current membership of the table is **8 families** (measured 2026-08-26:
`sed -n '/| Family | What it looked like/,/^\s*$/p' CLAUDE.md` lists eight body
rows — a published field; which rows or which part of a row reached a field; a
duration; a statistic over rows the caller may not see; an error that fires for
one input and not another; a resource the query consumes; another tool reaching
the same content; state/lifecycle/concurrency artefacts). **The design mirrors
the mechanism, not a frozen count.** The table lives in an orchestration file that
will keep changing, so the corpus-seed step re-reads it rather than hard-coding
its rows, and the taxonomy carries a residual `unclassified` member for a finding
that matches none of them (decision 1, Trailer C). Reconciling this
disclosure-shaped taxonomy with FR-V2's eleven review-comment categories is an
implementation question, deferred and named in *What this does not close*.

### 5. A recurrence query replaces the manual burn-in

The manual "caught twice → written into the specialist's agent definition" step
is replaced by a **recurrence query over the ingested finding records**, keyed on
**(family, specialist)**.

- **Key:** `(family, specialist)`. **Input population:** the finding records of
  decision 1 **that the embargo boundary (decision 6) allowed to be served** — the
  count is computed over embargo-cleared (served) rows only. This is load-bearing
  because the count `N` is itself a served value: a `(family, specialist)`
  aggregate is a *statistic over rows the caller may not see*, so if a withheld
  finding could move `N`, the aggregate would leak that finding's existence even
  while decision 6 refused its content. Restricting the population to served rows
  is what keeps `N` from varying with a withheld finding. On this ADR's source the
  restriction is a no-op — only public `main` is ingested, so every ingested row
  is already servable — but it is stated normatively so the property survives any
  future non-public ingestion path.
- **Given** N prior findings against specialist X on family Y, **When** a brief
  for X touches family Y, **Then** the query surfaces those prior findings at
  assignment time — the same knowledge the manual burn-in wrote into the agent
  definition, served on demand instead of copied into a file.
- **Migration path:** when the recurrence query ships and serves at assignment
  time, the manual burn-in retires. The recurrence rule in `CLAUDE.md` — a family
  caught twice against a specialist is written into that specialist's definition
  in `.claude/agents/theurian-*.md` — stands until then and is removed by the CL
  that ships the query, so the two mechanisms never both run. Until that CL, the
  manual loop is the live mechanism and this ADR changes nothing about it.

This is where the design stays an **evidence plane, not a control plane
(ADR-0026)**: the recurrence query *surfaces* prior findings to the orchestrator
writing the brief. It does not block the assignment, reassign the work, or fail a
gate. The orchestrator reads the evidence and decides, exactly as it reads a
search result today.

### 6. This source holds no embargoed finding; the serving-layer rule is owed to any path that could

**This ADR's git-history source ingests only public `main`.** Embargoed
disclosure work lives on the private fork until its advisory ships (the project's
embargo discipline), so nothing — branch, trailer, or CHANGELOG hint — reaches
public `main` before publication. Public git-history ingestion therefore
*structurally* excludes embargoed trailers: there is no embargoed
`Review-Finding:` line on public `main` to ingest, and so none to serve. **On this
source the embargo protection is structural, and it is the only mechanism there
is** — the source cannot do better, because it has no way to check an advisory's
state.

**Why this source cannot enforce a serving-layer embargo rule.** The finding
record (decision 1) carries no advisory-reference or embargo-state field, and this
ADR bounds its source to an offline `git log` that works with no network and no
token (Context). Resolving whether a GHSA is published needs the GitHub
advisories API, which this source explicitly excludes. A serving-layer rule of the
form "never serve a finding whose advisory is unpublished" therefore **has no
predicate it can evaluate on this source**: none of the 28 emitted trailers
carries a `GHSA`, `CVE`, or advisory token to key on. Claiming the source enforces
such a rule "regardless of" the structural constraint would be asserting a control
the design cannot run — so this ADR does not claim it. The protection here is
100% structural.

**The serving-layer embargo rule is a requirement that binds any FUTURE
non-public ingestion path** — a private-fork ingestion, a manual seed — that could
carry an embargoed finding into the store. Its enforcement is **deferred to the
path that has advisory context**: the FR-V GitHub-API arm can mark a finding
`securityRelated` at ingestion time, where the advisory state is available, and
then refuse it **uniformly** at serve — the refusal must not distinguish "an
embargoed finding exists and is withheld" from "no such finding exists", or the
refusal is itself a disclosure channel. That ingestion-time-marking-then-uniform-
refusal mechanism is **owed future work for the path that has the advisory
context, not something this offline source can or does enforce.** It is named
here as owed, not claimed as holding.

The dated census of which advisories are currently published is in the
**non-normative appendix**, kept out of the normative text on purpose: a future
GHSA must not make this decision stale.

## Consequences

### Positive

- The review history the project already emits becomes queryable knowledge. The
  28 trailers on `main` are a corpus that exists today and is currently readable
  only by `git log --grep`; parsing them makes "what has this specialist been
  caught on before" a search, not a memory.
- The manual burn-in stops being a step a human has to remember. A family caught
  twice surfaces because the data says so, not because someone edited an agent
  definition.
- The trust boundary is decided before any code reads a trailer: the two tokens
  are governed, the free text is untrusted, and the serving path inherits the
  SEC-15 safety triple rather than inventing an exemption for "reviewer-authored"
  text.
- The taxonomy enters the store the way all knowledge does — propose, guard,
  accept — so it is versioned, reviewable, and diffable, instead of living only
  in an orchestration file.

### Negative

- **The derived labels are best-effort.** `family` and `specialist` are computed,
  not authored, so a fix that spans two owners' files or a finding outside the
  disclosure taxonomy produces an approximate label. The recurrence query is
  designed to tolerate that, but a reader must not treat `specialist` as a
  precise attribution.
- **The trailer grammar is now a contract with an installed base.** 28 lines are
  already frozen in history, so the parser cannot be made stricter than the lines
  it must already read, and a grammar change is a breaking change with a
  migration cost rather than a free edit.
- **This drives the first Git-commit parser (#200), which does not exist.** The
  design commits the project to building parser machinery it has so far only
  listed in `source-normalization.md`.
- **The disclosure-family taxonomy does not fit every finding.** A maintainability
  or prose finding takes the residual `unclassified` value, so the recurrence
  query is sharpest on disclosure-shaped families and weakest exactly where the
  finding is not about disclosure.

### Neutral

- The GitHub-API arm of FR-S1 (threads, inline comments, CI results, LLM
  candidate generation) is untouched. This ADR is the narrow, git-native floor of
  FR-V; the broader ingestion stays FR-V's, and the two share the finding record
  and the safety triple but not the source.
- On this ADR's source the embargo rule is a no-op **by construction, not by
  luck**: the source ingests only public `main`, which structurally holds no
  embargoed trailer, so there is nothing to refuse. That is correct-by-design, not
  a gap. The serving-layer embargo rule is owed to any future non-public ingestion
  path (decision 6), and costs this source nothing because no such path feeds it.
- FR-T6 contradiction reporting between review knowledge and current specs
  becomes *possible* once findings are served corpus items, but it is not
  designed here — it is a downstream capability the served corpus enables.

## What this does not close

1. **The Git-commit and Git-diff parsers do not exist**
   ([#200](https://github.com/theurian/theurian/issues/200)). This ADR drives the
   first one; it does not build it. Until it ships, there is no ingestion, and the
   manual trailer/`git log` loop is the live mechanism.
2. **FR-V's full GitHub-API review ingestion** — threads, inline comments,
   resolution state, CI results, and LLM `KnowledgeCandidate` generation
   (FR-V1–FR-V3) — is broader than the trailer source and is not designed here.
   The trailer source is FR-V5's floor: the record that ingests with no model in
   the loop.
3. **Reconciling the observable-family taxonomy with FR-V2's eleven categories.**
   The taxonomy this ADR mirrors is disclosure-shaped; FR-V2's categories are a
   different cut. The classifier's mapping between them — and the exact set of
   corpus items — is an implementation question, and the residual `unclassified`
   value is the seam it leaves.
4. **FR-T6 contradiction reporting** between review knowledge and current specs is
   named as a capability the served corpus enables, not a thing this ADR designs.
5. **The async review sweep** ([#378](https://github.com/theurian/theurian/issues/378))
   is a *producer* of findings this ingestion would consume. Its cadence and where
   its findings land are #378's to decide, not this ADR's.
6. **External-tool sources** — Jira, Confluence, Notion, Linear
   ([#223](https://github.com/theurian/theurian/issues/223)) — are a different,
   post-1.0 source class gated on a separate trust model, and are out of scope
   entirely.
7. **The `specialist` derivation's multi-owner case.** A fix touching more than
   one owner's files yields more than one candidate specialist; resolving which
   one the finding is *against* is left to the implementation, which may keep all
   candidates rather than guess.

## Closure argument: no serving surface is a disclosure channel

**The invariant is stated once, at the store, over every surface — not surface by
surface.** A field-by-field argument over the per-record read does not close the
design, because findings are served through more than one surface, and
enumerating surfaces one at a time always leaves one out: an earlier draft of this
argument named only the per-record read and the recurrence aggregate and missed
the reverse relation graph and the review-unit view below. So the closure is not a
per-surface checklist. It is a single property of the **population every surface
derives from**.

**The served population is per-caller.** A finding row that is withheld from a
caller is **excluded from that caller's served population**. A row is withheld
either because its advisory is embargoed (decision 6) *or* because the caller may
not read it under any other withholding dimension — SEC-13 cross-project
isolation, a sensitivity label, or a draft/rejected lifecycle status. The served
population is therefore the embargo-cleared rows intersected with the rows this
caller may read. On this ADR's source those other dimensions **degenerate**: the
source ingests only public `main`, and a finding record carries no sensitivity
label and no draft/rejected status. SEC-13's isolation does not vanish — the
standard `chunks.project_id` project filter still runs; it simply **narrows to one
project**, because this source's findings all belong to a single project — so here
*served = embargo-cleared*.
That degeneration is a property of this source, named so it is not mistaken for
the invariant — a future store that aggregates multi-project findings or attaches
sensitivity restores the dimensions, and the per-caller filter is then the store's
serving controls' to apply, the same deferral family #8 makes for lifecycle.

**The invariant, over all surfaces and all value kinds.** Every value any serving
surface publishes — a **field**, a **count**, an **edge** (or edge-set
cardinality), or a **view member** — is a function of the caller's served
population *only*. A withheld row contributes to no response on **any** surface.
The surfaces the design defines today are instances of this, not the extent of it
(including, but not limited to):

- **S1 — the per-record read** (decision 1): a finding's fields;
- **S2 — the recurrence aggregate** (decision 5): a `(family, specialist)` count
  `N`;
- **Relation-graph traversal in either direction** (FR-K10, decision 1): the typed
  `recorded-in` edges from a finding to its commit, PR, and issue, *and the
  reverse* — "which findings `recorded-in` commit C?", "which findings relate to
  issue #I?". A reverse query returns an **edge set** whose membership and
  cardinality are a statistic over the findings sharing that node, and co-location
  is the *standard* shape of this data, not an edge case: measured 2026-08-26,
  `dd4b991` (#364) carries **17** `Review-Finding:` trailers on one commit/PR node,
  `e39572c` **5**, `6c3019c` **3**
  (`git log -1 --format=%b <sha> | grep -c 'Review-Finding:'`). A withheld finding
  sharing a node with a served one must appear in neither that node's edge set nor
  its cardinality;
- **The FR-V6 review-unit (PR-level) Markdown view** (decision 4): its **members**
  are the findings it lists — the same co-location enumeration as the reverse
  relation query;
- **Taxonomy search** (decision 4): the family corpus items are seeded from
  `CLAUDE.md` static prose, independent of any finding, so they produce identical
  responses across the two corpora below by construction;
- **and any serving surface a future implementation adds.**

**Why the universal quantifier holds for a surface not yet built.** The
served-population invariant states *what* must hold — every published value is a
function of the caller's served population — but the store does not discharge it
with one result-set filter, because a result-set filter is provably insufficient
on two surfaces this project has taken CRITICALs on. It discharges the invariant
through a **per-surface control applied over the served population**, and the kind
of value the surface publishes chooses the control:

- the **retrieval scope filter** (project/status/sensitivity) for the per-record
  read (S1) and the recurrence aggregate (S2) — a WHERE predicate over the served
  rows, evaluated at query time;
- the **T-17a withdrawal→purge** for any *ranked-search* surface, because a surface
  that publishes `fusedScore` (`mcp/search.py`) prices it over FTS5/BM25
  **collection statistics computed at index-build time over the physical index
  population** (`cli/index_commands.py`), not the query-time result set.
  Restricting the result population does not clean those statistics, and a
  tombstone does not move them either (established in Milestone 6); excluding a
  withheld finding from a ranked surface is therefore a **physical purge**
  (threat-model T-17a), not a filter;
- the **per-edge `_relation_is_visible` authority gate** (`mcp/tools.py`) for
  relation edges, because a population filter does not secure an edge: T-21
  (GHSA-vx8x) published a `rejected` item's rejection `note` onto an approved item
  through a `contradicts` edge, and closing it needed a per-endpoint authority read
  of *each* end by the id it literally names — not a wider or narrower population.
  (A `recorded-in` edge's endpoints are a commit sha, not a `KnowledgeItem`, so
  `_relation_is_visible` fails closed on them today; a future surface serving that
  edge owes its own per-edge gate.)

So the universal quantifier holds not because one filter covers every surface, but
because the invariant is enforced **at the served population by whichever control
that surface's value kind requires**. This is a weaker, more honest claim than
"one filter suffices": a surface is safe only once its control is applied over the
served population — the store already provides these controls for the surfaces it
serves, and a **new surface owes its own**. That owed discharge, not a property a
future surface inherits for free, is the failure a per-surface enumeration keeps
reopening; naming the control per value kind is what keeps "any future surface"
from being read as safe by remembering to patch each new surface as it lands.

The eight observable families from the review-round table are the checklist this
population-level invariant must survive; each is marked with the surface it touches
or the reason it is N/A for a read-only record derived from public git metadata:

1. **A published field.** *(S1)* Every field of a finding record — `reviewer`,
   `severity`, `findingText`, `commitSha`, `pullRequest`, `date`, and the derived
   `family`/`specialist` — is a function of **public inputs only** (the public
   trailer plus public commit metadata). None is computed from still-withheld
   pre-fix content, so no served field varies with content the caller cannot read.
2. **Which rows, or which part of a row, reached a field.** *(S1, assignment-time
   retrieval, reverse relation traversal, review-unit view)* The record has no
   excerpt-selection or candidate-displacement step: it publishes named fields
   verbatim, so there is no "which part of a row" choice to leak. Which *rows*
   reach a response — the findings a decision-5 brief query returns, the edge set a
   reverse `recorded-in` query returns for a shared commit/PR/issue node, and the
   members a review-unit view lists — are all drawn from the caller's served
   population only, so a withheld finding cannot take a slot in any returned set,
   edge set, or view.
3. **A duration.** *(N/A, with reason)* The design specifies no path whose latency
   varies with withheld content: the source is a batch parse of public git history
   and no served value's timing is documented to depend on a withheld row. A
   future serving path that adds a per-query timing surface inherits the T-17-class
   timing residual on that path, not on this record.
4. **A statistic over rows the caller may not see.** *(S2, reverse-relation
   edge-set cardinality, ranked-search collection statistics)* The recurrence count
   `N` is the leading case of this family, and the cardinality of a reverse
   `recorded-in` edge set — how many findings share a commit, PR, or issue node — is
   the same family on the relation graph. Both are evaluated at query time, and both
   are closed by restricting the population to the caller's served rows (decision 5,
   and the served-population definition above), so neither `N` nor an edge-set
   cardinality can vary with a withheld finding's existence. A **third** instance is
   not closed that way: a finding served through *ranked search* carries a
   `fusedScore` priced over FTS5/BM25 collection statistics computed at
   **index-build time over the physical index population**, so a result-set filter
   does not exclude a withheld finding from it. Its control is the **T-17a
   withdrawal→purge** (the enforcement paragraph above) — a physical purge, because
   a tombstone does not move collection statistics — so its served population is the
   purged physical index, not a filtered result set.
5. **An error that fires for one input and not another.** *(owed to the future
   non-public path)* On this ADR's source, structurally no embargoed finding
   exists to refuse (only public `main` is ingested), so the "embargoed-exists vs
   does-not-exist" distinction cannot arise here. The uniform-refusal requirement
   that removes it is owed to any future non-public ingestion path (decision 6).
6. **A resource the query consumes.** *(N/A, with reason)* Serving a parsed record,
   or an aggregate over served rows, from public git metadata consumes no per-query
   resource whose magnitude reveals a withheld row — there is none in the source to
   reveal.
7. **Another tool reaching the same content.** *(S1, relation graph)* A finding's
   `findingText` is a human one-line *summary* authored into a public commit on
   `main`; it is not the pre-fix vulnerable content. The `recorded-in` relation
   points at the fixing commit, whose *post-fix* state is public and whose *pre-fix*
   vulnerable content never lands on public `main` (decision 6). So neither the text
   nor the relation, reached through any tool, discloses a body or field the caller
   may not read.
8. **State, lifecycle, and concurrency artefacts.** *(N/A, deferred to the store's
   controls)* This ADR designs no index files, active pointer, or rebuild
   concurrency of its own: the parsed records are Canonical (decision 4) and any
   served or index artefact is rebuildable-derived (ADR-0010), inheriting the
   store's existing lifecycle controls. ADR-0022 governs the search/rebuild race,
   not this ADR.

Beyond the eight, **authored text as an injection carrier (T-3)**: `findingText` is
served under the SEC-15 safety triple (decision 3), so an instruction hidden in a
finding's text rides inside a result marked `mayContainInstructions: true`, exactly
as a knowledge body does. This is a not-executed guarantee, not a not-disclosed one.

**The closure, stated as one query against two corpora.** Take a finding corpus
that holds a withheld row — an embargoed finding, or one withheld under any other
dimension above — and a corpus that never held it. **The two must produce
identical responses on every serving surface, evaluated at all eight families
above** — the per-record read (S1), the recurrence aggregate (S2), relation-graph
traversal in either direction, the review-unit view, taxonomy search, and any
surface added later. The refutable one-liner that carries it: **every published
field, count, edge, and view member is a function of the served
(withholding-cleared) rows only**, so a withheld row changes no response on any
surface. This is stronger than both "every field of a record is a function of
public metadata" (which never reached the aggregate) and a two-surface enumeration
(which never reached the relation graph or the view): the property is stated over
the population every surface derives from, so it binds surfaces the design has not
yet named.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **A bespoke non-trailer store — a JSON sidecar of findings committed alongside the code** | It duplicates what Git already records and invites drift: a sidecar and the commit it describes are two records of one fact, and this project has been burned twice by two records of one fact disagreeing. The trailer is already emitted, already in history, and already the review record; a sidecar would be a second source of truth for a fact the commit trailer holds authoritatively, plus a merge-conflict surface the trailer does not have. |
| **Treat the finding text as trusted, governed metadata** | It is authored commit free text, and T-3 grades an agent acting on instructions embedded in ingested content as High. There is no way for the parser to tell a reviewer's summary from any other free text a commit body carries, so exempting it from the SEC-15 safety triple would open exactly the injection channel R-4 and T-3 exist to close. Only the two closed-vocabulary tokens are validated; the rest is untrusted. |
| **Enumerate the currently-embargoed advisory IDs normatively in the ADR** | An ID list is stale the moment the next advisory is drafted, and its job is to hold for *future, unpublished* advisories that by definition have no ID yet. A policy — "never serve a finding whose advisory is unpublished" — holds for advisories that do not exist when the ADR is written; an ID list would have to be amended for every new embargo and would silently fail for the one nobody remembered to add. The dated census is kept, but as a non-normative appendix that cannot make the decision stale. |
| **Build the parser, corpus seed, recurrence query and serving path now, in this CL** | The metadata shape, the trust boundary, and the embargo boundary are decisions that outlive any one implementation, and getting them wrong is expensive to reverse once a parser has ingested 28 trailers under the wrong trust assumption. Deciding first, and building against a recorded decision, is what keeps the implementation lanes from each re-deciding the trust boundary. The parser is #200's, driven by this design and not folded into it. |
| **Parse `family` and `specialist` out of the one-line finding text** | The text does not contain them — a reviewer writes a finding, not a classification — so a parser reading them from the text would be fabricating structure that was never authored. They are derived: `family` by classification (FR-V2), `specialist` from the fix commit's changed-file set. Deriving them is honest about their approximate nature in a way parsing a field that is not there would not be. |
| **Run the trailer source through FR-V's LLM promotion gate like a GitHub review thread** | The trailer is already a structured, human-authored, pre-classified record with a closed-vocabulary reviewer and severity. An LLM promotion gate exists to turn an unstructured review *thread* into a candidate; running it over a record that is already structured adds a model in the loop for no gain and violates FR-V5's guarantee that a born-structured source ingests without one. |

## Compliance

**This ADR ships no behaviour, so it has no shipped test to name.** Its
enforcement at design time is the measurements it cites; its enforcement at
implementation time is the tests the driven lanes owe. This is stated honestly:
ADR-0027's Compliance names *landed* tests because its decision shipped code;
this one names *owed* ones because its decision does not, and the test names below
are the properties an implementation must pin, not files that exist today.

Measured now, and reproducible from this ADR:

- The trailer population the parser must handle: **28 lines**, **5 commits**
  (`git log origin/main --format=%b | grep -c 'Review-Finding:'` and
  `git log origin/main --grep 'Review-Finding:' --oneline | wc -l`, 2026-08-26,
  `main` @ `e39572c`).
- The advisory census the embargo appendix rests on: **5 published, embargo over
  on all** (`gh api "repos/theurian/theurian/security-advisories?per_page=100&state=published"`,
  2026-08-26).
- The no-code scope of this change: `git diff origin/main...HEAD --stat` shows only
  paths under `docs/`, and `git diff origin/main...HEAD -- '*.py' '*.schema.json'`
  is empty. (`origin/main`, not a possibly-diverged local `main`, to match the rest
  of this ADR's measurement discipline.)

Owed at implementation, each tied to the lane that will discharge it:

- **The parser reads all 28 emitted trailers** and maps each to the decision-1
  record, with the two tokens validated against their closed vocabularies and a
  malformed trailer refused — driven by
  [#200](https://github.com/theurian/theurian/issues/200)'s first Git-commit
  parser. A test that feeds the 28 lines through the parser and asserts a total,
  loss-free mapping. "Loss-free" here means **byte-preservation**: some real
  finding texts embed semi-structured references — for example `(recorded, #64)`
  in `dd4b991`'s trailers, or a bare `#378` — that carry an FR-K10-shaped typed
  relation the record does not type. `findingText` preserves these byte-for-byte,
  so the mapping loses nothing; *extracting* them into typed relations is deferred
  implementation, not claimed here.
- **A served `findingText` carries the SEC-15 safety triple** — a test on the
  serving path asserting `contentClassification: untrusted-knowledge`,
  `mayContainInstructions: true`, `executable: false` on a finding result, and a
  companion that the check can fail (a result missing the triple is rejected).
- **The ingestion source is scoped to the public default branch (`origin/main`)
  only** — a test that the source does not read `--all`, local branches, or
  non-public remote refs, driven by
  [#200](https://github.com/theurian/theurian/issues/200)'s parser. The embargo
  closure (decision 6) rests on this scoping: it holds *because* only public `main`
  is ingested. But `git log` defaults to the current branch and reads everything
  under `--all`, so `origin/main` is an implementation choice the mechanism does
  not inherently guarantee — an implementer wiring `git log --all` (which may
  include fetched private-fork commits) silently loses the structural protection.
  This test is the regression that catches it, and it pins the scoping rather than
  trusting it.
- **Any future non-public ingestion path refuses an embargoed finding uniformly
  at serve** — owed to *that* path (the FR-V GitHub-API arm that has advisory
  context), not to this source, which structurally holds no embargoed trailer to
  serve (decision 6). A test that a finding marked `securityRelated` for an
  unpublished advisory is not served, and that its refusal is indistinguishable
  from the "no such finding" response.
- **The family-taxonomy items land through propose → guard → accept** — a
  corpus-governance test that the seeded taxonomy items are governed knowledge
  (ADR-0013), not written by fiat.
- **The recurrence query surfaces prior findings by (family, specialist)** — a
  test driving the Given-When-Then of decision 5 over a fixture of finding
  records.
- **The recurrence count is computed over embargo-cleared rows only** — a test
  that a withheld finding does not move the `(family, specialist)` count `N`
  (decision 5), so the aggregate cannot leak a withheld finding's existence.
- **A reverse `recorded-in` edge set excludes withheld findings** — a test that a
  withheld finding sharing a commit, PR, or issue node with a served one moves
  neither that node's reverse `recorded-in` **edge set** nor its **cardinality**
  (family 4). Enforced by the per-edge `_relation_is_visible` authority gate, not a
  population filter (the enforcement paragraph in the closure), so this is driven by
  [#200](https://github.com/theurian/theurian/issues/200)'s relation surface over
  the store's existing `_relation_is_visible` control.
- **An FR-V6 review-unit view lists only served findings** — a test that a withheld
  finding does not appear in the **members** of a rendered PR-level Markdown view
  (decision 4, FR-V6), the same co-location enumeration as the reverse relation
  query. Driven by [#200](https://github.com/theurian/theurian/issues/200)'s view
  renderer.
- **A ranked-search surface over findings ranks the T-17a-purged population** — a
  test that a withheld finding left in the physical index does not move a visible
  finding's `fusedScore` or rank (family 4): the served population for a ranked
  surface is the **withdrawal→purged** index population (threat-model T-17a), not a
  result-set filter, so the response is byte-identical to a build that never held
  the withheld finding — a tombstone would not move the collection statistics.
  Driven by the store's existing T-17a withdrawal→purge control, exercised over a
  findings corpus by [#200](https://github.com/theurian/theurian/issues/200).
- **The manual burn-in is retired by the CL that ships the query** — the
  implementation lane removes the recurrence rule from `CLAUDE.md` in the same CL,
  so the two mechanisms never both run (decision 5).

## Appendix — advisory census (non-normative, dated)

> **This appendix is explicitly non-normative.** It records the disclosure state
> on one date so a reviewer can see what the embargo policy (decision 6) was
> checked against; it is **not** part of the decision, and a future GHSA does not
> make this ADR stale. The normative rule is the policy in decision 6, which holds
> for advisories that do not appear here.

Checked against the disclosure history as of **2026-08-26, `main` @ `e39572c`**
(`gh api "repos/theurian/theurian/security-advisories?per_page=100&state=published"`):
**5 advisories are published, all with their embargo over.** No draft, triage, or
embargoed advisory appears here or anywhere in this ADR.

| GHSA | Severity | Published | Summary |
| :-- | :-- | :-- | :-- |
| GHSA-7997-g35f-q59h | critical | 2026-08-15 | A reused revision id discloses a withheld body |
| GHSA-266v-fcj2-qggx | critical | 2026-08-16 | Derived state committed to a repo is served without a build-provenance check |
| GHSA-w5cm-cqf9-vm7r | critical | 2026-08-19 | A body file shared across two revisions serves a withheld body |
| GHSA-vx8x-rjfj-9x54 | critical | 2026-08-19 | An alias key colliding with a live rejected item's id |
| GHSA-97q9-xxfg-33r6 | high | 2026-08-25 | A RAPTOR `raptorPath` title discloses a withdrawn body |

There are exactly five.
