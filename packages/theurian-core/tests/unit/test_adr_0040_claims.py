"""ADR-0040's claims, held from both sides (#1036, Part of #275).

ADR-0040 ships no code, so each sentence it states about the repository was
measured once, at ``5ad30799``. A *fact* pin recomputes it from live source or
schema and goes RED when the tree moves; a *prose* pin goes RED when the record
drifts. A dated fact's failure message names #1033 or #1034 and the sentence to
update.

**Reach.** Each key, beside the claim it holds:

- *Prose* is matched on the whitespace- and markup-collapsed text of the named
  section or file (``collapsed``), never on a line number. A fragment is held in
  the section its heading prefix names, so it moving elsewhere is RED.
- *Absent* fragments and the round-label regex are scanned over the whole text of
  ADR-0040, ``docs/roadmap.md``, ADR-0016 and ADR-0039, not over a section.
- *The roadmap's Security-row quotes* are three fragments, each required verbatim
  in the roadmap's Phase C Security row and in ADR-0040 §6's blockquote.
- *``ingest`` reads no Git log and opens no state database* is the set of names
  ``ingest_command``'s own body spells; a read through a function it calls is
  outside the key.
- *Callers of ``must_be_acyclic``* are source lines under ``src`` matching
  ``\\.must_be_acyclic([^a-z_]|$)``; a call through an alias is outside it.
- *Adapters of the edge port* are source lines under ``src`` outside
  ``domain/ports/`` matching ``def (add_traceability_edge|list_traceability_edges)[(]``.
- *Append-only* is ADR-0016 and ADR-0039 against ``5ad30799``: every base line
  survives in order, nothing follows the last one but ``Amendment 1``, and every
  run of lines inserted before it opens with ``*Amended in Amendment`` and is indented
  throughout (an indented stray line beside a marker passes).

Decision 2's identity and digests, 3(c)'s cap and decision 5's guard have no fact
side until #1033 builds them.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import jsonschema
import pytest
from adr_0037_support import collapsed
from adr_0039_support import (
    ADR_DIR,
    MIGRATION_SCHEMA,
    REPO_ROOT,
    ROADMAP,
    SRC,
    _blockquotes,
    _function,
    _git,
    _named,
    _schema,
    _scoped,
    _section,
    _table,
    _trees,
)

from theurian.domain.identifiers import _DOTTED_PATTERN, ItemId
from theurian.domain.migration import MIGRATION_ENGINE_VERSION
from theurian.domain.state import StateInputs
from theurian.infrastructure.sqlite.schema import DDL, SCHEMA_VERSION, is_supported

pytestmark = pytest.mark.unit

ADR: Final = ADR_DIR / "0040-traceability-representation-and-edge-sources.md"
ADR_0016: Final = ADR_DIR / "0016-state-hash-covers-the-working-tree.md"
ADR_0039: Final = ADR_DIR / "0039-closed-set-extension-compatibility.md"
CONTRIBUTING: Final = REPO_ROOT / "CONTRIBUTING.md"
ORCHESTRATION: Final = REPO_ROOT / "docs" / "contributing" / "orchestration.md"
BASE: Final = "5ad30799"
DATED: Final = "Dated fact (ADR-0040 {where}): #{issue} moves it on purpose. Update {sentence}."


def _text(path: Path) -> str:
    return collapsed(path.read_text(encoding="utf-8"))


def _section_lines(prefix: str) -> list[str]:
    """The lines of the ADR section whose heading starts with ``prefix``."""
    lines = ADR.read_text(encoding="utf-8").splitlines()
    [heading] = [line for line in lines if line.startswith(prefix)]
    return _section(heading, ADR)


def _under(prefix: str) -> str:
    return collapsed(" ".join(_section_lines(prefix)))


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(DDL)
    return connection


def _tables() -> dict[str, set[str]]:
    connection = _database()
    names = [
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    columns = {
        name: {row[1] for row in connection.execute(f"PRAGMA table_info({name})")} for name in names
    }
    connection.close()
    return columns


def _indexed_columns(table: str) -> set[str]:
    connection = _database()
    indexes = [row[1] for row in connection.execute(f"PRAGMA index_list({table})")]
    columns = {
        row[2] for index in indexes for row in connection.execute(f"PRAGMA index_info({index})")
    }
    connection.close()
    return columns


# --- Fact pins: standing -----------------------------------------------------


def test_the_schema_version_check_is_an_exact_match() -> None:
    """ADR-0040 decision 4 rests on a v4 build refusing v5 and the reverse (ADR-0017)."""
    assert is_supported(SCHEMA_VERSION)
    assert not is_supported(SCHEMA_VERSION - 1)
    assert not is_supported(SCHEMA_VERSION + 1)


def _callers(relative: str, name: str) -> set[str]:
    return {
        scope
        for node, scope in _scoped(_trees()[relative])
        if isinstance(node, ast.Call) and _named(node.func) == name
    }


def test_both_openers_of_an_existing_database_reach_the_version_check_through_prepare() -> None:
    """A path that opened a state database without `_prepare` would read rows of a wrong version."""
    connection = "infrastructure/sqlite/connection.py"
    select = _function(connection, "_assert_schema_version")

    assert _callers(connection, "_prepare") == {"open_read_connection", "_open_transaction"}
    assert _callers(connection, "_assert_schema_version") == {"_prepare"}
    assert _callers(connection, "_connect") == {
        "open_read_connection",
        "create_database",
        "_open_transaction",
    }
    assert "SELECT schema_version FROM schema_metadata" in ast.unparse(select)


def test_an_add_relation_endpoint_is_always_an_item_id() -> None:
    """So a module cannot be an endpoint of a migration-declared relation (F7)."""
    definition = _schema(MIGRATION_SCHEMA)["$defs"]["opAddRelation"]
    item = {"$ref": "#/$defs/itemId"}

    assert {"sourceItemId", "targetItemId"} <= set(definition["required"])
    assert definition["properties"]["sourceItemId"] == item
    assert definition["properties"]["targetItemId"] == item
    assert definition["additionalProperties"] is False


def _labels() -> dict[str, object]:
    labels: dict[str, object] = _schema(MIGRATION_SCHEMA)["$defs"]["revisionMetadata"][
        "properties"
    ]["labels"]
    return labels


def test_a_label_is_unique_within_one_revision_only_and_names_no_single_item() -> None:
    """Resolving a label to an item would pick one of many (decision 3's no-resolution rule)."""
    validator = jsonschema.Draft202012Validator(_labels())
    tables = _tables()

    assert validator.is_valid(["a", "b"])
    assert not validator.is_valid(["a", "a"])
    assert "labels" in tables["knowledge_revisions"]
    assert not [name for name in tables if "label" in name]
    assert "labels" not in _indexed_columns("knowledge_revisions")


def test_adr_0040_is_both_a_valid_label_and_a_valid_item_id() -> None:
    """The ambiguity decision 3's `Refs: adr-0040` example rests on."""
    validator = jsonschema.Draft202012Validator(_labels())

    assert _labels()["items"] == {"type": "string", "maxLength": 64}
    assert validator.is_valid(["adr-0040"])
    assert not validator.is_valid(["x" * 65])
    assert _DOTTED_PATTERN.match("adr-0040")
    assert str(ItemId("adr-0040")) == "adr-0040"


def test_the_state_hash_reads_no_git() -> None:
    """Its inputs are the whole of what it hashes; a Git input would make every commit move it."""
    assert {f.name for f in dataclasses.fields(StateInputs)} == {
        "migrations",
        "content_checksums",
        "schema_version",
        "engine_version",
    }


def test_the_migration_engine_version_has_not_been_bumped() -> None:
    """Decision 5 rejects the bump: it would invalidate every state built from an acyclic set."""
    assert MIGRATION_ENGINE_VERSION == 1


_CALL_KEY: Final = re.compile(r"\.must_be_acyclic([^a-z_]|$)")


def _acyclic_callers(root: Path) -> list[str]:
    return [
        f"{path.relative_to(root)}:{number}"
        for path in sorted(root.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _CALL_KEY.search(line)
    ]


def _sequencing_holds(schema_version: int, callers: Sequence[str]) -> bool:
    return schema_version < 5 or bool(callers)


def test_the_key_for_callers_of_must_be_acyclic_finds_a_planted_call(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = relation.must_be_acyclic()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def must_be_acyclic(self): ...\ny.must_be_acyclic_x\n")

    assert _acyclic_callers(tmp_path) == ["a.py:1"]


def test_the_sequencing_coupling_is_false_for_a_v5_schema_with_no_enforcing_caller() -> None:
    assert not _sequencing_holds(5, [])
    assert _sequencing_holds(5, ["x.py:1"])
    assert _sequencing_holds(4, [])


def test_the_schema_bump_and_inv_6_enforcement_land_together() -> None:
    """Decision 5's sequencing condition: v5 without the guard serves a cyclic state forever."""
    assert _sequencing_holds(SCHEMA_VERSION, _acyclic_callers(SRC)), (
        "SCHEMA_VERSION >= 5 with no caller of must_be_acyclic under src: "
        "ADR-0040 Compliance item 1 requires both in one pull request"
    )


# --- Fact pins: dated --------------------------------------------------------


def test_the_schema_version_is_four() -> None:
    assert SCHEMA_VERSION == 4, DATED.format(
        where="F6", issue=1033, sentence="F6's 'SCHEMA_VERSION is 4' and decision 4"
    )


def test_schema_metadata_has_the_columns_it_had_at_the_base() -> None:
    assert _tables()["schema_metadata"] == {
        "id",
        "schema_version",
        "engine_version",
        "state_hash",
        "created_at",
    }, DATED.format(where="decision 4", issue=1033, sentence="decision 4's `schema_metadata` gains")


def test_traceability_edges_has_no_source_commit_column() -> None:
    assert "source_commit" not in _tables()["traceability_edges"], DATED.format(
        where="F6", issue=1033, sentence="F6's 'has no source_commit column'"
    )


_PORT_KEY: Final = re.compile(r"def (add_traceability_edge|list_traceability_edges)[(]")


def _port_definitions(root: Path) -> list[Path]:
    return [
        path.relative_to(root)
        for path in sorted(root.rglob("*.py"))
        if _PORT_KEY.search(path.read_text(encoding="utf-8"))
    ]


def test_no_adapter_defines_the_edge_port_methods() -> None:
    """The key without the exclusion finds the port itself, which is its positive control."""
    everywhere = _port_definitions(SRC)
    adapters = [path for path in everywhere if "domain/ports/" not in path.as_posix()]

    assert Path("domain/ports/canonical_store.py") in everywhere
    assert not adapters, DATED.format(
        where="F5", issue=1033, sentence="F5's 'No adapter implements the edge port'"
    )


def test_must_be_acyclic_has_no_caller_under_src() -> None:
    assert not _acyclic_callers(SRC), DATED.format(
        where="F8", issue=1033, sentence="F8's 'has no caller in src' and decision 5"
    )


_GIT_LOG_AND_STATE: Final = frozenset(
    # The lock alone opens no state database; write_transaction, which takes it, is in the set.
    {
        "GitTrailerFindingSource",
        "FixCommitCheck",
        "create_database",
        "write_transaction",
        "open_read_connection",
        "SqliteCanonicalStore",
        "SqliteWriter",
        "SqliteIndexStore",
    }
)


def test_ingest_imports_no_git_log_adapter_and_opens_no_state_database() -> None:
    """The roadmap row moved collection out of `ingest` because it does neither (F10)."""
    commands = "cli/commands.py"
    spelled = {
        name
        for node in ast.walk(_function(commands, "ingest_command"))
        if (name := _named(node)) is not None
    }
    module = {name for node in ast.walk(_trees()[commands]) if (name := _named(node)) is not None}

    assert {"create_database", "write_transaction"} <= module
    assert not spelled & _GIT_LOG_AND_STATE, DATED.format(
        where="F10 / the roadmap's Phase C Architecture row",
        issue=1033,
        sentence="F10 and decision 2's 'Not in ingest'",
    )


# --- Prose pins: present -----------------------------------------------------

PRESENT: Final[dict[str, tuple[str, str]]] = {
    "the unchanged-hash sentence": (
        "### 2. ",
        "A commit that changes no migration and no body a migration references leaves "
        "state_hash unchanged.",
    ),
    "the identity sentence": (
        "### 2. ",
        "A state's identity is (state_hash, walk commit, boundary), and two states with one "
        "identity hold the same edges; a state whose collection was capped or bounded never "
        "shares an identity with one whose was not.",
    ),
    "the shallow digest derivation": (
        "### 2. ",
        "The shallow digest is the lowercase hex SHA-256 of the clone's shallow-commit list — "
        "the shas in the file git rev-parse --git-path shallow names, sorted byte-wise, each "
        "followed by a newline — and null when no such file exists, which is a full clone.",
    ),
    "the capped digest derivation": (
        "### 3. ",
        "The capped digest is the lowercase hex SHA-256 of the capped commits' shas, sorted "
        "byte-wise, each followed by a newline, and null when there are none",
    ),
    "the depth bound": (
        "### 2. ",
        "at most 10,000 first-parent commits, the walk commit first, and a read budget of "
        "100,000 path entries and 64 MiB, counted as each stream arrives, never read whole, "
        "capped commits included. Git is terminated where a commit would cross a bound; it and "
        "every older commit are not collected, and the boundary records which bound stopped "
        "the walk.",
    ),
    "the cap": (
        "### 3. ",
        "A commit naming more than 32 distinct valid ids yields no edges, and the boundary "
        "records it.",
    ),
    "the write bound": (
        "### 2. ",
        "So one apply writes at most 32 \N{MULTIPLICATION SIGN} 100,000 = 3,200,000 edges.",
    ),
    "the refresh rule": (
        "### 2. ",
        "Every migrate apply collects, including one with nothing pending, and replaces the "
        "whole edge set, the recorded walk commit and the boundary inside its write transaction.",
    ),
    "a boundary commit yields no edges": (
        "### 2. ",
        "A commit the list names yields no edges",
    ),
    "no version bump": (
        "### 5. ",
        "No apiVersion, protocolVersion or MIGRATION_ENGINE_VERSION bump.",
    ),
    "the state hash reads no Git": (
        "### Two further facts",
        "(standing) The state hash reads no Git.",
    ),
    "the guard's predicate": (
        "### 5. ",
        "The guard validates the whole relation set the apply leaves, on every apply, including "
        "one with nothing pending, so a cyclic set built by a non-enforcing Core is refused at "
        "its next apply.",
    ),
    "the sequencing predicate": (
        "### 5. ",
        "Under that condition, every state an enforcing Core serves or applies into was built "
        "by an enforcing Core.",
    ),
    "the sequencing condition": (
        "## Compliance",
        "Sequencing condition: decision 5's guard, on every apply, and RelationCycleError land "
        "in one commit with the SCHEMA_VERSION 4→5 bump, on the branch and so in the squash — "
        "never earlier (under v4 a cyclic state built by core-v0.5.3 hashes identically and is "
        "reused; its refused re-apply rolls back and keeps serving it), never later (a Core "
        "built between them serves a v5 state no guard checked, and a rule that no core-v* tag "
        "sits between them leans on release ordering, which this ADR rejects).",
    ),
    "the census sentence": (
        "## Compliance",
        "build: 393 units in 200 files, read at every match (FR-K8's row is a second match). "
        "The 25 units it finds false are listed, file by file, under [#1033's Sentences this "
        "slice falsifies](https://github.com/theurian/theurian/issues/1033#sentences-this-slice-falsifies);",
    ),
}


@pytest.mark.parametrize(("heading", "sentence"), PRESENT.values(), ids=list(PRESENT))
def test_the_adr_still_states(heading: str, sentence: str) -> None:
    assert collapsed(sentence) in _under(heading)


ROADMAP_QUOTES: Final = (
    "Apply the per-edge visibility gate at every hop — and apply it in T-21's corrected form, "
    "because per-edge gating on its own is what leaked: a traversal hop must not resolve an "
    "alias when deciding authority.",
    "Reachability may resolve; the visibility decision on each hop's endpoint reads the "
    "literally-named row's metadata — through get_item_exact_metadata or an equivalent that "
    "neither resolves an alias (T-21) nor reads the body (T-26), never the joined "
    "get_item_exact: a gate on that read materialises a withheld endpoint's body before "
    "withholding it, so the refusal's duration carries the body's size.",
    "Write the two-corpora equality test for trace responses in the same change as the "
    "implementation — not afterwards.",
)


def _security_row() -> str:
    [row] = [
        line
        for line in ROADMAP.read_text(encoding="utf-8").splitlines()
        if line.startswith("| **Security** |") and "per-edge visibility gate" in line
    ]
    return collapsed(row)


@pytest.mark.parametrize("quote", ROADMAP_QUOTES, ids=["T-21", "T-26", "two-corpora"])
def test_decision_6_quotes_the_roadmap_security_row_verbatim(quote: str) -> None:
    """If the roadmap row changes, the ADR's quote of it is stale."""
    quoted = " ".join(_blockquotes(ADR))

    assert collapsed(quote) in _security_row()
    assert collapsed(quote) in quoted


def test_the_response_table_holds_exactly_the_six_recorded_edges_fields() -> None:
    rows = _table(_section_lines("### 7. "))

    assert [collapsed(row[0]) for row in rows[1:]] == [
        "recordedEdgesCommit",
        "recordedEdgesWalked",
        "recordedEdgesHistoryShallow",
        "recordedEdgesDepthBounded",
        "recordedEdgesBudgetBounded",
        "recordedEdgesCapped",
        "recordedEdgesCoverage",
    ]


def test_contributing_states_the_refs_rule() -> None:
    assert collapsed(
        "A commit that relates to a knowledge item says so with a `Refs: <item id>` line at "
        "column 0, one line per item, where `<item id>` is the item's literal id."
    ) in _text(CONTRIBUTING)


def test_orchestration_says_a_squash_drops_a_refs_trailer_not_carried_in_the_body() -> None:
    assert collapsed(
        "The squash drops a `Refs:` trailer not carried in the body exactly as it drops "
        "`Review-Finding:`, and the edge it declares is then never recorded from `main`"
    ) in _text(ORCHESTRATION)


def test_the_roadmap_architecture_row_no_longer_plans_three_sources_collected_by_ingest() -> None:
    roadmap = _text(ROADMAP)

    assert "Three edge sources" not in roadmap
    assert "commit trailers (Refs: ADR-0021) collected by ingest" not in roadmap
    assert "decision 1 decides it: a Refs: <item id> commit trailer" in roadmap


# --- Prose pins: absent ------------------------------------------------------

ABSENT: Final = (
    "HEAD's commit joins the state hash",
    "joins the hash",
    "Every commit moves the state hash",
    'a fixed "no commit" input',
    "MIGRATION_ENGINE_VERSION goes from 1 to 2",
    "the MIGRATION_ENGINE_VERSION bump",
    "with the commit joining the state hash",
    "INV-6 pull request",
    "rejected in R2",
)
ROUND_LABEL: Final = re.compile(r"(?<![-\w])R[1-3]\b|\(\(c\)")
FILES: Final = {"ADR-0040": ADR, "roadmap": ROADMAP, "ADR-0016": ADR_0016, "ADR-0039": ADR_0039}


#: ADR-0039 *Still owed* item 2 says "the MIGRATION_ENGINE_VERSION bump" of #841's retirement,
#: a different engine bump from the one decision 5 rejects, so it is not held there.
EXEMPT: Final = {("ADR-0039", "the MIGRATION_ENGINE_VERSION bump")}
ABSENT_PAIRS: Final = [
    pytest.param(name, fragment, id=f"{name}-{fragment}")
    for name in FILES
    for fragment in ABSENT
    if (name, fragment) not in EXEMPT
]


@pytest.mark.parametrize(("name", "fragment"), ABSENT_PAIRS)
def test_a_rejected_position_stays_out_of_the_record(name: str, fragment: str) -> None:
    assert collapsed(fragment) not in _text(FILES[name])


@pytest.mark.parametrize("path", FILES.values(), ids=list(FILES))
def test_no_review_round_label_survives_in_the_record(path: Path) -> None:
    assert not ROUND_LABEL.findall(path.read_text(encoding="utf-8"))


def test_the_round_label_key_matches_a_label_and_spares_a_requirement_id() -> None:
    assert ROUND_LABEL.search("rejected in R2")
    assert ROUND_LABEL.search("the ((c) alternative")
    assert not ROUND_LABEL.search("FR-R1 and R10")


# --- Append-only -------------------------------------------------------------


def _append_only_breach(base: Sequence[str], working: Sequence[str]) -> str | None:
    """What stops ``working`` being ``base`` plus amendments, or None."""
    at = 0
    runs: list[list[str]] = []
    for line in base:
        start = at
        while at < len(working) and working[at] != line:
            at += 1
        if at == len(working):
            return f"a line of the base is gone or moved: {line!r}"
        runs.append(list(working[start:at]))
        at += 1
    tail = list(working[at:])
    if tail and not (len(tail) > 1 and tail[0] == "" and tail[1].startswith("## Amendment 1")):
        return "something other than an amendment follows the base's last line"
    for run in filter(any, runs):
        if not run[0].lstrip().startswith("*Amended in Amendment") or not all(
            line.startswith(" ") for line in run
        ):
            return f"a line inserted into the base is not an indented amendment marker: {run!r}"
    return None


@pytest.mark.parametrize("path", [ADR_0016, ADR_0039], ids=["ADR-0016", "ADR-0039"])
def test_a_record_amended_by_adr_0040_lost_no_line_of_main(path: Path) -> None:
    """A standing guard against the base commit, as `test_adr_0039_records` is against main."""
    relative = path.relative_to(REPO_ROOT).as_posix()
    base = _git("show", f"{BASE}:{relative}").splitlines()
    working = path.read_text(encoding="utf-8").splitlines()

    assert base, f"{BASE} has no {relative}; `git fetch origin main` first"
    assert _append_only_breach(base, working) is None
    assert "## Amendment 1" in " ".join(working)
    assert any("ADR-0040" in block for block in _blockquotes(path))


def test_the_append_only_check_refuses_a_deleted_a_reordered_and_a_trailing_line() -> None:
    base = ["a", "b", "c"]

    assert _append_only_breach(base, ["a", "b", "c", "", "## Amendment 1 - t", "x"]) is None
    assert _append_only_breach(base, ["a", "b", "c"]) is None
    assert _append_only_breach(base, ["a", "c"])
    assert _append_only_breach(base, ["b", "a", "c"])
    assert _append_only_breach(base, ["a", "b", "c", "edited"])
    assert _append_only_breach(base, ["a", "stray", "b", "c"])
    assert _append_only_breach(base, ["a", " *Amended in Amendment 1*", "b", "stray", "c"])
    assert _append_only_breach(base, ["a", " *Amended in Amendment 1*", "stray", "b", "c"])
    assert not _append_only_breach(base, ["a", " *Amended in Amendment 1* x", " y", "b", "c"])


# --- Fact pin: the sentences #1033 amends -------------------------------------

#: ADR-0040 Compliance's census at 5ad30799 finds these 25 (file, phrase) units false once
#: decision 2's write and decision 5's guard land; the list is #1033's *Sentences this slice
#: falsifies*, rows 1-25 in order. Each phrase occurs in its file exactly as many times as it is
#: listed. Rows 1-3 sit in governed ADRs, which are amended and never trimmed, so they go RED only
#: when an amendment quotes the phrase.
FALSIFIED: Final[tuple[tuple[str, str], ...]] = (
    ("docs/adr/0005-yaml-knowledge-migrations.md", "Re-application is a no-op"),
    (
        "docs/adr/0018-single-writer-synchronous-in-m1.md",
        "already applied and becomes a no-op (idempotence, FR-K8)",
    ),
    ("docs/adr/0018-single-writer-synchronous-in-m1.md", "changes nothing"),
    ("docs/architecture/requirements-analysis.md", "a no-op, not an error"),
    ("docs/protocol/mcp-tools.md", "immutable once built"),
    ("docs/protocol/migrations.md", "Re-applying an applied migration is a no-op"),
    ("docs/security/threat-model.md", "nothing pending writes nothing"),
    (
        "packages/theurian-core/src/theurian/application/migration_engine.py",
        "re-application is a no-op",
    ),
    ("packages/theurian-core/src/theurian/cli/commands.py", "changes nothing (FR-K8)"),
    ("packages/theurian-core/src/theurian/cli/commands.py", "an idempotent replay writes nothing"),
    (
        "packages/theurian-core/src/theurian/cli/migration_pipeline.py",
        "nothing pending writes nothing",
    ),
    ("packages/theurian-core/src/theurian/domain/ports/canonical_store.py", "immutable once built"),
    ("packages/theurian-core/src/theurian/mcp/tools.py", "immutable once built"),
    ("packages/theurian-core/src/theurian/mcp/tools.py", "immutable once built"),
    ("packages/theurian-core/tests/integration/test_mcp_tools.py", "immutable once built"),
    ("packages/theurian-core/tests/integration/test_mcp_tools.py", "immutable once built"),
    (
        "packages/theurian-core/tests/integration/test_mcp_tools.py",
        "a re-apply that changes nothing",
    ),
    (
        "packages/theurian-core/tests/integration/test_mcp_tools.py",
        "nothing pending writes nothing",
    ),
    ("packages/theurian-core/tests/integration/test_mcp_tools.py", "immutable once built"),
    ("packages/theurian-core/tests/unit/test_migration_engine.py", "re-application is a no-op"),
    ("schemas/mcp/knowledge-search-response.schema.json", "immutable once built"),
    ("schemas/mcp/knowledge-search-response.schema.json", "having changed nothing"),
    ("schemas/mcp/knowledge-status-response.schema.json", "immutable once built"),
    ("schemas/mcp/knowledge-status-response.schema.json", "immutable once built"),
    ("schemas/mcp/knowledge-status-response.schema.json", "having changed nothing"),
)


@pytest.mark.parametrize(
    ("path", "phrase"), FALSIFIED, ids=[f"row-{n}" for n in range(1, len(FALSIFIED) + 1)]
)
def test_each_sentence_1033_amends_is_still_in_its_file(path: str, phrase: str) -> None:
    """The seam moved the list out of the ADR on the premise that this pin holds it."""
    wanted = FALSIFIED.count((path, phrase))

    assert _text(REPO_ROOT / path).count(collapsed(phrase)) == wanted, DATED.format(
        where=f"the census unit in {path}",
        issue=1033,
        sentence=f"'{phrase}' there and this pin's FALSIFIED row with it",
    )
