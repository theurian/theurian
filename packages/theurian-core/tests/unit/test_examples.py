"""The bundled example must be valid (OSS-13).

A third party has to be able to clone this repository and see a real, working
`.theurian/` without writing one first. An example that has quietly drifted out
of conformance with the schemas is worse than no example: it teaches the wrong
shape.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

from theurian.application.project_service import ProjectPaths
from theurian.cli.context import schema_root
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.security import load_yaml_mapping

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCHEMAS = REPO_ROOT / "schemas"
EXAMPLE = REPO_ROOT / "examples" / "sample-project"
THEURIAN_DIR = EXAMPLE / ".theurian"
CONFIG = THEURIAN_DIR / "config.yaml"

MIGRATIONS = sorted((THEURIAN_DIR / "migrations").glob("*.yaml"))


def _validator(relative: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / relative).read_text(encoding="utf-8")))


def test_example_exists() -> None:
    assert THEURIAN_DIR.is_dir(), "the bundled example is missing"
    assert MIGRATIONS, "the example has no migrations"


def test_config_matches_its_schema() -> None:
    _validator("config/project-config.schema.json").validate(
        load_yaml_mapping(CONFIG.read_text(encoding="utf-8"))
    )


def test_the_example_does_not_switch_the_raptor_forest_on() -> None:
    """ADR-0008 decision 10's second place, and the one a reader actually copies.

    The schema default is the first (`tests/unit/test_schemas.py`), and it is
    the weaker of the two: this file sets `enabled` explicitly, so a reader who
    starts from the example gets whatever it says regardless of any default.
    Validating against the schema cannot catch a disagreement -- both values are
    valid booleans -- which is why it is asserted rather than left to
    `test_config_matches_its_schema` above.

    An example that teaches the wrong shape is worse than no example, and this
    module's own docstring says so; a forest switched on by the example is a
    build cost nobody measured and a capability whose acceptance tests are still
    owed, arriving to somebody who was following the documentation.
    """
    config = load_yaml_mapping(CONFIG.read_text(encoding="utf-8"))

    assert config["raptor"]["enabled"] is False


# -- Keys whose reach the example has to state ------------------------------
#
# Two keys in the sample config are a trap for a reader who copies the file, and
# since ADR-0030 decision 2 both traps are over-reading rather than under.
#
# `providers.review.repositories` (SEC-10's allowlist) now selects a real
# control: `security/project_config.py` reads the key and
# `security/review_allowlist.py` refuses a repository the list does not name,
# before any process is spawned. What a copying reader over-reads is the reach --
# that listing a repository here turns ingestion *on*. It does not: no command
# exposes review ingestion yet, and an empty or absent list allows nothing.
#
# Its annotation has been wrong in both directions, which is why the rows below
# pin the reach rather than a verdict. It first said "Nothing in `src/` reads
# this file", which ADR-0027 decision 3 falsified; the narrowed replacement said
# the allowlist "is not in force", which this change falsifies. The retracted
# file-wide universal is refused by `test_raptor_config_claims.py`, which scans
# this file's comment blocks; the rows below are the positive half and cannot see
# a sentence coming back beside them.
#
# `security.secretScan` (SEC-11's policy, #198 and #329) is the mirror image
# since ADR-0027 decision 3: it now selects real behaviour, and the trap is
# *over*-reading it. `secretScan: block` covers `theurian propose accept`, where
# it refuses, and `theurian index build`, where it only signals -- `theurian
# ingest` runs no scan of its own -- with a best-effort detector that is not a
# repository secret scanner. A reader who copies `block` and concludes that
# secrets cannot reach their knowledge base is as wrong as the reader who used
# to conclude it blocked anything at all.
#
# What makes the example honest is the comment above each key, and a comment is
# exactly the kind of thing a later edit drops without noticing, because nothing
# validates it. `test_config_matches_its_schema` cannot help: comments are gone
# before the parser sees the document, and both values are schema-valid with or
# without the annotation.


def _annotation_above(text: str, key: str) -> str:
    """The contiguous comment block immediately above ``key``'s line, joined.

    Joined into one string, so that where an annotation happens to wrap is not
    part of the contract. Both blocks run to several lines, and rewording one
    reflows the rest of it: #426 narrowed the `repositories` claim and moved
    every wrap in that block. A per-line search would have gone red on the
    reflow while the sentence it pins was still there, and would pass while a
    pinned sentence was broken in half. Leading `#` and indentation are
    stripped from each line first.

    The key line is located by ``<indent><key>:``, and the caller asserts that
    exactly one such line exists -- a second occurrence would make "the comment
    above it" ambiguous and the result meaningless.
    """
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().startswith(f"{key}:"))

    block: list[str] = []
    while index > 0 and lines[index - 1].strip().startswith("#"):
        index -= 1
        block.insert(0, lines[index].strip().lstrip("#").strip())
    return " ".join(block)


def _in_config(config: dict[str, Any], key: str) -> Any:
    """The value of ``key`` wherever it is nested, searched depth-first.

    Both keys under test are unique in the document -- the caller asserts that on
    the raw text before this runs -- so a search costs nothing and keeps the
    table below from carrying a path that duplicates what the file already says.
    """
    if key in config:
        return config[key]
    for value in config.values():
        if isinstance(value, dict):
            found = _in_config(value, key)
            if found is not None:
                return found
    return None


#: A required sentence that is nothing but an issue number. That shape is the
#: rows' "the annotation stays a claim someone owns" half, and it is the only one
#: :data:`_CITE_SAID_TO_BE_CLOSED` is applied to: a required sentence carrying
#: prose as well says something a closed issue can still be the correct authority
#: for, and these annotations cite closed issues on purpose.
_ISSUE_CITE: Final = re.compile(r"^#\d+$")

#: The same cite described as closed, within one clause of itself. This is what
#: makes *naming* an issue different from naming a **live** one, and it is keyed on
#: the required cite rather than on the annotation at large: these annotations
#: name closed issues on purpose, as the history that explains the live owner.
#:
#: The window stops at a full stop or a semicolon, which is what keeps the shipped
#: text legal: ``#330 owns it; the ... half shipped under #198`` puts the semicolon
#: between the required cite and the word, and ``#429 owns it; #129 was closed``
#: does the same one row down. Measured 2026-09-01 against the wording then
#: shipped (``#329 owns those two; #198 is closed``) -- both rows green as
#: written, and the defect shape (``#500 owns those two; #198 is closed``, with
#: ``#198`` still required) RED. #329 moved the ``secretScan`` row's live owner to
#: ``#330`` and reflowed its clause; the rule the measurement established is
#: unchanged.
#:
#: **Escapes measured in both directions, recorded rather than chased.** A closure
#: written past the window -- ``#330 owns it, and after a long paragraph of
#: qualification it is closed`` -- is not caught, and a live owner whose clause
#: happens to carry the word as an adjective -- ``#330 owns the closed-loop pass``
#: -- is caught although it is correct. Neither shape is in the file today. The
#: rule is a cheap second signal beside the substring test, not a classifier.
_CITE_SAID_TO_BE_CLOSED: Final = r"{cite}\b[^.;]{{0,30}}?\bclosed\b"


def _cites_said_to_be_closed(annotation: str, required: tuple[str, ...]) -> dict[str, str]:
    """The requirements of ``required`` that ``annotation`` calls closed, to the match.

    Extracted rather than left inline so that :data:`CLOSED_CITE_CASES` drives
    *this* predicate. A driver that rebuilt the rule out of :data:`_ISSUE_CITE`
    and :data:`_CITE_SAID_TO_BE_CLOSED` by hand would go RED against its own copy
    and stay green whatever the rows below actually ran, which is the failure the
    two constants were already in: the shipped annotations are compliant, so both
    patterns matched nothing and deleting either changed no result.

    Keyed on the requirement rather than on the annotation at large. Only a
    requirement that is *nothing but* an issue number is a claim about who owns
    the gap the row states; a requirement carrying prose says something a closed
    issue can still be the correct authority for, and these annotations cite
    closed issues on purpose.
    """
    return {
        sentence: found.group(0)
        for sentence in required
        if _ISSUE_CITE.match(sentence)
        and (
            found := re.search(_CITE_SAID_TO_BE_CLOSED.format(cite=re.escape(sentence)), annotation)
        )
    }


#: ``(key, the value the example teaches, the sentences its annotation must keep)``.
#:
#: The value is asserted as well as the annotation because the two together are
#: the claim: the example keeps a realistic policy *and* bounds what it does.
#: Dropping the key would also be dishonest -- it would hide a published part of
#: the contract rather than annotate it -- so both halves fail here.
#:
#: Both rows now require the annotation to say *how far* its key reaches, and
#: that is the change ADR-0030 decision 2 made to the first of them.
#: ``repositories`` used to read nowhere, so its annotation had to say so; the
#: allowlist is read and enforced now, so what a copying reader must not
#: over-read is the **reach**: it refuses before a spawn, an empty list allows
#: nothing, and no command exposes review ingestion yet -- so listing a
#: repository here starts nothing on its own. ``secretScan`` has had that shape
#: since ADR-0027 decision 3 -- the approval gate, where it refuses, and the
#: index build, where it only signals.
#:
#: ``repositories``' four sentences are one claim in four parts, and the last two
#: are there because the annotation has been wrong in both directions. It said
#: "Nothing in ``src/`` reads this file" (#426), which ADR-0027 decision 3
#: falsified; the narrowed replacement said the allowlist "is not in force",
#: which this change falsifies. So the row pins **the reader by module**
#: (``security/review_allowlist.py`` is what refuses), **when the refusal
#: happens** -- before a process is spawned, which is the property that makes it
#: a control rather than a filter -- **that an empty list allows nothing**, and
#: **that no command reaches it yet**, which is the sentence that keeps a reader
#: from believing they have turned something on.
#:
#: ``secretScan``'s third sentence is the same requirement on the other key, and
#: it has now moved twice for the same reason (#428, then #329). It required
#: ``#198`` until that issue closed by *shipping* the ``propose accept`` half the
#: annotation's first sentence describes; it then required ``#329``, which owned
#: the ingest- and index-time gap until #329 closed by shipping the index-build
#: half -- so both are history here and own nothing. What the annotation still
#: states as owed is the **draft-time advisory**, and ``#330`` owns it. A live
#: owner is what this row is for, and each time a closed one satisfied it.
#:
#: The row therefore moves whenever the *gap the annotation states* changes, not
#: whenever an issue closes: a rewrite that dropped the draft-time sentence would
#: leave nothing for ``#330`` to own and this row would be pinning a cite to
#: prose that no longer makes a claim.
#:
#: The module fragment is required *as well as* the key because the two are
#: different facts and the annotation's job is to carry both. A rewrite naming
#: only ``security.secretScan`` says what the file is read *for* and leaves a
#: reader with nowhere to check it; it passed this row until round one.
ANNOTATED_KEYS: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    ("secretScan", "block", ("propose accept", "best effort", "#330")),
    (
        "repositories",
        ["acme/order-service"],
        (
            "`security/review_allowlist.py`",
            "before the process that reaches GitHub is started",
            "an empty or absent list allows nothing",
            "No command exposes review ingestion yet",
        ),
    ),
)


@pytest.mark.parametrize(
    ("key", "value", "required"), ANNOTATED_KEYS, ids=[case[0] for case in ANNOTATED_KEYS]
)
def test_a_key_the_example_sets_still_states_how_far_it_reaches(
    key: str, value: Any, required: tuple[str, ...]
) -> None:
    """The example is what a reader copies, so each key must state its own reach.

    `providers.review.repositories` now selects a real control (ADR-0030 decision
    2): `security/review_allowlist.py` refuses a repository the list does not
    name, before any process is spawned. A reader who copies the key and believes
    they have turned review ingestion on is wrong -- no command reaches it yet --
    and the annotation is what tells them. The reader's *module* is required
    because a sentence naming only the behaviour leaves nowhere to check it, and
    the *before a spawn* clause is required because that is the difference
    between a control and a filter: an allowlist consulted after the fetch would
    satisfy any sentence that omits it.

    **The retracted sentence is refused, not merely superseded.**
    `tests/unit/test_raptor_config_claims.py::test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file`
    scans this file's comment blocks for it, in the pronoun form the annotation
    actually used ("Nothing in `src/` reads this file"). Without that half, the
    sentence could be restored verbatim beside these required ones and every
    test here would still pass -- measured in round one.

    `secretScan: block` is the other error. Until ADR-0027 decision 3 it selected
    nothing either, and this test required the annotation to say so. It now
    selects real behaviour at `theurian propose accept` (#198) and at `theurian
    index build` (#329) -- and a reader who concludes from `block` that secrets
    cannot reach their knowledge base is as wrong as the reader who used to
    conclude the opposite. The detector is best effort, it gates at one point
    and only signals at the other, and `theurian ingest` runs no scan of its own.
    So the required sentences flipped from "nothing reads this" to what it
    reaches, rather than being dropped.

    Same reckoning as `test_the_example_does_not_switch_the_raptor_forest_on`
    above: `test_config_matches_its_schema` cannot catch this, because the
    document is schema-valid whether or not the annotation is there.

    Deliberately prose-sensitive. These sentences are what make the example
    honest, not a stylistic choice, so rewording one should bring someone here to
    re-read what the example is promising rather than pass unremarked. The issue
    reference is required beside them so the annotation stays a claim someone
    owns.

    **A named issue is not a live one, and the substring test alone cannot tell
    them apart.** ``sentence in annotation`` is satisfied by the number appearing
    anywhere in the block -- including inside a sentence saying that issue is
    closed. That is not hypothetical: #428 found the ``secretScan`` row satisfied
    by a closed #198 and moved it to #329, and the naive coordinated change did
    not go RED. Measured again here, 2026-09-01, one number over: with the
    annotation rewritten to *"(#500 owns those two; #329 is closed, having
    shipped ...)"* and the row still requiring ``"#329"``, this module reported
    **16 passed** while the live owner it names had changed. So a required
    sentence that is *only* an issue number must also not be described as closed
    within its own clause. The rows' history cites are untouched -- they sit on
    the far side of a semicolon, and the window stops there.

    **That half is driven elsewhere and has to be**, by
    :func:`test_an_annotation_that_calls_its_own_owner_closed_is_refused`. Both
    shipped annotations are compliant, so the guard reports nothing on either row
    whether it works or matches nothing at all. Measured on this module as it
    stood at ``57c3da3``, the commit before that driver: `_ISSUE_CITE` made
    unmatchable, `_CITE_SAID_TO_BE_CLOSED` made unmatchable, and both at once --
    16 passed on all three. These rows hold the config; the synthetic rows hold
    the guard.
    """
    text = CONFIG.read_text(encoding="utf-8")
    config = load_yaml_mapping(text)

    occurrences = [line for line in text.splitlines() if line.strip().startswith(f"{key}:")]
    assert len(occurrences) == 1, (
        f"`{key}:` appears {len(occurrences)} time(s) in the example config; "
        + (
            "the key is gone. It is kept as data on purpose -- the example "
            "teaches the shape the control will need -- so deleting it hides a "
            "published part of the contract rather than annotating it."
            if not occurrences
            else "'the comment above it' is therefore ambiguous, and the "
            "annotation check below would be reading an arbitrary block."
        )
    )

    assert _in_config(config, key) == value, (
        f"the example no longer sets `{key}` to {value!r}. It is kept as data on "
        f"purpose -- it teaches the shape the control needs -- so removing it "
        f"hides a published key rather than annotating it. If what the control "
        f"does has changed, this test and the annotation are what change with it."
    )
    annotation = _annotation_above(text, key)
    for sentence in required:
        assert sentence in annotation, (
            f"the annotation above `{key}` in {CONFIG.relative_to(REPO_ROOT)} no "
            f"longer says {sentence!r}. It reads:\n  {annotation!r}\n\n"
            f"A reader copies this file, and without that sentence `{key}` reads "
            f"as something it is not. `secretScan` is in force at `theurian "
            f"propose accept` and nowhere else, with a best-effort detector "
            f"(#198); `providers.review.repositories` is read by nothing, though "
            f"the file it sits in is read for `security.secretScan` -- say the "
            f"key, not the file, or the annotation is the false claim #426 "
            f"corrected -- and #429 owns the allowlist against the first "
            f"external fetch path. `tests/unit/test_config_key_call_sites.py` is the pin that "
            f"records which keys have readers, and the schema descriptions are "
            f"what change with them."
        )

    retired = _cites_said_to_be_closed(annotation, required)

    assert not retired, (
        f"the annotation above `{key}` names {sorted(retired)} and says in the "
        f"same clause that it is closed: {retired}. This row requires that token "
        f"so the gap it states stays somebody's, and a closed issue owns nothing "
        f"-- the substring test alone cannot tell the owner from the history "
        f"beside it, which is how a closed #198 satisfied this row until #428. "
        f"Move the requirement to whichever issue the annotation now names as "
        f"the owner."
    )


