"""ADR-0039's repository facts, held from both sides.

ADR-0039 decides how the migration format's closed sets change and ships no
code, so every sentence it states about the codebase was measured once, at
``f0e4d754``. Each claim is held twice: a fact half recomputed from live source,
schema or behaviour, RED when the tree moves; and a prose half -- a table or list
parsed out of the ADR, or a fragment in :data:`ADR_STATES` -- RED when the record
drifts.

The pin is six modules and a support module, split by section:

- this one -- the closed sets and their mirrors, the matrix, the decisions, the
  Compliance section's naming of this module, and the prose half;
- ``test_adr_0039_refusals.py`` -- what an unknown value meets at each entry
  point today, and the frozen-history premise of decision 4;
- ``test_adr_0039_populations.py`` -- the entry-point population, the derived
  store, ``compat check``, and the writers decisions 3 and 4 name;
- ``test_adr_0039_schemas.py`` -- the schema scan: every governed spelling
  under ``schemas/``, classified and held exact;
- ``test_adr_0039_wire.py`` -- what the wire closes and what it carries open;
- ``test_adr_0039_records.py`` -- the amendments to ADR-0005 and ADR-0038, and
  the roadmap;
- ``adr_0039_support.py`` (tests root) -- the readers they share.

**Reach.** Each scan's key, beside the claim it holds:

- *The closed sets* are every ``enum`` and ``const`` node of
  ``schemas/migrations/migration.schema.json``, keyed by JSON pointer.
- *A governed position* is a ``$defs`` property whose schema is a ``$ref`` to one
  of the six enum ``$defs``, an inline ``enum``, the ``op`` discriminator, or the
  root ``apiVersion``.
- *An entry point* is detected by reference and classified by form. Detection:
  every ``Name`` or ``Attribute`` naming ``KnowledgeKind``, ``RelationType`` or
  ``OperationKind`` in a value position of any ``.py`` under ``src/theurian`` --
  every position but an annotation, where a ``cli/`` parameter's annotation
  counts as an option because Typer parses a value into it. Classification, by
  the node that holds the reference: a call of the class, the first argument of
  ``_closed_value``, the type of an ``isinstance``, a member access, a CLI
  option, and anything else as *unclassified*. The population of references is
  held exact by file, function and form, so any new reference goes RED for a
  person. Outside the key: an aliased import, a class reached through a
  variable or through reflection (``vars``, ``globals``, ``importlib``), and
  ``type(member)(raw)``; a method defined on a class is held apart, by
  asserting each class holds its members and nothing else. A table row
  *accounts for* a site when the site's token appears anywhere in the row's
  first two cells.
- *What ``compat check`` reads* is every name ``compat_check`` and
  ``domain/compatibility.py`` spell or import; a read through another module's
  function is outside it.
- *A governed occurrence* in a schema is the ADR's *Detection* rule, scanned
  over every key and string value of every schema under ``schemas/``, not
  over a list of constructs (``adr_0039_support.occurrences``): an object key
  spelling a member, unless it is a 2020-12 keyword (read from the
  specification's metaschemas) in keyword position; a string value spelling a
  member, outside ``description``, ``title``, ``$comment``, ``$schema``,
  ``$id``, ``$ref``, ``$anchor`` and ``x-`` keys; and a member spelled as a
  whole word of a ``pattern`` or ``patternProperties`` key. Both populations
  are held exact, per class, file and pointer. The closing-construct key only
  classifies. Outside the rule, as the ADR states: a ``$ref`` (its cross-file
  population is held exact on its own), a pattern that closes without spelling
  a member (the patterns that accept a member are held by file, pointer and
  text), a
  member embedded in a longer non-pattern value, and a superset construct's
  meaning (it classifies as ``overlap``).
- *The wire* is ``schemas/mcp/*.json`` plus every file a ``$ref`` reaches from
  them, an absolute ``$ref`` resolved by the ``$id`` it names.
- *Published to a client* is a dict-literal key ``relationType`` or
  ``operations`` under ``mcp/``. A key built at run time, or a payload assembled
  by ``dict(...)`` or subscript assignment, is outside it.
- *Loads migrations* is a module spelling ``load_migrations``. A call reaching
  the loader through another module's function is outside it.
- *A gate's reads* are the governed class names in a module-level function of
  ``domain/enums.py``, annotations included, followed one module-level constant
  deep.
- *Decoded inside the guard* is every reference to a ``store.py`` ``*_from_row``
  function, outside such a function's own body, lying inside a
  ``with _reading()`` block or an argument of ``_read_one``/``_read_all``.
- *A store method decodes* the ``*_from_row`` functions its body names,
  following ``self.<method>(...)`` within ``SqliteCanonicalStore``; *before the
  gate* is source position within ``knowledge_get``, not a line number.
- *A statement* is a string literal holding an upper-case ``SELECT ... FROM`` or
  ``INSERT INTO``; SQL assembled at run time is outside it.
- *An example advertising a member* is an ``e.g.`` list naming a
  ``KnowledgeKind`` member in the description of a property named ``kind``.

**Not pinned.** The ``file:line`` citations are the ADR's dated measurement at
``f0e4d754``; what each line holds is pinned by symbol, never by number.

The Markdown readers are in ``adr_0039_support``, not imported from
``adr_0038_support``: that module's ``_parsed`` refuses to run while any module
outside ``test_adr_0038_*.py`` imports it at column 0.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import re
from enum import Enum
from pathlib import Path
from typing import Final

import pytest
from adr_0037_support import collapsed
from adr_0039_support import (
    MIGRATION_SCHEMA,
    REPO_ROOT,
    _adr,
    _nodes,
    _numbered,
    _schema,
    _section,
    _table,
    _trees,
)

from theurian.domain.migration import MIGRATION_ENGINE_VERSION
from theurian.domain.state import StateInputs, compute_state_hash
from theurian.infrastructure.sqlite import schema as sqlite_schema

pytestmark = pytest.mark.unit


THIS_MODULE: Final = "packages/theurian-core/tests/unit/test_adr_0039_claims.py"


#: The table's `Set` column against its `Class` column: decision 1's assignment.
CLASSES: Final = {
    "`kind`": "vocabulary",
    "`relationType`": "vocabulary",
    "the operation set": "grammar",
    "`status`": "wire-enumerated",
    "`sensitivity`": "wire-enumerated",
    "`trustLevel`": "wire-enumerated",
    "a specification's status": "retiring",
    "the format version": "the version constant",
}


def _closed_set_rows() -> list[tuple[str, ...]]:
    rows = _table(_section("### The closed sets the v1 format publishes"))
    assert rows[0] == ("Set", "Published as", "Python mirror", "Members", "Class (decision 1)")
    return rows[1:]


def _pointers(published_as: str) -> set[str]:
    """The schema pointers a `Published as` cell names."""
    token = re.findall(r"`([^`]+)`", published_as)[0]
    if token.endswith(".oneOf"):
        branches = _schema(MIGRATION_SCHEMA)["$defs"]["operation"]["oneOf"]
        return {f"#/$defs/{branch['$ref'].rsplit('/', 1)[1]}/properties/op" for branch in branches}
    return {f"#/{token}"}


def test_the_table_names_every_closed_set_the_migration_schema_publishes_in_one_class() -> None:
    text = MIGRATION_SCHEMA.read_text(encoding="utf-8")
    walked = {
        pointer: keyword
        for pointer, node in _nodes(_schema(MIGRATION_SCHEMA))
        for keyword in ("enum", "const")
        if keyword in node
    }
    rows = _closed_set_rows()

    assert sorted((row[0], row[4]) for row in rows) == sorted(CLASSES.items())
    assert set().union(*(_pointers(row[1]) for row in rows)) == set(walked)
    assert (
        len(re.findall(r'"(?:enum|const)":', text)),
        list(walked.values()).count("enum"),
        list(walked.values()).count("const"),
    ) == (21, 6, 15), "the ADR's `prints 21 lines: the apiVersion const, six enums, ...`"


@pytest.mark.parametrize("name", CLASSES, ids=[collapsed(name) for name in CLASSES])
def test_each_python_mirror_equals_its_published_set_member_for_member(name: str) -> None:
    """Adding a member to the schema or to the enum alone goes RED here."""
    [row] = [row for row in _closed_set_rows() if row[0] == name]
    module, attribute = re.findall(r"`([^`]+)`", row[2])
    mirror = getattr(
        importlib.import_module(f"theurian.{module[:-3].replace('/', '.')}"), attribute
    )
    schema = _schema(MIGRATION_SCHEMA)
    published = [
        value
        for pointer in _pointers(row[1])
        for node in [dict(_nodes(schema))[pointer]]
        for value in node.get("enum", [node.get("const")])
    ]
    enumerated = isinstance(mirror, type) and issubclass(mirror, Enum)
    mirrored = [member.value for member in mirror] if enumerated else [mirror]

    assert sorted(published) == sorted(mirrored)
    assert len(published) == int(row[3])


def test_the_engine_version_is_one_hashed_into_the_state_and_published_by_project_status() -> None:
    inputs = StateInputs(
        migrations=(), content_checksums=(), schema_version=sqlite_schema.SCHEMA_VERSION
    )
    bumped = dataclasses.replace(inputs, engine_version=MIGRATION_ENGINE_VERSION + 1)
    published = {
        ast.unparse(value)
        for node in ast.walk(_trees()["cli/commands.py"])
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "engineVersion"
    }

    assert MIGRATION_ENGINE_VERSION == 1
    assert compute_state_hash(inputs) != compute_state_hash(bumped)
    assert published == {"MIGRATION_ENGINE_VERSION"}


def test_the_matrix_holds_rows_c1_to_c8_each_filling_every_column() -> None:
    header, *rows = _table(_section("### The matrix"))

    assert [label for row in rows for label in re.findall(r"^\*\*(C\d+)\*\* ", row[0])] == [
        f"C{number}" for number in range(1, 9)
    ]
    assert [(len(row), all(row)) for row in rows] == [(len(header), True)] * len(rows)


#: Each decision's anchor phrase: a rewording that keeps the decision stays GREEN.
DECISIONS: Final = {
    1: "The policy governs the eight entries of the table above, each in exactly one class",
    2: "Adding a vocabulary member is additive.",
    3: "Adding an operation keeps ADR-0005's rule: it bumps apiVersion.",
    4: "Removing or renaming a member or an operation takes it out of Core's own writers only.",
    5: "Read support is permanent.",
    6: "Changing what an existing member or operation means, under its existing spelling, bumps",
    7: "Reordering members is a reviewed diff, not a version event.",
    8: "Wire-enumerated sets are outside the additive class.",
    9: "No change to kind, relationType or the operation set bumps protocolVersion",
    10: "theurian compat check does not surface an enum mismatch",
}


def test_the_decision_section_holds_exactly_decisions_one_to_ten() -> None:
    decisions = _numbered(_section("## Decision"))

    assert sorted(decisions) == sorted(DECISIONS)
    assert [n for n, anchor in DECISIONS.items() if not decisions[n].startswith(anchor)] == []


def test_the_two_statements_of_the_rule_carry_the_meaning_clause() -> None:
    """Cut to "whose grammar admits the document", the rule lets Core write a changed
    meaning under an earlier version whose grammar also admits it (decision 6).

    Key: the anchor "lowest apiVersion (or version) whose grammar admits", stated
    exactly at decision 3 and matrix row C2. A paraphrase of the rule that does not
    spell the anchor is outside the key.
    """
    anchor = r"lowest (?:apiVersion |version )whose grammar admits"
    tail = "the document and under which it carries the meaning Core wrote"
    [c2] = [row for row in _table(_section("### The matrix"))[1:] if row[0].startswith("**C2**")]
    sites = {"decision 3": _numbered(_section("## Decision"))[3], "C2": collapsed(c2[1])}

    assert {site: len(re.findall(anchor, text)) for site, text in sites.items()} == {
        "decision 3": 1,
        "C2": 1,
    }
    assert len(re.findall(anchor, _adr())) == 2, "the rule is stated outside decision 3 and C2"
    assert [
        site for site, text in sites.items() if f"whose grammar admits {tail}" not in text
    ] == []


#: The alternatives table's first column, in order.
ALTERNATIVES: Final = [
    "Bump apiVersion for every new vocabulary member",
    "A tolerant reader: an older Core admits an unknown member as opaque, or skips the operation",
    "Removal as a version event: drop the member from a new apiVersion's read grammar",
    "Make theurian compat check detect an enum mismatch (roadmap §4's third clause)",
    "Bump protocolVersion for a vocabulary or grammar change",
    "Stop bumping apiVersion for operations too",
]


def test_the_alternatives_table_holds_exactly_the_six_it_weighs() -> None:
    rows = _table(_section("## Alternatives considered"))

    assert rows[0] == ("Alternative", "Why rejected")
    assert [collapsed(row[0]) for row in rows[1:]] == ALTERNATIVES


def test_the_compliance_section_names_this_module_whose_docstring_states_its_reach() -> None:
    assert Path(__file__).resolve().relative_to(REPO_ROOT).as_posix() == THIS_MODULE
    assert collapsed(f"`{THIS_MODULE}`, the claims pin.") in _adr()
    assert "**Reach.**" in (__doc__ or "")


#: What ADR-0039 still has to say, in its own markup and wrapped at will: the
#: comparison collapses whitespace, emphasis and code markers on both sides.
#: Each fragment's fact half is the test, here or in a sibling module the module
#: docstring names, that carries the same claim.
ADR_STATES: Final[dict[str, str]] = {
    "measured-at": """**Every repository fact below was measured on 2026-09-30 against
        `origin/main` at `f0e4d754`.**""",
    "schema-lines": """prints 21 lines: the `apiVersion` const, six enums, and the fourteen
        `op` consts of `$defs/operation.oneOf`'s fourteen branches.""",
    "engine-version": """`domain/migration.py` also holds `MIGRATION_ENGINE_VERSION = 1`, which
        is hashed into the state hash (`domain/state.py:132`) and reported as `engineVersion`
        by `theurian project status`""",
    "adr-0005-silent": """It said nothing of adding a `kind` or `relationType` member, of
        removing, renaming or reordering anything, or of changing what a member means.""",
    "entry-key": """The population is keyed on the three classes the additive policy governs —
        `KnowledgeKind`, `RelationType` and `OperationKind` — at every place a value becomes
        one of them: a construction from a value, a call of the MCP layer's closed-set parser
        with one of them, and a CLI option typed by one of them.""",
    "entry-accounting": """Of the first command's twelve lines, three are class statements and
        four are the loader's constructions, which run after schema validation, so no unknown
        value reaches them.""",
    "outside": """`SqliteCanonicalStore.count_surfaceable_by_status` constructs `Sensitivity`
        from a stored row (`infrastructure/sqlite/store.py:845`), and `knowledge.status` reaches
        it""",
    "loader-row": """`<file> is invalid at operations/<N>: does not satisfy 'oneOf' (expected
        [...]); the value there is {...}`. The location is the operation index and the keyword
        is the discriminated `oneOf`, which fails whole, so neither the field nor the enum that
        failed is named""",
    "loader-cases": """An unknown `kind` in `createItem` or in `upsertRevision.metadata`,
        `relationType`, `status`, `sensitivity`, `trustLevel` or `op`, and a
        `registerSpecification` renamed away, were each refused so, with the value inside the
        echo; the unmodified copy loads.""",
    "truncation": """An `addRelation` carrying a legal 990-character `note` (its `maxLength` is
        1,000, and `note` sorts before `relationType` in the echo) and `relationType: traces_to`
        was refused with `traces_to` nowhere in the 1,315-character message; the same operation
        with `constrained_by` loads""",
    "api-version": """before the compiled comparison at `:1613` is reached. With
        `theurian.dev/v2` and an unknown `kind` in one document, this is the refusal reported""",
    "validator": """`MigrationError`: `invalid migration at operations/<N>: does not satisfy
        'oneOf' ...`""",
    "v1-gate": """The v1 gate skips an unknown `op` (`OperationKind(raw)` at `:3767-3770`
        `continue`s) and leaves it to the injected validator""",
    "wrap": """`_refuse_a_document_the_schema_rejects` wraps the validator's refusal as
        `ProposalError`""",
    "closed-value": "No: it names the field and the valid set",
    "typer": """Option parsing, Typer `BadParameter`: `'requirement' is not one of
        'architecture', ..., 'known-exception'.`""",
    "okf-concept": """`ImportRefusal(kind="concept", key=<file>,
        literal="unrecognized type: 'specification'")`; other concepts still import""",
    "okf-relations": """Carried verbatim into an `addRelation` in the one relations draft. An
        unknown type fails that draft's validation, and the whole draft is refused as one
        `ImportRefusal(kind="relations", key="addRelation")`""",
    "store-guard": """every store read runs its decoder inside `_reading()` (`_read_one`,
        `:418-420`), which re-raises it as `StateDatabaseUnreadableError`""",
    "store-cause": "No: the message names only the exception type; the value is on `__cause__`",
    "state-inputs": """The state hash's inputs are the migration ids and checksums, the body
        checksums, `SCHEMA_VERSION` and `MIGRATION_ENGINE_VERSION` (`domain/state.py:47-54`); no
        Core version and no enum membership.""",
    "cli-loads": "`resolve_state_hash(loaded: LoadedMigrations, ...)`",
    "mcp-no-load": """The MCP tools do not load migrations: they open the database the
        project's active-state pointer names (`mcp/tools.py:1769`, `read_active_state`; `:1817`,
        `state_database_named`).""",
    "compat-signature": """`resolve_compatibility(declaration, core_version,
        core_protocol_version)`""",
    "compat-outcomes": """decides one of `compatible`, `core-missing`, `core-too-old`,
        `core-too-new` and `protocol-mismatch`""",
    "compat-options": """The command's options are `--plugin-version`, `--core-minimum`,
        `--core-maximum-exclusive`, `--protocol-version` and `--json`""",
    "compat-protocol": """`CURRENT_PROTOCOL_VERSION = "theurian/v1"`
        (`domain/compatibility.py:125`). It reads no project, no migration and no enum.""",
    "detection-keys": """An object key equal to a member is an occurrence unless it is a JSON
        Schema 2020-12 keyword in keyword position; keys under `properties`, `$defs`,
        `definitions`, `dependentSchemas` and `dependentRequired` are names, so they are always
        candidates, and the `deprecated` annotation is the live keyword case.""",
    "detection-values": """A string value equal to a member is an occurrence wherever it sits —
        `enum`, `const`, `default`, `examples`, `required`, a `propertyNames` subschema — except
        under `description`, `title`, `$comment`, `$schema`, `$id`, `$ref`, `$anchor` or an
        `x-` key, whose values are prose or identifiers.""",
    "detection-pattern": """And a `pattern` value or a `patternProperties` key is an occurrence
        of each member it spells as a whole word of the regex source, with `-` and `_` counted as
        word characters, so `rejected-approach` and `depends_on` match whole and `domain` does
        not match inside `domain-behavior`.""",
    "pasted-control": """*Tripwire.* That population is held exact. A new occurrence of any
        member, in any of those positions, is a change a person classifies before it is
        accepted.""",
    "enumerates-key": """A closing construct is an `enum`, a `const`, or a `oneOf` or `anyOf`
        whose every branch is one of those, and it enumerates a governed set when every value it
        admits other than null is a member of that set. An object closes its property names
        through `additionalProperties: false` or through `propertyNames`.""",
    "more-classes": """The classifier has two more classes, `definition-name` for a key under
        `$defs` or `definitions` and `custom-key` for any other non-keyword key, and neither
        occurs today.""",
    "migration-population": """The migration schema's own population is 61 occurrences: 57
        enumerated, 2 non-closing defaults (`unverified`, `internal`) and 2 property names
        (`operations` as a property and as a `required` entry).""",
    "enumerates-found": """Outside the migration schema, only `enumerated` and `key-closure`
        close a published value or key set to a governed set, no `overlap` contains a whole
        governed set, and none of them holds a member of `kind`, `relationType` or the operation
        set.""",
    "overlaps": """`review-generate-knowledge-candidate-input`'s `category`: `rejected-approach`
        and `known-exception`""",
    "overlaps-reviewer": "`review-findings-response`'s `reviewer`: `security`",
    "hole": """*What the scan cannot report.* Each of these is classified by a person when it
        appears, not passed silently.""",
    "ref-hole": """A `$ref` spells a pointer, not a member: a schema that `$ref`s
        `migration.schema.json#/$defs/kind` would close a value to `KnowledgeKind` with no member
        spelled in its own file.""",
    "ref-population": """prints 10 cross-file `$ref`s, to `tool-context`, `retrieval-result` and
        `retrieval-metadata` — so the claims pin holds that population exact beside the
        scan.""",
    "pattern-hole": """A `pattern` that closes a value without spelling a member, a character
        class for instance, is not an occurrence.""",
    "default-hole": """A member embedded in a longer value that is not a pattern, such as a
        `default` or `examples` entry spelled `"status:draft"`, is not an occurrence either""",
    "superset-hole": """And a construct that carries a whole governed set plus other members
        classifies as `overlap`, not `enumerated`; the scan still reports each member it spells,
        and a person decides whether it publishes the set.""",
    "items-by-status": """its counts come from `count_surfaceable_by_status`, which reads
        `SURFACEABLE_STATUSES` directly""",
    "items-by-status-measured": """Measured: that object schema refuses `{"superseded": 1}` and
        accepts `{"approved": 1}`.""",
    "record-kind-silent": """and none of its values spells a member, so the scan reports
        nothing there.""",
    "inputs": """type `kind` as a string (and the first types `trustLevel` and `sensitivity` as
        string or null), and `knowledge-generate-migration-draft-input` constrains no `op`.""",
    "record-kind": """`review-search-response`'s `records.items.kind`, `pull-request`,
        `review-submission` and `review-thread`""",
    "two-fields": """Values still travel, un-enumerated, in two response fields, neither under
        a response schema: `knowledge.get` publishes each visible relation's `relationType`""",
    "operations-field": """`knowledge.generateMigrationDraft` publishes the drafted document's
        operation names as `operations` (`_drafted_migration_payload`""",
    "no-kind": """No MCP response publishes a `KnowledgeKind`: under `mcp/`, it appears only as
        the two `_closed_value` inputs above, and the one `.kind` published is the review
        record's""",
    "search-ref": """`knowledge-search-response` types each result by `$ref` to
        `schemas/knowledge/retrieval-result.schema.json`, which enumerates `status` as the three
        surfaceable members (`approved`, `draft`, `proposed`), `trustLevel` (4) and
        `sensitivity` (4); `mcp/results.py:95-97` publishes all three on every result.""",
    "plugins": """`plugins/` uses a member in two places and parses none""",
    "plugins-count": "The key is all 39 `kind`, `relationType` and `op` values, as whole words",
    "plugins-lines": "It prints 37 lines, all in Markdown.",
    "freeze": """`verify_no_applied_migration_changed` (`application/migration_engine.py:168`)
        refuses an applied migration whose checksum moved""",
    "gates": "`status` feeds `may_surface` and `sensitivity` feeds `may_disclose`",
    "trust-neither": "`trustLevel` feeds neither gate",
    "trust-inferred": "which import and candidate generation fix at `inferred`",
    "stamps": """the two lines under `packages/theurian-core/src` that stamp `apiVersion` are
        both in `application/proposal_service.py`""",
    "v1-enumerated": """`V1_OPERATION_KINDS` is already that pattern for the operation set —
        enumerated rather than derived as `frozenset(OperationKind) - refused`""",
    "reorder-test": """holds `$defs/relationType` equal to `RelationType` as an ordered list
        because "a reorder is a diff its reviewer should see\"""",
    "count-test": "asserts `len(RelationType) == 14`",
    "partition-test": """`test_the_v1_operation_set_partitions_operation_kind` fails when an
        `OperationKind` is routed nowhere.""",
    "config-status": """A change to `status` moves, besides `retrieval-result.schema.json`'s
        `status` enum: `itemsByStatus`, whose key set is exactly `SURFACEABLE_STATUSES` and is
        `knowledge.status`'s published T-17 contract, a second tool's; and
        `project-config.schema.json`'s `retrieval.includeStatuses`, which lists all six members,
        a published configuration schema rather than the wire.""",
    "config-api-version": """`schemas/config/project-config.schema.json` carries its own
        `apiVersion` const with the same spelling, `theurian.dev/v1`.""",
    "reach": "Its reach is stated in its module docstring.",
    "decode-order": """`knowledge.get`'s first store read is `get_item_metadata` →
        `_item_from_row` (`mcp/tools.py:2343`), before its `may_surface`/`may_disclose` gate
        (`:2351-2352`); `list_relations` decodes every edge inside `_read_all`
        (`store.py:968-973`) before `_relation_is_visible` filters them (`mcp/tools.py:2415`);
        `_revision_from_row` runs after the gate""",
    "owed-853": """3. **[#853](https://github.com/theurian/theurian/issues/853), what an older
        Core does with a state database a newer build wrote**, due before the first `kind` or
        `relationType` member, whichever slice adds it.""",
    "owed-853-faces": """Its faces are keyed on the gate register decision 6 uses,
        `STATUS_GATE_CALL_SITES` and `DISCLOSURE_GATE_CALL_SITES`: every gate site that decodes
        a row before it judges it. Three are established. `knowledge.get`'s `get_item_metadata`
        and `list_relations` decode ahead of its gate (*Context*), and a known-type edge from a
        visible item to a withheld neighbour carrying an unknown member raises through
        `_relation_is_visible` → `get_item_exact_metadata` → `_item_from_row`""",
    "owed-853-more": """The write tools' `current_revision` (`mcp/tools.py:1942`) and
        `CanonicalVisibility.item` (`application/visibility.py:372`) decode before their gates
        too, read from source.""",
    "due-849-853": """It and #853, the check at open that keeps an older Core from decoding a
        newer build's database row by row (*Context*, *The derived store*), are both due before
        the first `kind` or `relationType` member, whichever slice adds it.""",
    "d3-cost": """Keeping the bump costs one permanently read `apiVersion` per added operation
        and the multi-version read the first bump must build""",
    "d6-routing": """A change whose effect reaches `status`, `sensitivity` or the withdrawal
        purge, or that moves which rows a gate reads — alias resolution (T-21), an item's
        `current_revision_id` and the served-content hash bound to it
        (`current_served_content_sha256`, GHSA-3f65), or relation rows — does not take this
        path; it takes decision 8's ADR-first route.""",
    "d6-replay": """It needs a named corpus that exercises every operation whose effect
        changes, including planted inputs the old engine refused, and that holds a withheld
        row; a positive control showing that the comparison tells the two engines apart when
        one is perturbed; and a comparison of each migration's apply outcome and of the OKF
        export bundle, as well as of tool responses.""",
    # Fact half: test_adr_0038_records.py's two committed-YAML population pins.
    "d6-corpus": """the dogfood corpus names neither retiring operation (`git grep -c -E
        'registerSpecification|supersedeSpecification' -- .theurian/migrations/` prints nothing),
        the one committed document that does is the sample project's `registerSpecification`""",
    "engine-unread": """`create_database` writes `schema_metadata.engine_version`
        (`infrastructure/sqlite/connection.py:1075`), and the only read of `schema_metadata` at
        open selects `schema_version` alone (`:1084`); no statement under
        `packages/theurian-core/src` selects the `engine_version` column.""",
    "serve-path": "The serve-path check is #853's.",
    "readers-register": """A reader of the canonical state is any site in
        `STATUS_GATE_CALL_SITES` or `DISCLOSURE_GATE_CALL_SITES`
        (`packages/theurian-core/tests/unit/test_gate_call_sites.py`) — the index builder, the
        withdrawal purge and the OKF export among them — plus `knowledge.status`'s counts""",
    "retiring-parse": """the loader's parse of a v1 `registerSpecification` keeps reading its
        `specId` and `status`""",
    "d9-key": """because *Context*'s scan finds no occurrence of their members outside the
        migration format that closes a value or a key set to one of them: the occurrences there
        are `KnowledgeKind` members in two overlaps (`category`, `reviewer`), in a pattern over
        another vocabulary (`traceabilityPolicy`) and in one property name (`security`), and no
        `relationType` or operation member occurs at all.""",
    "d9-fields": """in the two response fields that publish any of the three: a
        `relationType` in `knowledge.get`'s `relations`, and an operation name in
        `knowledge.generateMigrationDraft`'s `operations`.""",
    "c3-examples": """The removal updates the examples that advertise a member:
        `plugins/claude-code/commands/propose.md:51` (`--kind architecture`), and the `kind`
        descriptions of `knowledge-propose-change-input` ("e.g. architecture, decision,
        security") and `review-generate-knowledge-candidate-input` ("e.g. convention,
        architecture, security")""",
    "declined": "Declined here, not refuted",
}


@pytest.mark.parametrize(("name", "fragment"), ADR_STATES.items(), ids=list(ADR_STATES))
def test_the_adr_still_states(name: str, fragment: str) -> None:
    assert collapsed(fragment) in _adr(), (
        f"ADR-0039 no longer states ({name}):\n\n  {collapsed(fragment)}\n\nIf the fact half is "
        f"GREEN, the tree did not move and the record is what gets restored; if it is RED, the "
        f"sentence moves with the tree."
    )
