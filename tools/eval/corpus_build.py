"""Build the two projects a Phase A corpus run measures (ADR-0036 decision 6).

``full`` applies every migration the manifest declares; ``clean`` applies the
``visible``-plane ones only. Both are built through the real CLI -- ``init``,
``project register``, ``migrate apply``, ``index build`` -- so the harness
measures the shipped write, projection and index path rather than a
hand-assembled database that could drift from it.

Named ``corpus_build`` rather than ``build``: this flat module directory has
no package, so a sibling import puts ``tools/eval`` on ``sys.path`` and a
module named ``build`` would shadow the PyPI ``build`` package for the rest
of that process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from corpus import Corpus, CorpusCensus, CorpusError
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, read_active_state
from theurian.cli.main import app
from theurian.domain.context import RequestContext
from theurian.domain.identifiers import ProjectId
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

_RUNNER = CliRunner()

#: The two projects every corpus run builds, named after the plane they admit.
PLANES: tuple[str, ...] = ("full", "clean")

_GIT_IDENTITY: tuple[tuple[str, str], ...] = (
    ("user.email", "eval-harness@theurian.dev"),
    ("user.name", "Theurian eval harness"),
    ("commit.gpgsign", "false"),
)


@dataclass(frozen=True, slots=True)
class IndexBuildCost:
    wall_clock_ms: float
    chunks: int
    embeddings: int
    nodes: int
    index_bytes: int


@dataclass(frozen=True, slots=True)
class BuiltProject:
    name: str
    root: Path
    project_id: str
    data_dir: Path
    census: CorpusCensus
    build_cost: IndexBuildCost


@dataclass(frozen=True, slots=True)
class BuildResult:
    projects: dict[str, BuiltProject]
    home: Path


def build_both(loaded: Corpus, workspace: Path) -> BuildResult:
    """Build ``full`` and ``clean``, each under its own ``THEURIAN_DATA_DIR``.

    Both builds register under the **same** ``projectId`` -- the manifest's
    ``corpusId`` -- in their own separate registry, so a caller cannot tell
    the two apart by the one echoed field neither build's content can touch.
    ``ProjectRegistry.register`` refuses two entries sharing an id in one
    registry, which is why they cannot share one; the depth-corpus equality
    tests (``tests/integration/test_mcp_tools.py``) hold the two apart the
    same way, one registry and one server per side of the pair. Raises
    :class:`CorpusError` if either build's measured census disagrees with
    ``manifest.yaml``: a corpus defect is not a measurement.
    """
    home = workspace / "home"
    home.mkdir(parents=True, exist_ok=True)
    projects: dict[str, BuiltProject] = {}
    for name in PLANES:
        data_dir = workspace / name / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        with _environment(HOME=str(home), THEURIAN_DATA_DIR=str(data_dir)):
            projects[name] = _build_one(loaded, workspace / name / "project", name, data_dir)
    return BuildResult(projects=projects, home=home)


def _build_one(loaded: Corpus, root: Path, name: str, data_dir: Path) -> BuiltProject:
    project_id = loaded.manifest.corpus_id
    root.mkdir(parents=True)
    _git_init(root)
    _invoke(root, "init")
    _invoke(root, "project", "register", "--project-id", project_id)

    _populate_migrations(loaded, root, name)
    _commit_all(root)
    _invoke(root, "migrate", "apply")

    cost = _measure_index_build_cost(root)
    census = _measure_and_verify_census(loaded, root, name, project_id, cost.chunks)

    return BuiltProject(
        name=name,
        root=root,
        project_id=project_id,
        data_dir=data_dir,
        census=census,
        build_cost=cost,
    )


def _populate_migrations(loaded: Corpus, root: Path, name: str) -> None:
    """Copy the corpus's knowledge bodies and this build's selected migrations into ``root``."""
    knowledge_src = loaded.root / "knowledge"
    if knowledge_src.is_dir():
        shutil.copytree(knowledge_src, root / ".theurian" / "knowledge", dirs_exist_ok=True)

    migrations_dst = root / ".theurian" / "migrations"
    for entry in loaded.manifest.migrations:
        if name == "clean" and entry.plane != "visible":
            continue
        source = loaded.root / "migrations" / entry.file
        if not source.is_file():
            raise CorpusError(
                "migration-file-missing", f"manifest.yaml names {source}, which does not exist"
            )
        shutil.copy2(source, migrations_dst / entry.file)


def _measure_index_build_cost(root: Path) -> IndexBuildCost:
    """Build the index and measure its cost.

    Both flavors, not just `full` (ADR-0036, the gate-vs-census derivation
    rule): `clean` never holds a withheld row, so admitting drafts to its
    index is a no-op there, while making `full` the only flavor that does it
    would put `retrieval.indexesUnapproved` -- a published field reporting
    exactly this flavor -- into the differing set an equality query is
    supposed to hold to {indexBuildId, snapshotId}. One flavor on both sides
    turns the property into one query against an index that holds the
    withheld documents and an index that never did, with the query itself
    left at default flags (`includeUnapproved=false`) -- the query-time gate
    is the thing under measurement, not the build.
    """
    started = time.monotonic()
    build_report = _invoke(root, "index", "build", "--include-unapproved")
    elapsed_ms = (time.monotonic() - started) * 1000
    index_path = Path(build_report["indexPath"])
    return IndexBuildCost(
        wall_clock_ms=elapsed_ms,
        chunks=build_report["chunks"],
        embeddings=build_report["embeddings"],
        nodes=build_report["nodes"],
        index_bytes=index_path.stat().st_size,
    )


def _measure_and_verify_census(
    loaded: Corpus, root: Path, name: str, project_id: str, chunks: int
) -> CorpusCensus:
    """Measure the built project's real census and refuse if it disagrees with the manifest."""
    measured = _measure_census(root, project_id=project_id, chunks=chunks)
    expected = loaded.manifest.census[name]
    if measured != expected:
        raise CorpusError(
            "census-mismatch",
            f"{name!r} corpus census does not match manifest.yaml: "
            f"measured {measured}, expected {expected}",
        )
    return measured


