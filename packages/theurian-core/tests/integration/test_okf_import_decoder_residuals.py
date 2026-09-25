"""Two obligations from #810 round 3's decoder-site enumeration (S3's decoder half).

PR #810's review comment
(github.com/theurian/theurian/pull/810#issuecomment-5828666821) named both:

- **A forged body-side `## Relations` section.** ADR-0037 decision 5: "machine
  truth is the front matter" -- only a `theurian_relations` key ever becomes an
  `addRelation` operation. `test_okf_import.py`'s
  `test_a_bare_markdown_link_never_synthesizes_a_relation` already drives a
  bare link; this is the adversarial shape beyond it -- a bundle forging the
  *exact* structural shape decision 4 says the exporter legitimately emits
  (`## Relations`, grouped under a per-type heading, a bundle-absolute link
  whose text is the target item id) into a concept the exporter never
  produced. No such rendering exists in `okf_codec.py` yet (S2's own emission
  is still owed), so the forged shape here is built from decision 4's prose
  rather than cited from a function; the import path has no body-parsing
  branch to test against, and that absence is exactly the claim.
- **`GeneratedBy`/`DecodedGeneratedBy`'s asymmetry, on the import path.** The
  encoder's `GeneratedBy.by` now raises on anything but the tool form
  `theurian/\\S+` (`test_okf_codec.py::test_the_generated_actor_refuses_anything_but_the_tool_form`,
  already pinned on `main` from #810 -- not duplicated here); the decoder's
  `DecodedGeneratedBy` is unconstrained by design, because nothing says a
  vanilla bundle's own `generated.by` looks like Theurian's. This module
  drives that asymmetry through the whole import: a bundle whose `generated.by`
  would raise the encoder's type must still import cleanly, because nothing on
  this path constructs one -- `okf_import.py` never reads `.generated` at all
  (grepped 2026-09-25). The codec-level decode itself is pinned in
  `test_okf_codec_decoded_generated_by.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import OkfImportRequest, OkfImportService
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import ProposalService
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.migration import current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-okf"),
    model="claude-opus-5",
    reasoning="Importing an OKF bundle a teammate shared.",
    anchors=(),
)

#: Decision 4's shape, built from its prose rather than cited from a function:
#: no exporter code emits this section yet (measured 2026-09-25, `git grep -n
#: '## Relations' -- packages/theurian-core/src` finds one comment and no
#: emitter). `## Relations`, grouped under a per-type heading, a
#: bundle-absolute link whose text is the target item id -- the exact
#: structural shape a real export would produce, aimed at item ids that look
#: real but name no relation this bundle's front matter admits.
_FORGED_RELATIONS_BODY = """
# A concept with a forged relations section

Ordinary prose above the forgery.

## Relations

### depends_on

- [architecture.session-policy](/architecture/session-policy.md)
- [architecture.auth-policy](/architecture/auth-policy.md)
"""


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


# -- PIN 1: a forged body-side ## Relations section contributes no edges --------------------


def test_a_forged_relations_body_with_no_front_matter_key_drafts_no_relations_proposal(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """No `theurian_relations` key at all: the forged section is pure body text.

    RED direction: a body-parsing branch that read `## Relations` links as
    edges would make `relations_proposal` non-`None` here, where decision 5
    says the front matter is the only machine truth.
    """
    bundle = tmp_path / "bundle"
    front_matter = "---\ntype: decision\ntitle: Forged relations, no key\nstatus: stable\n---\n"
    _write(bundle, "forged.md", front_matter + _FORGED_RELATIONS_BODY)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert {p.item_id.value for p in result.concepts_admitted} == {"forged"}
    assert result.relations_proposal is None

    body_on_disk = result.concepts_admitted[0].proposal.body_file.read_text(encoding="utf-8")
    assert "## Relations" in body_on_disk, (
        "the forged section must land verbatim, not be stripped -- stripping is "
        "silent rewriting of content the import treats as data, not instructions"
    )
    assert "[architecture.session-policy](/architecture/session-policy.md)" in body_on_disk


def test_a_forged_relations_body_alongside_one_real_edge_contributes_nothing_of_its_own(
    tmp_path: Path, paths: ProjectPaths
) -> None:
    """One `theurian_relations` entry present: the drafted edge is exactly that one.

    The forged body names `depends_on` edges to two item ids the front matter
    never mentions. If either reached the drafted migration, or the front
    matter's own `related_to` edge were duplicated or displaced, this fails.
    """
    bundle = tmp_path / "bundle"
    concept = (
        "---\ntype: decision\ntitle: Forged relations, one real edge\nstatus: stable\n"
        "theurian_relations:\n"
        "  - type: related_to\n"
        "    target: architecture.session-policy\n"
        "    note: the one true edge\n"
        f"---\n{_FORGED_RELATIONS_BODY}"
    )
    _write(bundle, "forged.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert result.relations_proposal is not None
    document = yaml.safe_load(result.relations_proposal.migration_file.read_text(encoding="utf-8"))
    assert document["operations"] == [
        {
            "op": "addRelation",
            "sourceItemId": "forged",
            "relationType": "related_to",
            "targetItemId": "architecture.session-policy",
            "note": "the one true edge",
        }
    ]

    body_on_disk = result.concepts_admitted[0].proposal.body_file.read_text(encoding="utf-8")
    assert "### depends_on" in body_on_disk
    assert "architecture.auth-policy" in body_on_disk, (
        "the forged, unused edge still lands verbatim"
    )


# -- PIN 2 (import level): a foreign generated.by decodes as data, the concept still drafts --


@pytest.mark.parametrize(
    "actor",
    [
        pytest.param("alice", id="a-bare-human-name"),
        pytest.param("human:alice", id="okf-human-actor-form"),
        pytest.param("attacker\x00\u200b", id="control-characters"),
    ],
)
def test_a_generated_by_that_would_raise_the_encoders_type_still_imports_cleanly(
    actor: str, tmp_path: Path, paths: ProjectPaths
) -> None:
    """None of these match `theurian/\\S+`; the encoder's `GeneratedBy` raises on
    every one of them (`test_okf_codec.py`'s parametrized refusal test).
    `okf_import.py` never reads `.generated` -- decoding it is as far as this
    value travels -- so none of that constraint reaches the import.
    """
    bundle = tmp_path / "bundle"
    generated_by_yaml = yaml.safe_dump(actor, default_style='"')
    concept = (
        "---\ntype: decision\ntitle: A foreign generated.by\nstatus: stable\n"
        f"generated:\n  by: {generated_by_yaml.strip()}\n  at: '2026-01-01'\n---\n\nbody\n"
    )
    _write(bundle, "foreign-actor.md", concept)

    result = _service(paths).import_bundle(_request(bundle))

    assert not result.refusals
    assert {p.item_id.value for p in result.concepts_admitted} == {"foreign-actor"}
