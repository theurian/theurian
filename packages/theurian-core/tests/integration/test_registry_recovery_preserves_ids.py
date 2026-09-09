"""What following the registry cure gets back, field by field, and what it costs.

``tests/integration/test_registry_cure_execution.py`` asks whether a reader who
follows an arm ends up with a working registry. This file asks the narrower
question that round three found unanswered: **which of the things they had come
back, and does the cure name the ones that do not.**

The cure offers a deletion, so it owes the reader an inventory of the loss. At
``b9e8296b`` it named two costs -- every other project's registration, and each
entry's ``registeredAt`` -- and omitted a third that its own recommended
invocation caused: ``theurian project register`` with no ``--project-id`` derives
the id from the *directory name*, so a project registered under any other id came
back addressed differently, and two checkouts whose directories share a name
competed for one id. That is the SEC-13 misrouting
``ProjectRegistry.register`` refuses to perform -- it has nothing to refuse
against once the file is gone -- and the cure delivered it at exit 0 with nothing
said. The text names it now; this module is what holds the naming to the
measurement.

**The completeness check is derived, not hand-listed.** A test that spelled out
the fields it expected to survive would be a second copy of the cure's promise,
and would stay green when ``register`` grew a seventh field that nothing
restores and nothing names. So the field set comes from the entry
:meth:`ProjectRegistry.register` actually wrote -- read back out of the file --
plus the key it is stored under, and every member of it must be either equal
across the recovery or named in the shipped text. A new field that is neither
fails here, and so does a text edit that drops one of the names.

**And the naming is checked against the rendered cure**, not against a
transcription: the names are the claim, so they are read from
``registry_deletion_remedy``'s own bytes. The *steps* are transcribed and
checked for containment, in the same quote-then-execute frame as the execution
module, so a re-pin of the recovery sentence sends a human back to this script.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import RegistryFailureArm, registry_deletion_remedy
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The sentences these tests carry out, transcribed from the shipped cure.
#: Containment is asserted before the steps run, so a re-pin of the recovery
#: breaks here and the script below it gets re-read.
THE_RECOVERY_THESE_TESTS_FOLLOW: Final = (
    "So read out every entry's projectId (the key it sits under) and its rootPath first; "
    "then delete it and re-register each project with `theurian project register`, passing "
    "`--project-id <its projectId>` and running it inside that rootPath."
)

#: The sentence that says what a re-registration without the flag does, and the
#: hazard the last test in this module plants.
THE_HAZARD_THE_CURE_NAMES: Final = (
    "two checkouts whose directories share a name compete for a single id -- whichever "
    "re-registers first takes it"
)

#: A ``registeredAt`` far enough in the past that "re-registering stamps today's
#: date over it" is measurable. Planted rather than produced: two registrations
#: inside one test can land in the same microsecond, and the reader this cure is
#: written for registered their projects on some earlier day. Nothing else about
#: the entries is touched -- the plant is the pre-state, and what is under test is
#: what the *re-registration* writes over it.
A_REGISTRATION_FROM_ANOTHER_DAY: Final = "2020-01-02T03:04:05+00:00"

#: A knowledge directory no CLI invocation produces. ``project register`` writes
#: ``DEFAULT_KNOWLEDGE_DIRECTORY`` unconditionally, so a pre-state holding
#: anything else has to be planted for the cure's "knowledgeDirectory comes back
#: as the default" to be falsifiable at all.
A_KNOWLEDGE_DIRECTORY_THE_DEFAULT_IS_NOT: Final = ".knowledge-custom"


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command in-process and parse its JSON.

    In-process on purpose: nothing here may run a real ``setup``, register a
    service, or start a daemon. ``init`` and ``project register`` are the only
    commands these tests need, and they write inside ``tmp_path`` and
    ``THEURIAN_DATA_DIR`` and nowhere else.
    """
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


@dataclass(frozen=True)
class _ACheckout:
    """One registered Git working tree, and the id its registration landed under."""

    root: Path
    project_id: str


@dataclass(frozen=True)
class _TwoRegisteredProjects:
    """Two real registrations in one registry, which is what "each project" means.

    One registration cannot exercise a cure whose cost sentence is about *every*
    project, and cannot exercise an id at all: with one entry, an id re-derived
    from the directory name is indistinguishable from the id that was there.
    """

    checkouts: tuple[_ACheckout, ...]
    registry: Path


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def _a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, directory: str, branch: str
) -> Path:
    """A Git working tree with a branch and an ``origin`` remote of its own."""
    root = tmp_path / directory
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", branch)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", f"https://example.invalid/{directory}.git")
    monkeypatch.chdir(root)
    assert _invoke("init")[0] == 0, f"the fixture must produce a project at {root}"
    return root


