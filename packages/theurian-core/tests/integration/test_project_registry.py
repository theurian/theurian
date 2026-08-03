"""Project identity: one id names one root (H-5, SEC-13, FR-L2).

Ids default to the directory name, and directory names repeat -- ``team-one/api``
and ``team-two/api`` both propose ``api``. Registration used to overwrite on a
clash, which silently re-pointed the id at whichever repository registered last.
Because every MCP tool resolves a project by asking the registry for a root path,
an agent working in ``team-one`` and asking for ``api`` was served ``team-two``'s
knowledge, with nothing in the answer naming the repository it came from.

That is the SEC-13 failure this module exists to prevent, so the tests below
assert more than "an exception is raised": they assert that the other project's
knowledge does not come back.

Real Git repositories, a real registry file, and the real CLI, all under
``tmp_path`` with ``THEURIAN_DATA_DIR`` redirected -- nothing here touches the
developer's own machine.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectError, ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.identifiers import ProjectId
from theurian.domain.project import Project

pytestmark = pytest.mark.integration

runner = CliRunner()

ALPHA_MARKER = "alpha-only-rotation-clause"
BETA_MARKER = "beta-only-quota-clause"


def _migration(item: str, letter: str, title: str, filename: str) -> str:
    return f"""apiVersion: theurian.dev/v1
id: 01K1{letter}AAAAA01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: 01K1{letter}AAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/{filename}
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{filename}
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A user's machine with an empty registry and nothing registered yet."""
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    yield tmp_path


def _repo(machine: Path, team: str, name: str = "api") -> Path:
    """A Git working tree at ``<team>/<name>``, initialised for Theurian.

    Two teams, one directory name: the collision is the point.
    """
    root = machine / team / name
    root.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    _in(root, "init")
    return root


def _knowledge(root: Path, *, item: str, letter: str, title: str, marker: str) -> None:
    filename = f"{item.split('.', 1)[1]}.md"
    (root / f".theurian/knowledge/architecture/{filename}").write_text(
        f"# {title}\n\nThis repository's own policy: {marker}.\n"
    )
    (root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{item}.yaml").write_text(
        _migration(item, letter, title, filename)
    )


