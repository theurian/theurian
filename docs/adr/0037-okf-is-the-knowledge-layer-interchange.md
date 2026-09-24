# ADR-0037: OKF is the knowledge layer's interchange — an Index-class export and a gated import

- Status: accepted
- Date: 2026-09-24
- Deciders: Theurian maintainers
- Requirements: FR-I3, FR-V4, SEC-11, INV-6, INV-8, T-3
- **Resolves** [`docs/roadmap.md`](../roadmap.md) §9 ADR candidate 8 — the
  governance of a derived context package
  ([#279](https://github.com/theurian/theurian/issues/279)) — by fixing it as
  Index-class *and* naming the format, which is what
  [#705](https://github.com/theurian/theurian/issues/705) asks for
- Situates against [ADR-0010](0010-three-layer-knowledge-model.md) (the three
  layers this adapter sits between), [ADR-0005](0005-yaml-knowledge-migrations.md)
  (the migration format it does not touch),
  [ADR-0019](0019-front-matter-is-data-not-governance.md) (front matter is data,
  never governance), [ADR-0013](0013-ai-writes-produce-proposals.md) (the
  approval gate the import preserves),
  [ADR-0035](0035-interactive-source-curation-is-agent-mediated.md) (the on-ramp
  shape it reuses), [ADR-0009](0009-no-llm-vendor-lock-in.md) (vendor
  neutrality), [ADR-0027](0027-accept-validates-before-it-moves.md) (the digest
  pin) and [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) /
  [ADR-0033](0033-knowledge-candidate-generation.md) (the write path the import
  terminates in)
- Specification: **OKF v0.2**,
  <https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>,
  read 2026-09-24. Every `§` reference below is a section of that document.

**This ADR records a design and ships no code.** No command grows an option, no
schema changes, no enum moves; the diff is confined to `docs/`. It is slice S1 of
the OKF campaign: slice S2 builds the export command against it and slice S3 the
import, so the decisions below are written to be implemented rather than
revisited.

**Every repository fact below was measured on 2026-09-24 against `origin/main` at
`39ad65d1`,** and each carries the path that settles it.

## Context

[#705](https://github.com/theurian/theurian/issues/705) proposes adopting Google
Cloud's Open Knowledge Format as the concrete interchange for Theurian's
knowledge layer, in two directions: an OKF-target export for the roadmap's
Phase F ② context package, and a gated OKF import.

OKF is a small, open specification. A bundle is a directory tree of Markdown
files with YAML front matter (§3); one concept per file; `type` is the only
required key (§4.1); `index.md` and `log.md` are reserved names (§3.1); links
between concepts are ordinary Markdown links (§6.1); and conformance is
deliberately permissive — a consumer MUST NOT reject a bundle for unknown `type`
values, unknown additional front-matter keys, missing optional fields, broken
cross-links or a missing `index.md` (§11). There is no schema registry and no
required tooling.

Two premises in the proposal have to be corrected before a design can rest on
them, and both corrections make the design better rather than worse.

**The first correction is the one #705 makes itself, and it is right.** OKF
represents knowledge *state*; Theurian's migration YAML is an *operation log*
([ADR-0005](0005-yaml-knowledge-migrations.md)), and the entire Phase B
governance surface is bound to that log — the accept-time secret scan (SEC-11),
schema validation of MCP input (SEC-12), the committed-at-`HEAD` check
([ADR-0034](0034-migrate-apply-enforces-the-merge.md)), the write-intent tools
and their v1 operation gate. Making the migration format "OKF-compatible" would
be a layer mismatch, and it is out of scope here.

**The second correction is to a premise #705 states as an alignment.** It says
Theurian's canonical knowledge "is already `.theurian/knowledge/**/*.md` —
markdown with YAML frontmatter". That is not what the canonical format is. The
canonical form is a **two-file split**:

- **Governance lives only in the migration.**
  `schemas/migrations/migration.schema.json`'s `$defs/revisionMetadata` requires
  `title`, `contentType`, `kind`, `namespace`, `status` and `owner`, and carries
  `trustLevel` (default `unverified`), `sensitivity` (default `internal`),
  `tenantId` (default `local`), `aclGroup` (default `default`), `validFrom`,
  `validTo`, `labels`, `scope.paths` and `sourceAnchors`.
- **The body file holds the body**, at
  `.theurian/knowledge/<namespace…>/<leaf>.<revisionId>.md`
  (`domain/proposal.py::body_relative_path`), pinned by `contentSha256` and
  re-hashed on every load ([ADR-0027](0027-accept-validates-before-it-moves.md);
  `domain/knowledge.py` lines 201–206 raise on a mismatch).
- **SQLite is derived** from both
  ([ADR-0004](0004-sqlite-is-a-derived-artifact.md)).

Front matter *in* a body is not a third home for governance:
[ADR-0019](0019-front-matter-is-data-not-governance.md) makes it data, and
`infrastructure/filesystem/parsers/markdown.py`'s `GOVERNED_FIELDS` warns and
ignores `status`, `trustlevel`/`trust_level`, `sensitivity`, `owner`,
`validfrom`/`valid_from`, `validto`/`valid_to`, `kind`, `namespace`, `acl*` and
`tenant*`.

So the alignment with OKF is narrower than "we already are OKF": the **body**
layer aligns, and the governance metadata is *derivable* from the canonical
store into OKF's front-matter families. That is enough to build an adapter on,
and it is not enough to call the two formats the same thing. It also decides the
direction of every rule below — an exporter may synthesize front matter, and an
importer may never read front matter as governance.

## Decision

### 1. OKF is an adapter at the knowledge layer, in both directions

OKF import and export are **adapters over the knowledge/state layer**. The
migration format is untouched and is not made OKF-compatible; no operation kind,
no schema field and no closed enum moves for either direction.

The consequence of the second premise correction, stated as the rule the two
slices follow:

> **Export MAY synthesize OKF front matter. Import MUST NEVER read OKF front
> matter as governance.**

The asymmetry is [ADR-0019](0019-front-matter-is-data-not-governance.md)'s, read
at its own boundary. ADR-0019 governs *ingestion* — what a Markdown file's front
matter is allowed to mean when Theurian reads it — and its answer is *data,
never governance*. Export is outside that scope: writing `status: stable` into a
file Theurian produced from a migration is a projection of governance, not a
second source of it. Import is squarely inside it: a bundle's
`status: stable`, its `verified:` list and any `theurian_*` key it carries are
the same untrusted front matter ADR-0019 refuses to let govern, and the importer
refuses in exactly the same way — it emits a proposal, and a human sets
governance by merging a migration.

### 2. The export is Index-class, and every byte of the bundle is a function of the exported population

The exported bundle is an **Index-class derivative** under
[ADR-0010](0010-three-layer-knowledge-model.md)'s authority rules: never a record
of truth, never cited as team knowledge, losable without loss, never promoted to
approved knowledge. This is roadmap §9 ADR candidate 8's proposal, accepted, with
OKF as the concrete format.

The artifact says so about itself, at two levels:

- **Every concept file** carries OKF `generated: { by: process:theurian/<version>,
  at: <the revision's own instant> }` (§5.2, §7) and
  `theurian_export_version: 1`. A file that escapes the bundle still names what
  produced it.
- **A bundle-root manifest concept**, `theurian-bundle.md` with
  `type: Theurian Bundle`, carries `theurian_export_version` and
  `theurian_bundle_digest` — a digest over the bundle's own files, the manifest
  excluded, since it is where the digest sits. Its inputs are the bundle and
  nothing else, which is the property that makes it publishable.

`generated.by` names the *exporter*, not an author, because the document is a
projection: §5.2 defines `by` as the actor that produced the current content, and
the content of this file is Theurian's rendering. `generated.at` is the
**revision's own `created_at`** (`domain/knowledge.py`'s `KnowledgeRevision`),
not the time the export ran — §5.2 defines `at` as "the content's last meaningful
change", and revisions are immutable (INV-1), so the revision's instant is
exactly that.

The manifest is a concept document rather than front matter on the root
`index.md` because §8 permits index files no front matter at all, with one
exception: a bundle-root `index.md` MAY carry an `okf_version` key. So the root
`index.md` carries `okf_version: "0.2"` (§12) and nothing else, and the producer
extension permission that makes the `theurian_*` keys conformant (§4.1) applies
to concept documents, which is where they go.

**The invariant that makes the bundle safe to hand to someone. Nothing measures
it today — no export command exists — so it is S2's to pin, and *Compliance*
carries it as owed:**

> **Every byte of the bundle is a function of the exported population and the
> exporter's own version, and of nothing else.** Nothing in it varies with a
> canonical row the bundle does not contain, and nothing in it varies with when
> the export ran.

Both of the properties this ADR is asked for fall out of that one sentence
rather than out of a checklist of excluded fields: the two-corpora equality of
decision 3, and the regeneration determinism the roadmap's Phase F ② exit
criterion names. It also settles a value the roadmap's Phase F ② row and
[#279](https://github.com/theurian/theurian/issues/279) had both specified the
other way — the *stamp the whole-tree `stateHash`* row of the alternatives table
carries the reasoning and the measurement.

### 3. The exported population is exactly the rows the default channel serves

The bundle contains one concept document per knowledge item this deployment
serves **under the default channel**: `approved` status and a sensitivity the
deployment's grant permits. The two predicates are
`domain/enums.py::may_surface` (with `include_unapproved=False`, which admits
`APPROVED` alone out of `SURFACEABLE_STATUSES = {APPROVED, DRAFT, PROPOSED}`) and
`domain/enums.py::may_disclose`, against the set
`application/authorization.py::DISCLOSURE_ORDER` expands from the operator's
declared ceiling — `internal` when none is declared.

The disclosure invariant, in the form this project has found actually closes such
a class — one export against two corpora, not a field-by-field argument:

> **An index that holds withheld documents and an index that never held them must
> produce the byte-identical bundle.**

There is no `--include-unapproved` bundle and no flag that widens this
population. A caller that may not read a row through `search` or `knowledge.get`
may not read it through a bundle either, and the bundle is the easier artifact to
forward.

### 4. Relations export twice — as prose links, and as one namespaced typed key

Theurian's relations are typed: a closed 14-member `RelationType`
(`domain/enums.py` lines 73–89, mirrored by `$defs/relationType` in the migration
schema), of which `INVERSE_RELATIONS` maps two pairs and `ACYCLIC_RELATIONS`
names `{SUPERSEDES, SUPERSEDED_BY, DEPENDS_ON}` — INV-6's `supersedes` and two
more. OKF's
links are untyped by design: §6.1 says the kind of relationship "is conveyed by
the surrounding prose, not by the link itself". Projecting typed edges onto
untyped links loses the type; inventing an OKF link syntax for it would not be
OKF. So each visible relation is exported through **both** channels:

1. **A Markdown link in a generated `## Relations` body section**, grouped under
   a heading naming the relation type — the type conveyed in prose, which is
   exactly what §6.1 asks for, and readable by any OKF consumer with no Theurian
   knowledge. Links are bundle-absolute (a leading `/`), §6.1's recommended form.
2. **A `theurian_relations` front-matter key**, a list of
   `{ type, target, note }` — the typed edge, machine-readable, lossless.
   Conformant because a consumer MUST NOT reject unknown additional front-matter
   keys (§11) and producers MAY add them (§4.1).

The same dual-channel principle governs anchors, one decision lower: OKF's own
slot carries what fits, and a namespaced carrier holds what OKF drops
(decision 7).

**Visibility inherits the both-endpoints gate.** A relation is exported only when
*both* of its endpoints clear the gate of decision 3, each read by the id it
literally names rather than through an alias — the rule
`mcp/tools.py::_relation_is_visible` records, including the two corrections its
docstring carries: gating the target alone published an incoming edge's `note` on
a visible item's response (measured: a `rejected` item's rejection rationale
reached an approved item), and resolving an alias at the gate let a `rejected`
item that was also an alias key clear as the item the alias pointed at (T-21). A
missing endpoint fails closed. One consequence worth naming: since both endpoints
must be in the exported population, **no exported link is broken within the
bundle** — a property of the gate, not of a link check.

The rule already has an application-layer twin, which is the layer an export
command sits in: `application/index_builder.py::_both_ends_visible` asks it of
the set the build's own walk produced. S2 has a shape to follow rather than a
predicate to invent.

**The relation `note` is included.** It is already SEC-11-scanned by both
controls — `propose accept` scans each operation's free text before a migration
lands, and the index build scans the notes this deployment would publish
(`application/index_builder.py::_relation_secrets`) — and, as that function's own
docstring records, `knowledge.get` already serves it verbatim to this same
population. Withholding it from the bundle would protect nothing that is not
already published, while dropping the sentence that says *why* the edge exists.

### 5. The import never manufactures a typed relation

A vanilla OKF bundle's body links are untrusted prose pointing at a closed enum.
**The importer never synthesizes a `RelationType` from a bare Markdown link.**
§6.1 is explicit that the kind lives in the surrounding prose; deriving
`supersedes` from a sentence is a judgement, and a wrong one lands an edge that a
human then has to find and remove.

Where a bundle carries a `theurian_relations` key — a bundle Theurian itself
exported, or one a producer chose to write in this vocabulary — those edges MAY
be offered as `addRelation` operations inside the drafted migration, reviewed by
a human like every other operation in it. A type outside the 14 is refused by the
published schema (`$defs/relationType`) before any of this, so a bundle cannot
widen the enum by asserting a new value.

This keeps two pressures at zero: no garbage edges, and no pressure to extend
`RelationType`, whose compatibility policy is roadmap §9 ADR candidate 3 and is not
decided here.

### 6. The import is an on-ramp to the existing write path, not a new source class

**The import's whole output is a proposal draft through the existing
`ProposalService`** — `draft` for a concept's body and revision,
`draft_from_document` for the operations decision 5 admits
(`application/proposal_service.py`). It adds **no write primitive**, which is
[ADR-0035](0035-interactive-source-curation-is-agent-mediated.md) decision 2's
invariant applied to a second on-ramp, and nothing about it enters the source
layer. How many proposals one bundle becomes is slice S3's question, not this
ADR's.

Everything downstream is unchanged and is the point of choosing this terminus:

- **The approval gate.** proposal → PR → human review → merge → `migrate apply`
  ([ADR-0013](0013-ai-writes-produce-proposals.md)), the only way anything lands.
  FR-I3 routes the write to a proposal file; FR-V4 keeps approval a human act
  recorded as a migration.
- **The accept-time secret scan.** SEC-11's six inputs are scanned at
  `ProposalService.accept` — each body's content, the migration document's own
  author-written fields, the migration file's bytes, its filename, each body's
  landed path and the evidence record's text — `block` by default. Every string
  an untrusted bundle contributes passes through it, synthesized source anchors
  included.
- **A trust ceiling of `INFERRED`.** An imported concept is never proposed at
  `REVIEWED`, which is the tier a human reviewer grants by merging. The precedent
  is `domain/review.py`'s `KnowledgeCandidate`, whose `trust_level` is an
  `init=False` field fixed at `TrustLevel.INFERRED` so the generator cannot raise
  it, beside a `CandidateStatus` with no `auto_approved` member.
- **INV-8 holds by construction.** The bundle's own identity is always recorded
  as a source anchor, so a proposal from an import is never unattributed
  (`domain/knowledge.py` lines 208–215). An OKF `sources[]` entry becomes an
  additional anchor when its `resource` is a followable URI or path; §5.1 allows
  `resource` to be a *scope descriptor* instead ("all queries in BigQuery project
  X"), and a scope descriptor is not a `sourceUri` — the schema would accept the
  string, and recording it as a source URI would misstate what it is.

T-3 — an agent acting on instructions injected into indexed content — is the live
threat on this direction, and it is guarded by exactly the controls ADR-0035
decision 5 names: the source anchors are visible on the draft, the raw bundle
stays viewable beside it, and the final artifact is a reviewable pull request.

### 7. The governance projection is lossy, one-way, and enumerated

| Theurian | OKF slot | Notes |
| :-- | :-- | :-- |
| `kind` (11 members) | `type` (§4.1, required) | Exported verbatim: `architecture`, `decision`, … Type values are not centrally registered and consumers MUST NOT reject unknown ones (§4.1, §11). |
| `title` | `title` (§4.1) | |
| item id | the concept's path in the bundle (§2 Concept ID) | Derived from the **item id**, never from `namespace` — see below. |
| item id | `theurian_item_id` | So a consumer cites the id rather than reconstructing it from a path. |
| `namespace` | `theurian_namespace` | Governed metadata, and not a path component. |
| `status` | `status` (§5.4) | Only `approved` is exportable (decision 3), so `stable` is the only value emitted. |
| `status` | `theurian_status` | The canonical value, uncollapsed. |
| `labels` | `tags` (§4.1) | A direct fit: short strings for cross-cutting categorization. |
| `owner` | `theurian_owner` | |
| `sourceAnchors[]` | `sources[]` (§5.1) | `sourceUri` → the entry's required `resource`; the remaining anchor fields ride the same entry as `theurian_anchor: { provider, repository, commit_sha, blob_sha, file_path, line_start, line_end, external_id }`. |
| `validTo` | `stale_after` (§5.5) | Emitted only when the revision records one. |
| `created_at` | `generated.at` (§5.2) | Decision 2. |
| `trustLevel` | `theurian_trust_level` | Not `verified` — see below. |
| `sensitivity` | `theurian_sensitivity` | The label travels with the document. |
| relations | body links + `theurian_relations` | Decision 4. |
| `revisionId` | `theurian_revision_id` | So a reader can cite the exact revision back. |

Two OKF recommended keys are deliberately absent: `resource`, which §4.1 says is
"absent for concepts that describe abstract ideas rather than physical
resources", and `description`, for which Theurian holds no counterpart — so
`index.md` entries carry title and link without the description §8 says they
SHOULD include.

**`stable` is the only OKF `status` the export can emit today, and the rest of
the mapping is reserved rather than live.** `deprecated` and `superseded` are not
in `SURFACEABLE_STATUSES` at all, so decision 3's population never contains one;
whether they become historically disclosable is roadmap §9 ADR candidate 2, which
changes that constant and is a Phase D decision. The mapping is recorded here so
that the slice which takes it has nothing to re-derive — `deprecated` and
`superseded` both project to OKF `deprecated` (§5.4), collapsing a distinction
`theurian_status` keeps — and S2 implements the `approved → stable` row alone. An
export that emitted a `deprecated` concept today would be a disclosure defect,
not a mapping gap.

**The path is derived from the item id, and `namespace` is never a path
component.** `domain/proposal.py::body_relative_path` already refuses to use
`namespace` for this, and its reason transfers unchanged to a bundle: `namespace`
is free text bounded only by a control-character exclusion, so `../` is a
spellable value in a field nothing treats as a path, while an `ItemId` is dotted
lowercase kebab-case and cannot express a traversal at all. A bundle is a
directory tree, so this is the difference between a contained export and one that
writes outside its own root.

**OKF `verified` is not emitted, and that is a decision rather than an
omission.** §5.3 derives a consumer's trust tier from `verified` alone: no key
means *unverified*, a non-`human:` actor means *machine-confirmed*, and a
`human:<id>` actor means *human-reviewed*. Theurian's approval is a pull-request
merge, and **the canonical store does not hold the approving identity** —
recording the merge commit at `migrate apply` is roadmap §9 ADR candidate 10, not
yet taken. `KnowledgeRevision.author` is the *authoring* identity, self-declared
in the migration and not authenticated; emitting it as a verifier would launder
authorship into review, and routing it through `process:` would claim a machine
confirmation that never ran. Both would move a consumer's trust tier on a fact
Theurian cannot supply. The honest value is no value, and OKF is explicitly built
to be read that way: absence means unverified, and a consumer MUST NOT reject a
concept for it (§5.3, §11).

**Dropped without a slot**, enumerated so that "lossy" is a list rather than an
adjective:

| Dropped | Why it has no home |
| :-- | :-- |
| The immutable revision history | The bundle carries the current revision only; `theurian_revision_id` names which. |
| `contentSha256` | The exported body is a projection — it gains the generated `## Relations` section — so a digest of the canonical body sitting beside it would be unverifiable against the file it is on. |
| `tenantId`, `aclGroup` | Fixed today and unenforceable otherwise: `application/migration_engine.py` refuses a revision naming a tenant other than `local` or an ACL group other than `default`. Exporting a field that carries no information invites a consumer to route on it. |
| Evidence records | The evidence plane has no OKF counterpart; as prose it would read as content. |
| `scope.paths` | Glob patterns over a repository the bundle's recipient may not have. |
| `validFrom` | OKF's `stale_after` has no opening twin (§5.5). |
| `author` | OKF's only actor slots are `generated.by` and `verified[].by`; decision 2 gives the first to the exporter, and the second is the field above. |

**One-way, and stated as a rule:** nothing reads this projection back as
governance. An import of a bundle Theurian exported is decision 6's path like any
other — a proposal a human reviews.

## Consequences

### Positive

- **Theurian's governed knowledge becomes portable into an ecosystem**, in a
  format whose consumers are obliged to tolerate the optional families Theurian
  does not supply and the extension keys it adds (§11), so the lossy projection
  is a conformant bundle rather than a degraded one.
- **The Phase F ② governance question is settled with a format attached.** ADR
  candidate 8 asked whether a derived context package needs a new
  classification. It does not: Index-class, losable, asserting no truth — and now
  with the concrete artifact named, so S2 implements rather than re-decides.
- **The disclosure story is one sentence, not a field audit.** Decision 2's
  invariant covers a published field, which rows reach the bundle, a statistic
  over rows the recipient may not read, and a duration-shaped value like a
  wall-clock stamp — in one statement a test can drive.
- **No vendor dependency is taken on.** OKF has no registry, no central authority
  and no required tooling, so adopting it adds no hosted service and no vendor
  name in domain or application code — [ADR-0009](0009-no-llm-vendor-lock-in.md)'s
  rules 5 and 6 hold. Its rules are about model and infrastructure providers
  rather than interchange formats, so what carries over is the principle, and the
  Index-class framing is what makes it airtight: the bundle is losable, so
  nothing of the canonical record depends on OKF at all.
- **Enum pressure stays at zero.** Decision 5 means the 14-member `RelationType`
  is neither widened nor filled with guesses, so roadmap §9 ADR candidate 3 stays
  unforced.

### Negative

- **The bundle is a second copy of approved knowledge with no purge machinery.**
  See the first residual below. This is accepted, not solved.
- **A recipient cannot reconstruct governance**, by design. The projection is
  lossy and one-way; the dropped list above is the price, stated in full so that
  nobody discovers it later by needing one of the rows.
- **The recorded `owner` string travels with the bundle.** It is the accountable
  party the governance record names, which is what makes it worth exporting — and
  a team that has set it to a person's handle is exporting that handle. An
  operator handing a bundle outside the organisation should know this.
- **Regeneration is how staleness is detected**, because decision 2 keeps the
  whole-tree `stateHash` out of the bundle. Comparing one stamped value would
  have been cheaper than regenerating and comparing digests. Regeneration is
  deterministic and local, so the cost is small and it is paid by the operator,
  never by the recipient.
- **A mapping change is a visible break.** Any later change to decision 7's table
  bumps `theurian_export_version` and changes every exported byte, so a consumer
  reading `theurian_*` keys must follow. That is the point of the key existing.

### Neutral

- **Nothing ships here.** No schema, no enum, no operation kind, no MCP tool;
  `knowledge.getContext` stays `Planned` in
  [`docs/protocol/mcp-tools.md`](../protocol/mcp-tools.md), and `knowledge.trace`
  with it.
- **The export command's surface is S2's.** This ADR fixes the artifact and the
  population, not the flags, the help text or the output format — those are a
  wire contract reviewed in their own slice.
- **The `theurian_*` key spellings are fixed here** so S2 has nothing to invent:
  `theurian_export_version`, `theurian_bundle_digest`, `theurian_item_id`,
  `theurian_revision_id`, `theurian_status`, `theurian_namespace`,
  `theurian_owner`, `theurian_trust_level`, `theurian_sensitivity`,
  `theurian_relations`, `theurian_anchor`. Snake case, matching OKF's own key
  style (`okf_version`, `stale_after`, `last_modified`).

## What this does not close

1. **The purge gap, which is real and accepted.** A withdrawal produces a new
   index build and swaps the pointer
   ([ADR-0024](0024-a-purge-is-a-build.md)); **an exported bundle on disk has no
   equivalent, and a file survives a withdrawal the index has already honoured.**
   Three things bound it rather than fix it: the bundle is Index-class, so
   deleting it loses nothing; `theurian_bundle_digest` makes staleness
   *detectable* by regenerating and comparing; and the guidance the export ships
   with is to regenerate rather than to edit. What none of that reaches is a copy
   already handed to someone. Recorded as accepted, at the same standing as any
   other derived artifact a user has copied out of the project.
2. **Round-trip is not identity.** Export followed by import produces a
   *proposal*, never a restoration: new revision ids, a trust ceiling of
   `INFERRED`, and a human merge in between. Anyone reading the two directions as
   a backup mechanism is reading them wrong, and decision 6 is why.
3. **OKF is at v0.2 and may move.** The bundle root declares the version it
   targets (§12), and a spec bump is a new decision — a minor bump is additive by
   §12's own rule, and a major one may rename required fields or reserved
   filenames, which would change decision 7's table and
   `theurian_export_version` with it.
4. **The T-3 threat-model entry for the import path**, owed by slice S3 when the
   mechanism lands, alongside ADR-0033's and ADR-0035's owed entries. A prose
   obligation with no test today, recorded here rather than dressed as
   discharged.
5. **Whether an imported concept is a fair reading of its bundle.** That is
   FR-V4's human at the pull request, the same authority ADR-0033 and ADR-0035
   give the question. Nothing here proposes a machine that would replace them.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Make the migration YAML OKF-compatible** | A layer mismatch: OKF is state, the migration is an operation log ([ADR-0005](0005-yaml-knowledge-migrations.md)), and the whole Phase B governance surface — SEC-11's accept scan, SEC-12's input validation, the committed-at-`HEAD` check, the v1 operation gate — is bound to that log. [#705](https://github.com/theurian/theurian/issues/705) names this as the tempting framing and rules it out; this ADR agrees. |
| **Export an ad-hoc `AGENTS.md`-style summary**, as Phase F ② originally worded it | An ad-hoc format has no consumers, no conformance rules, and no obligation on a reader to tolerate what we cannot supply. OKF costs nothing extra — the artifact is Markdown with front matter either way — and §11's permissive conformance is precisely what a lossy projection needs. |
| **Stamp the whole-tree `stateHash` in the bundle**, as Phase F ② and [#279](https://github.com/theurian/theurian/issues/279) both say | `domain/state.py`'s `StateInputs` covers *every* migration in the working tree plus every referenced body checksum ([ADR-0016](0016-state-hash-covers-the-working-tree.md)), so the value moves when a `rejected`, `draft` or above-ceiling row moves. Putting it in a shippable artifact would carry a statistic over rows the recipient may not read, and would falsify decision 3's two-corpora equality by construction — the bundle would differ across the two corpora in exactly one field. The staleness need it served is met by a digest over the bundle's own files. Whether the *command* reports the state hash locally, to its operator, is S2's and is untouched by this. |
| **Emit OKF `verified` from Theurian's approval** | §5.3 keys the trust tier off this field, and the approving identity is not in the canonical store (roadmap §9 candidate 10). A `human:<id>` built from `KnowledgeRevision.author` launders authorship into review; a `process:` actor claims a machine confirmation that never ran. Absence is a meaning OKF defines, and it is the true one. |
| **Synthesize typed relations from OKF body links on import** | §6.1 puts the relationship kind in the surrounding prose, so filling a closed 14-member enum from a bare link is guesswork, and every wrong guess is an edge a human must find and remove. Untyped links do not need a new type; they need a human. |
| **Treat OKF import as [#223](https://github.com/theurian/theurian/issues/223)-class external-source ingestion, gated on the trust model** | #223 governs *connectors* that snapshot external systems into the source layer without passing a pull request; its gate exists because that path bypasses review. This import produces only a reviewable proposal and reaches approved state through the same merge as everything else, so it inherits ADR-0013's gate rather than needing #223's. Ingesting a bundle as a governed *source* remains #223's, and remains out of scope. |
| **Reuse `KnowledgeCandidate` for imported concepts**, as #705's "bundle → source → KnowledgeCandidate → proposal" sketch has it | `domain/review.py`'s `PromotionGate` requires seven review-shaped signals — `pull_request_merged`, `thread_resolved`, `fix_commit_present`, `not_dismissed_or_outdated`, `ci_successful`, `generalizable`, `has_evidence` — and `KnowledgeCandidate.__post_init__` raises when the gate is unsatisfied. An OKF bundle satisfies none of them, so reuse means fabricating review facts or weakening the gate for every candidate, review-derived ones included. What the type is kept for is its *precedent*: the `INFERRED` ceiling of decision 6. |
| **Extend `RelationType` so OKF's untyped links have a home** | Enum extension is roadmap §9 ADR candidate 3, which owes a compatibility policy first; and the problem is not a missing member. An untyped link is untyped, and decision 5 is the answer to it. |
| **Derive the bundle path from `namespace`** | `namespace` is free text where `../` is spellable; `domain/proposal.py::body_relative_path` already refuses it for exactly this reason, and a bundle is a directory tree, so the failure is writing outside the bundle root. |

## Compliance

**This ADR ships no behaviour, so it names no test of its own.** What follows
separates the enforcement that already exists from what slices S2 and S3 owe.

Rests on enforcement that already holds:

- **The serve gate of decision 3 is the shipped predicate pair.**
  `domain/enums.py::may_surface` admits `APPROVED` alone when
  `include_unapproved=False`, and `may_disclose` answers the sensitivity axis
  against the expanded ceiling in `application/authorization.py`. The export does
  not define a population; it reuses this one.
- **The both-endpoints relation gate exists and carries its own history.**
  `mcp/tools.py::_relation_is_visible` gates both ends, reads each by the id it
  literally names, and fails closed on a missing endpoint.
- **A fabricated relation type cannot land.** `$defs/relationType` in
  `schemas/migrations/migration.schema.json` enumerates the 14 members, so an
  imported operation naming anything else is refused at validation, before any
  human sees it.
- **An unattributed revision cannot be constructed.** `domain/knowledge.py` lines
  208–215 raise unless a revision has a source anchor or the
  `authored-in-theurian` label (INV-8).
- **An unenforceable tenant or ACL group is refused.**
  `application/migration_engine.py` refuses a revision naming a tenant other than
  `local` or an ACL group other than `default`, which is why decision 7 drops
  both.
- **The approval gate the import terminates in** is
  [ADR-0013](0013-ai-writes-produce-proposals.md)'s, unchanged, with SEC-11's
  six-input scan at `ProposalService.accept` and
  [ADR-0034](0034-migrate-apply-enforces-the-merge.md)'s committed check beyond
  it.

Still owed, with the slice that will satisfy it:

- **Slice S2 (export):** the two-corpora equality of decision 3 — one export run
  against two corpora, one holding the withheld rows and one that never did,
  asserted to produce the byte-identical bundle; the determinism of decision 2 —
  two runs against one canonical state producing byte-identical output, which is
  Phase F ②'s exit criterion; and a pin that the bundle path is derived from the
  item id, so a crafted `namespace` cannot reach a path component.
- **Slice S3 (import):** that a bundle carrying only bare Markdown links yields a
  drafted migration with **no** `addRelation` operation (decision 5); that an
  import lands only under a proposal directory and reaches no approved state
  (decision 6, the same shape ADR-0032 and ADR-0035 owe); that the proposed trust
  level is capped at `INFERRED`; and that a bundle with no usable `sources[]`
  still produces a proposal satisfying INV-8 through the bundle's own anchor.
- **Slice S3, prose:** the T-3 threat-model entry for the import path, named in
  *What this does not close* item 4.