@pytest.fixture
def two_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_TwoRegisteredProjects]:
    """Two registered projects, one of them under an id nothing would re-derive.

    ``gamma-prod`` lives in a directory named ``gamma``, which is the whole point:
    it is the case a bare re-registration cannot reproduce, and the case round
    three demonstrated coming back as ``gamma``. Its branch is ``develop`` and it
    has an ``origin`` remote, so ``defaultBranch`` and ``repositoryUrl`` hold
    values that are this tree's rather than a default's.
    """
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))

    alpha = _a_checkout(tmp_path, monkeypatch, directory="alpha", branch="main")
    code, registered_alpha = _invoke("project", "register")
    assert code == 0, "the fixture must produce a registration to lose"

    gamma = _a_checkout(tmp_path, monkeypatch, directory="gamma", branch="develop")
    code, registered_gamma = _invoke("project", "register", "--project-id", "gamma-prod")
    assert code == 0, "and a second one, under an id the directory name does not derive"
    assert registered_gamma["projectId"] != gamma.name, (
        "the non-default id is what makes this fixture worth having: an id equal to the "
        "directory name is restored by a bare re-registration too"
    )

    yield _TwoRegisteredProjects(
        checkouts=(
            _ACheckout(root=alpha, project_id=registered_alpha["projectId"]),
            _ACheckout(root=gamma, project_id=registered_gamma["projectId"]),
        ),
        registry=data_dir / "projects.json",
    )


def _the_shipped_cure(registry: Path) -> str:
    """The cure as a reader receives it, rendered by production over this path.

    Any arm: the cost sentence and the recovery sentence are shared by all four,
    and they are what this module measures.
    """
    return registry_deletion_remedy(registry, RegistryFailureArm.UNPARSABLE)


def _read_the_registry_out(registry: Path) -> dict[str, dict[str, Any]]:
    """The cure's first instruction: read every entry's projectId and rootPath.

    Returned whole rather than as the two fields the sentence names, because what
    the tests below compare is every field an entry has -- including the ones the
    reader is *not* told to carry across, which is the omission being measured.
    """
    entries: dict[str, dict[str, Any]] = json.loads(registry.read_text(encoding="utf-8"))
    return entries


