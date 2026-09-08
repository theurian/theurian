"""One spelling on disk: the paths the write builds, at the write and at the read.

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

The rule the fix applies, in one line: **fold to find, byte-compare to accept.**
Finding folds so a case-variant is never invisible; accepting stays byte-wise so
this store never reports a record under a spelling it did not choose -- the path
is an opaque key that ``ReviewIngestService`` and slice 3's store are both built
on.

**What the earlier version of this file claimed, and why it is gone.** It said
every site where a filesystem-supplied name meets a derived one "has to be in a
table", and drove that with a walk over six hand-named calls -- ``lstat``,
``replace``, ``scandir``, ``iterdir``, ``mkdir``, ``resolve`` -- plus ``.name``
and ``.casefold``. A verdict pass planted ``exists()``, ``glob()`` and
``Path.rename`` in this package and the walk passed on all three: the sentence
was a universal and the key was a watch-list. So both halves changed. The two
checks below recompute their populations from source, and each states what it
still cannot see:

* :func:`test_every_name_taking_site_records_a_verdict` derives its watched set
  from ``dir(pathlib.Path)``, ``dir(pathlib.PurePath)`` and the ``str`` methods
  that change case, rather than naming calls. ``exists``, ``glob`` and ``rename``
  are members of the first of those, so they are watched by construction. It is
  keyed on **spelling**, so it sees ``entry.name`` and ``os.lstat`` and does not
  see a name reached through a helper this package imports, an alias, or
  ``getattr``; and being keyed on spelling it also collects false positives --
  ``record.anchor`` is not a ``Path``, and its row says so.
* :func:`test_the_guard_covers_every_path_the_write_builds_under_the_root`
  recomputes both sides of the guard's coverage from ``store.py``'s own syntax:
  the derived path expressions ``_write_one`` constructs against the two
  ``_refuse_a_folded_spelling`` checks. That is the check the ``.writing``
  temporary needed -- a fourth derived name the walk above sees only as an
  ``open``, and one the six-name key never described.

Neither of them replaces :func:`store._refuse_a_folded_spelling`: that guard runs
at run time against whatever the disk holds and does not depend on either walk
finding anything.

Most rows run on every filesystem, because the *guard* keys on casefold equality
rather than on what the disk does: a planted ``Pull-Request/`` is refused on a
case-sensitive filesystem for the same reason. The rows that demonstrate the
**harm** need a folding one, and :func:`folding_filesystem` skips them where
there is none rather than passing vacuously.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath
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


# -- the population, recomputed from source rather than watched ---------------

#: A probe carrying both cases in both orders, so a method that changes case
#: cannot come back equal to it by accident. ``"Aa"`` would: ``title`` and
#: ``capitalize`` are fixed points on it and would drop out of the derived set.
_CASE_PROBE: Final = "aA bB"


def _changes_case(name: str) -> bool:
    """Whether ``str.<name>`` is a no-argument method that recases :data:`_CASE_PROBE`."""
    method = getattr(str, name)
    if not callable(method):
        return False
    try:
        answered = method(_CASE_PROBE)
    except TypeError:
        return False
    return isinstance(answered, str) and answered != _CASE_PROBE


#: The ``str`` methods that decide a case question, derived rather than listed.
#: ``casefold`` is the only one correct for comparing two filesystem names, so
#: one of the others turning up in this package is itself the finding.
_CASE_METHODS: Final[frozenset[str]] = frozenset(
    name for name in dir(str) if not name.startswith("_") and _changes_case(name)
)

#: Every public member of a ``Path``, derived from the class. This is what
#: replaced the six hand-named calls: ``exists``, ``glob`` and ``rename`` -- the
#: three a verdict pass planted and the old key passed on -- are members of it,
#: so they are watched without anybody having met them first.
_PATH_MEMBERS: Final[frozenset[str]] = frozenset(
    name for name in [*dir(Path), *dir(PurePath), *dir(PurePosixPath)] if not name.startswith("_")
)

#: Every site in ``review_evidence/`` that names something the filesystem or a
#: case comparison resolves, with the verdict for each. Keyed by
#: ``<file>:<receiver as written>.<member>``, which survives an ordinary edit
#: where a line number would not.
#:
#: The key is a **spelling**, so some rows are false positives of it --
#: ``record.anchor`` is a ``SourceAnchor`` and ``PurePath.anchor`` is what put it
#: here. Those rows say so rather than being filtered out: a filter is a second
#: watch-list, and the previous one is what this file exists to have replaced.
_ACCOUNTED: Final[dict[str, str]] = {
    "codec.py:event.title": (
        "not a case decision: `str.title` shares its spelling with the pull "
        "request's own title field, which this reads out of a document"
    ),
    "layout.py:_FILESYSTEM_SAFE.match": (
        "not a `Path`: `re.Pattern.match` against the derived leaf, which decides "
        "whether the id is spelled out or hashed and touches no filesystem"
    ),
    "layout.py:character.casefold": (
        "`_case_tag` asking, per character, whether a folding filesystem would "
        "change it -- the tag that keeps two ids' leaves apart after folding"
    ),
    "layout.py:provider_id.casefold": (
        "the same question over the whole id, deciding whether a tag is needed at "
        "all, and matching the `sha256-` escape prefix against the folded id so "
        "`SHA256-...` cannot be spelled out to name a hashed leaf"
    ),
    "store.py:PurePosixPath(relative).parent": (
        "`_refuse_a_relocated_directory` taking the record's directory off a "
        "derived path; no filesystem name is read"
    ),
    "store.py:PurePosixPath(relative).parts": (
        "the guard's own component split, which is why it covers the repository "
        "hash, the kind directory and the leaf with one rule"
    ),
    "store.py:PurePosixPath(on_disk).parts": (
        "`_first_differing_component` splitting the path a file was found under, so "
        "the read-side cure names a component pair rather than two whole paths -- the "
        "byte comparison that follows is deliberately unfolded"
    ),
    "store.py:PurePosixPath(derived).parts": "the same split over the path this build derives",
    "store.py:component.casefold": "the guard's lookup of a derived component in the index",
    "store.py:entry.name": (
        "`_OnDiskSpellings` reading what the disk actually holds, which is the only "
        "place in the package that wants the on-disk spelling for its own sake"
    ),
    "store.py:entry.name.casefold": (
        "the index's key, so a lookup by a derived name finds a variant"
    ),
    "store.py:kind_directory.is_dir": (
        "a shape question, not a name one. It swallows its own `OSError`, so an "
        "`ELOOP` directory reads as absent -- recorded at `_relative_paths`"
    ),
    "store.py:kind_directory.iterdir": "the read walk's third level; folds to find, below",
    "store.py:kind_directory.name": "folded before the membership test, so a variant is found",
    "store.py:kind_directory.name.casefold": "the folded membership test itself -- the fix",
    "store.py:leaf.name": "folded before the suffix test, so a variant is found",
    "store.py:leaf.name.casefold": "the folded suffix test itself -- the fix",
    "store.py:os.lstat": (
        "asks the kernel about a derived path; the filesystem folds and the answer "
        "is about whatever object that name reaches, which is what the caller wants"
    ),
    "store.py:os.replace": (
        "lets the filesystem resolve the target. Measured: renaming onto `42.json` "
        "beside an existing `42.JSON` lands the bytes and keeps `42.JSON`. Refused "
        "before it runs, by `_refuse_a_folded_spelling`"
    ),
    "store.py:os.scandir": "`_OnDiskSpellings` itself, which is the guard",
    "store.py:record.anchor": (
        "not a `Path`: `EvidenceRecord.anchor`, a `SourceAnchor`. `PurePath.anchor` "
        "shares the spelling, which is what this key matches on"
    ),
    "store.py:record.relative_path.casefold": (
        "`_stored` telling a case difference apart from a genuinely misfiled record, "
        "so the two get different cures. The byte comparison one line above it is "
        "the accept half: deliberately unfolded, because the path is an opaque key "
        "two layers up"
    ),
    "store.py:relative.casefold": "the within-run collision guard, folded since round one",
    "store.py:repository.is_dir": "a shape question at the walk's second level, as above",
    "store.py:repository.iterdir": "the read walk's second level",
    "store.py:repository.name": (
        "carried into the path verbatim and compared by `_stored` against the derived "
        "one, which refuses a case difference by name"
    ),
    "store.py:self._root.is_dir": (
        "whether there is a review directory to walk at all; an absent one reads as "
        "an empty corpus, which is the honest answer before a first run"
    ),
    "store.py:self._root.iterdir": "the read walk's first level",
    "store.py:self._root.resolve": (
        "`_refuse_a_relocated_directory` compares two *derived* paths. Measured on "
        "APFS: `resolve()` does not canonicalise case, so it neither false-refuses a "
        "case-variant nor detects one -- it answers a different question"
    ),
    "store.py:self.anchor": ("not a `Path`: `EvidenceRecord.anchor` again, in `__post_init__`"),
    "store.py:target.is_symlink": (
        "a shape question about the record's own leaf immediately before the rename; "
        "a case variant is already refused by `_refuse_a_folded_spelling`"
    ),
    "store.py:target.parent": "the directory the record lands in, off a derived path",
    "store.py:target.parent.mkdir": (
        "lets the filesystem resolve the directory. Measured: `mkdir(exist_ok=True)` "
        "on `pull-request` beside `Pull-Request` succeeds silently and the entry keeps "
        "its own spelling. Refused before it runs, by `_refuse_a_folded_spelling`"
    ),
    "store.py:writing.unlink": (
        "removes the temporary this write opened, after an `lstat` has said it is a "
        "regular file. Reached only for the `.writing` name, which "
        "`_refuse_a_folded_spelling` now covers as its own derived spelling"
    ),
}


def _name_taking_sites() -> set[str]:
    """Every attribute in the package spelled like a ``Path`` member or a case method."""
    package = Path(store_module.__file__).parent
    found: set[str] = set()
    for path in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Attribute):
                continue
            owner = ast.unparse(node.value)
            if owner == "os" or node.attr in _PATH_MEMBERS or node.attr in _CASE_METHODS:
                found.add(f"{path.name}:{owner}.{node.attr}")
    return found


def test_the_watched_set_is_derived_and_covers_what_the_old_key_missed() -> None:
    """The can-fail companion for the derivation itself.

    Both halves of the watched set are computed rather than written down, and a
    computation that silently answered nothing would make the row below vacuous.
    The three named members are the ones a verdict pass planted in this package
    while the previous six-name key passed on all of them.
    """
    assert {"exists", "glob", "rename"} <= _PATH_MEMBERS
    assert "casefold" in _CASE_METHODS
    assert sorted(_CASE_METHODS) == [
        "capitalize",
        "casefold",
        "lower",
        "swapcase",
        "title",
        "upper",
    ], (
        f"the case-method derivation answered {sorted(_CASE_METHODS)}, so the probe no "
        "longer separates the methods that recase from the ones that do not"
    )
    assert len(_name_taking_sites()) >= 25, "the walk found too little to be the population"


def test_every_name_taking_site_records_a_verdict() -> None:
    """RED means a site names something the filesystem resolves and nobody said what about case.

    Keyed on the **member's spelling** rather than on six calls somebody had met.
    An ``exists()``, a ``glob()`` or a ``Path.rename`` added to this package is a
    ``Path`` member, so it lands here until a row says what it does about a
    filesystem that folds -- which is what the previous key could not do, and
    was measured not doing.
    """
    unaccounted = sorted(_name_taking_sites() - set(_ACCOUNTED))

    assert not unaccounted, (
        f"{unaccounted} name something a filesystem or a case comparison resolves and "
        "no verdict is recorded for them. Add a row to `_ACCOUNTED` saying what the "
        "site does about a filesystem that folds case -- fold to find, byte-compare to "
        "accept -- or fix the site. A row may also say the match is a false positive of "
        "the spelling, which several are."
    )


def test_no_verdict_outlives_the_site_it_was_written_about() -> None:
    """RED means a row survived the site it excused.

    The staleness direction. A row matching nothing today matches whatever takes
    that spelling next, so a genuinely new ``entry.name`` would inherit a verdict
    written about a call that has since been deleted -- and the population would
    have quietly shrunk while this file went on reporting it clean.
    """
    stale = sorted(set(_ACCOUNTED) - _name_taking_sites())

    assert not stale, (
        f"{stale} record a verdict about a site that is no longer in the package. "
        "Remove the row, or correct it to the site's new spelling."
    )


def _derived_paths_built_by(function: str) -> set[str]:
    """Every path expression ``function`` hands to ``PurePosixPath``, unparsed.

    The write builds its concrete paths as ``self._root / PurePosixPath(<expr>)``
    and proves them through ``resolve_within_root`` and
    ``assert_no_symlink_escape`` the same way, so ``PurePosixPath``'s argument is
    where a derived name is spelled in this module. Reading the *expression*
    rather than the value is what lets the guard's coverage be compared against
    it without running anything.
    """
    tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
    body = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    return {
        ast.unparse(call.args[0])
        for call in ast.walk(body)
        if isinstance(call, ast.Call)
        and ast.unparse(call.func).endswith("PurePosixPath")
        and call.args
    }


def _spellings_the_guard_checks() -> set[str]:
    """Every expression ``_refuse_a_folded_spelling`` runs the index over, unparsed.

    Read off the loop's own tuple rather than the guard's behaviour, so the two
    sides of the comparison below come from the same file and neither is a list
    kept in step by hand.
    """
    tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
    body = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_refuse_a_folded_spelling"
    )
    return {
        ast.unparse(element)
        for loop in ast.walk(body)
        if isinstance(loop, ast.For) and isinstance(loop.iter, ast.Tuple)
        for element in loop.iter.elts
    }


def test_the_guard_covers_every_path_the_write_builds_under_the_root() -> None:
    """RED means the write derives a name the folded-spelling guard never looks at.

    The fourth derived name is what the six-call watch-list above could not
    describe: ``_write_one`` builds **two** paths, the record and its
    ``.writing`` sibling, and the guard checked one. Measured on APFS with a
    regular ``42.json.WRITING`` planted beside the derived temporary: the open
    resolved to the operator's file, truncated it, wrote the record into it and
    renamed it away at exit 0.

    Both sides are recomputed from ``store.py``'s syntax -- the expressions the
    write hands to ``PurePosixPath`` against the expressions the guard's loop
    ranges over -- so a fifth derived name reddens here whichever side adds it.
    """
    built = _derived_paths_built_by("_write_one")
    checked = _spellings_the_guard_checks()

    assert built, "the walk found no derived path in `_write_one`, so this proves nothing"
    assert built == checked, (
        f"`_write_one` builds {sorted(built)} under the review root and "
        f"`_refuse_a_folded_spelling` checks {sorted(checked)}. A derived name the "
        "guard does not check is one the filesystem resolves for us: on a folding "
        "filesystem the write opens or renames onto whatever is already spelled that "
        "way."
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


def test_a_case_variant_of_the_temporary_is_refused_rather_than_truncated(
    review_root: Path, folding_filesystem: None
) -> None:
    """RED means an operator's own file is emptied and renamed away at exit 0.

    The fourth derived name. ``_write_one`` opens ``<record>.writing`` with
    ``O_CREAT``, and on a folding filesystem that open resolves to a
    ``<record>.WRITING`` already there: measured before the guard covered it, the
    plant was truncated, the record was written into it and ``os.replace`` moved
    it onto the record's own name, with nothing published.

    The plant is a **regular file** on purpose. A link, a pipe or a socket at
    that name is refused by ``O_NOFOLLOW`` and the shape check, which is a
    different guard with a different cure; a regular file is precisely the shape
    those two accept, so it is the one this rule has to catch.

    Needs a folding filesystem: on a case-sensitive one the two names are two
    files and the open simply creates the second.
    """
    record = _record()
    repository, kind, leaf = record.relative_path.split("/")
    directory = review_root / repository / kind
    directory.mkdir(parents=True)
    planted = directory / f"{leaf}.WRITING"
    planted.write_text("an operator's own notes\n", encoding="utf-8")

    with pytest.raises(ReviewEvidenceError) as raised:
        ReviewEvidenceStore(review_root).write([record], run=_run())

    message, remedy = str(raised.value), raised.value.remedy
    assert f"{leaf}.WRITING" in message, (
        f"the refusal does not name the spelling on disk: {message}"
    )
    assert f"{leaf}.writing" in message, f"the refusal does not name the derived name: {message}"
    assert f"{leaf}.WRITING" in remedy, f"the cure does not name the spelling on disk: {remedy}"
    assert planted.read_text(encoding="utf-8") == "an operator's own notes\n", (
        "the plant was truncated, which is the harm this refusal exists to prevent"
    )
    assert not list(review_root.rglob("*.json")), "the record landed through the operator's file"


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