#: ``(what the row is, a fabricated annotation, what it requires, the requirements
#: the guard must refuse)`` -- the driver for :data:`_ISSUE_CITE` and
#: :data:`_CITE_SAID_TO_BE_CLOSED`.
#:
#: Both constants survived their own deletion before this table existed. The two
#: shipped annotations are compliant, so ``_cites_said_to_be_closed`` returns ``{}``
#: on them whether the patterns work or match nothing at all: at ``57c3da3``, the
#: commit before these rows, the module reported **16 passed** with `_ISSUE_CITE`
#: unmatchable, 16 with :data:`_CITE_SAID_TO_BE_CLOSED` unmatchable, and 16 with
#: both (measured 2026-09-01). A guard no input reaches reports a safety it does
#: not have, and every row here exists because one shape of mutation has to die
#: on it.
#:
#: **The annotations are fabricated, and one of them is false on purpose.** #330
#: is live; the first row writes *"#330 is closed"* as **input** to the guard, and
#: says so here so that a search for that sentence lands on this note rather than
#: on a claim the repository appears to be making. The cite moved from #329 to
#: #330 when #329 shipped the index-build control and stopped being the live
#: owner. Nothing in this table is read off disk: a driver keyed on
#: the real annotations would go green the day somebody rewords one, which is the
#: hole the shipped rows already have and the reason for these.
#:
#: One row per direction the guard can be wrong in:
#:
#: 1. the required cite called closed inside its own clause -- the shape #428
#:    found on the ``secretScan`` row, and the only row that fails if either
#:    pattern stops matching;
#: 2. the shipped shape, whose closure sits past a semicolon -- it fails if the
#:    ``[^.;]`` boundary is widened, which would make the live annotation the
#:    defect;
#: 3. a requirement that is prose beside a genuinely closed cite -- it fails if
#:    :data:`_ISSUE_CITE` stops being anchored, since ``best effort`` sits 18
#:    characters from ``closed`` in the same clause. This is the false positive
#:    the rows have to stay clear of: these annotations name closed issues as
#:    history on purpose.
CLOSED_CITE_CASES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "the required cite, called closed in its own clause",
        "a draft-time advisory is still owed (#330 is closed, having shipped the "
        "`index build` half above; #500 owns it now).",
        ("#330",),
        ("#330",),
    ),
    (
        "the shipped shape, the closure past a semicolon",
        "a draft-time advisory is still owed (#330 owns it; the `index build` half "
        "shipped under #329, which is closed).",
        ("#330",),
        (),
    ),
    (
        "a prose requirement in a clause about a closed issue",
        "The detector is best effort now that #198 is closed; #329 owns the two gaps above.",
        ("best effort", "#329"),
        (),
    ),
)


