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

**Two of #705's further framings are refined rather than followed, and the
arguments for both live below rather than here**, so that a reader of the
proposal can see where it was corrected instead of finding an ADR that quietly
diverged from it. The proposal routes the import "through the external-source
ingestion family ([#223](https://github.com/theurian/theurian/issues/223))" and
sketches it as "OKF bundle → source → `KnowledgeCandidate` → proposal". Decision
6 takes the first — the import is an on-ramp to the write path that already
exists, not a #223-class connector, and the boundary that keeps those apart is
stated there — and the alternatives table takes the second, with the measurement
that `KnowledgeCandidate` is a review-shaped type an OKF bundle cannot satisfy.
Counting the front-matter premise above, **this ADR corrects #705 in three
places and agrees with it everywhere else**, its layer correction included.

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

- **Every concept document projected from a knowledge row** carries OKF
  `generated: { by: theurian/<version>, at: <the revision's own instant> }`
  (§5.2, §7), `theurian_export_version: 1` and the `theurian_content_type` of
  decision 7. A file that escapes the bundle still names what produced it. These
  are per-row rules and the manifest is not a row, so they do not reach it.
- **A bundle-root manifest concept**, `theurian-bundle.md` with
  `type: Theurian Bundle`, carries `theurian_export_version`,
  `theurian_bundle_digest` and the holder notice below — and **no `generated`
  block at all**. It has no revision, so it has no revision instant to carry, and
  export time is forbidden by the invariant below; a `generated` with a `by` and
  no honest `at` would be worse than none. For the same reason it carries no
  `theurian_content_type`: it projects no body.

  `theurian_bundle_digest` is a digest over every file the export writes —
  concept documents, index files and the body sidecars of decision 7 alike —
  with the manifest excluded since it is where the digest sits. Its inputs are
  the bundle and nothing else, which is the property that makes it publishable.

**The manifest carries a holder-facing notice, and it is mandatory rather than
courteous.** In plain prose in its body: *this bundle is a point-in-time,
Index-class copy of approved knowledge; no later withdrawal, correction or
secret removal reaches it; regenerate from the source deployment rather than
trusting it as current.* **It is fixed text and names no deployment.** An
earlier draft allowed the exporting deployment's identity "if deterministic",
offering a project id as qualifying; it does not. A project id is neither served
row data nor a constant of the exporter, so admitting it would be a sixth
widening owing its own justification — and the notice does its whole job without
it, since whoever holds the bundle knows where they got it. Fixed text also
needs no argument about what varies between runs. This is the
one control in the whole residual that travels *with* the artifact; the three in
*What this does not close* item 1 all act on the operator's side, where the copy
is not.

`generated.by` names the *exporter*, not an author, because the document is a
projection: §5.2 defines `by` as the actor that produced the current content, and
the content of this file is Theurian's rendering. `generated.at` is the
**revision's own `created_at`** (`domain/knowledge.py`'s `KnowledgeRevision`),
not the time the export ran — §5.2 defines `at` as "the content's last meaningful
change", and a revision is immutable, so the row's content last changed when the
revision landed.

**The relation projection is deliberately outside that "meaningful change", and
saying so is the honest form of this rule.** `addRelation` and `removeRelation`
mint no revision, so the `## Relations` section and `theurian_relations` can
differ between two exports whose `generated.at` is identical. The section
describes the graph *as of export*, and the alternative — taking `at` as the
maximum over the row's relation instants — is rejected on a measured property of
the store: `removeRelation` **deletes** the `knowledge_relations` row rather than
tombstoning it (`infrastructure/sqlite/store.py::remove_relation`, a bare
`DELETE FROM knowledge_relations`), so that maximum is non-monotonic and removing
an edge would move `generated.at` *backwards*. A timestamp that can run backwards
is worse than one with a stated scope.

**The actor form is §7's tool form, `theurian/<version>`**, not `process:`. §7
gives three spellings — `<producer>/<version>` for agents and tools, `human:<id>`
for a person, `process:<id>` for an automated process — and an earlier draft here
hybridised the first two into `process:theurian/<version>`, which is none of
them. The export is a tool a person runs, so the tool form is the accurate one;
what matters for a consumer either way is that it is not `human:`, which is what
§5.3's trust tiers key on.

The manifest is a concept document rather than front matter on the root
`index.md` because §8 permits index files no front matter at all, with one
exception: a bundle-root `index.md` MAY carry an `okf_version` key. So the root
`index.md` carries `okf_version: "0.2"` (§12) and nothing else in its front
matter, and the producer extension permission that makes the `theurian_*` keys
conformant (§4.1) applies to concept documents, which is where they go.

**The index files, fixed here because §8 leaves their shape to the producer and
an unfixed shape is a byte nobody bounded.** §8 says an `index.md` MAY appear in
any directory and gives its body as sections of bulleted entries, each a
bracketed title linking its target.

- **One index in every directory the bundle contains**, not only in those
  holding a concept. The narrower rule breaks the walk it exists to support: an
  item id of `architecture.auth.session.policy` puts its concept at
  `architecture/auth/session/policy.md`, and `architecture/` then holds nothing
  but a subdirectory. Skipping its index leaves it unlisted by the root, and
  everything beneath it unreachable by following indexes — which is the
  progressive disclosure §8 is for. Every directory exists only because a
  concept sits somewhere beneath it, so "every directory" is the rule that
  matches the shape.
- **One section per index**, headed `# ` plus the directory's own path
  component. The root has no path component, so its heading is the exporter
  constant `# Theurian Bundle`, matching the manifest's `type`.
- **Entries are the directory's own concept documents** — the manifest included,
  at the root — each a bracketed title linking its bundle-absolute path, **and
  one entry per immediate subdirectory**, titled by its path component and
  linking to it with a trailing slash, the form §8's own example uses. **The
  manifest's own entry is titled `Theurian Bundle`**, a fixed string like the
  root heading rather than a walked one: the manifest projects no row, so it
  carries no `title` for an entry to draw on, and leaving it unsaid is a byte
  nobody bounded.
- **All entries in one list, ordered bytewise by their bundle-absolute path.**
  One total order over both kinds rather than two rules, which is what the
  determinism pin needs.
- **No description.** Entries SHOULD carry one (§8) and Theurian holds no
  counterpart, as decision 7 records.

**The export writes no `log.md`.** §9 makes it a chronological history of
updates, which needs a per-change instant; the invariant below forbids any value
that varies with when the export ran, and a revision's own instant is not a
record of *this bundle's* updates. A bundle that needs history is regenerated,
and the deployment it came from has the real one.

**The invariant that makes the bundle safe to hand to someone. Nothing measures
it today — no export command exists — so it is S2's to pin, and *Compliance*
carries it as owed:**

> **Every byte the export writes is a function of the exported population and
> the exporter's own version, and of nothing else.** No byte varies with a
> canonical row the bundle does not contain, and none varies with when the
> export ran.

The subject is the *bytes of the files*. Filesystem metadata — mtimes, directory
enumeration order, permissions — is outside it and deliberately so: a copy, a
tarball or a clone rewrites all three, and a battery that compared them would
fail for reasons that have nothing to do with the projection. The batteries
compare file contents.

**A function of a set is not yet a sequence, so every ordering the bundle
contains is fixed here.** Without this the determinism pin has nothing to hold:
two runs over one state could emit the same values in a different order and both
satisfy the sentence above.

- **The digest** is over `(relative POSIX path, sha256 of the file)` pairs,
  sorted bytewise by path.
- **`index.md` entries** are ordered bytewise by their bundle-absolute path, one
  list over concepts and subdirectories alike — the index rule above states it
  once, and this is the same rule, not a second one.
- **`theurian_relations` and the `## Relations` section** are ordered by
  `(relation type, target item id)`. Neither is a stored order: `list_relations`
  answers a query, and the section groups by type in any case.
- **`sources[]` and `tags` keep their in-row order**, and that order is a
  property of the row rather than of the query. Measured: `labels` is a JSON
  array column round-tripped by `json.dumps`/`json.loads`
  (`knowledge_revisions.labels`), so the sequence the migration recorded is the
  sequence read back; anchors come back `ORDER BY anchor_id`, an
  `INTEGER PRIMARY KEY AUTOINCREMENT`, so the read is a total order that
  reproduces the order the revision recorded them in. Neither reaches the export
  through a query with no `ORDER BY`.

Both of the properties this ADR is asked for fall out of that one sentence
rather than out of a checklist of excluded fields: the two-corpora equality of
decision 3, and the regeneration determinism the roadmap's Phase F ② exit
criterion names. It also settles a value the roadmap's Phase F ② row and
[#279](https://github.com/theurian/theurian/issues/279) had both specified the
other way — the *stamp the whole-tree `stateHash`* row of the alternatives table
carries the reasoning and the measurement.

#### The bundle's byte sources, enumerated

**A universal is only as good as the enumeration under it, and twice now this
one was not.** The first draft of decision 7's disclosure bound was refuted by
fields outside the table it walked; the sidecar rule was then refuted by a byte
source — an author-written filename suffix — outside the front-matter keys
entirely. Both were true-sounding closure arguments over a population nobody had
listed. So the invariant above is closed the only way that survives: **every
byte of the bundle belongs to exactly one family below, and each family states
its bound and what checks it.**

| Family | Bound | Check |
| :-- | :-- | :-- |
| **Paths and names** | The stem from the item id's own dotted segments — never the `namespace` field, which decision 7's containment argument bars from paths — a sidecar's extension from `contentType`, bounded to `{.json, .yaml, .txt}`, and where the positional reserved-name escape fires | For the stem and the sidecar extension, the emission walk, since both derive from walked emissions (`itemId`, `contentType`); the structural names they are spelled with — a concept document's own `.md`, the three reserved names, the `_item` suffix — are the seventh family's. S2's collision and escape pins |
| **Concept front matter** | The measured served union plus the recorded widenings (decision 7), plus the two exporter constants: `theurian_export_version`, the mapping version of decision 7's table, and `generated.by`, the tool actor of §7 — both admitted by the invariant's own *and the exporter's own version* clause. This bounds the family's **front-matter keys**, not every constant spelling in the bundle: the document's structure around them is the seventh family's | The inventory walk pin, landing with this pull request |
| **Concept body** | When `contentType` is `text/markdown`, the canonical body; otherwise the generated sidecar-link paragraph, whose link text *and* target are both the sidecar's own derived filename — `theurian_body_file`'s derivation and not a second one, the stem from the item id and the extension from `contentType`, both served (decision 7). Either way, plus the generated `## Relations` section, which renders only the served relation triple `{type, target, note}` of relations visible at both endpoints — link text included, being the triple's `target` (decision 4) — in the stated `(type, target)` order. The frame around both — the heading, and the fixed wording the link sits in — is the seventh family's | Prose here; S2's bytes battery |
| **Sidecar bytes** | The snapshot's `body` column, byte for byte | S2's bytes-equal pin |
| **Index files** | Per decision 2 above: one per directory, a single section headed by the directory's path component, entries of title and bundle-absolute path drawn from walked emissions — a subdirectory entry's title being that path component, derived from item ids rather than from any walked `title` — path-ordered, no description | Prose here; S2's determinism battery |
| **The manifest** | Fixed text (`type`, the holder notice), `theurian_export_version` as a constant, and `theurian_bundle_digest` as a function of the bundle's own files in the stated path order | The manifest field-set paragraph above; S2's determinism battery |
| **Bundle-structural constants** | **Every byte the exporter renders that projects no row value.** The class bound is that each is a constant of the exporter version: its spelling is fixed before any row is read, so no member varies with a corpus or with a run. Enumerated — the concept skeleton (the front-matter fences, the front-matter key order and the serializer that renders it, the `## Relations` heading, the fixed wording the sidecar link sits in); a concept document's own `.md` extension; the reserved names `index.md`, `log.md` and `theurian-bundle.md`, and the `_item` escape suffix; the index entry syntax — the list marker `*`, then the bracketed title, then the parenthesized bundle-absolute path, §8's own form — and a subdirectory entry's trailing slash; the relations section's own list marker and the heading level its per-type sub-headings sit at; every index heading's `#` marker, the root's whole heading `# Theurian Bundle` and the manifest's index entry title of the same fixed string; `okf_version: "0.2"` on the root `index.md`, fixed by this ADR from the spec version it targets; and the absence the no-`log.md` rule names. The quoting a particular value draws from the serializer is that value's; which serializer runs, and in what key order, is not | Prose here; S2's determinism battery and decision 3's two-corpora battery, which quantify over every byte of the bundle and so redden on a member of this family that varied. Neither can see a member missing from the enumeration — the paragraph below says why |

**What the batteries close, and what only the enumeration closes.** S2's
determinism battery and decision 3's two-corpora battery quantify over every byte
of the bundle, so they hold the invariant itself in the two directions it names:
no byte varies with a withheld row, and none varies with when the export ran.
That is what checks the seventh family's class bound, and it is **not** a proof
of the partition. A byte belonging to no family at all is still corpus-invariant
and still deterministic — which is exactly what the concept document's `.md`
extension, the index entry's list marker and `okf_version` were, sitting outside
every bound, and neither battery would have said anything about any of them once
S2 lands it. **The partition is closed by reading this table; what the pins hold
is the enumeration's shape** — the seven family names, and every row stating both
a bound and a check — and the ratchet below is what keeps the reading current.

**Row text rendered into structural syntax is escaped, at both site kinds, and
the exact rule is S2's.** The strings that reach those positions are bounded by
*length alone*: `schemas/migrations/migration.schema.json` gives `title` a
`maxLength` of 300, `owner` 200, each `labels` item 64 and an `addRelation`
`note` 1000, and **none of the four carries `namespace`'s control-character
pattern** `^[^\u0000-\u001f\u007f]*$`. A newline is therefore a spellable
character in an approved row's title, and it lands in two different kinds of
position:

- **YAML front matter.** Every front-matter value is emitted through a YAML
  serializer that quotes and escapes, so a newline inside a title terminates no
  value and opens no key. The concrete failure without it: a title ending in a
  line break followed by `theurian_sensitivity: public` forges a governance key
  in a distributed bundle, and the row asserts its own sensitivity label to
  every consumer that reads front matter.
- **Markdown.** An index entry's title and a relation line's `note` land where
  Markdown means something: a title carrying a bracket followed by a parenthesis
  closes the entry's link early, and a note beginning with a run of `#` reads as
  a heading and splits the `## Relations` section. Both are rendered with
  Markdown-syntax escaping.

Neither site may be forged from row text. What each escape covers, and the pin
that drives **both**, are S2's — *Compliance* carries it as owed.

**The ratchet, in prose, so the next design change cannot repeat the last two:**
a new byte source takes a row in this table *first*. Not a bound widened to
admit it, not a sentence elsewhere describing it — a row, with its own bound and
its own check. The seventh family is what that discipline produced on its first
run: enumerating the six that were drafted turned up `okf_version` and the root
heading sitting in no family at all, benign constants that no bound reached.
Finding them is the exercise working; a bound quietly stretched to cover them
would have been the same defect a third time.

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

**The export reads one canonical snapshot**, a single connection and transaction
over the active state, and every row, relation and body in a bundle comes from
that one read. The invariant in decision 2 does not reach this: it bounds what a
byte may be a *function of*, and says nothing about a withdrawal landing between
the twentieth row and the fortieth. Without the rule, a `migrate apply` running
during a long export could leave a bundle straddling two states — a withdrawn
row present as a concept, absent from the index file, and pointed at by a
relation whose other end was gated under the newer state. That is the state and
lifecycle family, and it is the family a published index build already answers
with a pointer swap ([ADR-0024](0024-a-purge-is-a-build.md)); the export answers
it by reading once. S2 owes the pin.

**"Every row, relation and body" is exact, and bodies are the part worth
checking.** A body is not fetched from `.theurian/knowledge/` at export time: it
is the `body` column of `knowledge_revisions`, beside `content_type`, and the
store reconstructs a whole `KnowledgeRevision` from those columns. So the
transaction covers the bundle's contents entirely, with no filesystem read
running beside it and no second consistency rule to state. The file on disk is
tied to that column by the `contentSha256` re-check at load
(`domain/knowledge.py` lines 201–206), which belongs to the path that built the
snapshot.

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
   **The link's text is the target's item id** — the `target` of the same triple
   — so the whole section renders from `{type, target, note}` and nothing else.
   The alternative was the target's *title*, which is served and would read
   better; it is rejected because it couples this section's bytes to a second
   row's field for a cosmetic gain, and widening a bound for cosmetics is how
   the bound stops being checkable. A consumer wanting the title follows the
   link and reads the concept's own front matter.
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
the set the build's own walk produced. **What makes that form safe is where its
set comes from** — the walk's own visible set, built from rows read by the id
each literally names — so it inherits the alias property rather than restating
it. An export that rebuilt the same shape over an *alias-resolving* lookup would
reintroduce T-21 while looking identical on the page, which is why the source of
the set is stated here and not left for S2 to infer.

**The relation `note` is included.** Two SEC-11 controls already read it, and
they are not the same kind of thing: `propose accept` **enforces** — it scans
each operation's free text before a migration lands and refuses under the default
`block` policy — while the index build **detects**, scanning the notes this
deployment would publish (`application/index_builder.py::_relation_secrets`) and
reporting rather than refusing, because by then the content is already served.
The enforcement is the accept gate; the index build is the second look. And, as
that function's own docstring records, `knowledge.get` already serves the note
verbatim to this same population. Withholding it from the bundle would protect
nothing that is not already published, while dropping the sentence that says
*why* the edge exists.

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

**The input is a local directory the operator names, and the importer fetches
nothing.** No URL, no network call, no credential: the bundle is already on disk
when the import runs, put there by whoever cloned, unpacked or copied it. **A
remote OKF fetch — pulling a bundle from a URL or a registry — is out of scope
and is [#223](https://github.com/theurian/theurian/issues/223)-class**, because
fetching is what makes something a connector: it would carry
[T-7](../security/threat-model.md)'s fetch-control family (SEC-10's repository
allowlist, the scheme allowlist and private-network rejection — the last two
owed on the non-`gh` path to
[#429](https://github.com/theurian/theurian/issues/429)) on top of #223's
trust-model gate, and neither obligation is discharged by anything here.

This boundary is what keeps decision 6's classification stable rather than
rhetorical. An on-ramp that fetches is a connector wearing the wrong name: the
word "on-ramp" would quietly absorb a network surface, and the controls that
surface owes would be owed by an ADR that never mentioned them. A local
directory has no such obligations, which is exactly why the line is drawn at the
filesystem and stated here rather than left to S3.

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
  additional anchor when its `resource` is **syntactically a URI or a relative
  path**; §5.1 allows `resource` to be a *scope descriptor* instead ("all queries
  in BigQuery project X"), and a scope descriptor is not a `sourceUri` — the
  schema would accept the string, and recording it as a source URI would misstate
  what it is. The test is syntactic and nothing more: **the importer does not
  follow the reference, and performs no reachability check on it** — that would
  be the fetch the boundary above forbids, and §6.1 in any case tells consumers
  to tolerate a reference that resolves to nothing.

**Every path a bundle names is resolved and contained before it is read.**
`theurian_body_file`, and any §6.2 or §10.2 file reference the importer chooses
to read, must resolve **under the bundle root after symlink resolution**, or
that reference is refused — the reference, not the import, so a bundle with one
bad path still yields a proposal for everything else. **The root is resolved
first, not only the reference:** a bundle unpacked under a symlinked directory
— `/tmp` on macOS is one — would otherwise fail every containment check it
should pass. This is `security/paths.py::resolve_within_root`, which already
resolves both sides and compares them with `is_relative_to`, with
`assert_no_symlink_escape` beside it; S3 calls those rather than rebuilding the
comparison. The bundle is untrusted input that arrived as a directory someone
unpacked, which makes it exactly the shape SEC-7 and T-4/T-5 already name: a
`../` in a front-matter value, or a symlink inside the tree pointing at
`~/.ssh/id_rsa`, would otherwise read a file outside the bundle and land its
contents in a proposal a human is about to approve. S3 owes both pins — the
escape and the symlink.

**A refused reference is recorded as it was written** — the front-matter key and
the literal string the bundle carried — and **never as the path it resolved
to**. The resolved form names directories on the machine that ran the import,
and the record of a refusal ends up in a proposal draft that is committed and
pushed for review: printing the target would carry the operator's filesystem
layout into a public pull request, which is the disclosure T-25 closed on the
MCP surface and which has no reason to reopen here. S3 owes this pin beside the
other two.

T-3 — an agent acting on instructions injected into indexed content — is the live
threat on this direction, and it is guarded by exactly the controls ADR-0035
decision 5 names: the source anchors are visible on the draft, the raw bundle
stays viewable beside it, and the final artifact is a reviewable pull request.

### 7. The governance projection is lossy, one-way, and enumerated

| Theurian | OKF slot | Notes |
| :-- | :-- | :-- |
| `kind` | `type` (§4.1, required) | Exported verbatim: `architecture`, `decision`, … Type values are not centrally registered and consumers MUST NOT reject unknown ones (§4.1, §11). |
| `title` | `title` (§4.1) | |
| `contentType` | `theurian_content_type`, always | On every concept, markdown included, so the body's type is stated rather than inferred from whether a sidecar is present. It discloses nothing new: `result_payload` publishes `contentType` (see below). It also **decides where the body goes** — the next block. |
| item id | the concept's path in the bundle (§2 Concept ID) | Derived from the **item id**, never from `namespace` — see below. |
| item id | `theurian_item_id` | So a consumer cites the id rather than reconstructing it from a path. |
| `namespace` | `theurian_namespace` | Governed metadata, and not a path component. |
| `status` | `status` (§5.4) | Only `approved` is exportable (decision 3), so `stable` is the only value emitted. |
| `status` | `theurian_status` | The canonical value, uncollapsed. |
| `labels` | `tags` (§4.1) | A direct fit: short strings for cross-cutting categorization. |
| `owner` | `theurian_owner` | |
| `sourceAnchors[]` | `sources[]` (§5.1) | `sourceUri` → the entry's required `resource`; the remaining **served** anchor fields ride the same entry as `theurian_anchor: { provider, repository, commit_sha, file_path, line_start, line_end }`. `blobSha` and `externalId` are not exported — see below. |
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
SHOULD include, which is the last bullet of decision 2's index rule seen from
this end.

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

**Three filenames are reserved against derived concept paths, and a collision
takes a documented escape rather than a refusal or a silent overwrite.**
**The reservation is positional — it holds at the level where the name means
something, not everywhere.** `index.md` and `log.md` are OKF's and are reserved
at *every* level, because §3.1 gives them meaning "at any level of the
hierarchy"; `theurian-bundle.md` is this ADR's and is reserved **at the bundle
root only**, so a nested `architecture/theurian-bundle.md` collides with nothing
and does **not** escape — there is no manifest at that level for it to displace.
Reserving it everywhere would rename a row for no reason, and a rename with no
collision behind it is the kind of rule a later reader deletes because it looks
arbitrary. Nothing stops an item id from deriving onto one: `ItemId('index')`,
`ItemId('log')`,
`ItemId('theurian-bundle')` and `ItemId('architecture.index')` are all valid —
the id grammar is lowercase alphanumerics with hyphens and dotted namespaces, and
none of those names is special to it. Left unhandled, a concept would displace
the directory listing or the manifest, or be dropped in favour of them. **It is a
function of the row's own id, so the two-corpora battery cannot see it**: both
corpora hold that row, so both bundles are equally wrong.

The rule: a derived leaf equal to a reserved name is written with an `_item`
suffix on its stem — `index.md` → `index_item.md`, and its sidecar follows the
same stem. **The escape cannot collide with anything**, and that is a property of
the grammar rather than a hope: `ItemId('index_item')` raises
`InvalidIdentifierError`, because the underscore is outside the id alphabet
entirely, so no item id can derive onto an escaped name. `theurian_item_id` on
the concept still names the unescaped id, so nothing is lost in the projection.
A refusal was the alternative and is rejected for the reason the sidecar rule
rejects it: a gate-cleared row is never refused, and one item called `index`
would otherwise deny the whole corpus its bundle. S2 owes the pin.

**A markdown body embeds in its concept document; a non-markdown body is written
as a sidecar file beside it, byte for byte.** `contentType` is a free media type
in the published schema, whose own description states the rule this follows —
*"Media type of the body. Preserved rather than converted, so a structured source
stays structured (ADR-0010)"* — and [ADR-0010](0010-three-layer-knowledge-model.md)'s
rule 5 says the same from the other end: Markdown is the recommended authoring
format for human prose and is *never* the canonical form of something that was
born structured. A bundle that flattened an OpenAPI document into prose would
destroy exactly what that rule protects, and would do it in the artifact most
likely to be read by a tool rather than a person.

So the split is by media type:

- **`text/markdown`** — the overwhelming case — is the concept document's body,
  as everywhere else in this ADR.
- **Anything else** is written beside its concept document, at the concept's own
  path with the body's own extension, and the concept's body carries an ordinary
  Markdown link to it — **its link text is that same derived filename**, never
  the canonical body file's author-written name — plus a `theurian_body_file`
  key holding the same path. The dual-channel shape of decision 4 again: OKF's
  own channel for a consumer that reads links, a namespaced key for one that
  reads front matter. A sidecar is not
  a concept document (§3.1) and carries no front matter: its concept document
  holds the whole governance projection for the row.

**A gate-cleared row is never refused, and the sidecar extension is a total
function of `contentType` alone.** The whole mapping, and there is no other
input — written as rules rather than as a table, because decision 7's tables are
projections of governance and this is a derivation of a filename:

- `application/json`, or anything ending `+json` → **`.json`**
- `application/yaml`, `text/x-yaml`, or anything ending `+yaml` → **`.yaml`**
- everything else → **`.txt`**

`text/x-yaml` is named explicitly because it is the one member of
`domain/values.py::_STRUCTURED_MEDIA_TYPES` no other arm reaches with the right
extension: the suffix rule cannot see it — `x-yaml` is a hyphen, not a `+yaml`
suffix — and the exact spellings beside it carry `application/json` and
`application/yaml` only, so a rule without it spells a YAML body as text.

**"The one member matching neither `endswith` arm" is not that criterion, and an
earlier draft of this sentence said it was.** Measured 2026-09-24: **five of the
seven** match neither — `application/json`, `application/vnd.aai.asyncapi`,
`application/vnd.oai.openapi`, `application/yaml` and `text/x-yaml` — and two of
those five take `.txt` **by design**. `application/vnd.aai.asyncapi` and
`application/vnd.oai.openapi` are API-description formats with no canonical
serialization: the same document is written as JSON or as YAML, so there is no
correct structured extension to send them to, and `.txt` is the honest answer
rather than a gap the rule failed to close. *Compliance* carries the pin over
that constant, landing with this pull request. The two `endswith` arms are
`MediaType.is_structured`'s own rule, reused so the bundle spells a type the way
the domain recognises it.

Four properties, and each is load-bearing rather than pleasant:

- **The range is `{.json, .yaml, .txt}`** — bounded, filesystem-safe, and short.
  No path built from it can overrun a filename limit.
- **It is never `.md`.** Markdown embeds in its concept document and never
  reaches this rule, so a sidecar can never be written at a concept document's
  own path.
- **`contentType` is inside the disclosure bound** (`result_payload` publishes
  it), so `theurian_body_file` is a function of served data end to end: the stem
  from the item id, the extension from `contentType`, and `itemId` is served too.
  **It is a derived key defined in prose rather than a projection-table row, and
  the walk's population is the emission inventory for exactly that reason** — a
  key documented outside the table was how an unbounded input reached a filename
  for a round, and widening the *instrument* is what stops the next one rather
  than relocating this one.
- **`contentType` is in the one-snapshot read**, as a column of the revision row
  decision 3's single transaction already reads.

**The canonical body file's own suffix is dropped as an input, and this is a
correction.** An earlier draft made it step one — "the recorded suffix when it
has one" — and it was wrong twice over. It is **not in the snapshot**:
`knowledge_revisions` has no path column and `KnowledgeRevision` has no path
field, so reading it means a second read outside the transaction decision 3
commits to. And it is **unconstrained author text**: `$defs/contentFile` bounds
length at 1024 and refuses an absolute path, and says nothing whatever about the
suffix. The alternatives table carries the three faces that fell out of it.

**The sidecar's bytes are the snapshot's, not the filesystem's.** "Preserved
byte for byte" means the canonical body the revision row holds — `body` is a
column of `knowledge_revisions` beside `content_type`, and the store
reconstructs a `KnowledgeRevision` from those columns — so writing a sidecar
needs no second read of `.theurian/knowledge/` and stays inside decision 3's one
transaction. What pins that body to the file the author wrote is the
`contentSha256` check at load (`domain/knowledge.py` lines 201–206), on the path
that built the snapshot rather than on the export.

**The OKF spec settles that a non-markdown member is legal, and does not merely
tolerate it.** §3 characterises a bundle as a directory tree of markdown files,
but every rule that classifies or constrains a member is keyed on the `.md`
suffix: §3.1 reserves two filenames and says "all other `.md` files are concept
documents", and §11's three conformance clauses each range over `.md` files, so a
non-`.md` member cannot make a bundle non-conformant. The spec then uses such
members itself. §6.3's `references/` convention mirrors "external material, run
instructions, or code as first-class concepts within the bundle", its own example
being `references/attesters/revenue.py`; §10.2 gives `computation` as "a path
(§6.2) to a file holding the computation"; and §10.3 offers precisely this
ADR's choice — *inline* a fenced block in the body, or *file*, the latter "best
for a long or generated computation, or one already kept as a real file shared
with non-OKF tooling", with `references/computations/lib/revenue.sql` as the
example. A structured knowledge body is that case exactly.

**Decision 3's population is untouched by any of this.** A non-markdown row is
exported like every other row the default channel serves; the media type
decides where its bytes go and what its sidecar is called, never whether it
goes — which is a property of the `contentType` mapping above being total, not
an aspiration. The alternatives table carries the rejected reading, and it is
worth saying why it is a trap rather than merely wrong: excluding non-markdown
rows would leave the two-corpora battery green — the filter is a function of
the row itself, not of any withheld row — while the bundle silently omitted
governed knowledge. A passing battery would have said nothing about it.

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

**What the projection may publish is bounded by two measured instruments, and
five deliberate widenings are recorded against that bound.** An earlier draft of
this paragraph said the projection publishes nothing the serve path withholds and
cited `result_payload` alone. That was false of its own table — five of the rows
above are not in that payload at all — and it named the wrong instrument for the
body, which the export carries whole while `result_payload` carries an `excerpt`.
Both halves are corrected here rather than softened.

**The bound is the union of two payloads**, measured on 2026-09-24:

- `mcp/results.py::result_payload` (the payload it builds, L88–120) is the shape
  underneath every result — a floor rather than a ceiling, since a tool may add
  to it — and it emits `itemId`, `revisionId`, `title`,
  `excerpt`, `contentType`, `status`, **`trustLevel`** (from
  `revision.metadata.trust_level`), **`sensitivity`** (the item's current one,
  threaded in rather than read off the revision), `freshness.revisionCreatedAt`
  and a `sourceAnchors[]` of exactly `provider`, `sourceUri`, `repository`,
  `commitSha`, `filePath`, `lineStart` and `lineEnd`. Beside that instant the
  same `freshness` object holds `isWithinValidity` and `ageDays`, and the payload
  carries the three `SAFETY` labels and `raptorPath` when a forest was walked —
  named here because a *bound* has to be the whole payload, where the
  enumeration before it is the part this projection draws from.
- **`knowledge.get`'s additions** (`mcp/tools.py` L2430–2442), which are exactly
  four: **`body`** — the whole body, not the excerpt — **`relations`**, each
  `{relationType, targetItemId, note}` and each gated by
  `_relation_is_visible`, **`structured`**, and a conditional `integrity`.

That union is what covers the parts of this projection that carry the most: the
full body in the concept document or its sidecar, and `theurian_relations` with
the `## Relations` section, whose triple is `knowledge.get`'s triple.

**The union is the two tools this projection draws from, and it is deliberately
not every served key.** `knowledge.search` adds `fusedScore` and `foundBy` on top
of the same base shape (`mcp/search.py` L789–790); neither is row metadata, and
neither is in the union above, so the bound stays where the projection can
actually reach. Counting them would widen the bound without widening what the
bundle may say — the wrong direction for a claim whose whole job is to be
conservative.

**Five things decision 7 emits sit outside the union, and each is a recorded
widening rather than an oversight:**

| Widened | The justification, one line each |
| :-- | :-- |
| `type` ← `kind` | OKF's one required key (§4.1): a bundle cannot exist without it. It states the governance *class* of a row the recipient is already reading. |
| `theurian_namespace` ← `namespace` | One of the six required fields, so a faithful re-proposal of an exported row needs it. It names internal structure — the same kind of exposure `owner` carries, and it is recorded here for the same reason. |
| `tags` ← `labels` | **Unserved, so this is a recorded exposure like `owner`'s, not a safe projection.** No `result_payload` key carries labels, no search parameter filters on them, and the FTS5 table indexes `heading` and `text` only (`infrastructure/sqlite/index_schema.py`'s `chunks_fts`) — a label reaches no reader today. What makes emitting them acceptable is the gate, not the intent: `metadata.labels` is scanned element by element at accept, under the default `block` policy (`application/proposal_service.py::_metadata_strings` → `_list_strings`, L4245). |
| `stale_after` ← `validTo` | The serve path publishes `freshness.isWithinValidity`, a boolean this instant produces; the bundle publishes the instant, which is strictly more than the boolean. Emitted because a bundle is read asynchronously, where a boolean computed at export time would be wrong by the time it is read. |
| `theurian_owner` ← `owner` | Already carried as a residual under *Negative* — the recorded `owner` string travels, and a team that set it to a person's handle is exporting that handle. |

**So the universal is: the projection publishes nothing outside that union except
the five widenings above.** It is a statement a check can walk, which is the
point of writing it this way; a pin walking decision 7's table against the union
plus this table **lands with this pull request**, and *Compliance* carries it.

**The two anchor fields that payload does not carry, `blobSha` and `externalId`,
are therefore not exported either** — and neither of `knowledge.get`'s four
additions carries them, so widening there was available and was not taken. The
two-corpora battery of decision 3 holds the other half: withheld rows cannot
perturb any of these values, because they cannot reach the bundle at all.

**Dropped without a slot**, enumerated so that "lossy" is a list rather than an
adjective:

| Dropped | Why it has no home |
| :-- | :-- |
| The immutable revision history | The bundle carries the current revision only; `theurian_revision_id` names which. |
| The `supersedes` chain | A `superseded` item is outside decision 3's population, so a `SUPERSEDES` edge never clears decision 4's both-endpoints gate. Nothing of the chain survives but the exported row's own `theurian_status` — which is `approved` for every exported row today, so in practice **no trace of supersession leaves the bundle at all**. |
| `blobSha`, `externalId` | The two `sourceAnchor` fields neither served payload publishes (see the bound above). Exporting them would put provenance in a portable artifact that the serve path withholds from the same rows. |
| `contentSha256` | The exported body is a projection — it gains the generated `## Relations` section — so a digest of the canonical body sitting beside it would be unverifiable against the file it is on. A sidecar body *is* byte-identical, so the digest would be checkable there; it is still not emitted, so that one rule holds for every concept rather than for most of them. Available to a later mapping version if a consumer asks for it. |
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

- **The bundle is a second copy of approved knowledge that no withdrawal can
  reach.** A secret-removal withdrawal propagates to every index build on the
  machine and to no distributed copy. That is a disclosure residual rather than
  a staleness one, it is the price of the artifact being portable at all, and
  the first residual below states it in full — including the threat-model entry
  it owes.
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
  `theurian_content_type`, `theurian_body_file`, `theurian_relations`,
  `theurian_anchor`. Snake case, matching OKF's own key style (`okf_version`,
  `stale_after`, `last_modified`).

## What this does not close

1. **The purge gap. It is a disclosure residual, not a freshness caveat, and it
   is accepted as one.** A withdrawal publishes a purged index build
   synchronously and swaps the pointer
   ([ADR-0024](0024-a-purge-is-a-build.md),
   `application/withdrawal_purge.py`); **an exported bundle has no equivalent,
   and the withdrawal does not propagate to copies already distributed.** The
   case that fixes the severity is the one the purge machinery exists for: a
   **secret-removal withdrawal**. A credential withdrawn from every index build
   on this machine survives in every distributed bundle until that bundle is
   regenerated *and redistributed*, and redistribution is outside Theurian's
   reach entirely — there is no list of who holds a copy, and no mechanism that
   could reach them if there were. The same holds for a row withdrawn because it
   should never have been approved.

   This is the distributed-artifact shape: **an artifact in someone else's hands
   holds records the live state has since withdrawn.** It is not in the threat
   model today. Grepped on 2026-09-24: entries run T-1 through T-26, and the
   withdrawal→purge treatment there (T-17, T-17a, and T-15's remediation clause)
   is entirely about the *locally published index build*; T-24 is a different
   shape — a repository shipping its own `.theurian/review/` — and does not
   cover this. So **a threat-model entry is owed by slice S2**, when the export
   mechanism lands, alongside the T-3 entry S3 owes for the import; *Compliance*
   carries both.

   Four things bound the residual and none removes it. **Three are
   operator-side, where the distributed copy is not**: the bundle is
   Index-class, so deleting a copy loses nothing; `theurian_bundle_digest` makes
   staleness detectable by regenerating and comparing; and the guidance the
   export ships with is to regenerate rather than to edit. **One travels with the
   artifact** — the manifest's holder notice (decision 2), which is why that
   notice is a mandatory obligation on S2 rather than a nicety: it is the only
   thing that reaches the person holding the copy. An operator choosing to
   distribute a bundle is choosing this residual, which is the reason it is
   written here in those words rather than as a note about freshness.
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
| **Treat OKF import as [#223](https://github.com/theurian/theurian/issues/223)-class external-source ingestion, gated on the trust model** | #223 governs *connectors* that snapshot external systems into the source layer without passing a pull request; its gate exists because that path bypasses review. This import produces only a reviewable proposal and reaches approved state through the same merge as everything else, so it inherits ADR-0013's gate rather than needing #223's. **The boundary that makes the distinction hold is the one decision 6 states: the input is a local directory and the importer fetches nothing.** A remote fetch would put this back in #223's class and owe T-7's fetch controls with it; ingesting a bundle as a governed *source* is #223's either way, and remains out of scope. |
| **Reuse `KnowledgeCandidate` for imported concepts**, as #705's "bundle → source → KnowledgeCandidate → proposal" sketch has it | `domain/review.py`'s `PromotionGate` requires seven review-shaped signals — `pull_request_merged`, `thread_resolved`, `fix_commit_present`, `not_dismissed_or_outdated`, `ci_successful`, `generalizable`, `has_evidence` — and `KnowledgeCandidate.__post_init__` raises when the gate is unsatisfied. An OKF bundle satisfies none of them, so reuse means fabricating review facts or weakening the gate for every candidate, review-derived ones included. What the type is kept for is its *precedent*: the `INFERRED` ceiling of decision 6. |
| **Extend `RelationType` so OKF's untyped links have a home** | Enum extension is roadmap §9 ADR candidate 3, which owes a compatibility policy first; and the problem is not a missing member. An untyped link is untyped, and decision 5 is the answer to it. |
| **Export only the markdown-bodied rows, leaving structured knowledge out of the bundle** | It breaks decision 3's population rule — the bundle would no longer be the rows this deployment serves — and it drops governed knowledge from an artifact that says it carries the approved corpus. **The trap is that nothing would catch it:** the filter is a function of the row's own media type, not of any withheld row, so the two-corpora battery stays green while the bundle omits every OpenAPI document and JSON Schema in the corpus. A green battery would have asserted nothing about the omission. |
| **Derive the sidecar extension from the canonical body file's suffix** | This was step one of the accepted rule for one round, and it failed in three independent ways. **(a) It is outside the disclosure bound.** The suffix is an author-written string that neither served payload publishes, so `theurian_body_file` — itself defined in prose outside the projection table, and so outside the walk's population — carried unserved data into the bundle: round 1's under-enumerated universal, one key over. **(b) It admits `.md`.** A row with `contentType: application/yaml` and a `contentFile` ending `.md` — a plausible hand-authored mistake, and nothing refuses it — writes the sidecar at the concept document's own path, silently losing whichever of the two is written first: governance front matter, or the body bytes. Both owed batteries are blind to it by construction, since it is a function of the row's own data and both corpora hold the row. **(c) It is not in the snapshot and can be unbounded.** `knowledge_revisions` has no path column and `KnowledgeRevision` no path field, so reading it breaks decision 3's one-transaction rule; and `$defs/contentFile` permits 1024 characters with no suffix constraint, so a long tail becomes `ENAMETOOLONG` and denies the whole corpus its export. The accepted rule takes `contentType` alone, whose range is `{.json, .yaml, .txt}`. |
| **Take the sidecar extension from `body_extension`, refusing an unmapped media type** | The first draft of the sidecar rule did this and called the refusal a virtue. Measured 2026-09-24: `_EXTENSIONS` holds three media types against `_STRUCTURED_MEDIA_TYPES`' seven-plus, so **five of the seven** structured types and `text/plain` would refuse — including `application/vnd.oai.openapi`, the rule's own motivating example. Worse, the refusal is not per-row: one hand-authored row denies the whole corpus its export. A gate-cleared row must never be refused, which is why the three-step derivation above is total. |
| **Fence a non-markdown body inside its concept document instead of writing a sidecar** | It converts what [ADR-0010](0010-three-layer-knowledge-model.md) rule 5 and the schema's own `contentType` description say to *preserve*, and it does so silently: a consumer gets YAML wrapped in Markdown, no longer byte-identical to the canonical body and no longer parseable without unwrapping it first. The spec makes the choice unnecessary — §10.3 offers the file form for content "already kept as a real file shared with non-OKF tooling", which is this case. It stays the fallback only if a future spec revision forbids non-`.md` members, and it would then be recorded as a conversion, not presented as a projection. |
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

Landing with this pull request:

- **A walk of everything this ADR emits against the disclosure bound** — the
  union of `result_payload` and `knowledge.get`'s four additions, plus the five
  recorded widenings. Its subject is **the whole emission inventory**, not one
  table: all thirteen `theurian_*` keys (*Neutral*, third bullet) **and** the
  OKF-side emissions — `type`, `title`, `tags`, `status`, `stale_after`,
  `generated`, `sources[]` and the body. Scoping it to the projection table is
  the defect it exists to catch: `theurian_body_file` lived in prose outside that
  table for a round, and an unserved input reached a filename through it. It goes
  RED when anything emitted is in neither the union nor the widenings.
- **The sidecar extension rule, walked over the media types it ranges on.**
  `domain/values.py::_STRUCTURED_MEDIA_TYPES` has seven members; decision 7's
  three arms — `application/json` and anything ending `+json` to `.json`;
  `application/yaml`, `text/x-yaml` and anything ending `+yaml` to `.yaml`;
  everything else to `.txt` — assign each of them the extension recorded above.
  It goes RED when a member arrives whose extension those arms get wrong, and
  when the paragraph's five-of-seven measurement — the members matching neither
  `endswith` arm — stops being five.

Still owed, with the slice that will satisfy it:

- **Slice S2 (export):** the two-corpora equality of decision 3 — one export run
  against two corpora, one holding the withheld rows and one that never did,
  asserted to produce the byte-identical bundle; the determinism of decision 2 —
  two runs against one canonical state producing byte-identical output, which is
  Phase F ②'s exit criterion and which now has the four ordering rules of
  decision 2 to hold rather than a property with no sequence in it; a pin that
  the bundle path is derived from the item id, so a crafted `namespace` cannot
  reach a path component; and a pin that
  a non-markdown row exports as a sidecar whose bytes equal the canonical body,
  with the row present in the bundle either way — the property the two-corpora
  battery cannot see, per the alternatives table's first new row. With it, a pin
  that **an approved row whose media type is outside `_EXTENSIONS` exports as a
  sidecar rather than refusing**, since that is where the first draft's refusal
  would have fired — and beside it the collision case that closes the suffix
  rule's second face: **an approved row with `contentType:
  application/vnd.oai.openapi` and a `contentFile` ending `.md` exports a concept
  document *and* a distinct sidecar, both present**, neither overwriting the
  other. A pin that **the export reads one canonical snapshot** —
  a withdrawal landing mid-walk leaves the bundle wholly on one side of it, never
  straddling both (decision 3). And a pin that **an approved item literally named
  `index`, `log` or `theurian-bundle` exports under its escaped stem**, with
  neither the concept nor the file it would have displaced going missing. And
  the escaping rule of decision 2, over **both** of its site kinds: **row text
  rendered into structural syntax cannot forge structure** — an approved row
  whose `title` carries a newline followed by `theurian_sensitivity: public`
  emits one front-matter `title` value and no second key, and an index entry
  whose title carries a bracket followed by a parenthesis, and a relation line
  whose `note` begins with a run of `#`, each leave the entry list and the
  `## Relations` section with the shape they had without it. The YAML half is
  the one with a governance consequence, and the schema bounds all four of these
  strings by length alone.
- **Slice S3 (import):** that a bundle carrying only bare Markdown links yields a
  drafted migration with **no** `addRelation` operation (decision 5); that an
  import lands only under a proposal directory and reaches no approved state
  (decision 6, the same shape ADR-0032 and ADR-0035 owe); that the proposed trust
  level is capped at `INFERRED`; that a bundle with no usable `sources[]`
  still produces a proposal satisfying INV-8 through the bundle's own anchor; and
  the two containment pins of decision 6 — a `theurian_body_file` of `../` form,
  and one reached through a symlink out of the tree, are each refused as
  references while the rest of the bundle still drafts. Beside those two, the
  third pin decision 6 assigns: **a refusal records the reference as written,
  never the resolved target**, so the operator's filesystem layout does not ride
  a proposal draft into a public pull request (T-25's disclosure).
- **Slice S2, prose:** a threat-model entry for the distributed-bundle residual
  — a withdrawal, secret removal included, does not propagate to already
  distributed copies. No existing entry covers that shape (*What this does not
  close* item 1 records the grep), so it is an addition rather than an amendment.
- **Slice S3, prose:** the T-3 threat-model entry for the import path, named in
  *What this does not close* item 4.
