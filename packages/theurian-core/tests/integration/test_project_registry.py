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

import asyncio
import json
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from migration_fixtures import body_pin
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


def _migration(item: str, letter: str, title: str, filename: str, body: str) -> str:
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
    contentSha256: {body_pin(body)}
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
    body = f"# {title}\n\nThis repository's own policy: {marker}.\n"
    (root / f".theurian/knowledge/architecture/{filename}").write_text(body)
    (root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{item}.yaml").write_text(
        _migration(item, letter, title, filename, body)
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


# -- And the mirror image: one root, two ids ---------------------------------
#
# Refusing "one id, two roots" while permitting "one root, two ids" left the
# documented escape from a collision -- `--project-id` -- walking into a second,
# quieter failure. A user who wanted a clearer name got a *duplicate*
# registration rather than a rename, and the new id addressed a project with no
# knowledge in it: canonical rows and index chunks are stamped with the id in
# force when they were written, and `migrate apply` is idempotent, so nothing
# restamps them. Every search under the new id answered `count: 0` while
# reporting `indexed: true`, and `theurian index status` said there was nothing
# to do.


def test_one_repository_cannot_be_registered_under_a_second_id(machine: Path) -> None:
    """H-5. A project id is an identity, not a label.

    The refusal is what turns "I would like a clearer name" into a rename the
    user performs deliberately, instead of a duplicate they discover weeks later
    as an empty knowledge base.
    """
    root = _repo(machine, "team-one")
    _in(root, "project", "register")

    code, payload = _in(root, "project", "register", "--project-id", "api-renamed")

    assert code != 0
    assert "already registered as api" in payload["error"]
    assert list(_entries(machine)) == ["api"], "no duplicate registration is created"


def test_the_refusal_names_the_rename_and_the_state_rebuild_it_needs(
    machine: Path,
) -> None:
    """The remedy is two commands, and omitting the second is the whole defect.

    Unregistering and re-registering changes which id *addresses* the project
    and restamps nothing: `migrate apply` is idempotent per project, so the
    canonical rows keep the old id and the new one addresses an empty project.
    A refusal that named only `project unregister` would hand the user the
    silent half of the failure it just prevented.
    """
    root = _repo(machine, "team-one")
    _in(root, "project", "register")

    _, payload = _in(root, "project", "register", "--project-id", "api-renamed")

    assert "theurian project unregister api" in payload["remedy"]
    assert "delete .theurian/state/" in payload["remedy"]
    assert "migrate apply" in payload["remedy"]


def test_the_rename_the_refusal_describes_actually_works(machine: Path) -> None:
    """A remedy nobody runs is a remedy nobody has checked.

    Follows the printed instructions end to end -- unregister, re-register under
    the new id, rebuild state -- and asserts the renamed project can read its own
    knowledge back. Without the state rebuild this returns `count: 0`, which is
    the failure the refusal exists to prevent rather than a passing test.
    """
    root = _repo(machine, "team-one")
    _knowledge(
        root, item="architecture.alpha", letter="A", title="Alpha policy", marker=ALPHA_MARKER
    )
    _in(root, "project", "register")
    _in(root, "migrate", "apply")

    _in(root, "project", "unregister", "api")
    shutil.rmtree(root / ".theurian/state")
    code, _ = _in(root, "project", "register", "--project-id", "api-renamed")
    _in(root, "migrate", "apply")

    assert code == 0
    result = asyncio.run(_search(machine, "api-renamed", "policy"))
    assert result["count"] == 1, "the renamed project must read its own knowledge"
    assert ALPHA_MARKER in json.dumps(result)


def test_re_registering_the_same_root_under_the_same_id_is_still_allowed(
    machine: Path,
) -> None:
    """The control. A guard on "this root already has an id" that did not exempt
    the id it already has would break FR-L2 idempotence for every project, which
    is a worse failure than the one it prevents."""
    root = _repo(machine, "team-one")
    _in(root, "project", "register")

    code, again = _in(root, "project", "register", "--project-id", "api")

    assert code == 0
    assert again["changed"] is False


# -- id_for_root will not guess ----------------------------------------------


def test_a_root_registered_under_two_ids_is_refused_rather_than_guessed(
    machine: Path,
) -> None:
    """`register` refuses to create this state, so reaching it means the
    registry was edited by hand — which is a thing people do to a JSON file in
    their home directory.

    Picking the first match would answer a question the registry no longer has
    one answer to, and would do it *silently*: the CLI would address one project
    while every agent naming the other id read an empty one. The registry is
    written here directly because no supported command can produce it.
    """
    root = _repo(machine, "team-one")
    _in(root, "project", "register")
    registry = ProjectRegistry.default(machine / "datadir")
    entry = registry.load()["api"]
    registry.path.write_text(json.dumps({"api": entry, "api-also": dict(entry)}), encoding="utf-8")

    with pytest.raises(ProjectError) as raised:
        registry.id_for_root(root)

    assert "more than one project id" in str(raised.value)
    assert "api, api-also" in str(raised.value), "both ids are named, in a stable order"
    assert "project unregister" in raised.value.remedy


@pytest.fixture
def ambiguously_registered(machine: Path) -> Path:
    """One root under two ids, written directly.

    `register` refuses to create this, so it can only arrive by hand-editing the
    JSON file in the user's home directory -- which is a thing people do.
    """
    root = _repo(machine, "team-one")
    _in(root, "project", "register")
    registry = ProjectRegistry.default(machine / "datadir")
    entry = registry.load()["api"]
    registry.path.write_text(json.dumps({"api": entry, "api-also": dict(entry)}), encoding="utf-8")
    return root


def test_the_ambiguous_registry_is_reported_by_the_cli_rather_than_crashing(
    ambiguously_registered: Path,
) -> None:
    """`resolve_context` asks the registry which project this directory is, so
    the ambiguity surfaces on every project-scoped command.

    It has to arrive as the `{"error", "remedy"}` contract every other failure
    uses (CP-2), not as a traceback: the user who hand-edited the file is
    exactly the user who can fix it, and they need to be told which ids to
    choose between. `index status` stands in for that whole family here -- it
    reaches the registry through the same `_require_project`.
    """
    code, payload = _in(ambiguously_registered, "index", "status")

    assert code != 0
    assert "more than one project id" in payload["error"]
    assert "project unregister" in payload["remedy"]


def test_project_status_reports_the_ambiguity_instead_of_failing(
    ambiguously_registered: Path,
) -> None:
    """The one command that deliberately does not use the `{error, remedy}` /
    non-zero exit contract.

    `project status` answers for repositories that are *not* registered, so it
    reports every resolution failure as a status at exit 0 rather than raising.
    That is the right call for this command and it is asserted here so the
    difference stays a decision rather than an inconsistency someone later
    "fixes" in whichever direction they meet first.

    The remedy still has to reach the user, though: this is the command a
    confused user reaches for first, and `ProjectError.remedy` is the only place
    the two `project unregister` invocations that resolve the ambiguity are
    named. Fixed defect, formerly recorded here rather than asserted away: the
    remedy now travels into the payload alongside `reason`, at the same exit 0.

    `registered` used to be asserted `False` here, and that was the second face
    of issue #226: this root is in the file *twice*, both entries readable, and
    the command reported it as unregistered because the field was answering
    "did resolution succeed" rather than "does the registry hold this root".
    The ambiguity is over *which id*, never over whether -- `reason` and
    `remedy` are what carry it, and they still do.
    """
    code, payload = _in(ambiguously_registered, "project", "status")

    assert code == 0
    assert payload["registered"] is True, (
        "two entries name this root; `False` would deny a registration the file shows twice"
    )
    assert "more than one project id" in payload["reason"], (
        "a status that said only `registered: true` would hide the ambiguity"
    )
    assert "project unregister" in payload["remedy"], (
        "the two commands that resolve the ambiguity must reach the user who ran "
        "`project status` first, not only the commands that fail loudly"
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
    user is offered.

    The neighbour is registered first and the directory name deliberately
    collides -- ``_repo``'s default, "two teams, one directory name: the
    collision is the point". This test used to stand alone at
    ``name="payments"``, where the fallback id matched no registry key and every
    possible membership rule agreed by accident. With the collision in place
    they separate: the fallback id ``api`` *is* a key, held by ``team-one``,
    while nothing registers ``team-two``'s root. Judging membership by that id
    answered ``True`` for an unregistered repository, disagreeing with
    ``project list``, ``project register`` and ``setup`` about the same file.
    """
    _in(_repo(machine, "team-one"), "project", "register")
    fresh = _repo(machine, "team-two")

    _, status = _in(fresh, "project", "status")

    assert status["projectId"] == "api", "the derived default is still what a user is offered"
    assert Path(status["root"]) == fresh.resolve(), "and it is offered for *this* root"
    assert status["registered"] is False, (
        "nothing names this root; the id it would take is another team's registration"
    )


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


# -- An entry that cannot be read --------------------------------------------
#
# `ProjectRegistry.load` skips a malformed entry rather than refusing the whole
# file, so one hand-edited line no longer stops every project on the machine
# (ADR-0002). The cost of that tolerance is paid in these tests: a skipped entry
# is *absent*, and absent used to read as "this root was never registered" --
# which sent `resolve_context` on to `derive_project_id`, so a project
# registered under a disambiguated id *because its derived default collided* was
# addressed by the id belonging to the project it collided with (SEC-13).
# `ids_for_root` refuses instead, for every root rather than for the entry that
# caused it, because an unreadable entry is exactly one that names no root and
# so cannot be attributed to a directory at all.


def _corrupt_entry(machine: Path, project_id: str) -> None:
    """Leave ``project_id``'s entry in the shape a hand edit leaves behind.

    A JSON object with no ``rootPath``: the file still parses, the id is still
    present, and the one field that could say which directory the entry belongs
    to is the one that is gone. That is what makes per-root judgement
    unavailable rather than merely expensive, and it is the shape every test
    below depends on.
    """
    registry = ProjectRegistry.default(machine / "datadir")
    raw = json.loads(registry.path.read_text(encoding="utf-8"))
    assert project_id in raw, "corrupting an id that was never registered would prove nothing"
    raw[project_id] = {"defaultBranch": "main", "registeredAt": "2026-08-02T10:00:00+00:00"}
    registry.path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")


def _raw_ids(machine: Path) -> list[str]:
    """Every id in the file, readable or not, sorted.

    `load` hides the malformed ones by design, so a test asserting that a
    refused registration wrote *nothing* has to read past it -- otherwise a
    registration that silently landed on top of an unreadable entry would look
    like an absence.
    """
    path = ProjectRegistry.default(machine / "datadir").path
    return sorted(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def corrupted_after_collision(machine: Path) -> tuple[Path, Path]:
    """``team-one/api`` as ``api``; ``team-two/api`` as ``api-team-two``, corrupted.

    The exact shape the regression needs, and it is not an arbitrary one: both
    roots derive the id ``api``, the second holds a disambiguated id *because*
    of that collision, and its entry is then unreadable. Treating that root as
    unregistered therefore means treating it as ``api`` -- which is the other
    repository, and is registered, and works.
    """
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    second = _repo(machine, "team-two")
    _in(second, "project", "register", "--project-id", "api-team-two")

    _corrupt_entry(machine, "api-team-two")
    return first, second


def test_a_corrupt_entry_does_not_hand_a_working_tree_the_id_it_collided_with(
    corrupted_after_collision: tuple[Path, Path],
) -> None:
    """SEC-13. The regression per-entry tolerance introduced, reproduced.

    `id_for_root` answering ``None`` for an entry it merely could not *read* is
    indistinguishable from "this root was never registered", so
    `resolve_context` fell through to `derive_project_id` and every command run
    in ``team-two`` addressed ``api`` -- ``team-one``'s live, readable,
    still-registered project -- with nothing said.

    Asserted on the resolved **id**, not on the fact that something was refused:
    a guard replaced by some other exception thrown for some other reason would
    still satisfy `pytest.raises`, while the id is the thing the defect actually
    got wrong.
    """
    _, second = corrupted_after_collision

    _, payload = _in(second, "project", "status")

    assert payload.get("projectId") != "api", (
        "team-two must never be addressed by the id that belongs to team-one"
    )
    # Not `False`: `api-team-two`'s own entry is the one that is unreadable, so
    # this repository may well be registered -- `resolve_context` simply could
    # not tell, the same impossibility `ids_for_root` refuses on.
    assert payload["registered"] is None
    assert "api-team-two" in payload["reason"], "the unreadable id has to be named to be removable"
    assert "theurian project unregister api-team-two" in payload["remedy"]


def test_an_unreadable_entry_refuses_a_root_it_could_not_possibly_be(machine: Path) -> None:
    """The refusal is deliberately broader than the entry that caused it.

    ``payments``'s entry names no root, so nothing in the file says it is *not*
    a second registration of ``team-one/api``: the field that would settle it is
    the field that is missing. Per-root decidability is unavailable here, not
    merely expensive, so the honest answer for every root is a refusal --
    including for a root whose own entry is present, readable, and matches.

    Pinned because the tempting narrowing is to keep answering for roots that
    already have a readable entry, and that is precisely the reasoning
    `ids_for_root` rejects.
    """
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    _in(_repo(machine, "team-three", name="payments"), "project", "register")
    _corrupt_entry(machine, "payments")
    registry = ProjectRegistry.default(machine / "datadir")

    with pytest.raises(ProjectError) as raised:
        registry.ids_for_root(first)

    assert "payments" in str(raised.value), "the unreadable id is named, not merely counted"
    assert "theurian project unregister payments" in raised.value.remedy


@pytest.mark.parametrize(
    ("form", "extra"),
    [("bare", ()), ("explicit-id", ("--project-id", "payments"))],
    ids=["bare", "explicit-id"],
)
def test_registration_is_refused_while_the_file_holds_an_unreadable_entry(
    corrupted_after_collision: tuple[Path, Path],
    machine: Path,
    form: str,
    extra: tuple[str, ...],
) -> None:
    """H-5. Both forms of ``project register`` inherit the refusal, by different routes.

    The bare form never reaches `register`: `resolve_context` asks the registry
    which project this directory is and refuses there. The ``--project-id`` form
    short-circuits that lookup, so it arrives at `register`, which asks
    `ids_for_root` to enforce "one root, one id" and refuses on the way. Only
    testing the bare form would leave the flag -- the documented escape from a
    collision -- as the one path that could register on top of an entry that may
    already be this very root's, producing the "one root, two ids" duplicate
    that addresses an empty project.

    The id-level assertion is the last one: nothing new appears in the file.
    """
    fresh = _repo(machine, "team-three", name="payments")

    code, payload = _in(fresh, "project", "register", *extra)

    assert code != 0, f"the {form} form must refuse while an entry is unreadable"
    assert "api-team-two" in payload["error"], "the entry that blocks registration is named"
    assert "theurian project unregister api-team-two" in payload["remedy"]
    assert _raw_ids(machine) == ["api", "api-team-two"], "no registration was created"


def test_re_registering_the_id_whose_own_entry_is_broken_names_that_id_not_the_file(
    corrupted_after_collision: tuple[Path, Path],
) -> None:
    """Two refusals apply at once here, and the more precise one has to win.

    Re-registering ``api-team-two`` from its own working tree hits both `register`
    checks: the id already has an entry that cannot be read, *and* the file holds
    an unreadable entry at all. `register` tests its own id first on purpose --
    the general refusal would tell the user to go and find which entries are
    broken, when the answer is the id they just typed, and would omit the part
    that matters: registering now would silently discard whatever that entry
    held, which is how an id gets quietly re-pointed at a different repository
    (SEC-13).

    Asserted by *excluding* the general message as well as matching the specific
    one, because both refusals name ``api-team-two`` and a test that only looked
    for the id would pass whichever fired.
    """
    _, second = corrupted_after_collision

    code, payload = _in(second, "project", "register", "--project-id", "api-team-two")

    assert code != 0
    assert "already has an entry" in payload["error"]
    assert "Cannot say which project" not in payload["error"], (
        "the id's own broken entry is a more precise answer than the file's"
    )
    assert "theurian project unregister api-team-two" in payload["remedy"]
    assert "then register again" in payload["remedy"]


def test_removing_the_unreadable_entry_restores_resolution(
    corrupted_after_collision: tuple[Path, Path],
) -> None:
    """The remedy every surface prints, run verbatim, all the way back to working.

    `unregister` reads the raw file rather than `load`'s validated subset, and
    that is the only reason the entry it has to remove is visible to it: the
    entry `load` skips is exactly the entry the remedy names. Filtering through
    `load` would make `theurian project unregister` report ``removed: false``
    for the id every error message tells the user to remove, leaving the file
    unfixable by any supported command -- a remedy loop with no exit.
    """
    first, second = corrupted_after_collision

    _, removed = _in(second, "project", "unregister", "api-team-two")
    code, _ = _in(second, "project", "register", "--project-id", "api-team-two")
    _, recovered = _in(second, "project", "status")
    _, neighbour = _in(first, "project", "status")

    assert removed["removed"] is True, "the entry the remedy names has to actually go"
    assert code == 0, "registration is possible again once the file holds no unreadable entry"
    assert recovered["projectId"] == "api-team-two"
    assert recovered["registered"] is True
    assert neighbour["projectId"] == "api", "the project that was never broken resolves again too"


# -- What `project status` says while the file cannot be partitioned ---------
#
# `project status` is the command a confused user reaches for first, and it is
# the one place where "unreadable" changes the *answer* rather than only adding
# a note: `registered` becomes `null` for a repository whose own entry is
# perfectly readable, because `ids_for_root` refuses machine-wide. That reads as
# a bug until the reason is stated, so it is pinned rather than left to be
# rediscovered and "fixed".


@pytest.fixture
def broken_neighbour(machine: Path) -> Path:
    """``team-one/api`` registered and readable; an unrelated entry corrupted.

    Deliberately *not* this repository's own entry -- that case already has
    tests. What is untested is the one that looks wrong: the repository asking
    the question is registered, its entry is intact, and the answer is still
    "cannot tell", because the broken entry names no root and so cannot be
    ruled out as a second registration of this one.
    """
    first = _repo(machine, "team-one")
    _in(first, "project", "register")
    _in(_repo(machine, "team-three", name="payments"), "project", "register")
    _corrupt_entry(machine, "payments")
    return first


def test_status_will_not_call_a_readable_repository_registered_while_another_entry_is_broken(
    broken_neighbour: Path,
) -> None:
    """SEC-13. `null` is a third answer, and collapsing it either way is a defect.

    `False` would be the guess `ids_for_root` refuses to make, and it is the one
    that misroutes: `resolve_context` reads "never registered" as licence to fall
    back to the id derived from the directory name, which may already belong to a
    different project.

    `True` is the answer this is now the boundary against, since issue #226
    taught `registered` to answer about the registry rather than about
    resolution -- and a readable entry does name this root. It is still refused,
    because `payments` names *no* root: nothing rules it out as a second
    registration of this same directory, so "is this root registered" is
    incomplete rather than answered, in exactly the way "is this id a key of
    this file" never is. `_RegistryRead.holds_root` orders the unreadable check
    ahead of the match for this case, and this is what holds that order.
    """
    _, payload = _in(broken_neighbour, "project", "status")

    assert payload["registered"] is None
    assert "payments" in payload["reason"], "the entry that blocks the answer has to be named"
    assert "theurian project unregister payments" in payload["remedy"]


def test_status_reports_the_unreadable_ids_by_value(broken_neighbour: Path) -> None:
    """The field is the only machine-readable copy of what has to be removed.

    `reason` and `remedy` are prose for a person. A script -- or the plugin --
    reads `unreadable`, and a test asserting only that the key exists would pass
    against an empty list, which is the value that says nothing is wrong.
    """
    _, payload = _in(broken_neighbour, "project", "status")

    assert payload["unreadable"] == ["payments"]


def test_status_reports_an_empty_unreadable_set_rather_than_omitting_the_field(
    machine: Path,
) -> None:
    """`project list`'s model, on the other answer path.

    The resolved path cannot produce a non-empty list -- `resolve_context` would
    have raised first -- so the key exists here purely so that a consumer never
    has to branch on its presence. A key that appears only on the failure path is
    one a caller eventually forgets to check for, and the check it forgets is the
    one that matters.
    """
    first = _repo(machine, "team-one")
    _in(first, "project", "register")

    _, payload = _in(first, "project", "status")

    assert payload["registered"] is True, "this path must be the resolved one, not the refusal"
    assert payload["unreadable"] == []


def test_outside_a_git_repository_a_broken_entry_does_not_soften_a_certain_answer(
    machine: Path,
) -> None:
    """An unrelated ambiguity must not weaken an answer that is not ambiguous.

    "Not inside a Git repository" has nothing to do with the registry: there is
    no root to attribute an entry to, so no unreadable entry could possibly be
    this directory's registration. `registered` stays `False` -- the honest
    answer -- while `unreadable` still reports what is broken, because the user
    standing here is exactly the one who needs to be told.
    """
    _in(_repo(machine, "team-three", name="payments"), "project", "register")
    _corrupt_entry(machine, "payments")

    _, payload = _in(machine, "project", "status")

    assert "not inside a Git repository" in payload["reason"], "the fixture must be outside one"
    assert payload["registered"] is False
    assert payload["unreadable"] == ["payments"]


def test_one_unreadable_entry_does_not_hide_the_projects_that_are_fine(machine: Path) -> None:
    """ADR-0002: the registry is per-user and one daemon serves many projects.

    `load` used to raise on the first entry that failed validation, so a single
    hand edit made every registration on the machine unreadable at once --
    `theurian project list`, `setup`'s registry scan and every MCP tool failing
    together, on repositories that had done nothing. That repeats, at machine
    scale, the one-bad-row-answers-for-all failure `IndexUnreadableError` exists
    to avoid at the scale of a single project.

    The readable projects are asserted by id rather than by count: a `list` that
    reported two projects but named the wrong ones would satisfy a count.
    """
    _in(_repo(machine, "team-one"), "project", "register")
    _in(_repo(machine, "team-two"), "project", "register", "--project-id", "api-team-two")
    third = _repo(machine, "team-three", name="payments")
    _in(third, "project", "register")
    _corrupt_entry(machine, "api-team-two")

    code, listed = _in(third, "project", "list")

    assert code == 0, "one broken entry must not fail the command that reports it"
    assert [p["projectId"] for p in listed["projects"]] == ["api", "payments"]
    assert listed["unreadable"] == ["api-team-two"]


@pytest.mark.asyncio
async def test_the_daemon_keeps_serving_a_readable_project_beside_a_broken_entry(
    machine: Path,
) -> None:
    """ADR-0002, end to end: every MCP tool resolves its project through `load`.

    So the whole-file refusal did not merely break `project list`: it took the
    daemon down for every project on the machine, including the ones whose
    registrations were untouched. Asserted on the marker string rather than on a
    count, so a search that answered from the wrong corpus would not pass.
    """
    first = _repo(machine, "team-one")
    _knowledge(
        first, item="architecture.alpha", letter="A", title="Alpha policy", marker=ALPHA_MARKER
    )
    _in(first, "project", "register")
    _in(first, "migrate", "apply")
    _in(_repo(machine, "team-two"), "project", "register", "--project-id", "api-team-two")
    _corrupt_entry(machine, "api-team-two")

    result = await _search(machine, "api", "policy")

    assert result["count"] == 1
    assert ALPHA_MARKER in json.dumps(result), (
        "team-one's knowledge is still served while team-two's entry is unreadable"
    )


def test_every_id_in_the_file_is_either_loaded_or_reported_unreadable(machine: Path) -> None:
    """The invariant behind folding the predicate into ``_entry_root_path``.

    `load` and `unreadable_ids` partition the same file, and the two sets have
    to be exact complements in both directions. An id in neither is a project
    that vanished from `theurian project list` with nothing said; an id in both
    would have `project list` reporting a project it also tells the user to
    delete. A second, hand-rolled copy of the "is this entry readable" test is
    how those sets drift apart, so every shape a hand edit can leave behind is
    checked here at once.

    Exact memberships are asserted, not only complementarity: a predicate broken
    so that *nothing* loads still yields two complementary sets. The ids are
    written to the file in deliberately unsorted order, because `unreadable_ids`
    reaches a command the user retypes and JSON-file order would read
    differently on two machines holding the same registry.
    """
    registry = ProjectRegistry.default(machine / "datadir")
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {
        "zebra-no-root-key": {"defaultBranch": "main"},
        "readable": {"rootPath": str(machine / "team-one" / "api")},
        "empty-root": {"rootPath": ""},
        "blank-root": {"rootPath": "   "},
        "numeric-root": {"rootPath": 42},
        "null-root": {"rootPath": None},
        "entry-is-a-string": str(machine),
        "entry-is-a-list": [str(machine)],
        "entry-is-null": None,
        "another-readable": {"rootPath": str(machine / "team-two" / "api")},
    }
    registry.path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = set(registry.load())
    unreadable = registry.unreadable_ids()

    assert loaded == {"readable", "another-readable"}
    assert unreadable == (
        "blank-root",
        "empty-root",
        "entry-is-a-list",
        "entry-is-a-string",
        "entry-is-null",
        "null-root",
        "numeric-root",
        "zebra-no-root-key",
    ), "reported in a stable order, and every shape a hand edit leaves is one of them"
    assert loaded.isdisjoint(unreadable), "no id is both listed and named for deletion"
    assert loaded | set(unreadable) == set(raw), "no id disappears from both surfaces"