def _the_fields_a_registration_has(entries: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """Every field of a registry entry, derived from what ``register`` wrote.

    The derivation, stated so it can be attacked: the entry
    :meth:`ProjectRegistry.register` builds is the value in this file, and the id
    is the key it is filed under. Reading both back out of the file makes the
    field set whatever production writes today -- a seventh field added to that
    dict arrives here without anyone editing this test, which is the point. A
    hand-written tuple would silently stop covering it.
    """
    written = {frozenset(entry) for entry in entries.values()}
    assert len(written) == 1, (
        f"every entry must have the same fields, or 'the field set' is not one thing: {written!r}"
    )
    return frozenset({"projectId"}) | next(iter(written))


def _as_fields(project_id: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """One entry as the reader sees it: its fields, with the key among them."""
    return {"projectId": project_id, **entry}


def _plant_a_pre_state(registry: Path, **fields: str) -> None:
    """Overwrite one or more fields of every entry, leaving the rest as written."""
    entries = _read_the_registry_out(registry)
    planted = {pid: {**entry, **fields} for pid, entry in entries.items()}
    registry.write_text(json.dumps(planted, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _follow_the_cure(
    projects: _TwoRegisteredProjects, monkeypatch: pytest.MonkeyPatch, *, with_the_id: bool = True
) -> dict[str, dict[str, Any]]:
    """Read the ids and roots out, delete, re-register each -- and report the result.

    Exactly the recovery sentence: the ids and roots come out of the file first,
    the file goes, and each project is re-registered *inside its rootPath*. Only
    the ``--project-id`` half is optional, because the omission is what one of
    the tests below is about.
    """
    before = _read_the_registry_out(projects.registry)
    carried_across = {pid: Path(entry["rootPath"]) for pid, entry in before.items()}

    projects.registry.unlink()

    for project_id, root in sorted(carried_across.items()):
        monkeypatch.chdir(root)
        arguments = ["project", "register"]
        if with_the_id:
            arguments += ["--project-id", project_id]
        code, payload = _invoke(*arguments)
        assert code == 0, f"re-registering {project_id} inside {root} must succeed: {payload!r}"

    return _read_the_registry_out(projects.registry)


def test_following_the_cure_restores_every_field_it_does_not_name_as_a_cost(
    two_projects: _TwoRegisteredProjects, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cost sentence is complete: what changes is what the cure says changes.

    This is the closure argument for round three's HIGH, expressed as a
    measurement rather than as a reading. A cure that offers a deletion is
    honest only if every field it does not preserve is a field it names, so the
    check walks the *derived* field set and demands, of each one, either equality
    across the recovery or its own name in the shipped text.

    Both halves have teeth. A field that stops being restored -- a future
    ``register`` that no longer honours ``--project-id``, or a recovery sentence
    that stops saying "running it inside that rootPath" -- moves out of the
    equality set, and the exact-set assertion below refuses it even though its
    name is in the text. A field ``register`` starts writing that nothing
    restores and nothing names fails the per-field check. And a text edit that
    drops ``registeredAt`` from the cost sentence fails the per-field check too,
    since that is the one field this recovery genuinely loses.

    ``registeredAt`` is planted into the past first; see the constant for why.
    """
    cure = _the_shipped_cure(two_projects.registry)
    assert THE_RECOVERY_THESE_TESTS_FOLLOW in cure, (
        "this script carries out a recovery sentence the cure no longer contains; re-read the "
        "new one and re-write the steps below to match it"
    )
    _plant_a_pre_state(two_projects.registry, registeredAt=A_REGISTRATION_FROM_ANOTHER_DAY)
    before = _read_the_registry_out(two_projects.registry)
    fields = _the_fields_a_registration_has(before)

    after = _follow_the_cure(two_projects, monkeypatch)

    assert sorted(after) == sorted(before), (
        f"the recovery has to bring back the same set of ids, or the comparison below is "
        f"between different projects: {sorted(before)!r} -> {sorted(after)!r}"
    )
    changed: set[str] = set()
    for project_id in sorted(before):
        was = _as_fields(project_id, before[project_id])
        now = _as_fields(project_id, after[project_id])
        for field in sorted(fields):
            assert was[field] == now[field] or field in cure, (
                f"following the cure changed {project_id}'s {field} from {was[field]!r} to "
                f"{now[field]!r}, and the cure never names it -- a deletion is offered with "
                f"an inventory of its losses, so a field that does not survive the recovery "
                f"has to be one the reader was told about: {cure!r}"
            )
            if was[field] != now[field]:
                changed.add(field)

    assert changed == {"registeredAt"}, (
        f"with the trees unmoved, the restamped registeredAt is the only loss this recovery "
        f"has -- everything else is either carried across by the reader or re-read to the "
        f"same value. A field arriving in this set is a new cost the cure has to name, and a "
        f"field leaving it means the text names a cost that is not one: {sorted(changed)!r}"
    )


def test_the_fields_the_cure_declines_to_restore_come_back_from_the_tree_as_it_stands(
    two_projects: _TwoRegisteredProjects, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "not restored from what you deleted" is a real loss, not a hedge.

    The previous test measures the recovery with nothing else moving, where
    ``repositoryUrl`` and ``defaultBranch`` come back to the same values and the
    claim that they are *not restored* is invisible. It is visible the moment the
    tree moves, which is the reader's actual situation whenever the registry has
    been broken for a while: the remote is gone, the checkout is on another
    branch, and re-registration reads the tree rather than the record.

    So this drives what the text says happens -- ``repositoryUrl`` and
    ``defaultBranch`` re-read from Git, ``knowledgeDirectory`` rewritten as the
    default -- and demands that each of the three is *named* in the cure. Without
    it, "the other three fields are not restored" could be deleted from the text
    and every other check here would still pass.
    """
    cure = _the_shipped_cure(two_projects.registry)
    _plant_a_pre_state(
        two_projects.registry,
        knowledgeDirectory=A_KNOWLEDGE_DIRECTORY_THE_DEFAULT_IS_NOT,
    )
    before = _read_the_registry_out(two_projects.registry)

    for checkout in two_projects.checkouts:
        _git(checkout.root, "remote", "remove", "origin")
        _git(checkout.root, "checkout", "-q", "-b", "hotfix")
    after = _follow_the_cure(two_projects, monkeypatch)

    for checkout in two_projects.checkouts:
        was = before[checkout.project_id]
        now = after[checkout.project_id]
        assert was["repositoryUrl"] and now["repositoryUrl"] == "", (
            f"the remote is gone from the tree, so the url the record held is not restored: "
            f"{was['repositoryUrl']!r} -> {now['repositoryUrl']!r}"
        )
        assert now["defaultBranch"] == "hotfix" != was["defaultBranch"], (
            f"and the branch is whatever the checkout is on now: {was['defaultBranch']!r} -> "
            f"{now['defaultBranch']!r}"
        )
        assert now["knowledgeDirectory"] != A_KNOWLEDGE_DIRECTORY_THE_DEFAULT_IS_NOT, (
            f"and the knowledge directory comes back as the default rather than as the "
            f"planted value: {now['knowledgeDirectory']!r}"
        )

    for field in ("repositoryUrl", "defaultBranch", "knowledgeDirectory"):
        assert field in cure, (
            f"{field} does not survive the recovery, so the cure that offers the deletion has "
            f"to name it: {cure!r}"
        )


def test_re_registering_without_the_id_the_cure_names_takes_it_from_another_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why the recovery names ``--project-id``: without it an id changes repository.

    The hazard, planted and demonstrated. Two checkouts of two different
    repositories whose directories are both named ``api`` can both be registered
    -- the second under an id of its own, which is what
    :meth:`ProjectRegistry.register` refuses a collision *in order to* force. Once
    the registry is deleted that refusal has nothing to compare against, so the
    first bare re-registration takes the plain ``api`` id whatever it belonged to
    before, and the second is refused outright. An agent configured to ask for
    ``api`` is then served the other team's repository, with nothing in the
    answer saying so: the SEC-13 misrouting, delivered by a remedy at exit 0.

    ``register`` itself is in contract throughout -- it refuses every collision it
    can see. What it cannot see is a registry that no longer exists, which is why
    this is a cost of the *deletion* and belongs in the cure's inventory rather
    than in a production fix.

    The contrast is the closing half: the same deletion, recovered the way the
    text now prescribes, puts both ids back on their own roots.
    """
    cure = _the_shipped_cure(tmp_path / "datadir" / "projects.json")
    assert THE_HAZARD_THE_CURE_NAMES in cure, (
        "the reader is only warned if the text says this happens; if this sentence has been "
        f"rewritten, read the new one against what this test measures: {cure!r}"
    )

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    registry = data_dir / "projects.json"
    one = _a_checkout(tmp_path / "one", monkeypatch, directory="api", branch="main")
    assert _invoke("project", "register")[0] == 0
    two = _a_checkout(tmp_path / "two", monkeypatch, directory="api", branch="main")
    assert _invoke("project", "register", "--project-id", "api-two")[0] == 0

    before = _read_the_registry_out(registry)
    assert {pid: Path(entry["rootPath"]) for pid, entry in before.items()} == {
        "api": one,
        "api-two": two,
    }, "the fixture must start with each id on its own checkout"

    registry.unlink()
    monkeypatch.chdir(two)
    taken, _ = _invoke("project", "register")
    monkeypatch.chdir(one)
    refused, _ = _invoke("project", "register")

    assert taken == 0, "the invocation the reader is warned about succeeds, which is the point"
    assert Path(_read_the_registry_out(registry)["api"]["rootPath"]) == two, (
        "and the id `api` now names the other checkout -- an agent asking for `api` is served "
        "a repository it was never registered against"
    )
    assert refused == 1, (
        "and the checkout that held `api` cannot get it back: the cure has left the reader "
        "with one project re-pointed and one unregisterable"
    )

    registry.unlink()
    for project_id, root in sorted({"api": one, "api-two": two}.items()):
        monkeypatch.chdir(root)
        assert _invoke("project", "register", "--project-id", project_id)[0] == 0

    assert {
        pid: Path(entry["rootPath"]) for pid, entry in _read_the_registry_out(registry).items()
    } == {"api": one, "api-two": two}, (
        "while the recovery the cure now prescribes puts both ids back where they were, which "
        "is what makes `--project-id <its projectId>` the difference rather than advice"
    )
