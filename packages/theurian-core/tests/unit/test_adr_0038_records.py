"""ADR-0038's records: what the documents, schemas and corpora it cites say.

``test_adr_0038_claims.py`` names the rest of the pin.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import json
import posixpath
import re
from pathlib import PurePosixPath
from typing import Final

import pytest
import yaml
from adr_0038_support import (
    ADR_0005,
    CORRUPTION_TEST,
    LOADER,
    MIGRATION,
    README,
    REPO_ROOT,
    ROADMAP,
    SAMPLE_MIGRATION,
    TRACEABILITY,
    _adr,
    _arms,
    _collapsed,
    _function,
    _git,
    _migration_defs,
    _numbered,
    _section,
    _table,
    _tracked,
    _trees,
)

from theurian.application.proposal_service import V1_OPERATION_KINDS
from theurian.domain.migration import (
    MIGRATION_API_VERSION,
    MIGRATION_ENGINE_VERSION,
    OperationKind,
)
from theurian.domain.state import StateInputs, compute_state_hash
from theurian.infrastructure.sqlite import schema as sqlite_schema

pytestmark = pytest.mark.unit


_NAMES_A_SPECIFICATION_OPERATION: Final = re.compile(
    r"registerSpecification|supersedeSpecification"
)


# -- Context: the sample project's registration -------------------------------


def test_the_sample_registers_a_separate_tracked_yaml_document_against_a_markdown_item() -> None:
    migration = PurePosixPath(SAMPLE_MIGRATION)
    operations = yaml.safe_load((REPO_ROOT / migration).read_text(encoding="utf-8"))["operations"]
    [registration] = [op for op in operations if op["op"] == "registerSpecification"]
    [created] = [
        op
        for op in operations
        if op["op"] == "createItem" and op["itemId"] == registration["itemId"]
    ]
    [revision] = [
        op
        for op in operations
        if op["op"] == "upsertRevision" and op["itemId"] == registration["itemId"]
    ]
    # The loader stores `sourceUri` verbatim and nothing in src resolves it. It is
    # read here against the project root -- the directory holding `.theurian/` --
    # which is the resolution the ADR's own path spells; `contentFile` is relative
    # to the migration's directory, as the loader resolves it.
    document = (migration.parents[2] / registration["sourceUri"]).as_posix()
    body = posixpath.normpath(migration.parent / revision["contentFile"])

    assert (registration["specId"], registration["itemId"]) == (
        "spec.order-cancellation",
        "domain.order-cancellation",
    )
    assert created["kind"] == revision["metadata"]["kind"] == "domain"
    assert revision["metadata"]["contentType"] == "text/markdown"
    assert registration["format"] == "application/yaml"
    assert document == "examples/sample-project/.theurian/specifications/order-cancellation.yaml"
    assert body != document
    assert _tracked(document, body) == tuple(sorted((document, body)))


# -- Context: what the roadmap and the README say -----------------------------


def test_the_roadmap_recommends_the_fold_and_owes_candidates_three_and_six_before_phase_c() -> None:
    additions = _numbered(_section(ROADMAP, "### Four additions"))
    candidates = _numbered(_section(ROADMAP, "## 9. ADR candidates"))

    assert "ADR candidate #6" in additions[1]
    assert (
        "The recommendation is the unified form: spec-as-knowledge goes in kind, and the "
        "machine-readable payload goes in structured."
    ) in additions[1]
    assert "and source_commit pinning when it was measured." in additions[2]
    assert candidates[6].startswith("Whether the Specification entity folds into a knowledge kind")
    # Containment, not `endswith`: a recorded candidate keeps its tag and gains an appended
    # `Recorded:` marker (§9 candidate 8's form); split before it, so marker prose can't
    # satisfy or trip the check — "(Phase C)." differs from "(before Phase C)." only by the "(".
    questions = {n: candidates[n].split("Recorded:", 1)[0] for n in (3, 4, 6)}
    assert "(before Phase C)." in questions[3]
    assert "(before Phase C)." in questions[6]
    assert "(Phase C)." in questions[4]


def test_only_phase_d_is_adr_first_while_phase_c_security_opens_a_disclosure_family() -> None:
    principle = _collapsed(" ".join(_section(ROADMAP, "### 3. A disclosure change is ADR-first")))
    readme = README.read_text(encoding="utf-8").splitlines()
    phases = _table(readme[readme.index("| Phase | Scope |") :])[1:]
    phase_c = {
        row[0]: _collapsed(row[1])
        for row in _table(_section(ROADMAP, "### Phase C — Traceability foundation"))
    }

    assert "most directly Phase D's history access" in principle
    assert {phase for phase, scope in phases if "ADR-first" in scope} == {"D"}
    assert phase_c["**Security**"].startswith("A graph response is a new disclosure family.")
    assert (
        "a traversal hop must not resolve an alias when deciding authority."
        in phase_c["**Security**"]
    )


def test_the_traceability_design_draws_a_spec_trailer_and_a_coverage_query() -> None:
    text = TRACEABILITY.read_text(encoding="utf-8")

    assert 'B["Commit trailers: Refs: spec.order-cancellation"]' in text
    assert "| `spec.getCoverage` |" in text


# -- Consequences: the closed sets the fold moves -----------------------------


@pytest.mark.parametrize(
    "schema", ["knowledge-propose-change-input", "review-generate-knowledge-candidate-input"]
)
def test_the_mcp_input_schema_types_kind_as_a_string_the_handler_closes(schema: str) -> None:
    path = REPO_ROOT / "schemas" / "mcp" / f"{schema}.schema.json"
    kind = json.loads(path.read_text(encoding="utf-8"))["properties"]["kind"]

    assert kind["type"] == "string"
    assert "enum" not in kind
    assert (
        "The closed set is `KnowledgeKind`; an unknown value is refused by the handler"
        in kind["description"]
    )


#: Removal stems. `deprecat` is left out because `deprecateItem` is an operation.
_REMOVAL: Final = re.compile(
    r"(?i)remov|retir|delet|drop|withdr|eliminat|discontinu|retract|abolish|prun|struck|strik"
)


def _outside_amendments(text: str) -> tuple[str, list[str]]:
    """``text`` without its amendment runs, and those runs, collapsed.

    A run is consecutive lines opening ``>``, indented or not; it is an amendment
    when its first line opens ``> **Amended``. Any other blockquote stays in.
    """
    kept: list[str] = []
    amendments: list[str] = []
    run: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith(">"):
            run.append(line.lstrip().removeprefix(">"))
            continue
        if run and run[0].lstrip().startswith("**Amended"):
            amendments.append(_collapsed(" ".join(run)))
        else:
            kept.extend(f">{quoted}" for quoted in run)
        run = []
        kept.append(line)
    return "\n".join(kept), amendments


def test_adr_0005_closes_the_set_over_both_operations_and_governs_only_adding_one() -> None:
    """Reach: ADR-0005 outside ADR-0039's amendment run, since that run governs removal.

    Removal is keyed by the stems in :data:`_REMOVAL` -- remov, retir, delet, drop,
    withdr, eliminat, discontinu, retract, abolish, prun, struck and strik, case
    folded; a synonym outside them is outside the key. Every other amendment run is
    held to name no removal either,
    so a later amendment stating a removal rule goes RED for a person.

    ADR-0038's *Negative* sentence that ADR-0005 "says nothing of removing one" is
    held as true when measured, and superseded, by ADR-0038's own amendment block.
    """
    text, amendments = _outside_amendments(ADR_0005.read_text(encoding="utf-8"))
    adr_0039 = "Amended in Phase C, by [ADR-0039](0039-closed-set-extension-compatibility.md)"
    others = [amendment for amendment in amendments if not amendment.startswith(adr_0039)]
    head = _adr().read_text(encoding="utf-8").split("\n## Context\n", 1)[0].splitlines()
    block = "\n".join(line.removeprefix(">") for line in head if line.startswith(">"))
    first_bullet = _collapsed(re.split(r"\n\s*- ", block)[1])
    closed = re.search(r"The operation set is closed: (.*?)\. Adding", " ".join(text.split()))
    residue = re.sub(r"\b(?:removeRelation|removeAlias|removeEvidence)\b", "", text)

    assert closed
    assert {"registerSpecification", "supersedeSpecification"} <= set(
        re.findall(r"`(\w+)`", closed.group(1))
    )
    assert (
        "Adding an operation is a protocol change and requires a version bump of apiVersion."
        in _collapsed(text)
    )
    assert re.search(r"(?i)remov", text), "positive control: the removal key reads this file"
    assert not _REMOVAL.search(residue)
    assert not _REMOVAL.search(_outside_amendments("> **Amended in X.** Removal is governed.")[0])
    assert _REMOVAL.search(_outside_amendments("> A quoted rule: removal is refused.")[0])
    assert len(others) == len(amendments) - 1, "ADR-0039's amendment run is gone"
    assert [a for a in others if a.startswith("Amended in Milestone 7")], "positive control"
    assert [a[:60] for a in others if _REMOVAL.search(a)] == []
    assert (
        'That item\'s sentence that ADR-0005 "says nothing of removing one" was true when '
        "measured and is not now: ADR-0005 carries an amendment pointing to ADR-0039."
    ) in first_bullet


def test_both_operations_are_published_admitted_to_drafts_and_parsed_into_their_own_classes() -> (
    None
):
    defs = _migration_defs()
    union = {branch["$ref"] for branch in defs["operation"]["oneOf"]}
    arms = _arms(_function(LOADER, "_parse_operation"))

    for operation, parsed_as in (
        ("registerSpecification", "RegisterSpecification"),
        ("supersedeSpecification", "SupersedeSpecification"),
    ):
        definition = f"op{operation[0].upper()}{operation[1:]}"
        returned = [
            ast.unparse(node.value.func)
            for node in ast.walk(arms[operation])
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
        ]

        assert defs[definition]["properties"]["op"]["const"] == operation
        assert f"#/$defs/{definition}" in union
        assert OperationKind(operation) in V1_OPERATION_KINDS
        assert returned == [parsed_as]


def test_every_committed_dogfood_migration_is_v1_and_names_no_specification_operation() -> None:
    migrations = [path for path in _tracked(".theurian/migrations") if path.endswith(".yaml")]
    sample = (REPO_ROOT / SAMPLE_MIGRATION).read_text(encoding="utf-8")
    texts = {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in migrations}

    assert migrations, (
        "no committed dogfood migration was listed; the checks below would hold vacuously"
    )
    assert _NAMES_A_SPECIFICATION_OPERATION.search(sample), "positive control"
    assert [
        path
        for path, text in texts.items()
        if yaml.safe_load(text)["apiVersion"] != MIGRATION_API_VERSION
    ] == []
    assert [
        path for path, text in texts.items() if _NAMES_A_SPECIFICATION_OPERATION.search(text)
    ] == []


def test_only_the_sample_migration_names_a_specification_operation_in_committed_yaml() -> None:
    grep = _git(
        "grep", "-l", "-z", "-E", _NAMES_A_SPECIFICATION_OPERATION.pattern, "--", "*.yaml", "*.yml"
    )

    assert grep.returncode in {0, 1}, grep.stderr.decode("utf-8", "replace")
    assert set(filter(None, grep.stdout.decode("utf-8", "surrogateescape").split("\0"))) == {
        SAMPLE_MIGRATION
    }, "the retirement slice's owed list of documents to move is read off this set"


# -- Neutral and the alternatives' reasons ------------------------------------


def test_the_schema_module_docstring_says_the_store_is_replayable_from_git() -> None:
    assert (
        "Every byte in it is reconstructible by replaying Git-tracked YAML migrations into an "
        "empty file"
    ) in _collapsed(sqlite_schema.__doc__ or "")


def test_unpopulated_tables_excludes_traceability_edges_alone() -> None:
    values = [
        node.value
        for node in ast.walk(_trees()[CORRUPTION_TEST])
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and "UNPOPULATED_TABLES"
        in {
            ast.unparse(target)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        }
    ]

    assert len(values) == 1
    assert isinstance(values[0], ast.Call)
    assert ast.unparse(values[0].func) == "frozenset"
    assert ast.literal_eval(values[0].args[0]) == {"traceability_edges"}


def test_the_engine_version_is_hashed_into_the_state_and_its_comment_says_why() -> None:
    lines = (REPO_ROOT / MIGRATION).read_text(encoding="utf-8").splitlines()
    declared = next(
        at for at, line in enumerate(lines) if line.startswith("MIGRATION_ENGINE_VERSION")
    )
    comment = reversed(
        list(itertools.takewhile(lambda line: line.startswith("#:"), reversed(lines[:declared])))
    )
    inputs = StateInputs(
        migrations=(), content_checksums=(), schema_version=sqlite_schema.SCHEMA_VERSION
    )
    bumped = dataclasses.replace(inputs, engine_version=MIGRATION_ENGINE_VERSION + 1)

    assert inputs.engine_version == MIGRATION_ENGINE_VERSION
    assert compute_state_hash(inputs) != compute_state_hash(bumped)
    assert (
        "so an engine change invalidates cached state instead of silently reinterpreting it "
        "(ADR-0007)"
    ) in _collapsed(" ".join(line.removeprefix("#:") for line in comment))
