"""Every command an MCP refusal publishes as its cure is run, where it was published.

A refusal that names a command makes a promise: run this, and you will not be
here again. Before this file nothing in the suite ran one -- every other
assertion about these refusals reads their *text*, and text is exactly what a
closed loop satisfies.

The loop was real. ``FINDINGS_UNAVAILABLE_REFUSAL`` folded the escaping-store-leaf
arm in and told the caller to run ``theurian findings build``, which resolves that
same leaf through that same helper before it reads a byte of git history and exits
4 on it. The caller was handed back the refusal that sent them (#483 round one
H-1; overturned at ``c7da702e``, which is the commit this file's ratchet is the
closure instrument for). Every assertion the suite had on that constant stayed
green throughout, because not one of them ran the command it names::

    git grep -cE '^ +assert FINDINGS_UNAVAILABLE_REFUSAL ' -- packages/theurian-core/tests

answered 10 on 2026-09-12, across two files.

So this file does the only thing that can catch the next one:

1. drive the arm through the built MCP server, in a real corpus;
2. read the commands out of **the refusal the server just published**, never out
   of a list written here;
3. run them, through the real CLI, in that same corpus;
4. call the tool again and require the answer to have moved.

**A cure is executable when the answer moves, not when the commands exit 0.** A
cure whose steps all succeed and leaves the caller on the identical refusal has
not cured anything; a cure one of whose alternatives fails by design -- ``rm
.theurian/state`` where ``state`` is a real directory -- has still cured it if
the caller is somewhere else afterwards. So the ratchet's assertion is on the
answer, and the exit codes travel in the failure message as evidence.

**The population is derived, not listed.** :func:`_cure_publishing_constants`
reads every string ``theurian.mcp.tools`` can publish and keeps the ones that
name a command; :func:`test_every_cure_this_surface_publishes_is_ratcheted_or_recorded`
requires each to be claimed by an entry in :data:`ARMS`. A new unavailability
constant therefore fails this file until somebody either ratchets it or records,
in writing, why it is not ratcheted.

**Four of the eight arms are recorded rather than ratcheted, and each carries the
measured reason** -- this file's stated coverage gaps, in writing rather than in
silence. One of them is an open defect: ``review.search``'s escaping-store-leaf
arm publishes a cure that exits 1 on the plant that produced it and leaves the
caller on a byte-identical refusal, which is the very shape ``c7da702e``
overturned at the tool next door. It is measured by
:func:`test_the_recorded_closed_loop_is_still_closed`, which goes RED the day it
is fixed.

**Nothing here touches the developer's machine.** ``HOME`` and
``THEURIAN_DATA_DIR`` are redirected into ``tmp_path`` before the first CLI call;
every CLI invocation names its working directory in the same expression that
makes it (:func:`_cli`), because ``theurian init`` writes ``.theurian/`` into
``Path.cwd()`` and takes no argument that says where. The only commands run are
the ones a refusal published, and :func:`_run_one` refuses to run anything but
``theurian`` and ``rm`` -- and refuses an ``rm`` operand that is absolute or
climbs out of the corpus, so a reworded remedy cannot turn this file into a
delete of something it was never pointed at. No ``setup``, no ``uninstall``, no
daemon.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping
from contextlib import chdir
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    REVIEW_SEARCH_STORE_ID,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import ReviewEvent, ReviewParticipant
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.mcp import tools as tools_module
from theurian.mcp.tools import (
    FINDINGS_UNAVAILABLE_REFUSAL,
    PATH_ESCAPE_REFUSAL,
    REVIEW_SEARCH_UNAVAILABLE_REFUSAL,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT_ID: Final = "demo"

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

#: The one commit ``findings build`` has to find something in. The trailer's
#: reviewer token is one of the three the allowlist takes, so the build lands a
#: row rather than rejecting it -- a store built with nothing in it would still
#: serve, but "the cure produced a servable store" is a weaker statement than
#: "the cure produced the store the caller asked for".
FINDING_TRAILER: Final = (
    "feat(demo): a commit that carries a review finding\n\n"
    "Review-Finding: adversarial HIGH — the published cure exits 4 on the same fault\n"
)

PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"
RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))


# -- Reading a cure out of a refusal -----------------------------------------

#: A published command is a backtick span. Theurian writes every one of them that
#: way -- ``theurian migrate apply``, ``rm -rf .theurian/state`` -- and the
#: alternative, matching the word ``theurian`` in prose, would pick up the product
#: name out of every sentence that mentions it.
_BACKTICKED: Final = re.compile(r"`([^`]+)`")

#: The two programs this file will run. Everything else a cure might quote --
#: ``.theurian/state`` on its own, a file name, a URL -- is not a command, and a
#: cure that started naming a third program has to be classified here rather than
#: executed silently.
_RUNNABLE: Final = frozenset({"theurian", "rm"})


def published_commands(message: str) -> tuple[tuple[str, ...], ...]:
    """Every runnable command ``message`` publishes, in the order it publishes them.

    Order is kept and duplicates are kept, because a cure is a sequence: the
    escape remedy names ``rm`` before ``theurian migrate apply`` and the reverse
    order cures nothing.

    **A span has to name what to act on, not merely which program.** The escape
    remedy's trailing-slash warning quotes a bare ``rm -rf`` -- a *mention* of the
    program, in a sentence about what it does to a link, and not an instruction
    to run it. Keying on the first word alone put that span in the cure and made
    this file try to run an ``rm`` with nothing to remove; requiring one non-flag
    word past the program name is what tells a use from a mention, and it costs
    nothing real, because every instruction Theurian publishes has a subcommand
    or an operand.

    Answering ``()`` is a real answer and the ratchet asserts against it -- a
    refusal naming no command at all cannot be run, and would otherwise satisfy
    "the cure was executed" vacuously.
    """
    found: list[tuple[str, ...]] = []
    for span in _BACKTICKED.findall(message):
        try:
            argv = tuple(shlex.split(span))
        except ValueError:  # pragma: no cover - an unbalanced quote in a remedy
            continue
        operands = [word for word in argv[1:] if not word.startswith("-")]
        if argv and argv[0] in _RUNNABLE and operands:
            found.append(argv)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Step:
    """One published command, and what running it reported."""

    command: str
    exit_code: int


def _run_one(root: Path, argv: tuple[str, ...]) -> Step:
    """Run one published command inside ``root``, and answer what it exited with.

    The working directory is named in the same expression that runs the command
    rather than inherited from an earlier one: ``theurian init`` resolves its
    project from ``Path.cwd()`` and takes no argument that says where, so a
    cure-runner that leant on a fixture's ``chdir`` would initialise Theurian
    into whatever directory the previous test left behind.
    """
    assert argv[0] in _RUNNABLE, f"this file runs published commands only: {shlex.join(argv)}"
    if argv[0] == "rm":
        operands = [word for word in argv[1:] if not word.startswith("-")]
        assert operands, f"an `rm` with nothing to remove: {shlex.join(argv)}"
        for operand in operands:
            path = Path(operand)
            assert not path.is_absolute() and ".." not in path.parts, (
                f"a published cure asked this test to remove something outside the corpus "
                f"it was published in, which it will not do: {shlex.join(argv)}"
            )
        command = list(argv)
        completed = subprocess.run(  # noqa: S603
            command, cwd=root, capture_output=True, text=True, check=False
        )
        return Step(shlex.join(argv), completed.returncode)
    with chdir(root):
        result = runner.invoke(app, list(argv[1:]))
    return Step(shlex.join(argv), result.exit_code)


async def _run_the_cure(root: Path, commands: tuple[tuple[str, ...], ...]) -> tuple[Step, ...]:
    """Run the whole cure, in order, off the test's own event loop.

    ``asyncio.to_thread`` and not a direct call: ``theurian index build`` -- which
    the escape remedy names -- reaches
    :class:`~theurian.application.index_builder.IndexBuilder` code that calls
    ``asyncio.run(self._embedder.embed(...))``, and ``asyncio.run`` refuses to
    start inside a loop that is already running. Called from the body of an
    ``async def`` test it left a ``HashingEmbedding.embed`` coroutine un-awaited,
    which ``filterwarnings = error`` turns into a failure that has nothing to do
    with the cure. The thread gives the CLI the loop-free context it would have
    on a terminal.
    """
    return await asyncio.to_thread(lambda: tuple(_run_one(root, argv) for argv in commands))


# -- The corpus ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Corpus:
    """A registered, migrated project and the registry that knows about it."""

    root: Path
    registry: ProjectRegistry


def _git(root: Path, *args: str) -> None:
    command = ["git", *args]
    subprocess.run(command, cwd=root, check=True, capture_output=True)  # noqa: S603


def _cli(root: Path, *args: str) -> None:
    """Run one setup command for the corpus, and require it to succeed."""
    with chdir(root):
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, (
        f"building the corpus failed at `{' '.join(args)}`: {result.stdout}"
    )


def _evidence() -> tuple[EvidenceRecord, ...]:
    """One landed pull request, written through the real evidence writer.

    Enough for ``theurian review build`` to have something to project. The fields
    are the ones the record type requires; none of them is what this file
    measures, which is why there is one record here and not the three the review
    search tool's own suite lands.
    """
    anchor = SourceAnchor(
        provider=PROVIDER,
        source_uri=f"https://github.com/{REPOSITORY}/pull/42",
        repository=REPOSITORY,
        commit_sha="a" * 40,
        file_path="src/pay/retry_budget.py",
        line_start=10,
        line_end=12,
        external_id="PRRT_kwDOABCD",
    )
    return (
        EvidenceRecord(
            provider=PROVIDER,
            repository=REPOSITORY,
            anchor=anchor,
            payload=ReviewEvent(
                project_id=ProjectId(PROJECT_ID),
                provider=PROVIDER,
                repository=REPOSITORY,
                number=42,
                title="Bound the retry budget",
                body="署名付きトークンを持つ呼び出しだけを再試行する。",
                author=ReviewParticipant(
                    provider=PROVIDER, external_id="USER_A", display_name="Reviewer One"
                ),
                created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                url=f"https://github.com/{REPOSITORY}/pull/42",
                head_commit="b" * 40,
                base_commit="c" * 40,
                head_ref_name="fix/retry-budget",
                labels=("security",),
                merged=True,
                merge_commit="d" * 40,
                merged_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
                ci_successful=True,
                milestone=None,
            ),
        ),
    )


def _build_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, builds: tuple[str, ...]
) -> Corpus:
    """A real project with everything a cure could need already on disk.

    ``HOME`` and ``THEURIAN_DATA_DIR`` move into ``tmp_path`` before the first
    CLI call, through ``monkeypatch`` so nothing survives the test. The git
    history and the evidence files are here for every arm, not only the ones
    whose corpus builds a store from them: a cure that names ``theurian findings
    build`` has to be able to succeed, or "the answer did not move" would be a
    statement about the fixture.
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    root = tmp_path / PROJECT_ID
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")

    _cli(root, "init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY, encoding="utf-8")
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(
        MIGRATION, encoding="utf-8"
    )
    _cli(root, "project", "register")
    _cli(root, "migrate", "apply")

    # `findings build` reads `refs/remotes/origin/main` and nothing else, so the
    # ref is made rather than assumed -- a corpus without it makes the findings
    # cure exit non-zero for a reason that has nothing to do with the arm.
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", FINDING_TRAILER)
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")

    ReviewEvidenceStore(ProjectPaths.of(root).review).write(_evidence(), run=RUN)

    for store in builds:
        _cli(root, *{"findings": ("findings", "build"), "review": ("review", "build")}[store])

    return Corpus(root=root, registry=ProjectRegistry.default(tmp_path / "datadir"))


# -- The plants ---------------------------------------------------------------


def _escape(corpus: Corpus, path: Path, outside: Path) -> None:
    """Move ``path`` out of the working tree and leave a link where it was.

    What a clone hands a victim: the artefact force-added past ADR-0004's ignore
    as a link that resolves outside the tree it was delivered in. The escape is
    checked rather than assumed -- a plant that still resolved inside the root
    would drive no containment refusal at all, and every assertion over it would
    be about something else.
    """
    assert path.exists(), f"the premise: {path.name} must be there to be replaced by a link"
    target = outside / path.name
    path.rename(target)
    path.symlink_to(target)
    assert not path.resolve().is_relative_to(corpus.root.resolve()), (
        f"the plant must resolve genuinely outside the project root: {path}"
    )


def _plant_nothing(corpus: Corpus, outside: Path) -> None:
    """The arms whose refusal is the corpus's own state, not a doctored artefact."""


def _plant_findings_leaf(corpus: Corpus, outside: Path) -> None:
    _escape(corpus, ProjectPaths.of(corpus.root).findings_for(FINDINGS_STORE_ID), outside)


def _plant_review_search_leaf(corpus: Corpus, outside: Path) -> None:
    _escape(corpus, ProjectPaths.of(corpus.root).review_search_for(REVIEW_SEARCH_STORE_ID), outside)


def _plant_state_directory(corpus: Corpus, outside: Path) -> None:
    _escape(corpus, ProjectPaths.of(corpus.root).state, outside)


# -- The arms -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Arm:
    """One refusal that publishes a command, and everything measured about it.

    ``publishes`` and ``serves_after_the_cure`` are measurements written out by
    hand and compared against what the server really said. Reading them off the
    response instead would make every assertion below a question asked of
    production and answered by production: a constant reworded to name no command
    at all would move the expectation with it and stay green.

    ``outside_the_ratchet_because`` is the other way this table could look like
    coverage and be none. An arm carrying it is **not** ratcheted -- the cure is
    not run for it -- and the field holds the measured reason, so a gap here is a
    recorded one. :func:`test_every_cure_this_surface_publishes_is_ratcheted_or_recorded`
    holds the partition exact in both directions.
    """

    name: str
    tool: str
    arguments: Mapping[str, Any]
    #: Which derived stores the corpus builds before the plant.
    builds: tuple[str, ...]
    plant: Callable[[Corpus, Path], None]
    #: The constant the refusal must carry, so an arm that stopped firing --
    #: or that started answering a different arm's constant -- fails rather
    #: than measuring whatever refusal it happened to produce. Empty on a
    #: recorded arm this file never calls, where a value would be a claim
    #: nothing here checks.
    refusal: str
    #: Where the cure's text comes from. ``tools.<NAME>`` is a constant this
    #: surface publishes and is the key the population guard ranges over;
    #: anything else is a remedy carried on the exception.
    cure_source: str
    #: Exactly the commands the refusal publishes, in order.
    publishes: tuple[str, ...]
    #: Whether the tool answers rather than refuses once the cure has run.
    serves_after_the_cure: bool = True
    outside_the_ratchet_because: str = ""

    @property
    def is_ratcheted(self) -> bool:
        return not self.outside_the_ratchet_because


#: What the escape remedy publishes for anything under ``.theurian/state``. Two
#: mutually exclusive ``rm`` spellings and four rebuilds -- written out here
#: rather than recomputed from ``derived_escape_remedy``, for ``Arm.publishes``'
#: reason.
_STATE_ESCAPE_CURE: Final = (
    "rm .theurian/state",
    "rm -rf .theurian/state",
    "theurian migrate apply",
    "theurian index build",
    "theurian findings build",
    "theurian review build",
)

ARMS: Final = (
    Arm(
        name="the findings store's leaf escapes the project",
        tool="review.findings",
        arguments={},
        builds=("findings",),
        plant=_plant_findings_leaf,
        refusal=PATH_ESCAPE_REFUSAL,
        cure_source="project_service.derived_escape_remedy",
        publishes=_STATE_ESCAPE_CURE,
    ),
    Arm(
        name="no findings store has been built",
        tool="review.findings",
        arguments={},
        builds=(),
        plant=_plant_nothing,
        refusal=FINDINGS_UNAVAILABLE_REFUSAL,
        cure_source="tools.FINDINGS_UNAVAILABLE_REFUSAL",
        publishes=("theurian findings build",),
    ),
    Arm(
        name="no review search store has been built",
        tool="review.search",
        arguments={},
        builds=(),
        plant=_plant_nothing,
        refusal=REVIEW_SEARCH_UNAVAILABLE_REFUSAL,
        cure_source="tools.REVIEW_SEARCH_UNAVAILABLE_REFUSAL",
        publishes=("theurian review build",),
    ),
    Arm(
        name="the .theurian/state directory escapes the project",
        tool="review.findings",
        arguments={},
        builds=("findings",),
        plant=_plant_state_directory,
        refusal=PATH_ESCAPE_REFUSAL,
        cure_source="project_service.derived_escape_remedy",
        publishes=_STATE_ESCAPE_CURE,
    ),
    Arm(
        name="the review search store's leaf escapes the project",
        tool="review.search",
        arguments={},
        builds=("review",),
        plant=_plant_review_search_leaf,
        refusal=REVIEW_SEARCH_UNAVAILABLE_REFUSAL,
        cure_source="tools.REVIEW_SEARCH_UNAVAILABLE_REFUSAL",
        publishes=("theurian review build",),
        serves_after_the_cure=False,
        outside_the_ratchet_because=(
            "its cure is a closed loop, measured on this branch and open. "
            "`review_search_for`'s store-id containment raises a plain `ProjectError` -- "
            "not the `ProjectPathEscapeError` the leaf beside it raises -- so "
            "`review.search`'s base arm folds it into the availability constant, whose "
            "cure is `theurian review build`; that command resolves this same leaf "
            "through this same helper before it reads an evidence file and exits 1 on "
            "it, and the refusal after the cure is byte-identical to the one before. It "
            "is the shape `c7da702e` overturned at the findings twin, at the one arm "
            "that commit's type split deliberately left folded. The CLI publishes the "
            "step the fold drops -- `REVIEW_SEARCH_STORE_REMEDY` says to remove the "
            "file first -- so the cure exists and this surface does not print it. "
            "`test_the_recorded_closed_loop_is_still_closed` measures all of it and "
            "goes RED the day it is fixed, which is when this entry moves into the "
            "ratchet."
        ),
    ),
    Arm(
        name="the knowledge directory escapes the project",
        tool="review.findings",
        arguments={},
        builds=(),
        plant=_plant_nothing,
        refusal=PATH_ESCAPE_REFUSAL,
        cure_source="project_service.KNOWLEDGE_DIR_ESCAPE_REMEDY",
        publishes=("theurian init",),
        outside_the_ratchet_because=(
            "the decisive step of its cure is published as prose rather than as a "
            "command: `KNOWLEDGE_DIR_ESCAPE_REMEDY` says to remove the link in words "
            "and backticks only `theurian init`, which meets the same refusal while "
            "the link is still there. Running what this file can read would therefore "
            "report a closed loop for a cure that is not one, so the arm is measured "
            "by its text instead -- "
            "`test_the_knowledge_directory_cure_names_its_decisive_step_in_prose` -- "
            "and the acting-out of it belongs to "
            "`test_escaping_knowledge_dir_grading.py`."
        ),
    ),
    Arm(
        name="the active state pointer names a database outside .theurian/state/",
        tool="knowledge.status",
        arguments={},
        builds=(),
        plant=_plant_nothing,
        refusal="",
        cure_source="tools.ACTIVE_POINTER_REMEDY",
        publishes=("theurian migrate apply",),
        outside_the_ratchet_because=(
            "this file builds no corpus that reaches it. The arm needs an `active.json` "
            "whose `databaseFilename` escapes the state directory, which is a write to "
            "a derived pointer rather than a plant on a path, and the tool that "
            "publishes the remedy is `knowledge.status` rather than either of the two "
            "this file drives. What holds it today is an assertion on the remedy's "
            "*text* -- `test_mcp_tools.py"
            "::test_a_negative_migration_count_is_refused_by_every_read_tool` -- which "
            "is the weaker check this file exists to replace. A recorded gap, not "
            "coverage."
        ),
    ),
    Arm(
        name="the derived state disagrees with its own records",
        tool="knowledge.get",
        arguments={},
        builds=(),
        plant=_plant_nothing,
        refusal="",
        cure_source="tools.INTEGRITY_REMEDY",
        publishes=("theurian migrate apply", "theurian migrate apply", "theurian index build"),
        outside_the_ratchet_because=(
            "this file builds no corpus that reaches it either: the arm needs a "
            "canonical store damaged after it was built, and the cure's own claim is "
            "about recovering rows rather than about becoming servable, so 'the answer "
            "moved' is not the property to assert over it. Held today by assertions on "
            "the remedy's text -- `test_mcp_tools.py"
            "::test_an_absent_item_over_a_damaged_state_is_refused_as_damage_not_absence` "
            "and its neighbours. A recorded gap, not coverage."
        ),
    ),
)

RATCHETED: Final = tuple(arm for arm in ARMS if arm.is_ratcheted)
RECORDED: Final = tuple(arm for arm in ARMS if not arm.is_ratcheted)
ARM_BY_NAME: Final = {arm.name: arm for arm in ARMS}

#: Where a recorded arm's cited test has to really live, so a citation is checked
#: rather than believed.
_TESTS_ROOT: Final = Path(__file__).parent.parent


# -- Asking the tool ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Answer:
    """What one tool call answered, reduced to what "the answer moved" means.

    A served call is one answer and every refusal is another, compared by its
    message. Comparing the served *payload* would make the ratchet notice a
    changed row count, which is not what it is about.
    """

    served: bool
    message: str


_SERVED: Final = "<the tool answered>"


async def _ask(corpus: Corpus, arm: Arm) -> Answer:
    try:
        await build_server(corpus.registry).call_tool(
            arm.tool, {"projectId": PROJECT_ID, **arm.arguments}
        )
    except SdkToolError as exc:
        return Answer(served=False, message=str(exc))
    return Answer(served=True, message=_SERVED)


# -- The population is derived, not listed ------------------------------------


def _cure_publishing_constants() -> dict[str, tuple[str, ...]]:
    """Every string ``theurian.mcp.tools`` can publish that names a command.

    Read off the module's own namespace rather than listed here, so a refusal
    constant added in a later milestone joins this population by existing and
    fails the partition below until it is ratcheted or recorded. Imported names
    count: the question is what this surface can put on the wire, not where the
    literal was typed.
    """
    return {
        name: tuple(shlex.join(argv) for argv in published_commands(value))
        for name, value in vars(tools_module).items()
        if isinstance(value, str) and not name.startswith("__") and published_commands(value)
    }


def test_every_cure_this_surface_publishes_is_ratcheted_or_recorded() -> None:
    """The closure argument: no cure-publishing constant is outside this file.

    Set equality both ways over a population the module decides. A constant that
    starts naming a command has no arm and fails here; an arm naming a constant
    the module no longer publishes fails here too, which is what stops a stale
    entry from reading as coverage after the thing it covered was deleted.

    The partition into ratcheted and recorded is asserted separately, because the
    two fail differently: a ratcheted arm is one whose cure this file runs, and a
    recorded one is a gap somebody wrote down.
    """
    published = _cure_publishing_constants()

    assert published, (
        "no constant in `theurian.mcp.tools` publishes a command at all, which is not a "
        "state this surface has been in -- the availability refusals each name a "
        "rebuild. A population that matched nothing would satisfy every assertion below "
        "vacuously."
    )
    claimed = {
        arm.cure_source.removeprefix("tools.")
        for arm in ARMS
        if arm.cure_source.startswith("tools.")
    }
    assert claimed == set(published), (
        f"the cures this surface publishes and the arms this file claims have moved "
        f"apart; unclaimed constants: {sorted(set(published) - claimed)}, claimed but no "
        f"longer published: {sorted(claimed - set(published))}"
    )
    for arm in ARMS:
        constant = arm.cure_source.removeprefix("tools.")
        if constant in published:
            assert published[constant] == arm.publishes, (
                f"{arm.name}: the constant now publishes {published[constant]}, and this "
                f"arm still expects {arm.publishes}"
            )
    assert RATCHETED, "every arm is recorded as a gap, so the ratchet runs no cure at all"
    assert all(arm.outside_the_ratchet_because for arm in RECORDED), (
        "an arm held out of the ratchet without a reason is one somebody forgot"
    )
    assert all(arm.refusal for arm in RATCHETED), (
        "a ratcheted arm with an empty `refusal` makes `arm.refusal in before.message` "
        "true of every refusal there is, so the attribution check stops attributing "
        f"anything: {[arm.name for arm in RATCHETED if not arm.refusal]}"
    )
    assert all(arm.publishes for arm in ARMS), (
        f"an arm that publishes no command at all is not in this file's population: "
        f"{[arm.name for arm in ARMS if not arm.publishes]}"
    )


@pytest.mark.parametrize("arm", RECORDED, ids=lambda arm: arm.name)
def test_a_recorded_gap_cites_only_tests_that_exist(arm: Arm) -> None:
    """A recorded gap is an argument, and its citations are checked.

    A reason that names the test holding an arm is worth what the name is worth.
    A renamed or deleted test fails here rather than leaving a sentence that
    reads like coverage -- the failure mode ``test_resolved_layout_never_crosses.py``'s
    disposition table exists to refuse, applied to this table.
    """
    reason = arm.outside_the_ratchet_because
    cited = re.findall(r"`?(test_[a-z0-9_]+\.py)::(test_[a-z0-9_]+)`?", reason)
    bare = re.findall(r"`(test_[a-z0-9_]+)`", reason)

    assert cited or bare, f"{arm.name}: a recorded gap names no test at all"
    for file_name, test_name in cited:
        (path,) = list(_TESTS_ROOT.rglob(file_name))
        assert f"def {test_name}(" in path.read_text(encoding="utf-8"), (
            f"{arm.name}: cites a test its own file does not define: {file_name}::{test_name}"
        )
    for test_name in bare:
        found = [
            path
            for path in _TESTS_ROOT.rglob("test_*.py")
            if f"def {test_name}(" in path.read_text(encoding="utf-8")
        ]
        assert found, f"{arm.name}: cites a test no file under tests/ defines: {test_name}"


# -- The control --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_corpus_serves_both_tools_before_any_plant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this, no refusal below is attributable to the arm that was driven.

    A corpus that could not serve in the first place makes every "the answer
    moved" assertion a statement about the fixture -- and makes
    ``serves_after_the_cure`` unreachable for reasons the cure cannot fix.
    """
    corpus = _build_corpus(tmp_path, monkeypatch, builds=("findings", "review"))

    findings = await _ask(corpus, ARM_BY_NAME["no findings store has been built"])
    search = await _ask(corpus, ARM_BY_NAME["no review search store has been built"])

    assert findings.served, f"the corpus cannot serve `review.findings`: {findings.message}"
    assert search.served, f"the corpus cannot serve `review.search`: {search.message}"


# -- The ratchet --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", RATCHETED, ids=lambda arm: arm.name)
async def test_a_published_cure_moves_the_caller_off_the_refusal_that_published_it(
    arm: Arm, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ratchet. A cure that returns the caller to where they started is a defect.

    Read the commands out of the refusal the server just published, run them in
    the corpus that earned it, ask again, and require the answer to have moved.
    Nothing here reads a cure out of this file: a constant reworded to publish a
    command that cannot help would change what gets run, and would fail on the
    answer rather than on the text.

    This is the check that ``c7da702e``'s fix needed and did not have. Reverting
    that commit's type split -- routing the containment arm back into
    ``FINDINGS_UNAVAILABLE_REFUSAL`` -- makes the first case below publish
    ``theurian findings build`` for a fault that command meets first, and the
    refusal after the cure is byte-identical to the one before.
    """
    corpus = _build_corpus(tmp_path, monkeypatch, builds=arm.builds)
    outside = tmp_path / "outside-the-checkout"
    outside.mkdir()
    arm.plant(corpus, outside)

    before = await _ask(corpus, arm)
    published = published_commands(before.message)
    steps = await _run_the_cure(corpus.root, published)
    after = await _ask(corpus, arm)

    # The closed loop is asserted before the attribution checks below it, so that
    # the failure a reader meets first is the property this file exists for.
    # Ordered the other way, reverting `c7da702e` fails on "that is not this arm's
    # constant" -- true, and about the wrong thing.
    assert not before.served, f"{arm.name}: the arm did not fire, so no cure was published"
    assert after != before, (
        f"{arm.name}: the cure this refusal published returned the caller to the "
        f"identical refusal -- a closed loop. The commands it named and what each "
        f"exited with: {[(step.command, step.exit_code) for step in steps]}"
    )
    assert arm.refusal in before.message, (
        f"{arm.name}: the refusal that fired is not the one this arm is about, so the "
        f"cure run above belongs to some other arm: {before.message}"
    )
    assert tuple(shlex.join(argv) for argv in published) == arm.publishes, (
        f"{arm.name}: the refusal published {[shlex.join(a) for a in published]} where "
        f"this arm measured {list(arm.publishes)}"
    )
    assert after.served == arm.serves_after_the_cure, (
        f"{arm.name}: measured to {'serve' if arm.serves_after_the_cure else 'refuse'} "
        f"after its cure, and it did not. Steps: "
        f"{[(step.command, step.exit_code) for step in steps]}. Answer: {after.message}"
    )


# -- The two recorded gaps, measured ------------------------------------------


@pytest.mark.asyncio
async def test_the_recorded_closed_loop_is_still_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``review.search``'s store-leaf arm publishes a cure that meets the same fault.

    A recorded gap with no measurement behind it is a sentence. This is the
    measurement: the arm fires, its refusal is the availability constant and
    carries no layout, the command that constant names exits non-zero on the
    plant that produced it, and the refusal afterwards is **byte-identical** to
    the one before.

    **This test is RED the day the arm is fixed, and that is its purpose.** The
    fix is the one ``c7da702e`` made at the findings twin -- a cure that survives
    the fold -- and when it lands, this case fails, the arm moves out of
    :data:`RECORDED` into the ratchet, and this test is deleted. Until then the
    gap cannot be closed by accident or reported as coverage.
    """
    arm = ARM_BY_NAME["the review search store's leaf escapes the project"]
    assert not arm.is_ratcheted, "this arm is ratcheted now, so this test has outlived its subject"
    corpus = _build_corpus(tmp_path, monkeypatch, builds=arm.builds)
    outside = tmp_path / "outside-the-checkout"
    outside.mkdir()
    arm.plant(corpus, outside)

    before = await _ask(corpus, arm)
    steps = await _run_the_cure(corpus.root, published_commands(before.message))
    after = await _ask(corpus, arm)

    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in before.message
    assert str(ProjectPaths.of(corpus.root).state) not in before.message, (
        "the containment refusal published the operator's resolved state directory "
        "(GHSA-97q9), which is the disclosure the fold exists to prevent"
    )
    assert [step.exit_code for step in steps] != [0 for _ in steps], (
        f"the published cure now succeeds against the plant that produced the refusal, "
        f"so this arm may be curable: {[(s.command, s.exit_code) for s in steps]}"
    )
    assert after == before, (
        f"the loop is no longer closed -- move this arm into the ratchet and delete "
        f"this test. Steps: {[(s.command, s.exit_code) for s in steps]}"
    )


def test_the_knowledge_directory_cure_names_its_decisive_step_in_prose() -> None:
    """The other recorded gap, measured on the text rather than by running it.

    ``KNOWLEDGE_DIR_ESCAPE_REMEDY``'s decisive act -- removing the link -- is
    words, and the only thing it backticks is ``theurian init``, which meets the
    same refusal while the link is still there. So the ratchet's key cannot run
    this cure without reporting a closed loop for a cure that is not one, and
    that is the measured reason the arm is recorded rather than ratcheted.

    A rewording that gave the removal a command of its own would fail here, which
    is the moment the arm becomes ratchetable.
    """
    published = published_commands(KNOWLEDGE_DIR_ESCAPE_REMEDY)

    assert [shlex.join(argv) for argv in published] == ["theurian init"], (
        f"the knowledge-directory cure's published commands have changed: {published}"
    )
    assert "remove the link" in KNOWLEDGE_DIR_ESCAPE_REMEDY, (
        "the decisive step is no longer named even in prose, so the cure names nothing "
        "a caller can act on before the rebuild it does publish"
    )
