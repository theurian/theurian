"""The bundled example must be valid (OSS-13).

A third party has to be able to clone this repository and see a real, working
`.theurian/` without writing one first. An example that has quietly drifted out
of conformance with the schemas is worse than no example: it teaches the wrong
shape.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

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


# -- Keys the example sets that take no effect ------------------------------
#
# Two keys in the sample config select a control that does not exist:
# `security.secretScan` (SEC-11's policy, #198) and
# `providers.review.repositories` (SEC-10's allowlist, #129). Both are kept as
# data on purpose -- the example teaches the shape a reader will need once the
# controls ship -- and both are therefore a trap: a reader who copies this file
# copies `secretScan: block` and reasonably concludes that secrets are blocked.
#
# What makes the example honest is the comment above each key, and a comment is
# exactly the kind of thing a later edit drops without noticing, because nothing
# validates it. `test_config_matches_its_schema` cannot help: comments are gone
# before the parser sees the document, and both values are schema-valid with or
# without the annotation.


def _annotation_above(text: str, key: str) -> str:
    """The contiguous comment block immediately above ``key``'s line, joined.

    Joined into one string because both annotations wrap across lines: the
    `repositories` one splits "Nothing in `src/` reads / this file" over a line
    break, and a per-line search would miss the sentence that is actually there.
    Leading `#` and indentation are stripped from each line first.

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


#: ``(key, the value the example teaches, the sentences its annotation must keep)``.
#:
#: The value is asserted as well as the annotation because the two together are
#: the claim: the example keeps a realistic policy *and* says it is not applied.
#: Dropping the key would also be dishonest -- it would hide a published part of
#: the contract rather than annotate it -- so both halves fail here.
NOT_IN_FORCE_KEYS: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    ("secretScan", "block", ("Nothing in `src/` reads", "#198")),
    ("repositories", ["acme/order-service"], ("Nothing in `src/` reads", "#129")),
)


@pytest.mark.parametrize(
    ("key", "value", "required"), NOT_IN_FORCE_KEYS, ids=[case[0] for case in NOT_IN_FORCE_KEYS]
)
def test_a_key_the_example_sets_but_nothing_reads_stays_marked_not_in_force(
    key: str, value: Any, required: tuple[str, ...]
) -> None:
    """The example is what a reader copies, so an inert key must say it is inert.

    `secretScan: block` reads as a shipped control. It is not one: SEC-11's
    scanner does not exist (#198), and nothing in `src/` reads
    `.theurian/config.yaml` at all (#129), so the value blocks nothing.
    `providers.review.repositories` is the same shape for SEC-10's allowlist.
    Six documents were corrected to stop claiming either control was in force,
    and this file was one of them -- but the correction landed as a *comment*,
    which no schema validates and no other test reads.

    Same reckoning as `test_the_example_does_not_switch_the_raptor_forest_on`
    above: `test_config_matches_its_schema` cannot catch this, because the
    document is schema-valid whether or not the annotation is there.

    Deliberately prose-sensitive. "Nothing in `src/` reads" is the sentence that
    makes the example honest, not a stylistic choice, so rewording it should
    bring someone here to re-read what the example is promising rather than pass
    unremarked. The issue reference is required beside it so the annotation
    stays a claim someone owns.
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
        f"purpose -- it teaches the shape the control will need -- so removing it "
        f"hides a published key rather than annotating it. If the control now "
        f"ships, this test and the annotation are what change with it."
    )
    annotation = _annotation_above(text, key)
    for sentence in required:
        assert sentence in annotation, (
            f"the annotation above `{key}` in {CONFIG.relative_to(REPO_ROOT)} no "
            f"longer says {sentence!r}. It reads:\n  {annotation!r}\n\n"
            f"A reader copies this file. Without that sentence `{key}` reads as a "
            f"control that is in force, and it is not: SEC-11's secret scanner "
            f"does not exist (#198) and nothing in `src/` reads "
            f"`.theurian/config.yaml` at all (#129). If a reader has since "
            f"shipped, `tests/unit/test_config_key_call_sites.py` is the pin that "
            f"records it and the schema descriptions are what change with it."
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
