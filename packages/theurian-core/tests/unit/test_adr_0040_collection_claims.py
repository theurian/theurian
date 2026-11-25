"""ADR-0040's collection as #1033 built it, held from both sides (#1033).

The last four sentences ADR-0040 Compliance's claims-pin bullet lists are held standing here;
``test_adr_0040_claims.py`` holds their prose sides and counts this module's ``STANDING.format``
calls with its own. Beside them are the prose pins over what #1033's docs step amended for
derived edges.

**Reach.** Prose is matched on collapsed text with ``>`` markers dropped: an NFR's own table
row, or the section a heading prefix names. ``holds one project`` is scanned over the whole of
ADR-0040 and of ``cli/migration_pipeline.py``, the two places that stated it.
"""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from adr_0037_support import collapsed
from adr_0039_support import ADR_DIR, REPO_ROOT, SRC, _function, _section

from theurian.cli.migration_pipeline import _collect_edges
from theurian.domain.identifiers import ProjectId
from theurian.domain.refs_trailer import capped_digest, edges_from_commit
from theurian.infrastructure.git import first_parent_walk as walking
from theurian.infrastructure.git.first_parent_walk import FirstParentWalk, WalkedCommit
from theurian.infrastructure.sqlite.schema import DDL
from theurian.infrastructure.sqlite.store import SqliteWriter

pytestmark = pytest.mark.unit

ADR: Final = ADR_DIR / "0040-traceability-representation-and-edge-sources.md"
ADR_0007: Final = ADR_DIR / "0007-state-hash-partitioned-databases.md"
REQUIREMENTS: Final = REPO_ROOT / "docs" / "architecture" / "requirements-analysis.md"
STANDING: Final = "ADR-0040 {where} states {sentence!r}, and the tree no longer does."
_SHA: Final = "a" * 40
_WHEN: Final = datetime(2026, 1, 1, tzinfo=UTC)


