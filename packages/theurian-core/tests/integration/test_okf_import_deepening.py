"""ADR-0037 decision 6 behaviour the 56-test baseline does not pin.

Four things, each independent of the others:

- **`sources[]`'s syntactic classification** (decision 6): a URI or a relative
  path becomes an additional source anchor; a scope descriptor does not, and
  the bundle-identity anchor (INV-8's floor) is present either way.
- **An out-of-enum `theurian_relations` type is refused by the published
  schema** at draft time, while the concept it is attached to still drafts --
  decision 5's enum-pressure-stays-at-zero claim, driven rather than assumed.
- **The `{key, literal}` refusal shape is overloaded three ways**, not two:
  a containment refusal's `key` is a front-matter key, a malformed-concept
  refusal's `key` is the concept's own bundle-relative path, and a
  draft-refusal's `key` is either an item id or the literal `"addRelation"` --
  recorded here so a reader does not have to reconstruct it from three
  separate tests.
- **`MAX_UPSERT_OPERATIONS` is a whole-import cap, not a per-relations-document
  one** -- measured with 251 `addRelation` entries and with a 300-concept
  bundle, both recorded as facts rather than redesigned.
"""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application import okf_import
from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import OkfImportError, OkfImportRequest, OkfImportService
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import MAX_UPSERT_OPERATIONS, ProposalService
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.security.paths import read_source_file

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-okf"),
    model="claude-opus-5",
    reasoning="Importing an OKF bundle a teammate shared.",
    anchors=(),
)


@pytest.fixture
def paths(tmp_path: Path) -> ProjectPaths:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    return project