@pytest.mark.parametrize(
    ("annotation", "required", "refused"),
    [case[1:] for case in CLOSED_CITE_CASES],
    ids=[case[0] for case in CLOSED_CITE_CASES],
)
def test_an_annotation_that_calls_its_own_owner_closed_is_refused(
    annotation: str, required: tuple[str, ...], refused: tuple[str, ...]
) -> None:
    """RED means the closed-owner guard cannot fail, whatever the example says.

    The guard above exists because `sentence in annotation` is satisfied by a
    number appearing anywhere in the block, including inside a sentence saying
    that issue is closed -- which is how a closed #198 held the `secretScan` row
    until #428, with every test in this module green. It is checked here rather
    than on the shipped rows because the shipped rows cannot check it: both
    annotations are compliant, so the guard's result is `{}` either way and the
    two patterns were surviving their own deletion.

    Both directions in one table. Refusing too little is the defect the guard was
    added for. Refusing too much is worse than not having it: these annotations
    cite closed issues deliberately, as the history that explains the live owner,
    and a guard that reported those would be removed by the next author rather
    than narrowed.
    """
    found = _cites_said_to_be_closed(annotation, required)

    assert sorted(found) == sorted(refused), (
        f"the closed-owner guard read {sorted(found)} out of {list(required)}, "
        f"expected {sorted(refused)}. The annotation is:\n  {annotation!r}\n\n"
        f"Too few means it cannot see a required cite the annotation itself calls "
        f"closed -- `_ISSUE_CITE` selects which requirements are owner claims and "
        f"`_CITE_SAID_TO_BE_CLOSED` decides whether the clause retires one, and "
        f"the shipped rows exercise neither. Too many means it refuses text that "
        f"is correct: a history cite on the far side of a semicolon, or a "
        f"requirement that is prose rather than an owner cite."
    )


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migration_matches_its_schema(path: pathlib.Path) -> None:
    _validator("migrations/migration.schema.json").validate(
        load_yaml_mapping(path.read_text(encoding="utf-8"))
    )


