"""One spelling on disk: every path component, at the write and at the read.

Round two's R2-C. The leaf's injectivity-modulo-casefold held under two
independent brute-force probes, and the closure stopped there -- at the *leaf*.
The **directory** components fold too, and three sites compared them byte-wise
against a filesystem that does not:

* ``_relative_paths``' ``kind_directory.name in _KIND_DIRECTORIES``, so a
  planted ``Pull-Request/`` made every record written into it invisible to every
  later read -- ``new=1, kept=0`` on every run for ever, and slice 3's store
  built from a corpus quietly smaller than the disk;
* ``_relative_paths``' ``leaf.name.endswith(EVIDENCE_SUFFIX)``, the same face one
  level down: a planted ``42.JSON`` swallows the bytes of a record derived as
  ``42.json`` and keeps its own spelling (measured on APFS);
* ``mkdir(exist_ok=True)`` and ``os.replace``, which do not compare anything in
  Python at all -- the *filesystem* resolves the name, and that is precisely why
  a ``raise``-site or comparison-site enumeration is the wrong population here.

**So the population is stated as a search rather than as the three faces**, and
:func:`test_every_filesystem_supplied_name_is_accounted_for` is that search run
as a test: every site in the package where a name from the filesystem meets a
name this build derived has to be in a table with its verdict, so a fourth site
reddens here instead of becoming the next round's finding.

The rule the fix applies, in one line: **fold to find, byte-compare to accept.**
Finding folds so a case-variant is never invisible; accepting stays byte-wise so
this store never reports a record under a spelling it did not choose -- the path
is an opaque key that ``ReviewIngestService`` and slice 3's store are both built
on.

Most rows run on every filesystem, because the *guard* keys on casefold equality
rather than on what the disk does: a planted ``Pull-Request/`` is refused on a
case-sensitive filesystem for the same reason. The two rows that demonstrate the
**harm** need a folding one, and :func:`folding_filesystem` skips them where
there is none rather than passing vacuously.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewParticipant
from theurian.infrastructure.review_evidence import (
    EvidenceKind,
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceError,
    ReviewEvidenceStore,
)
from theurian.infrastructure.review_evidence import store as store_module

pytestmark = pytest.mark.unit

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"


def _record(number: int = 42) -> EvidenceRecord:
    event = ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title="Bound the retry budget",
        body="The retry loop is now bounded.",
        author=ReviewParticipant(
            provider=PROVIDER, external_id="U_kwDO1", display_name="Reviewer One"
        ),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="fix/retry-budget",
        labels=(),
    )
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=SourceAnchor(provider=PROVIDER, source_uri=event.url, repository=REPOSITORY),
        payload=event,
    )


def _run() -> IngestionRun:
    return IngestionRun(run_id="run-1", observed_at=datetime(2026, 8, 1, tzinfo=UTC))


@pytest.fixture
def review_root(tmp_path: Path) -> Path:
    root = tmp_path / "review"
    root.mkdir()
    return root


@pytest.fixture
def folding_filesystem(tmp_path: Path) -> None:
    """Skip unless *this* filesystem folds case, measured rather than assumed.

    Keyed on the platform in every earlier version of this idea, which is wrong
    in both directions: macOS can be formatted case-sensitively and Linux can
    mount a folding filesystem. The probe is two lines and answers about the
    directory the test is actually going to write in.
    """
    probe = tmp_path / "FoldingProbe"
    probe.mkdir()
    if not (tmp_path / "foldingprobe").is_dir():
        pytest.skip(
            "this filesystem does not fold case, so a case-variant plant collides with nothing"
        )


# -- the population, as a search rather than as the faces ---------------------

#: Every site in ``review_evidence/`` where a name the **filesystem** supplies
#: meets a name this build **derived**, with the verdict for each. The key that
#: produced it, re-runnable::
#:
#:     git grep -n -P '\.iterdir\(|\.name\b|\.mkdir\(|os\.replace\(|\.resolve\(\)|os\.lstat\(' \
#:         -- packages/theurian-core/src/theurian/infrastructure/review_evidence/
#:     git grep -n -P 'casefold\(\)' \
#:         -- packages/theurian-core/src/theurian/infrastructure/review_evidence/
#:
#: A ``raise``-site enumeration would have missed the two that matter most:
#: ``mkdir`` and ``os.replace`` compare nothing in Python and let the filesystem
#: decide, which is how a record lands under a spelling this build never chose.
_ACCOUNTED: Final[dict[str, str]] = {
    "os.lstat": (
        "asks the kernel about a derived path; the filesystem folds and the answer "
        "is about whatever object that name reaches, which is what the caller wants"
    ),
    "os.replace": (
        "lets the filesystem resolve the target. Measured: renaming onto `42.json` "
        "beside an existing `42.JSON` lands the bytes and keeps `42.JSON`. Refused "
        "before it runs, by `_refuse_a_folded_spelling`"
    ),
    "os.scandir": "`_OnDiskSpellings` itself, which is the guard",
    "Path.iterdir": "the read walk; folds to find, see the two selections below",
    "Path.mkdir": (
        "lets the filesystem resolve the directory. Measured: `mkdir(exist_ok=True)` "
        "on `pull-request` beside `Pull-Request` succeeds silently and the entry keeps "
        "its own spelling. Refused before it runs, by `_refuse_a_folded_spelling`"
    ),
    "Path.resolve": (
        "`_refuse_a_relocated_directory` compares two *derived* paths. Measured on "
        "APFS: `resolve()` does not canonicalise case, so it neither false-refuses a "
        "case-variant nor detects one -- it answers a different question"
    ),
    "kind_directory.name": "folded before the membership test, so a variant is found",
    "leaf.name": "folded before the suffix test, so a variant is found",
    "repository.name": (
        "carried into the path verbatim and compared by `_stored` against the derived "
        "one, which refuses a case difference by name"
    ),
    "record.relative_path": (
        "`_stored`'s byte comparison against where the file sits: deliberately not "
        "folded, because the path is an opaque key two layers above"
    ),
    "relative.casefold": "the within-run collision guard, folded since round one",
    "kind_directory.name.casefold": "the folded membership test itself -- the fix",
    "leaf.name.casefold": "the folded suffix test itself -- the fix",
    "entry.name": (
        "`_OnDiskSpellings` reading what the disk actually holds, which is the only "
        "place in the package that wants the on-disk spelling for its own sake"
    ),
    "entry.name.casefold": "the index's key, so a lookup by a derived name finds a variant",
    "component.casefold": "the guard's own lookup of a derived component in that index",
    "record.relative_path.casefold": (
        "`_stored` telling a case difference apart from a genuinely misfiled record, so "
        "the two get different cures"
    ),
}


def test_every_filesystem_supplied_name_is_accounted_for() -> None:
    """RED means a new site lets the filesystem resolve a name with no verdict recorded.

    The search in :data:`_ACCOUNTED`'s comment is the population; this is that
    search, run as a check. It is keyed on the **call or attribute** rather than
    on a line number, so ordinary edits move nothing and a genuinely new
    ``mkdir``, ``scandir``, ``iterdir``, ``replace``, ``lstat`` or ``.name`` in
    the package fails here until somebody writes down what it does about case.
    """
    package = Path(store_module.__file__).parent
    watched = {"lstat", "replace", "scandir", "iterdir", "mkdir", "resolve"}
    found: set[str] = set()
    for path in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in watched:
                owner = ast.unparse(node.value)
                found.add(f"{'os' if owner == 'os' else 'Path'}.{node.attr}")
            if isinstance(node, ast.Attribute) and node.attr == "name":
                found.add(f"{ast.unparse(node.value)}.name")
            if isinstance(node, ast.Attribute) and node.attr == "casefold":
                found.add(f"{ast.unparse(node.value)}.casefold")

    assert found, "the walk found no filesystem-supplied name, so this check proves nothing"
    unaccounted = sorted(
        site
        for site in found
        if site not in _ACCOUNTED and not site.startswith(("character.", "provider_id."))
    )

    assert not unaccounted, (
        f"{unaccounted} let the filesystem resolve or supply a name and no verdict is "
        "recorded for them. Add a row to `_ACCOUNTED` saying what the site does about a "
        "filesystem that folds case -- fold to find, byte-compare to accept -- or fix "
        "the site. The two that matter most compare nothing in Python at all."
    )


def test_the_kind_directory_names_are_their_own_casefold() -> None:
    """The premise the fold-to-find selection rests on, pinned rather than assumed.

    ``kind_directory.name.casefold() in _KIND_DIRECTORIES`` finds a variant only
    while the set's own members are already folded. A fourth kind spelled
    ``Pull_Request`` would make the membership test silently stop matching its
    own directory -- the exact invisibility this fix exists to remove, arriving
    through the enum instead of through a plant.
    """
    shouted = [kind.value for kind in EvidenceKind if kind.value != kind.value.casefold()]

    assert not shouted, (
        f"{shouted} are not their own casefold, so `_relative_paths`' folded membership "
        "test no longer recognises the directories this build writes."
    )


# -- the write guard: refuses before anything lands ---------------------------


@pytest.mark.parametrize(
    ("label", "plant"),
    [
        ("kind directory", "Pull-Request"),
        ("repository directory", "REPOSITORY"),
        ("leaf", "LEAF"),
    ],
)
def test_a_component_the_disk_already_spells_otherwise_refuses_before_the_write(
    label: str, plant: str, review_root: Path
) -> None:
    """RED means a record lands under a name this build never chose.

    All three components, because the population is every derived component and
    not the two a reviewer planted: the guard is one rule over
    ``PurePosixPath(relative).parts`` for exactly that reason.

    The refusal names **both** spellings. An operator told only the derived one
    lists the directory, sees the name they already have, and concludes the
    message is wrong.
    """
    record = _record()
    repository, kind, leaf = record.relative_path.split("/")
    if label == "kind directory":
        on_disk, path = plant, review_root / repository / plant
    elif label == "repository directory":
        on_disk, path = repository.upper(), review_root / repository.upper() / kind
    else:
        on_disk = leaf.upper()
        path = review_root / repository / kind
    path.mkdir(parents=True)
    if label == "leaf":
        (path / on_disk).write_text("planted\n", encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        ReviewEvidenceStore(review_root).write([record], run=_run())

    message, remedy = str(raised.value), raised.value.remedy
    assert on_disk in message, f"the refusal does not name the spelling on disk: {message}"
    assert on_disk in remedy, f"the cure does not name the spelling on disk: {remedy}"
    assert "Rename" in remedy, f"the cure does not say what to do about it: {remedy}"
    assert "delete" not in remedy.lower() or "Do not delete" in remedy, (
        f"the cure offers to delete a directory that may already hold records: {remedy}"
    )


def test_the_guard_scans_each_directory_once_per_call(review_root: Path) -> None:
    """The cost claim, measured rather than asserted in a docstring.

    ``_OnDiskSpellings`` says it scans at most five directories however many
    records a run lands, and the alternative -- a scan per component per record
    -- is quadratic in the number of records, which for a 500-pull-request window
    is millions of directory entries. A cost claim nobody measures is how the
    cheap version gets replaced by the expensive one in a later edit.
    """
    scans: list[str] = []
    spellings = store_module._OnDiskSpellings(review_root)
    original = spellings._folded

    def counted(parent: Path) -> dict[str, str]:
        scans.append(str(parent))
        return original(parent)

    spellings._folded = counted  # type: ignore[method-assign]
    for number in range(20):
        spellings.differently_spelled(_record(number + 1).relative_path)

    assert len(set(scans)) <= 5, f"scanned {len(set(scans))} distinct directories: {set(scans)}"
    assert len(scans) == 60, (
        f"the index was consulted {len(scans)} times for 20 records over 3 components, "
        "so the memoisation is not doing what its docstring says"
    )
    assert len(set(scans)) < len(scans), "every consultation was a fresh scan"


# -- the read walk: finds a variant instead of stepping over it ---------------


@pytest.mark.parametrize(
    ("label", "spell"),
    [("kind directory", "Pull-Request"), ("leaf suffix", "42.JSON")],
)
def test_a_planted_case_variant_is_found_and_then_refused_by_name(
    label: str, spell: str, review_root: Path
) -> None:
    """RED means a landed record is invisible to every later read.

    The two halves are asserted together on purpose. Folding the selection alone
    would make the file *visible* and then hand it to a byte comparison whose
    cure -- "open the file and compare it against what this build writes" --
    sends the reader to inspect a document that is entirely correct. So the walk
    finds it and ``_read_one`` names the case difference and the rename.
    """
    record = _record()
    repository, kind, leaf = record.relative_path.split("/")
    directory = review_root / repository / (spell if label == "kind directory" else kind)
    directory.mkdir(parents=True)
    document = store_module._document(record, _run())
    (directory / (spell if label == "leaf suffix" else leaf)).write_text(document, encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        ReviewEvidenceStore(review_root).read_all()

    message, remedy = str(raised.value), raised.value.remedy
    assert "differs only in case" in message, (
        f"the refusal does not say the fault is a spelling: {message}"
    )
    assert record.relative_path in message, f"the refusal does not name the derived path: {message}"
    assert "Rename" in remedy, f"the cure does not name the rename: {remedy}"
    assert "open the file" not in remedy.lower(), (
        f"the cure sends the reader to inspect bytes that are correct: {remedy}"
    )


def test_a_record_written_into_a_case_variant_directory_is_not_invisible(
    review_root: Path, folding_filesystem: None
) -> None:
    """The harm itself, on a filesystem that actually folds.

    This is the measured face: the plant, the write, and then a read that used to
    answer with **nothing at all** -- so a run reported the record as new on every
    invocation and slice 3's store would have omitted it. It needs a folding
    filesystem, because on a case-sensitive one ``Pull-Request/`` and
    ``pull-request/`` are two directories and the write simply creates the
    second.

    Asserted as *reported rather than silent*: the record is either refused by
    name or read back, and what it may never be is absent from an answer that
    claims to be the whole directory.
    """
    record = _record()
    repository, _kind, _leaf = record.relative_path.split("/")
    (review_root / repository / "Pull-Request").mkdir(parents=True)
    store = ReviewEvidenceStore(review_root)

    with pytest.raises(ReviewEvidenceError) as raised:
        store.write([record], run=_run())

    assert "Pull-Request" in str(raised.value)
    assert not list(review_root.rglob("*.json")), (
        "the record landed into the variant directory, which is what the guard exists to prevent"
    )


def test_a_landed_record_still_reads_back_unchanged(review_root: Path) -> None:
    """The other direction: none of the folding above refuses an ordinary corpus.

    A guard that refuses everything passes every hostile-input row above. Three
    records land and read back, and their reported paths are the derived ones --
    which is the property ``ReviewIngestService``'s ``new``/``kept`` arithmetic
    and slice 3's store keys are both built on.
    """
    store = ReviewEvidenceStore(review_root)
    records = [_record(number) for number in (41, 42, 43)]

    landed = store.write(records, run=_run())
    read_back = store.read_all()

    assert sorted(landed) == sorted(record.relative_path for record in records)
    assert [stored.relative_path for stored in read_back] == sorted(landed)
    assert [stored.record.record_key for stored in read_back] == ["41", "42", "43"]
