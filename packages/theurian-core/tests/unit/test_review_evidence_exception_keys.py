"""Every exception arm on the review-ingestion path, with a verdict per arm.

The class the verdict pass named is *an exception arm keyed on an enumeration
rather than on the complement*, and its measured member was a
``RecursionError``: a landed file of 20,000 nested arrays came out of
``reader._stored``'s ``json.loads`` as a ``RuntimeError`` subclass, outside
``_read_one``'s ``(ValueError, DomainError)`` **and** outside the CLI's own
``except TheurianError``, so ``theurian review ingest`` published no document at
all. A second face sat inside the handler grading the first:
``cures.repository_named_in`` re-parses the same bytes to name the repository,
so composing the refusal raised the identical error again.

Two arms were fixed and the rest are justified, and neither was decided by
reading. The population came from a search::

    git grep -n -P '^\\s+except ' -- \\
        packages/theurian-core/src/theurian/infrastructure/review_evidence/ \\
        packages/theurian-core/src/theurian/application/review_ingest_service.py \\
        packages/theurian-core/src/theurian/application/review_landing_gate.py \\
        packages/theurian-core/src/theurian/cli/review_commands.py

That key is line-shaped and misses two things this file does not: a
``contextlib.suppress`` is an exception arm with no ``except`` in it, and a
docstring mentioning the word matches (``layout.py`` carries one). So the
population here is the same file set walked as syntax, and every handler **and
every** ``suppress`` **call** in it must carry a row in :data:`_ACCOUNTED` --
which makes a newly-narrowed arm a red test rather than the next round's finding.

**What this walk does not see, said here rather than met later.** It keys on
``ast.ExceptHandler`` and on a call spelled ``suppress``, so an alias
(``from contextlib import suppress as quietly``) is invisible to it, as is any
other way of swallowing an exception -- ``Path.is_dir()``'s internal one is the
member that already exists in this package, and ``_relative_paths``' row is
where it is written down. It also stops at this file set: a helper these modules
call from elsewhere in the package carries its own arms and is not walked here.

Marked ``unit``; nothing here opens a file the repository does not ship.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final

import pytest

from theurian.application import review_ingest_service, review_landing_gate
from theurian.cli import review_commands
from theurian.infrastructure.review_evidence import store as store_module

pytestmark = pytest.mark.unit

#: The seams whose arm must be the **complement** of ``TheurianError`` rather
#: than a list of families: the two places one record crosses between what a
#: provider or a disk supplied and what ``review ingest`` publishes.
#:
#: One per module since the store was split at the 800-line ceiling -- the write
#: seam in ``store.py``, the read seam in ``reader.py`` -- which is why
#: :func:`_seam_try_blocks` walks the package rather than one file.
_SEAMS: Final[frozenset[str]] = frozenset({"ReviewEvidenceStore.write", "EvidenceReader._read_one"})

#: One verdict per exception arm on this path, keyed by
#: ``<file>:<qualified function>:<the type expression as written>``. The key is
#: deliberately not a line number: an ordinary edit moves every line and would
#: make this table read as stale when nothing about it had changed.
_ACCOUNTED: Final[dict[str, str]] = {
    "codec.py:_moment:ValueError": (
        "`datetime.fromisoformat` over a value `_string` has already proved is a "
        "`str`. Measured against a 5,000,000-character string, a lone surrogate, an "
        "out-of-range date and an empty string: `ValueError` for each. Re-raised as "
        "one, which the read seam grades"
    ),
    "cures.py:repository_named_in:(ValueError, UnicodeDecodeError, RecursionError)": (
        "best-effort by contract -- it answers `UNNAMED_REPOSITORY` rather than "
        "raising -- and `RecursionError` is the fix: this parse is the failed one "
        "repeated inside the arm composing the refusal about it"
    ),
    "reader.py:EvidenceReader._read_one:(ValueError, DomainError)": (
        "the codec's shape faults and the domain's invariants, whose messages name a "
        "field and a type. Kept for the *sentence*; the complement below is what "
        "keeps the observable"
    ),
    "reader.py:EvidenceReader._read_one:Exception": (
        "the read seam's complement key -- `_ungraded_read`, which names the class "
        "and never the text"
    ),
    "reader.py:EvidenceReader._read_one:OSError": "the filesystem's own refusal, by `strerror`",
    "reader.py:EvidenceReader._read_one:PathEscapeError": (
        "re-raised ahead of the `SecurityError` clause it would otherwise reach "
        "first, so a containment refusal is not re-labelled as unreadable bytes"
    ),
    "reader.py:EvidenceReader._read_one:SecurityError": (
        "SEC-8's size cap and the irregular-file refusal, whose own remedy is about "
        "an input an author can edit and would mislead about a record"
    ),
    "reader.py:EvidenceReader._read_one:TheurianError": (
        "a graded refusal that is not this store's, re-raised whole"
    ),
    "reader.py:EvidenceReader._read_one:_FoldedPathError": (
        "a `ValueError` caught ahead of its own base, and only to change the cure"
    ),
    "reader.py:EvidenceReader._relative_paths:OSError": (
        "the walk's only calls that are not total are `Path.iterdir` and "
        "`Path.is_dir`, and `OSError` is both contracts -- `is_dir` swallows its own "
        "internally, which is the recorded residual: a directory that is really an "
        "`ELOOP` reads as absent"
    ),
    "reader.py:_stored:RecursionError": (
        "the fix. A `RuntimeError`, so outside every family the caller names and "
        "outside `TheurianError`; raised as the `ValueError` this function's other "
        "shape faults already are, which buys the cure the complement arm cannot"
    ),
    # The PascalCase lock-class name is deliberately not spelled in this row: the
    # whole-word token trips `test_connection_claims.py`'s one-process
    # lock-construction census (#494), whose key is that name searched over each
    # test file's whole text, comments included -- and a verdict table is not a
    # member of the population that census is about. `test_adr_0018_claims.py`'s
    # findings entry avoids it for the same reason.
    "review_commands.py:_lock_write_section.section:OSError": (
        "the project's write lock -- acquisition, body and release -- converted into "
        "a `TheurianError` the commands below already grade. Nothing in the "
        "acquisition reaches it today: both calls the lock makes before it has a "
        "descriptor, its `mkdir` and its `open`, convert their own `OSError` into a "
        "graded error naming the lock file with a better cure. It is kept as the "
        "backstop a future acquisition step would otherwise escape through, exactly "
        "as its findings twin is"
    ),
    "review_commands.py:review_build:OSError": (
        "the provenance write, which is the one call on this path raising a bare "
        "`OSError`: the store converts its own and the lock's are converted one arm "
        "up. Graded separately from the arm above because its precondition is a "
        "different directory -- `THEURIAN_DATA_DIR`, outside the repository -- so a "
        "cure naming `.theurian/` would send a reader to the wrong one"
    ),
    "review_commands.py:review_build:ProjectPathEscapeError": (
        "`review_ingest`'s arm, for the same reason: exit code 4, a containment "
        "refusal carrying its own remedy about where a path points"
    ),
    "review_commands.py:review_build:TheurianError": (
        "`review_ingest`'s arm, and deliberately not `Exception` for the same "
        "reason: it is the class the store and the builder grade *into*, so widening "
        "it would publish a defect in this process as an operator-facing refusal"
    ),
    "review_commands.py:review_ingest:OSError": (
        "the provenance write reached through the post-landing rebuild, graded like "
        "`review_build`'s. Its sentence differs in the clause that matters: it says "
        "the records landed, because the evidence is durable before the rebuild "
        "starts and an operator told only that a build failed would go looking for "
        "records that are on disk. It sits in the rebuild's own `try` and emits the "
        "run document before it fails"
    ),
    "review_commands.py:review_ingest:ProjectPathEscapeError": (
        "narrows the arm below it to exit code 4, a containment refusal carrying its "
        "own remedy about where a path points. **Two arms wear this spelling** since "
        "the rebuild took a `try` of its own, and one verdict covers both because "
        "this table is keyed by type expression rather than by position: before "
        "landing it refuses with nothing to report, and in the rebuild half it emits "
        "the run document first, because the records are already on disk by then"
    ),
    "review_commands.py:review_ingest:TheurianError": (
        "the observable's own mechanism, and deliberately not `Exception`: it is the "
        "class both store seams grade *into*, so widening it here would publish a "
        "defect in this process as an operator-facing refusal instead of fixing the "
        "seam that let one through. **Two arms wear this spelling**, and the split is "
        "what #630's H-1 fixed: the fetch half has nothing landed to report, while "
        "the rebuild half emits the run document and only then refuses -- one handler "
        "for both graded a failed rebuild as a command that could not run, and threw "
        "away the `secretsWarned` and `findings` counts of a `warn` run that had just "
        "landed a flagged record"
    ),
    "review_ingest_service.py:ReviewIngestService._fetch:ReviewIngestRefusedError": (
        "the record-scope skip, narrow on purpose: the module's docstring records "
        "that a `ProjectConfigError` arriving mid-run halts rather than degrading "
        "every remaining record to a skip, and `test_review_ingest_service.py` drives "
        "both outcomes"
    ),
    "spellings.py:OnDiskSpellings._folded:OSError": (
        "a directory that cannot be listed is the caller's next refusal with a better "
        "message; grading it here would publish a cure about a spelling over a "
        "permission fault"
    ),
    "store.py:ReviewEvidenceStore._discard_the_temporary:suppress(OSError)": (
        "removing litter must not change what the caller is told; the faults that "
        "reach this arm make the `lstat` and the `unlink` fail too"
    ),
    "store.py:ReviewEvidenceStore._publish:FileNotFoundError": (
        "the one errno that means `nothing is in the way`. Every other one falls to "
        "`_write_one`'s `BaseException` arm below"
    ),
    "store.py:ReviewEvidenceStore._write_one:BaseException": (
        "wider than the complement on purpose: an interrupt between the open and the "
        "return leaves the same litter an error does"
    ),
    "store.py:ReviewEvidenceStore.write:Exception": (
        "the landing seam's complement key, and the one this class was named after"
    ),
    "store.py:ReviewEvidenceStore.write:ReviewEvidenceError": (
        "this store's own refusal, re-raised with the run's partial-landing count "
        "appended once rather than written at eight sites"
    ),
    "store.py:ReviewEvidenceStore.write:TheurianError": (
        "a graded refusal that is not this store's -- containment's -- re-raised whole"
    ),
    "store.py:_planted_shape:OSError": (
        "the path is gone or unreachable for the reason the open was, and the caller "
        "falls back to the errno sentence rather than guessing a shape"
    ),
}


def _source_files() -> list[pathlib.Path]:
    """The modules one ingestion run's exceptions travel through, in a stable order.

    Derived from the modules themselves rather than spelled as paths, so a move
    of the package is an import error here rather than a walk over nothing.
    """
    package = pathlib.Path(store_module.__file__).parent
    return sorted(package.glob("*.py")) + [
        pathlib.Path(module.__file__ or "")
        for module in (review_ingest_service, review_landing_gate, review_commands)
    ]


def _arms() -> list[str]:
    """Every exception arm in that file set, keyed by file, function and type.

    A ``suppress`` call is collected beside the ``except`` handlers because it is
    the same decision written another way -- and the one this package actually
    uses where a ``try`` would have been noisier.
    """
    found: list[str] = []

    def walk(node: ast.AST, prefix: str, path: pathlib.Path) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, f"{prefix}.{child.name}" if prefix else child.name, path)
                continue
            where = f"{path.name}:{prefix or '<module>'}"
            if isinstance(child, ast.ExceptHandler):
                caught = ast.unparse(child.type) if child.type is not None else "bare"
                found.append(f"{where}:{caught}")
            if isinstance(child, ast.Call) and ast.unparse(child.func).split(".")[-1] == "suppress":
                found.append(f"{where}:{ast.unparse(child)}")
            walk(child, prefix, path)

    for path in _source_files():
        walk(ast.parse(path.read_text(encoding="utf-8")), "", path)
    return found


def test_the_walk_finds_a_population_at_all() -> None:
    """The can-fail companion: an empty walk makes every row below vacuous.

    A structural check that finds nothing passes exactly as a correct one does.
    This pins that the walk reaches both a graded seam and the ``suppress`` shape
    the ``except``-keyed grep cannot see, so a rename of the package turns into a
    red test here rather than a clean report over nothing.
    """
    found = set(_arms())

    assert len(found) >= 20, f"the walk found {len(found)} arms, too few to be the population"
    assert "store.py:ReviewEvidenceStore.write:Exception" in found
    assert "store.py:ReviewEvidenceStore._discard_the_temporary:suppress(OSError)" in found


def test_every_exception_arm_on_this_path_records_a_verdict() -> None:
    """RED means an arm decides which faults are graded and nobody wrote down why.

    The arm that broke the observable was not a missing ``try`` -- it was a
    ``try`` whose tuple named two families and met a third. So the question this
    asks of a new arm is not whether it exists but whether somebody chose its
    key.
    """
    unaccounted = sorted(set(_arms()) - set(_ACCOUNTED))

    assert not unaccounted, (
        f"{unaccounted} decide which exceptions are graded on the review-ingestion "
        "path and record no verdict. Add a row to `_ACCOUNTED` saying what the arm "
        "covers and what it deliberately lets past -- or key it on the complement of "
        "`TheurianError`, which is what the two seams do."
    )


def test_no_verdict_outlives_the_arm_it_was_written_about() -> None:
    """RED means a row survived the arm it excused.

    The staleness direction, and it is not symmetry for its own sake: a row that
    matches nothing today matches whatever takes that spelling next, so a
    re-narrowed arm would inherit a verdict written about something else.
    """
    stale = sorted(set(_ACCOUNTED) - set(_arms()))

    assert not stale, (
        f"{stale} record a verdict for an arm that is no longer there. Remove the "
        "row, or correct it to the arm's new key -- an unmatched row silently "
        "excuses whatever is written at that spelling next."
    )


def _seam_try_blocks() -> list[tuple[str, list[str]]]:
    """Each ``try`` inside a seam function, as ``(where, its handler types in order)``.

    **Per ``try`` and not per function**, which is the correction a positive
    control forced: keyed by function, a seam with two ``try`` blocks reads as
    complement-keyed while only one of them is -- deleting the parse block's
    complement arm left this file green, because the read block above it still
    had one.

    **Over the package rather than one file**, which is the correction the split
    forced: this walked ``store.py`` alone while both seams lived there, and the
    read seam moved to ``reader.py``.
    :func:`test_the_seam_walk_finds_both_seams_and_all_their_blocks` is what
    fails when the walk stops reaching one of them.
    """
    blocks: list[tuple[str, list[str]]] = []
    package = pathlib.Path(store_module.__file__).parent

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, f"{prefix}.{child.name}" if prefix else child.name)
                continue
            if isinstance(child, ast.Try) and prefix in _SEAMS:
                blocks.append(
                    (
                        f"{prefix} line {child.lineno}",
                        [
                            ast.unparse(handler.type) if handler.type is not None else "bare"
                            for handler in child.handlers
                        ],
                    )
                )
            walk(child, prefix)

    for path in sorted(package.glob("*.py")):
        walk(ast.parse(path.read_text(encoding="utf-8")), "")
    return blocks


def test_the_seam_walk_finds_both_seams_and_all_their_blocks() -> None:
    """The can-fail companion for the row below, which is a loop over a list."""
    blocks = _seam_try_blocks()

    assert len(blocks) == 3, (
        f"the walk found {len(blocks)} `try` blocks across {sorted(_SEAMS)}, where the "
        f"write seam has one and the read seam has two: {blocks}"
    )
    assert {where.split(" line ")[0] for where, _ in blocks} == _SEAMS


@pytest.mark.parametrize(
    ("where", "handlers"),
    _seam_try_blocks(),
    ids=[where for where, _ in _seam_try_blocks()],
)
def test_each_store_seam_block_ends_on_the_complement_rather_than_a_family(
    where: str, handlers: list[str]
) -> None:
    """RED means a seam block went back to naming the families it happens to have met.

    ``review ingest``'s handler catches ``TheurianError``, so what ends a run
    with no document is precisely the complement of that class. A block keyed on
    ``(ValueError, DomainError)`` grades the members somebody has already been
    bitten by; this asserts the arm the observable rests on is the **last** one
    in every block at both seams, because an arm below ``Exception`` would never
    be reached and one above it would take its members first.
    """
    assert handlers[-1] in {"Exception", "BaseException"}, (
        f"{where} ends on {handlers[-1]!r} rather than on the complement of "
        f"`TheurianError`. Its arms are {handlers}: anything outside that list "
        f"leaves `review ingest` with a traceback and no document."
    )