def test_the_example_loads_through_the_loader_the_product_itself_runs() -> None:
    """ADR-0027 decision 1, compliance: "the example project still loads".

    Read literally, and through the production call site rather than a
    hand-rolled equivalent: ``resolve_context`` runs
    ``load_migrations(paths.root, paths.migrations, schema_root())``
    (``cli/context.py``), which is what every ``theurian migrate`` invocation
    against this directory would run. That one pass covers three checks the
    rules above cover separately, partially, or not at all -- schema
    conformance, containment of each ``contentFile``, and **the pin against the
    bytes on disk**.

    The third is the one this test was added for, and it was missing entirely.
    The root corpus has
    ``test_dogfood_corpus_governance.py::test_every_pinned_body_hashes_to_the_content_sha256_its_migration_declares``;
    the example population had no digest-*value* check of any kind.
    ``test_migration_matches_its_schema`` catches a pin that is absent -- the
    schema requires it now -- and cannot catch one that is wrong, because a
    wrong digest is a well-formed 64-hex string. Measured: appending a line to
    ``.theurian/knowledge/architecture/auth-policy.md`` without re-pinning left
    all 15 tests in this file green, while ``theurian migrate validate`` against
    the example exits 4. An example the product itself refuses is the exact
    failure this module's docstring says is worse than no example.

    ``ProjectPaths.of`` rather than :data:`THEURIAN_DIR` composed by hand, so
    the layout this reads is the layout the product derives.

    **The count assertion is the fixture guard and is not optional.**
    ``load_migrations`` answers a directory it cannot find with
    ``LoadedMigrations.empty()`` rather than raising, so a path that stopped
    pointing at the example would make this pass while loading nothing at all.
    """
    paths = ProjectPaths.of(EXAMPLE)

    loaded = load_migrations(paths.root, paths.migrations, schema_root())

    assert len(loaded.migration_set) == len(MIGRATIONS), (
        f"the loader read {len(loaded.migration_set)} of the "
        f"{len(MIGRATIONS)} migrations under {paths.migrations}; every assertion "
        f"this test makes is about the set it read"
    )


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migration_filename_starts_with_its_own_id(path: pathlib.Path) -> None:
    """`<ulid>-<slug>.yaml`. A filename that disagrees with the id inside is a
    trap for anyone reading a directory listing."""
    migration = load_yaml_mapping(path.read_text(encoding="utf-8"))
    assert path.name.startswith(f"{migration['id']}-")


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_every_content_file_exists_and_stays_inside_the_project(
    path: pathlib.Path,
) -> None:
    """A dangling `contentFile` would fail at apply time, not at review time."""
    project_root = EXAMPLE.resolve()
    migration = load_yaml_mapping(path.read_text(encoding="utf-8"))

    for operation in migration["operations"]:
        content_file = operation.get("contentFile")
        if content_file is None:
            continue
        resolved = (path.parent / content_file).resolve()
        assert resolved.is_relative_to(project_root), f"{content_file} escapes the project root"
        assert resolved.is_file(), f"{content_file} does not exist"


