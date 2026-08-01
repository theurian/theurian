"""The bundled example must be valid (OSS-13).

A third party has to be able to clone this repository and see a real, working
`.theurian/` without writing one first. An example that has quietly drifted out
of conformance with the schemas is worse than no example: it teaches the wrong
shape.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

from theurian.security import load_yaml_mapping

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCHEMAS = REPO_ROOT / "schemas"
EXAMPLE = REPO_ROOT / "examples" / "sample-project"
THEURIAN_DIR = EXAMPLE / ".theurian"

MIGRATIONS = sorted((THEURIAN_DIR / "migrations").glob("*.yaml"))


def _validator(relative: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / relative).read_text(encoding="utf-8")))


def test_example_exists() -> None:
    assert THEURIAN_DIR.is_dir(), "the bundled example is missing"
    assert MIGRATIONS, "the example has no migrations"


def test_config_matches_its_schema() -> None:
    _validator("config/project-config.schema.json").validate(
        load_yaml_mapping((THEURIAN_DIR / "config.yaml").read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migration_matches_its_schema(path: pathlib.Path) -> None:
    _validator("migrations/migration.schema.json").validate(
        load_yaml_mapping(path.read_text(encoding="utf-8"))
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