def _collected(walk: FirstParentWalk) -> tuple[tuple[object, ...], list[tuple[object, ...]]]:
    """The walk record and the edge rows the collector leaves for ``walk`` in a fresh state."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(DDL)
    connection.execute(
        "INSERT INTO schema_metadata (id, schema_version, engine_version, state_hash, created_at)"
        " VALUES (1, 5, 1, 'hash', 'now')"
    )
    _collect_edges(SqliteWriter(connection), ProjectId("demo"), walk)
    record = tuple(
        connection.execute(
            "SELECT walk_commit, walk_shallow_digest, walk_depth_bounded, walk_budget_bounded, "
            "walk_capped_digest FROM schema_metadata"
        ).fetchone()
    )
    edges = [tuple(row) for row in connection.execute("SELECT * FROM traceability_edges")]
    connection.close()
    return record, edges


def test_a_capped_or_bounded_collection_never_shares_the_identity_of_one_that_was_not() -> None:
    identity = STANDING.format(
        where="decision 2",
        sentence="A state's identity is (state_hash, walk commit, boundary), and two states with "
        "one identity hold the same edges; a state whose collection was capped or bounded never "
        "shares an identity with one whose was not.",
    )
    commit = WalkedCommit(_SHA, _WHEN, ("a.py",), b"change a\n\nRefs: a.b\n")
    plain = FirstParentWalk(_SHA, (commit,), None, depth_bounded=False, budget_bounded=False)
    others = (
        replace(plain, depth_bounded=True),
        replace(plain, budget_bounded=True),
        replace(plain, commits=(replace(commit, paths=None),)),
    )

    (record, edges), again = _collected(plain), _collected(plain)

    assert again == (record, edges), identity
    assert len(edges) == 1, "positive control: the plain walk's edge set is not empty"
    assert len({record, *(_collected(walk)[0] for walk in others)}) == 4, identity


def test_the_shallow_digest_is_over_the_sorted_lines_of_the_file_git_path_shallow_names(
    tmp_path: Path,
) -> None:
    digest = STANDING.format(
        where="decision 2",
        sentence="The shallow digest is the lowercase hex SHA-256 of the clone's shallow-commit "
        "list — the lines of the file git rev-parse --git-path shallow names, sorted byte-wise, "
        "each followed by a newline — and null when no such file exists, which is a full clone.",
    )
    low, high = "0" * 40, "f" * 40
    (tmp_path / "shallow").write_text(f"{high}\n{low}")
    read_head = ast.unparse(_function("infrastructure/git/first_parent_walk.py", "_read_head"))

    expected = hashlib.sha256(f"{low}\n{high}\n".encode()).hexdigest()

    assert walking._read_shallow(tmp_path / "shallow", 40)[0] == expected, digest
    assert walking._read_shallow(tmp_path / "absent", 40)[0] is None, digest
    assert "'--git-path', 'shallow'" in read_head, digest


def test_a_commit_naming_more_than_32_distinct_valid_ids_yields_no_edges_and_is_capped() -> None:
    cap = STANDING.format(
        where="decision 3(c)",
        sentence="A commit naming more than 32 distinct valid ids yields no edges, and the "
        "boundary records it.",
    )
    ids = [f"item.n{n}" for n in range(33)]

    def refs(values: list[str]) -> tuple[int, bool]:
        message = "".join(f"Refs: {value}\n" for value in values).encode()
        found = edges_from_commit(
            project_id=ProjectId("demo"),
            sha=_SHA,
            committed_at=_WHEN,
            paths=("a.py",),
            message=message,
        )
        return len(found.edges), found.capped

    assert refs([*ids[:32], ids[0], "Item.N99"]) == (32, False), cap
    assert refs(ids) == (0, True), cap


def test_the_capped_digest_is_over_the_sorted_newline_terminated_shas_and_null_with_none() -> None:
    digest = STANDING.format(
        where="decision 3(c)",
        sentence="The capped digest is the lowercase hex SHA-256 of the capped commits' shas, "
        "sorted byte-wise, each followed by a newline, and null when there are none",
    )
    low, high = "0" * 40, "f" * 40

    assert capped_digest([high, low]) == hashlib.sha256(f"{low}\n{high}\n".encode()).hexdigest(), (
        digest
    )
    assert capped_digest([]) is None, digest


def _quoted(prefix: str, path: Path) -> str:
    """The section of ``path`` whose heading starts with ``prefix``, ``>`` markers dropped."""
    [heading] = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)
    ]
    return collapsed(" ".join(line.removeprefix(">") for line in _section(heading, path)))


def _row(requirement: str) -> str:
    [row] = [
        line
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"| {requirement} |")
    ]
    return collapsed(row)


_AMENDMENT: Final = "### Amended 2026-11-24 — decision 2"

#: Each amended sentence, beside the section or row that must hold it.
AMENDED: Final[dict[str, tuple[str, str]]] = {
    "NFR-5's derived edges": (
        "NFR-5",
        "Traceability edges and the walk record are derived from the walk commit at every apply, "
        "not hashed: a state's identity is (`state_hash`, walk commit, boundary), and one "
        "identity holds one edge set (ADR-0040 decision 2).",
    ),
    "NFR-6's canonical switch": (
        "NFR-6",
        "Switching to a previously built `state_hash` is O(1) for the canonical state.",
    ),
    "NFR-6's walk": (
        "NFR-6",
        "An apply that switches to one still walks the first-parent history and replaces the "
        "whole edge set, bounded by ADR-0040 decision 2's limits.",
    ),
    "ADR-0007's derived state": (
        "ADR-0007",
        "The content-addressed state is no longer the whole database: `traceability_edges` and "
        "`schema_metadata`'s `walk_*` record are derived state, which every `migrate apply` "
        "replaces from a first-parent walk at `HEAD` and which the hash does not read.",
    ),
    "the whole-table replace": (
        "ADR-0040",
        "The replace therefore deletes the whole table, not the applying project's rows,",
    ),
    "the last apply to commit": (
        "ADR-0040",
        "The edge set is the walk of the last apply to commit in this checkout, as the walk "
        "record in `schema_metadata` is.",
    ),
}


def _holder(name: str) -> str:
    if name == "ADR-0007":
        return _quoted("## Decision", ADR_0007)
    if name == "ADR-0040":
        return _quoted(_AMENDMENT, ADR)
    return _row(name)


@pytest.mark.parametrize(("holder", "sentence"), AMENDED.values(), ids=list(AMENDED))
def test_the_record_still_states_what_derived_edges_amended(holder: str, sentence: str) -> None:
    assert collapsed(sentence) in _holder(holder)


@pytest.mark.parametrize(
    "path", [ADR, SRC / "cli" / "migration_pipeline.py"], ids=["ADR-0040", "migration_pipeline"]
)
def test_the_one_project_premise_stays_out_of_the_record(path: Path) -> None:
    """C-H1 (#1097): an id change puts a second project in one state database."""
    assert "holds one project" not in collapsed(path.read_text(encoding="utf-8"))
