"""The candidate path pays nothing for a record it will refuse (ADR-0033 decision 5).

Decision 5 puts *how long it takes* inside the withheld-versus-absent bind: a
gate recompute plus a commit verification is strictly more work than a miss on an
id that names nothing, so a caller timing two calls would learn which ids exist.
What makes the two indistinguishable is an ordering in one function -- the
visibility resolve runs first, and a miss raises before anything expensive
happens.

**That ordering is held at run time by a spy, and a spy keyed on a method name
is the blind spot T-26 already met.**
``test_candidate_generation_absence_proof.py``'s ``_Spend`` counts calls to
``EvidenceReader.record_at`` and ``FixCommitCheck._run`` and pins both at zero on
a miss. It counts *those methods*: a read that arrived by another route, or a
spawn from a second site, is tallied by neither and every pin stays GREEN --
exactly the shape ``test_gate_call_sites.py`` was written for when
``_BodyReadCounter`` turned out to be method-name-keyed. This module is that
file's missing sibling for this path: it reads the shipped source the spy cannot
see, and it is the reason the spy is no longer the only holder of the property.

Three claims, each a fact about ``application/candidate_generation.py`` as
shipped:

1. each of the three seams is called from exactly one place in the whole shipped
   tree, spelled out so a second site is a decision someone records rather than
   a diff nobody notices;
2. in ``CandidateGenerator._record``, the resolve's ``None`` guard raises
   **before** the evidence read;
3. in ``CandidateGenerator.generate``, the commit verification runs **after**
   both resolves, so a record that never arrives costs no ``git`` process.

**What this cannot see.** It reads names, so ``getattr(self, "_read_record")``,
a dispatch table, or a read performed by a collaborator under another name all
pass. It is a floor on the review a new call site gets, not a proof that an
early read cannot exist -- and the run-time spy is what covers the routes a
source scan misses, which is why neither file replaces the other.

Pure in the sense the other structural tests are: it parses the shipped ``.py``
files as text and opens no database, no socket, and no temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator
from typing import Final

import pytest

import theurian
from theurian.application.candidate_generation import CandidateGenerator

pytestmark = pytest.mark.unit

#: The package as *imported*, not a path relative to this file, for
#: ``test_gate_call_sites.py``'s reason: a hand-built relative path can drift
#: from the installed package and would then scan a directory with no call sites
#: in it at all.
SRC: Final = pathlib.Path(theurian.__file__).resolve().parent

#: The visibility resolve. A miss here is what a withheld record and an absent
#: one both answer, and everything below is about what must not happen first.
RESOLVE: Final = "_resolve_evidence_path"

#: The evidence-file read: the first thing on this path that costs more than a
#: lookup.
READ: Final = "_read_record"

#: The commit verification: the largest single cost, because it spawns ``git``.
VERIFY: Final = "_verify_fix_commit"

#: Where each seam may be called, as ``ClassName.method``. A list rather than a
#: set, so a *second* call to the same seam inside one function reddens too --
#: which is the shape a hoisted verification takes.
CALL_SITES: Final = {
    RESOLVE: ["CandidateGenerator._record"],
    READ: ["CandidateGenerator._record"],
    VERIFY: ["CandidateGenerator.generate"],
}

#: The two resolves ``generate`` makes before it may verify anything: the
#: caller's key, then the pull request the thread's ``event_key`` names.
RESOLVING_HELPERS: Final = ("_thread", "_event")

MODULE: Final = SRC / "application" / "candidate_generation.py"


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _self_calls(tree: ast.AST, attribute: str) -> Iterator[tuple[tuple[str, ...], ast.Call]]:
    """Every ``self.<attribute>(...)`` call, with the dotted scope it sits in.

    Calls rather than attribute references, which is what keeps ``__init__``'s
    ``self._verify_fix_commit = verify_fix_commit`` out of the count: that is an
    assignment target, not an invocation, and counting it would make every seam
    look like it had two sites.
    """

    def walk(node: ast.AST, scope: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], ast.Call]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, (*scope, child.name))
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == attribute
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
            ):
                yield scope, child
            yield from walk(child, scope)

    yield from walk(tree, ())


def _lines_of(function: ast.FunctionDef, attribute: str) -> list[int]:
    """The line of every ``self.<attribute>(...)`` call inside one function."""
    return sorted(call.lineno for _scope, call in _self_calls(function, attribute))


def _function(name: tuple[str, ...]) -> ast.FunctionDef:
    """One function of the shipped module, by its dotted scope."""
    found = [
        node
        for node in ast.walk(_tree(MODULE))
        if isinstance(node, ast.FunctionDef) and node.name == name[-1]
    ]
    assert len(found) == 1, (
        f"{'.'.join(name)} is defined {len(found)} time(s) in {MODULE.name}; this "
        f"reader resolves a function by name and cannot choose between copies"
    )
    return found[0]


def test_the_scanner_looks_for_seams_the_generator_actually_has() -> None:
    """Guards the guards below, which read names and cannot resolve a type.

    Renaming any of the three seams would leave the scans looking for a name
    nothing in the product has. Two of them would still fail -- the expected
    lists are non-empty -- but they would fail saying "no call sites found",
    which reads as a broken test rather than as the rename it is. The ordering
    assertions would fail the same way. This says which it was.
    """
    generator = CandidateGenerator(
        resolve_evidence_path=lambda _repository, _key: None,
        read_record=lambda _relative: None,  # type: ignore[arg-type,return-value]
        verify_fix_commit=lambda _commit, _path: None,  # type: ignore[arg-type,return-value]
        drafts=None,  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
    )

    for seam in (RESOLVE, READ, VERIFY):
        assert hasattr(generator, seam), (
            f"`CandidateGenerator.{seam}` no longer exists, so the call-site "
            f"enumeration below is counting a name the product has stopped using. "
            f"Rename the constant and CALL_SITES together, or these scans protect "
            f"nothing"
        )


@pytest.mark.parametrize("seam", sorted(CALL_SITES), ids=sorted(CALL_SITES))
def test_each_seam_on_the_candidate_path_is_called_from_exactly_one_place(seam: str) -> None:
    """A second call site is a new place the cost can be paid before the resolve.

    The whole shipped tree is scanned, not just the one module: what the property
    needs is that nothing anywhere else reads an evidence record or spawns
    ``git`` on this path's behalf, and a scan of the module that owns the seams
    would not see a caller that had grown elsewhere.

    The assertion is an equality against the whole enumeration rather than a
    length, so it fails in both directions -- a call site added, and the known
    one moved or deleted -- and its failure names what it found. Duplicates are
    kept: two calls to the same seam inside one function is what a hoisted
    verification looks like, and a set would swallow it.
    """
    sites = sorted(
        ".".join(scope) or "<module>"
        for path in sorted(SRC.rglob("*.py"))
        for scope, _call in _self_calls(_tree(path), seam)
    )

    assert sites == CALL_SITES[seam], (
        f"`self.{seam}(...)` is called from {len(sites)} place(s) in the shipped "
        f"source, expected {CALL_SITES[seam]}:\n"
        + "\n".join(f"  {site}" for site in sites)
        + f"\n\nADR-0033 decision 5 binds the *duration* of a refusal for a record "
        f"the caller may not see. What holds it is that the visibility resolve "
        f"runs first and a miss raises before {seam} is ever reached. A second "
        f"call site is a second place that ordering has to be established. Before "
        f"widening CALL_SITES, show that the new site cannot run for a record the "
        f"resolve did not answer for, and add the test that goes red when it can."
    )


def test_a_record_the_resolve_does_not_answer_for_is_refused_before_the_evidence_read() -> None:
    """The ordering the run-time spy measures, read off the source it cannot see.

    ``_record`` resolves, checks for ``None``, and only then reads the file the
    resolve named. Swap those and a withheld key costs an open where an absent
    one costs nothing -- identical wording, different work, which is the oracle
    decision 5 forbids and the reason the duration is in the bind rather than in
    a residual.

    Asserted on statement position rather than on the presence of the guard: a
    guard that exists but sits *after* the read is the defect, and a test that
    only checked for a ``raise`` somewhere in the function would pass on it.

    **This is a shape claim, and it is deliberately stricter than the
    behaviour.** Moving the read above the guard but leaving it conditional --
    ``record = None if relative_path is None else self._read_record(...)`` --
    changes nothing a spy can count and reddens here anyway. That is the same
    posture ``test_gate_call_sites.py`` takes on ``SELECT *``: a reader cannot
    tell a safe reordering from an unsafe one at a glance, so the ordering is
    held as written and a change to it is argued for rather than noticed.
    """
    record = _function(("CandidateGenerator", "_record"))
    reads = _lines_of(record, READ)
    raises = sorted(node.lineno for node in ast.walk(record) if isinstance(node, ast.Raise))

    assert reads, f"`_record` no longer calls `self.{READ}(...)`, so this checks nothing"
    assert raises, (
        "`_record` raises nothing, so a key the resolve did not answer for now "
        "falls through to the evidence read -- the miss path has stopped being free"
    )
    assert min(raises) < min(reads), (
        f"`_record` reaches `self.{READ}(...)` at line {min(reads)} before its first "
        f"`raise` at line {min(raises)}. The resolve's miss must refuse first: a "
        f"refusal composed after opening the evidence file is an oracle even with "
        f"identical wording, because the read is work an absent key does not pay."
    )


def test_the_commit_verification_runs_only_after_both_records_have_resolved() -> None:
    """The git spawn -- the largest cost on this path -- is behind both resolves.

    ``generate`` resolves the thread, then the pull request its ``event_key``
    names, and only then verifies the caller's ``fixCommit``. Hoisting the
    verification above either resolve makes a call for a withheld or absent
    record spawn ``git`` before it refuses, which is a cost a miss must not pay
    and the one this path could most easily be timed on.

    Both helpers are named because either alone would leave the other free to
    move: a verification hoisted above ``_event`` still spawns for a thread whose
    pull request is withheld, which is the neighbour case
    ``test_candidate_generation_absence_proof.py`` drives.
    """
    generate = _function(("CandidateGenerator", "generate"))
    verifications = _lines_of(generate, VERIFY)

    assert verifications, (
        f"`generate` no longer calls `self.{VERIFY}(...)`, so either the signal is "
        f"gone or it moved -- and this ordering check has stopped checking anything"
    )
    for helper in RESOLVING_HELPERS:
        resolved = _lines_of(generate, helper)
        assert resolved, (
            f"`generate` no longer calls `self.{helper}(...)`; the resolve this "
            f"ordering is measured against has moved or been renamed"
        )
        assert max(resolved) < min(verifications), (
            f"`generate` verifies the commit at line {min(verifications)}, before "
            f"`self.{helper}(...)` at line {max(resolved)}. A record the resolve "
            f"does not answer for would then spawn `git` before refusing, so a "
            f"withheld key costs a process an absent key does not -- ADR-0033 "
            f"decision 5's duration half, defeated by an ordering."
        )