def test_dependencies_reference_migrations_that_exist() -> None:
    """A missing dependency means the example cannot be applied at all."""
    ids = {load_yaml_mapping(p.read_text(encoding="utf-8"))["id"] for p in MIGRATIONS}
    for path in MIGRATIONS:
        migration = load_yaml_mapping(path.read_text(encoding="utf-8"))
        for dependency in migration.get("dependsOn", []):
            assert dependency in ids, f"{path.name} depends on unknown migration {dependency}"


def test_revision_ids_are_unique() -> None:
    """Two revisions sharing an id would make history ambiguous."""
    seen: list[str] = []
    for path in MIGRATIONS:
        migration = load_yaml_mapping(path.read_text(encoding="utf-8"))
        seen.extend(
            operation["revisionId"]
            for operation in migration["operations"]
            if "revisionId" in operation
        )
    assert len(seen) == len(set(seen)), "duplicate revision ids in the example"


def test_no_derived_artifact_is_committed() -> None:
    """ADR-0004: the example must demonstrate the right thing to check in."""
    derived = [
        p.relative_to(EXAMPLE)
        for p in EXAMPLE.rglob("*")
        if p.is_file()
        and (
            p.suffix in {".sqlite", ".sqlite-wal", ".sqlite-shm"}
            or {"state", "cache", "runtime", "generated"} & set(p.parts)
        )
    ]
    assert not derived, f"derived artifacts in the example: {derived}"


def test_example_demonstrates_a_structured_specification() -> None:
    """The point of ADR-0010: a spec keeps queryable fields, not just prose."""
    spec_files = list((THEURIAN_DIR / "specifications").glob("*.yaml"))
    assert spec_files, "the example should include a structured specification"

    spec = load_yaml_mapping(spec_files[0].read_text(encoding="utf-8"))
    assert "preconditions" in spec
    assert "rules" in spec
    assert "outcomes" in spec