def _in(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """Run a CLI command with ``root`` as the working directory.

    ``cwd`` rather than an explicit path argument, because the behaviour under
    test is precisely how the CLI decides which project it is standing in.
    """
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _entries(machine: Path) -> dict[str, dict[str, str]]:
    return ProjectRegistry.default(machine / "datadir").load()


async def _search(machine: Path, project_id: str, query: str) -> dict[str, Any]:
    """Call ``knowledge.search`` the way the transport does."""
    registry = ProjectRegistry.default(machine / "datadir")
    result = await build_server(registry).call_tool(
        "knowledge.search", {"projectId": project_id, "query": query}
    )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


# -- The clash is refused ----------------------------------------------------


def test_a_second_repository_with_the_same_directory_name_is_refused(machine: Path) -> None:
    """H-5. Both propose the id ``api``; the second must not take it.

    The alternative the code deliberately rejects is picking a suffix
    automatically: an already-configured agent keeps naming ``api`` and would
    silently follow the id to whichever project kept it.
    """
    _in(_repo(machine, "team-one"), "project", "register")

    code, payload = _in(_repo(machine, "team-two"), "project", "register")

    assert code != 0, "a silent overwrite is the failure this refusal prevents"
    assert "already registered" in payload["error"]
    assert "--project-id" in payload["remedy"], "the message must carry the way out"


def test_the_first_registration_survives_a_refused_collision(machine: Path) -> None:
    """A refusal that had already rewritten the entry would be worse than an
    overwrite, because it would look like it changed nothing."""
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    second = _repo(machine, "team-two")

    _in(second, "project", "register")

    assert Path(_entries(machine)["api"]["rootPath"]) == first.resolve()
    assert list(_entries(machine)) == ["api"], "the refused project is not half-registered"


def test_the_registry_itself_refuses_the_clash_not_only_the_cli(machine: Path) -> None:
    """The CLI is one caller. `setup` and the daemon reach the same method, so
    the guard belongs to the registry rather than to the command."""
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    registry = ProjectRegistry.default(machine / "datadir")
    entry = registry.load()["api"]
    other = _repo(machine, "team-two")

    with pytest.raises(ProjectError, match="already registered"):
        registry.register(_project_from(entry, project_id="api", root=other))

    assert Path(registry.load()["api"]["rootPath"]) == first.resolve()


def _project_from(entry: dict[str, str], *, project_id: str, root: Path) -> Project:
    """Rebuild a `Project` from a registry entry, pointed at a different root."""
    return Project(
        project_id=ProjectId(project_id),
        root_path=str(root.resolve()),
        repository_url=entry.get("repositoryUrl") or None,
        default_branch=entry.get("defaultBranch") or "main",
        knowledge_directory=PurePosixPath(entry.get("knowledgeDirectory") or ".theurian"),
        registered_at=datetime.now(UTC),
    )


# -- The way out -------------------------------------------------------------


def test_a_distinct_id_registers_the_colliding_repository(machine: Path) -> None:
    """`--project-id` is the documented remedy, so it has to actually work."""
    _in(_repo(machine, "team-one"), "project", "register")
    second = _repo(machine, "team-two")

    code, payload = _in(second, "project", "register", "--project-id", "api-team-two")

    assert code == 0
    assert payload["projectId"] == "api-team-two"
    assert Path(_entries(machine)["api-team-two"]["rootPath"]) == second.resolve()
    assert Path(_entries(machine)["api"]["rootPath"]) != second.resolve()


def test_a_registered_repository_resolves_to_its_registered_id_without_the_flag(
    machine: Path,
) -> None:
    """The half that makes the remedy usable.

    Requiring `--project-id` on every subsequent command would make the flag a
    trap: forget it once and the CLI silently addresses ``api``, which is
    another team's repository. The registry is keyed by root path precisely so
    that standing in the directory is enough.
    """
    _in(_repo(machine, "team-one"), "project", "register")
    second = _repo(machine, "team-two")
    _in(second, "project", "register", "--project-id", "api-team-two")

    _, status = _in(second, "project", "status")

    assert status["projectId"] == "api-team-two", "not the colliding directory-name default"
    assert Path(status["root"]) == second.resolve()
    assert status["registered"] is True


def test_an_unregistered_repository_falls_back_to_its_directory_name(machine: Path) -> None:
    """The registry answers for registered projects only. Before registration
    there is nothing to look up, and the directory name is still the proposal a
    user is offered."""
    fresh = _repo(machine, "team-three", name="payments")

    _, status = _in(fresh, "project", "status")

    assert status["projectId"] == "payments"
    assert status["registered"] is False


def test_re_registering_the_same_root_stays_idempotent(machine: Path) -> None:
    """FR-L2. Setup runs repeatedly; the collision guard must not turn the
    second run of an unchanged project into an error."""
    root = _repo(machine, "team-one")
    _in(root, "project", "register")

    code, again = _in(root, "project", "register")

    assert code == 0
    assert again["changed"] is False


def test_re_registering_a_disambiguated_project_stays_idempotent(machine: Path) -> None:
    """The same guarantee for the project that had to be renamed, which reaches
    `register` through the registry lookup rather than the derived default."""
    _in(_repo(machine, "team-one"), "project", "register")
    second = _repo(machine, "team-two")
    _in(second, "project", "register", "--project-id", "api-team-two")

    code, again = _in(second, "project", "register")

    assert code == 0
    assert again["projectId"] == "api-team-two", "the flag is not needed a second time"
    assert again["changed"] is False


# -- id_for_root: the path is the identity -----------------------------------


def test_a_root_is_looked_up_by_path_not_by_directory_name(machine: Path) -> None:
    """Two roots share a name; only one of them is ``api``."""
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    second = _repo(machine, "team-two")
    _in(second, "project", "register", "--project-id", "api-team-two")
    registry = ProjectRegistry.default(machine / "datadir")

    assert registry.id_for_root(first) is not None
    assert registry.id_for_root(first).value == "api"  # type: ignore[union-attr]
    assert registry.id_for_root(second).value == "api-team-two"  # type: ignore[union-attr]


def test_an_unregistered_root_has_no_id(machine: Path) -> None:
    """``None`` rather than a guess, so the caller decides what to fall back to."""
    _in(_repo(machine, "team-one"), "project", "register")
    registry = ProjectRegistry.default(machine / "datadir")

    assert registry.id_for_root(_repo(machine, "team-two")) is None


def test_an_unresolved_root_still_matches_its_registration(machine: Path) -> None:
    """Callers pass whatever path they were given.

    Comparing path strings rather than resolved paths would answer ``None`` for
    ``team-one/api/.`` -- and ``None`` degrades silently to the colliding
    directory-name default, which is the leak this lookup prevents.
    """
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    registry = ProjectRegistry.default(machine / "datadir")

    for spelling in (first / ".", first / ".." / "api", first.parent / "." / "api"):
        found = registry.id_for_root(spelling)
        assert found is not None, f"{spelling} is the same directory"
        assert found.value == "api"


# -- SEC-13: the knowledge itself stays apart --------------------------------


@pytest.fixture
def two_teams(machine: Path) -> tuple[Path, Path, Path]:
    """``team-one/api`` registered as ``api``; ``team-two/api`` disambiguated.

    Each holds one approved item carrying a marker string that appears nowhere
    else, so a leak is visible in the response rather than inferred from a count.
    Migrations are applied from inside each repository, *without* the flag --
    which is what makes this a test of the resolution order and not only of the
    registry.
    """
    first = _repo(machine, "team-one")
    _knowledge(
        first, item="architecture.alpha", letter="A", title="Alpha policy", marker=ALPHA_MARKER
    )
    _in(first, "project", "register")
    _in(first, "migrate", "apply")

    second = _repo(machine, "team-two")
    _knowledge(
        second, item="architecture.beta", letter="B", title="Beta policy", marker=BETA_MARKER
    )
    _in(second, "project", "register", "--project-id", "api-team-two")
    _in(second, "migrate", "apply")

    return machine, first, second


@pytest.mark.asyncio
async def test_knowledge_written_in_one_repository_is_readable_under_its_own_id(
    two_teams: tuple[Path, Path, Path],
) -> None:
    """The write side and the read side must agree on the id.

    `migrate apply` records items under the id the CLI resolved; the MCP tool
    reads them under the id the agent asked for. Resolving the CLI's id from the
    directory name would file ``team-two``'s knowledge under ``api`` and leave
    ``api-team-two`` answering every query with nothing -- a knowledge base that
    accepted writes and served none of them.
    """
    machine, _, _ = two_teams

    result = await _search(machine, "api-team-two", "policy")

    assert result["count"] == 1
    assert result["results"][0]["itemId"] == "architecture.beta"
    assert BETA_MARKER in json.dumps(result)


@pytest.mark.asyncio
async def test_the_other_repositorys_knowledge_is_never_returned(
    two_teams: tuple[Path, Path, Path],
) -> None:
    """SEC-13, the failure the whole guard exists for.

    Asserted on the marker strings rather than on a count: an answer that
    returned one hit from the wrong repository would satisfy ``count == 1``.
    """
    machine, _, _ = two_teams

    alpha = await _search(machine, "api", "policy")
    beta = await _search(machine, "api-team-two", "policy")

    assert ALPHA_MARKER in json.dumps(alpha)
    assert BETA_MARKER not in json.dumps(alpha), "team-two's knowledge must not reach team-one"
    assert BETA_MARKER in json.dumps(beta)
    assert ALPHA_MARKER not in json.dumps(beta), "team-one's knowledge must not reach team-two"


@pytest.mark.asyncio
async def test_the_id_that_lost_the_clash_still_points_at_the_first_repository(
    machine: Path,
) -> None:
    """The end-to-end form of the overwrite bug.

    Under the old behaviour this sequence returned ``team-two``'s knowledge for
    ``api``: the second registration repointed ``rootPath`` and every tool
    followed it. Nothing in the response said which repository answered.
    """
    first = _repo(machine, "team-one")
    _knowledge(
        first, item="architecture.alpha", letter="A", title="Alpha policy", marker=ALPHA_MARKER
    )
    _in(first, "project", "register")
    _in(first, "migrate", "apply")

    second = _repo(machine, "team-two")
    _knowledge(
        second, item="architecture.beta", letter="B", title="Beta policy", marker=BETA_MARKER
    )
    _in(second, "migrate", "apply")
    code, _ = _in(second, "project", "register")

    result = await _search(machine, "api", "policy")

    # Asserted before the exit code, so a registry that overwrote again would
    # fail here -- on the leak -- rather than on a status code.
    assert result["results"][0]["itemId"] == "architecture.alpha"
    assert BETA_MARKER not in json.dumps(result), "team-two must not answer for `api`"
    assert code != 0, "the clash must be refused before it can repoint anything"
