"""The documented ingest manifest path, against the path the command writes (#198).

``ingest_command`` computes its manifest path as
``context.paths.knowledge_dir / "cache" / "ingestion.json"``, which for the
default knowledge directory resolves to ``.theurian/cache/ingestion.json`` --
confirmed against the real CLI by
``tests/integration/test_cli_commands.py::test_ingest_writes_a_manifest_under_the_derived_cache``,
which asserts ``(project / ".theurian/cache/ingestion.json").is_file()``.

Four surfaces document that path in prose, and #198's own fix shipped all four
with a directory the command never writes: ``.theurian/knowledge/cache/ingestion.json``.
``.theurian/knowledge`` is a real subdirectory (``ProjectPaths.knowledge``), but
``.theurian/knowledge/cache`` is not -- the cache lives one level up, directly
under ``.theurian/``. So the documented path was a no-op: a user or an agent who
went looking for the manifest there would find nothing, and the docstring ships
in ``theurian ingest --help``.

This reproduces the exact class #198 set out to close -- documenting a file that
does not exist -- inside the #198 fix, which is why it earns a pin rather than a
one-line correction. The pin ties the docstring to the path the code *computes*,
not to a hand-typed literal, so a future change to ``knowledge_dir`` or the cache
layout fails here rather than quietly making the prose wrong again.

Pure: it reads the shipped ``__doc__`` and four repository files as text, builds
one ``ProjectPaths`` by path arithmetic, and opens no database, socket or
temporary directory.
"""

from __future__ import annotations

import pathlib

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.cli.commands import ingest_command

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

#: The directory the manifest never lives in. ``.theurian/knowledge`` exists, but
#: the manifest is under ``.theurian/cache``; the wrong path splices a real
#: subdirectory in between and points at nothing.
WRONG_MANIFEST_DIR = ".theurian/knowledge/cache/"


def _computed_manifest_relpath() -> str:
    """The manifest path the command writes, as the code computes it.

    Derived from ``ProjectPaths`` rather than typed out, so a change to the
    knowledge directory or the cache layout moves the expected string with the
    code instead of leaving this test asserting yesterday's path.
    """
    paths = ProjectPaths.of(pathlib.Path("/project"))
    manifest = paths.knowledge_dir / "cache" / "ingestion.json"
    return manifest.relative_to(paths.root).as_posix()


#: Every surface that documents the manifest path in prose, as
#: ``(label, text)``. The docstring ships in ``theurian ingest --help``; the
#: other three are read from disk. All four shipped #198 with the wrong path.
MANIFEST_DOC_SURFACES: tuple[tuple[str, str], ...] = (
    ("ingest_command.__doc__", ingest_command.__doc__ or ""),
    (
        "plugins/claude-code/commands/ingest.md",
        (REPO_ROOT / "plugins" / "claude-code" / "commands" / "ingest.md").read_text(
            encoding="utf-8"
        ),
    ),
    (
        "packages/theurian-core/CHANGELOG.md",
        (REPO_ROOT / "packages" / "theurian-core" / "CHANGELOG.md").read_text(encoding="utf-8"),
    ),
    (
        "plugins/claude-code/CHANGELOG.md",
        (REPO_ROOT / "plugins" / "claude-code" / "CHANGELOG.md").read_text(encoding="utf-8"),
    ),
)


def test_the_ingest_docstring_names_the_manifest_path_the_command_writes() -> None:
    """The shipped ``--help`` text must name the file the command actually writes.

    ``ingest_command.__doc__`` is what Typer renders for ``theurian ingest
    --help``. If it names ``.theurian/knowledge/cache/ingestion.json`` -- as #198
    shipped it -- a user reading the help goes looking for the manifest in a
    directory the command never creates. The expected string is derived from the
    same ``ProjectPaths`` arithmetic the command uses, so this fails if the
    docstring drifts from the code in either direction.
    """
    expected = _computed_manifest_relpath()
    doc = ingest_command.__doc__ or ""

    assert expected == ".theurian/cache/ingestion.json", (
        "the manifest path the command computes changed; update the four "
        f"documentation surfaces to match {expected!r}"
    )
    assert expected in doc, (
        f"`theurian ingest --help` does not name the manifest path the command "
        f"writes ({expected!r}). It reads:\n{doc}"
    )
    assert WRONG_MANIFEST_DIR not in doc, (
        f"the docstring still names {WRONG_MANIFEST_DIR!r}, a directory the "
        f"command never writes; the manifest is under `.theurian/cache/`, not "
        f"under `.theurian/knowledge/cache/`"
    )


@pytest.mark.parametrize(
    ("label", "text"),
    MANIFEST_DOC_SURFACES,
    ids=[label for label, _ in MANIFEST_DOC_SURFACES],
)
def test_no_manifest_surface_documents_a_directory_the_command_never_writes(
    label: str, text: str
) -> None:
    """All four #198 surfaces must name ``.theurian/cache/``, never ``.theurian/knowledge/cache/``.

    The docstring, the slash-command doc and the two changelogs all describe the
    manifest. Every one shipped with the wrong directory, so a single corrected
    surface next to three wrong ones would still teach a reader the no-op path.
    Holding all four to the computed path is what stops the class recurring.
    """
    expected = _computed_manifest_relpath()

    assert expected in text, (
        f"{label} does not name the manifest path the command writes ({expected!r})."
    )
    assert WRONG_MANIFEST_DIR not in text, (
        f"{label} still names {WRONG_MANIFEST_DIR!r}, a directory the command "
        f"never writes. The manifest is written to "
        f"`context.paths.knowledge_dir / 'cache' / 'ingestion.json'`, which is "
        f"`.theurian/cache/ingestion.json`; `.theurian/knowledge/cache/` splices "
        f"a real subdirectory in between and points at nothing."
    )