def _measure_census(root: Path, *, project_id: str, chunks: int) -> CorpusCensus:
    """Read items by status and sensitivity straight from the canonical store.

    Deliberately not the ``migrate apply`` report, which carries operation
    counts rather than a census, and deliberately not gated by any visibility
    grant: this is what the corpus *is*, not what one caller may see of it.
    """
    paths = ProjectPaths.of(root)
    active = read_active_state(paths)
    if active is None:
        raise CorpusError("no-active-state", f"{root} has no active canonical state after apply")
    database = paths.database_for(active.state_hash)
    context = RequestContext(project_id=ProjectId(project_id))
    with SqliteCanonicalStore(database) as store:
        items = store.list_items(context)
    by_status: dict[str, int] = {}
    by_sensitivity: dict[str, int] = {}
    for item in items:
        by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
        by_sensitivity[item.sensitivity.value] = by_sensitivity.get(item.sensitivity.value, 0) + 1
    return CorpusCensus(
        items=len(items),
        by_status=MappingProxyType(by_status),
        by_sensitivity=MappingProxyType(by_sensitivity),
        chunks=chunks,
    )


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, capture_output=True)  # noqa: S607
    for key, value in _GIT_IDENTITY:
        subprocess.run(  # noqa: S603 - key/value come from the fixed literal tuple above
            ["git", "config", key, value],  # noqa: S607
            cwd=root,
            check=True,
            capture_output=True,
        )


def _commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)  # noqa: S607
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "build eval corpus"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )


def _invoke(cwd: Path, *args: str) -> dict[str, Any]:
    """Run one CLI command in-process against ``cwd``, and parse its ``--json`` payload.

    Every command this harness drives -- ``init``, ``migrate apply``,
    ``index build`` -- resolves its project from ``Path.cwd()`` with no path
    argument, so this never depends on an earlier ``cd``: the working
    directory is set here, for this one invocation, and restored before this
    function returns (CLAUDE.md, "Running the CLI on a development machine").
    """
    with _chdir(cwd):
        result = _RUNNER.invoke(app, [*args, "--json"], catch_exceptions=False)
    if result.exit_code != 0:
        raise CorpusError(
            "cli-failure",
            f"`theurian {' '.join(args)}` exited {result.exit_code} in {cwd}: "
            f"{result.stdout}{result.stderr or ''}",
        )
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


@contextmanager
def _chdir(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def _environment(**values: str) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