def _service(paths: ProjectPaths) -> OkfImportService:
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    def landed_migration(migration_id: MigrationId) -> object:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set.get(migration_id)

    def landed_migrations() -> object:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set

    service = ProposalService(
        paths=paths,
        project_id=ProjectId("demo"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,  # type: ignore[arg-type]
        landed_migrations=landed_migrations,  # type: ignore[arg-type]
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )
    return OkfImportService(drafts=DraftOnlyProposals(service))


def _write(bundle: Path, relative: str, text: str) -> Path:
    target = bundle / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _request(bundle: Path, **overrides: object) -> OkfImportRequest:
    fields: dict[str, object] = {
        "root": bundle,
        "owner": "platform-team",
        "author": "dana@example.com",
        "evidence": EVIDENCE,
    }
    fields.update(overrides)
    return OkfImportRequest(**fields)  # type: ignore[arg-type]


def _source_anchors_of(proposal_directory: Path) -> list[dict[str, object]]:
    migrations = list(proposal_directory.glob("*.yaml"))
    assert len(migrations) == 1, migrations
    document = yaml.safe_load(migrations[0].read_text(encoding="utf-8"))
    metadata = document["operations"][1]["metadata"]
    anchors: list[dict[str, object]] = metadata["sourceAnchors"]
    return anchors


# -- sources[]'s syntactic classification (decision 6) --------------------------------------


def test_a_uri_and_a_relative_path_become_anchors_a_scope_descriptor_does_not(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """The test is syntactic alone: whitespace is what marks a scope descriptor.

    `_is_uri_or_relative_path` never follows, fetches or checks reachability
    -- this drives its three branches through the public `import_bundle`
    surface rather than by calling the private function directly.

    Two scope-descriptor phrasings are planted, not one: the plain-prose
    original, and a colon-prefixed one. "BigQuery" alone satisfies RFC 3986's
    scheme grammar, and the round found this fixture's own original phrasing
    -- which opens with no colon at all -- was the one shape that could never
    exercise the branch a colon-prefixed descriptor takes.
    """
    bundle = tmp_path / "bundle"
    concept = """---
type: decision
title: Sources classification
status: stable
sources:
  - resource: https://example.com/doc.pdf
  - resource: docs/architecture.md
  - resource: "all queries in BigQuery project X"
  - resource: "BigQuery: all queries in project X"
---

body
"""
    _write(bundle, "sources-test.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert len(result.concepts_admitted) == 1
    anchors = _source_anchors_of(result.concepts_admitted[0].proposal.directory)
    uris = [str(a["sourceUri"]) for a in anchors]

    assert uris[0] == "okf-bundle:sources-test.md", "INV-8's bundle-identity anchor is first"
    assert "https://example.com/doc.pdf" in uris
    assert "docs/architecture.md" in uris
    assert not any("BigQuery" in uri for uri in uris), (
        f"a scope descriptor became a source anchor: {uris}. A scope "
        f"descriptor carries whitespace no URI or relative path needs, and "
        f"decision 6 requires it be excluded rather than followed."
    )
    assert len(anchors) == 3, (
        f"expected the bundle-identity anchor plus the URI and the relative path, got {anchors}"
    )


def test_a_colon_prefixed_scope_descriptor_is_excluded_a_real_uri_still_admitted(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """Review-Finding: adversarial HIGH -- a colon-prefixed scope descriptor
    passes the syntactic anchor test its docstring excludes.

    "BigQuery" alone satisfies RFC 3986's scheme grammar (letters only), so a
    scope descriptor opening with a colon-terminated word -- unlike the plain
    prose example in the test above -- reached the scheme check first and was
    admitted as if it were a URI. The whitespace test now runs first and
    dominates, whichever a leading word looks like.
    """
    bundle = tmp_path / "bundle"
    concept = """---
type: decision
title: Colon-prefixed descriptor
status: stable
sources:
  - resource: "BigQuery: all queries in project X"
  - resource: https://example.com/doc.pdf
---

body
"""
    _write(bundle, "colon-descriptor.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    anchors = _source_anchors_of(result.concepts_admitted[0].proposal.directory)
    uris = [str(a["sourceUri"]) for a in anchors]

    assert not any("BigQuery" in uri for uri in uris), (
        f"the colon-prefixed scope descriptor became a source anchor: {uris}"
    )
    assert "https://example.com/doc.pdf" in uris
    assert len(anchors) == 2, (
        f"expected the bundle-identity anchor plus the real URI, got {anchors}"
    )


# -- INV-8's identity anchor carries its own concept path (round-1 pin) ----------------------


def test_the_bundle_identity_anchors_file_path_is_the_concepts_own_bundle_relative_path(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`_bundle_identity_anchor` sets `file_path`, not just `source_uri` --
    read from the drafted migration's own bytes, since that is what a
    reviewer and a future consumer actually see, not the in-memory
    `SourceAnchor`.
    """
    bundle = tmp_path / "bundle"
    _write(
        bundle,
        "architecture/auth-policy.md",
        "---\ntype: decision\ntitle: T\nstatus: stable\n---\n\nbody\n",
    )

    result = _service(paths).import_bundle(_request(bundle))

    anchors = _source_anchors_of(result.concepts_admitted[0].proposal.directory)
    identity_anchor = next(a for a in anchors if a["provider"] == "okf-bundle")
    assert identity_anchor["filePath"] == "architecture/auth-policy.md"


# -- The descriptor grammar's whole admitted/excluded population (round-1 pin) --------------


@pytest.mark.parametrize(
    ("resource", "admitted"),
    [
        pytest.param("BigQuery: all queries in project X", False, id="colon-prefixed"),
        pytest.param("Confluence: the space named Platform", False, id="colon-prefixed-2"),
        pytest.param("all queries in BigQuery project X", False, id="no-colon"),
        pytest.param("BigQuery:\tall queries in project X", False, id="tab-separated"),
        pytest.param("https://example.com/a b", False, id="uri-shaped-internal-space"),
        pytest.param("/etc/passwd", False, id="absolute-path"),
        pytest.param("https://example.com/doc.pdf", True, id="https-uri"),
        pytest.param("docs/architecture.md", True, id="relative-path"),
        pytest.param("s3://data-bucket/path/to/object", True, id="scheme-uri-no-whitespace"),
    ],
)
def test_the_descriptor_grammar_admits_or_excludes_on_whitespace_alone(
    tmp_path: Path, paths: ProjectPaths, resource: str, admitted: bool
) -> None:
    """Review-Finding: adversarial HIGH -- a colon-prefixed scope descriptor
    passes the syntactic anchor test its docstring excludes.

    The fixed predicate excludes on whitespace before it ever checks the
    scheme grammar; this enumerates the grammar the fix has to hold across,
    not only the one phrasing the finding happened to name. The internal-space
    URI case is graded on what the code actually does, not on what a URI
    "should" be: whitespace excludes it too, since no legitimate URI or
    relative path carries any.
    """
    bundle = tmp_path / "bundle"
    front_matter = yaml.safe_dump(
        {
            "type": "decision",
            "title": "Descriptor grammar",
            "status": "stable",
            "sources": [{"resource": resource}],
        },
        sort_keys=False,
    )
    _write(bundle, "descriptor.md", f"---\n{front_matter}---\n\nbody\n")

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    anchors = _source_anchors_of(result.concepts_admitted[0].proposal.directory)
    uris = [str(a["sourceUri"]) for a in anchors]
    assert (resource in uris) is admitted, (
        f"{resource!r} admitted={resource in uris}, expected {admitted}"
    )


# -- An out-of-enum relation type is refused by the schema, not synthesized ------------------


def test_an_out_of_enum_relation_type_is_refused_by_the_schema_while_the_concept_still_drafts(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """Decision 5: no `theurian_relations` value widens the 14-member enum.

    `_decode_relation_entry` accepts any string as `type` -- the decoder never
    treats front matter as governance (decision 1) -- so what refuses an
    invalid one is `$defs/relationType` at schema validation, inside
    `draft_from_document`. The concept's own `.draft()` is a separate call and
    is unaffected: only the relations proposal fails.
    """
    bundle = tmp_path / "bundle"
    concept = """---
type: decision
title: Bad relation
status: stable
theurian_relations:
  - type: made_up_relation_type
    target: other.concept
---

body
"""
    _write(bundle, "bad-relation.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert {p.item_id.value for p in result.concepts_admitted} == {"bad-relation"}
    assert result.relations_proposal is None
    assert len(result.refusals) == 1
    assert result.refusals[0].key == "addRelation"


# -- The {key, literal} shape is overloaded three ways, recorded in one place ----------------


def test_the_refusal_key_and_literal_pair_carries_three_distinct_meanings(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """`ImportRefusal`'s docstring names two `key` meanings; a third exists too.

    `kind` now names which of the three a given refusal is, so this is no
    longer a claim that the overload is ambiguous -- it pins what each `kind`
    means for `key` and `literal`, so a future change to any one of them is a
    decision rather than an accident:

    - **kind=reference**: `key` is the front-matter key that named a bad
      reference, `literal` is the bundle's own written value.
    - **kind=concept**: `key` is the concept's own bundle-relative path,
      `literal` is a short reason Theurian wrote.
    - **kind=draft** (or **kind=relations** for the relations document,
      keyed `"addRelation"` -- not a spellable `ItemId`, whose grammar is
      lowercase-only, so this key can never collide with a real concept's):
      `literal` is a Theurian-written reason plus the refusing exception's
      class name, never `str(exception)` -- a schema validator's own message
      can embed the bundle's offending value verbatim and unbounded.
    """
    bundle = tmp_path / "bundle"
    _write(
        bundle,
        "containment.md",
        "---\ntype: decision\ntitle: Containment\nstatus: stable\n"
        "theurian_content_type: application/json\n"
        "theurian_body_file: ../../../../etc/passwd\n---\n\nbody\n",
    )
    _write(bundle, "malformed.md", "---\ntitle: Missing type and status\n---\n\nbody\n")
    _write(
        bundle,
        "draft-refusal.md",
        "---\ntype: decision\ntitle: Draft refusal\nstatus: stable\n"
        "theurian_relations:\n  - type: made_up_relation_type\n    target: other.concept\n"
        "---\n\nbody\n",
    )

    result = _service(paths).import_bundle(_request(bundle))

    by_key = {refusal.key: refusal for refusal in result.refusals}
    assert by_key["theurian_body_file"].kind == "reference"
    assert by_key["theurian_body_file"].literal == "../../../../etc/passwd", (
        "kind=reference: key is the front-matter key, literal is the bundle's own written value"
    )
    assert by_key["malformed.md"].kind == "concept"
    assert by_key["malformed.md"].literal == "missing or empty required key(s): type, status", (
        "kind=concept: key is the concept's own bundle-relative path"
    )
    assert "addRelation" in by_key, "kind=relations: key is the literal string 'addRelation'"
    assert by_key["addRelation"].kind == "relations"
    assert by_key["addRelation"].literal == "the proposal service refused it: MigrationError", (
        "kind=relations: literal is a Theurian-written reason plus the exception's class name, "
        "never str(exception)"
    )


# -- MAX_UPSERT_OPERATIONS is a whole-import cap, measured rather than assumed ---------------


def test_a_bundle_computing_exactly_the_cap_admits_and_drafts_every_concept(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """M6: the cap boundary's positive direction. The check in
    `_admit_concepts` is `total_operations > MAX_UPSERT_OPERATIONS`: a bundle
    landing exactly on the cap, never over it, must draft cleanly. A `>=`
    boundary would refuse this legal bundle instead of only the next one.
    """
    concept_count = MAX_UPSERT_OPERATIONS // 2
    bundle = tmp_path / "bundle"
    for index in range(concept_count):
        _write(
            bundle,
            f"concept-{index}.md",
            f"---\ntype: decision\ntitle: Concept {index}\nstatus: stable\n---\n\nbody\n",
        )

    result = _service(paths).import_bundle(_request(bundle))

    assert len(result.concepts_admitted) == concept_count
    assert not result.refusals
    assert result.relations_proposal is None


def test_251_relations_on_one_concept_refuse_the_whole_import_not_only_the_relations_document(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """Recorded fact: the cap is on the *whole import's* operation count.

    A concept's own `.draft()` contributes 2 operations (create + revise); with
    251 `addRelation` entries the total is 253, over `MAX_UPSERT_OPERATIONS`
    (250). `_operation_cap_exceeded` fires before any drafting begins, so the
    concept that carries the relations is refused too -- not admitted with its
    relations dropped. Nothing in the published schema bounds `operations[]`
    by length (`schemas/migrations/migration.schema.json` sets `minItems: 1`
    and no `maxItems`), so a document with 251 `addRelation` entries alone
    would pass schema validation; what refuses it here is `okf_import.py`'s
    own whole-import count, never reached at draft time by count alone.
    """
    bundle = tmp_path / "bundle"
    relations = "\n".join(
        f"  - type: related_to\n    target: other.concept-{i}" for i in range(251)
    )
    _write(
        bundle,
        "many-relations.md",
        f"---\ntype: decision\ntitle: Many relations\nstatus: stable\n"
        f"theurian_relations:\n{relations}\n---\n\nbody\n",
    )

    with pytest.raises(OkfImportError) as excinfo:
        _service(paths).import_bundle(_request(bundle))

    assert str(excinfo.value) == (
        "This bundle passes 253 operations within its first 1 concepts, "
        f"more than the {MAX_UPSERT_OPERATIONS} a single import will draft."
    ), "253 is the running total at the crossing, computed within the first concept alone"
    assert not [p for p in paths.proposals.glob("*") if p.is_dir()], (
        "the whole-import cap fires before any proposal directory is written"
    )


def test_a_300_concept_bundle_refuses_fast_rather_than_drafting_unboundedly(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """Measured fact: a bundle this size never reaches proposal generation.

    300 concepts at 2 operations each is 600, already over
    `MAX_UPSERT_OPERATIONS` (250) with no relations at all -- so the cap that
    protects the relations document (above) is, incidentally, also a
    per-bundle concept-count cap: no bundle above 125 plain concepts can
    complete an import in one run regardless of content. The cap is checked
    incrementally as each concept is admitted (M-f), so the walk stops the
    moment the running total crosses 250 rather than reading all 300 first --
    the refusal names 252 (the 126th concept's own two operations are what
    cross it), never 600. Measured on this machine at commit 774b4728: under
    2 seconds to walk, decode and refuse 300 files: recorded here as a floor
    generous enough to catch a regression to unbounded or quadratic behaviour
    without being a tight timing assertion.
    """
    bundle = tmp_path / "bundle"
    for index in range(300):
        _write(
            bundle,
            f"concept-{index}.md",
            f"---\ntype: decision\ntitle: Concept {index}\nstatus: stable\n---\n\nbody\n",
        )

    started = time.monotonic()
    with pytest.raises(OkfImportError) as excinfo:
        _service(paths).import_bundle(_request(bundle))
    elapsed = time.monotonic() - started

    assert str(excinfo.value) == (
        "This bundle passes 252 operations within its first 126 concepts, "
        f"more than the {MAX_UPSERT_OPERATIONS} a single import will draft."
    ), "252 within 126 is the running total at the crossing, not the bundle's own 600"
    assert "600" not in str(excinfo.value), "the walk must stop at the crossing, not read all 300"
    assert elapsed < 5.0, f"a 300-concept bundle took {elapsed:.3f}s to refuse, expected < 5s"
    assert not [p for p in paths.proposals.glob("*") if p.is_dir()]


def test_the_incremental_cap_stops_reading_before_the_rest_of_the_bundle(
    tmp_path: Path, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-f, proved by a counting hook rather than inferred from the refusal's
    own arithmetic, as the test above does: a bundle far past the cap must
    never open the files past the crossing point. 126 concepts at 2
    operations each is 252, the first total over `MAX_UPSERT_OPERATIONS`
    (250), so exactly 126 of the 300 files are ever read.
    """
    bundle = tmp_path / "bundle"
    for index in range(300):
        _write(
            bundle,
            f"concept-{index}.md",
            f"---\ntype: decision\ntitle: Concept {index}\nstatus: stable\n---\n\nbody\n",
        )

    reads: list[str | PurePosixPath] = []

    def counting_read(root: Path, relative: str | PurePosixPath) -> bytes:
        reads.append(relative)
        return read_source_file(root, relative)

    monkeypatch.setattr(okf_import, "read_source_file", counting_read)

    with pytest.raises(OkfImportError):
        _service(paths).import_bundle(_request(bundle))

    assert len(reads) == 126, (
        f"expected exactly 126 concepts read before the cap fired, got {len(reads)}"
    )
