"""No MCP response names the operator's **resolved** filesystem layout (GHSA-97q9).

The closure instrument for the disclosure class *"an MCP response interpolates
the operator's resolved absolute filesystem layout"*. Every earlier member of the
class was found and fixed one raise site at a time -- an escaping findings leaf,
an escaping ``.theurian/state``, an escaping ``.theurian`` -- and each fix was
pinned by a test naming the tool and the guard that produced it. This file pins
the *property* instead, over every registered tool and every plant, so a raise
site nobody enumerated joins the sweep by existing rather than by being noticed.

**The invariant is a differential, and the naive form of it is false.**
"No absolute path in any response" cannot be the rule: ``project.list`` publishes
each project's registered ``rootPath``, which is an absolute path, **by design**
-- ADR-0002's daemon is per-user and SEC-13 governs content, not the location of
a checkout the caller's own operator registered. So this sweep registers one
project by a spelling that is a symbolic link and holds its physical tree
somewhere else, and asserts:

    with ``resolved != registered``, no response from any tool -- success or
    refusal, any field, any block -- contains the **resolved** physical form.

The registered spelling may appear wherever the design publishes it, and
:func:`test_project_list_publishes_the_registered_spelling` holds that it really
is published, so this differential cannot quietly become vacuous by the
registered form being withdrawn.

**Two narrower closure predicates fell in this arc before this one.** An AST key
over ``mcp/tools.py``'s ``try``/``except`` sites: satisfied by a handler that
caught the error and re-published its message. An exception-type key -- every
``ProjectPathEscapeError`` is answered with a constant -- satisfied by
:func:`~theurian.application.project_service.verify_state_provenance`, which was
not that type at all: it raised a plain ``ProjectError`` interpolating
``paths.state``. Both keyed on *how* a refusal is produced; this one keys on what
crosses the wire, which is the thing the advisory is about, and the
``delivered-state`` plant is what drove that fourth raise site out. Measured RED
at ``19ec2b8d``: five cells, one raise site -- every project-scoped tool refusing
through the same interpolation. It is GREEN as of this commit, where that refusal
became the module-level constant ``_UNBUILT_STATE_REFUSAL`` and names only the
project-relative ``.theurian/state/``; the plant goes RED again the moment an
interpolation returns.

**The population key is the point where the two spellings diverge**, not either
path in full. The checkout is registered as ``<tmp>/demo`` and physically lives at
``<tmp>/opaque-elsewhere/real-demo``; the two agree up to ``<tmp>/`` and part at
the next character, so ``<tmp>/o`` is the shortest string only the resolved layout
can produce. Any response containing it has published a byte the registered
spelling does not have.

**The key is that short because a published value is bounded, and a cut value can
still disclose.** ``_publishable_field`` cuts at 120 characters and
``_bounded_message`` at 600, and pytest's own temporary root already exceeds the
first. Measured 2026-09-11 with ``project.list`` perturbed to publish
``Path(rootPath).resolve()``: the wire carried
``…/test_no_registered_tool_publis0/opa… (cut)``. A key spelling the whole
resolved path out missed that, and so did a key spelling the physical home's
*name* out -- both had been cut away while a layout bit was on the wire. The
divergence survives any cut that does not also remove the disclosure: a value cut
inside ``<tmp>`` itself is equally consistent with the registered spelling and
carries no bit about where the tree physically is.

The directory names and the whole paths are swept **beside** the key, as labels
that say how much of the layout a leak carried. The temporary tree deliberately is
not swept at all: it is a prefix of the registered spelling too, so a key on it
would either ban a designed publication or need a carve-out and stop being
universal. Every plant's escape target is created *under* the physical home for
the same reason -- a target elsewhere under the temporary tree would need a key of
its own, and a plant added later would then be swept by its label alone.

**Out of scope, stated rather than implied.** This is a response-*content*
invariant only. A layout bit carried by a duration, by which rows reach a field,
or by a resource the query consumes is a different family and is recorded
elsewhere (``docs/security/threat-model.md``). The installation's own private
directories -- ``THEURIAN_DATA_DIR``, ``HOME`` -- are outside the key as well:
what the class is about is the *project's* physical location, and widening the
key to the data directory would sweep an unrelated population under one name.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
    read_active_state,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review_finding import (
    FindingLoad,
    FindingSeverity,
    ReviewerToken,
    ReviewFinding,
)
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore
from theurian.mcp.tools import _CUT_MARKER, _publishable_field

pytestmark = pytest.mark.integration

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

PROJECT_ID: Final = "demo"
ITEM_ID: Final = "architecture.auth-policy"

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {ITEM_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {ITEM_ID}
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

#: A state hash no build here produced, for the delivered-state plant. Valid as a
#: ``StateHash`` -- 64 hexadecimal characters, or ``ActiveState.from_json``
#: refuses the pointer and the plant would measure the parser instead of the
#: provenance gate.
UNBUILT_STATE_HASH: Final = "0" * 64

#: One value per *required* argument any registered tool takes. Keyed by the
#: published wire name, so a tool added later either takes arguments this sweep
#: already knows how to fill or fails :func:`_arguments_for` by name -- a new tool
#: is never silently swept with an empty payload.
ARGUMENT_VALUES: Final[dict[str, Any]] = {
    "projectId": PROJECT_ID,
    "query": "authentication",
    "itemId": ITEM_ID,
}

runner = CliRunner()


@dataclass(frozen=True)
class Corpus:
    """One registered, migrated project whose registered spelling is a link.

    ``registered`` is what the registry holds and what ``project.list`` publishes.
    ``physical`` is where the tree really is. ``elsewhere`` is ``physical``'s
    parent: every resolved path this corpus can build, and every plant's escape
    target, sits under it, and the registered spelling does not.
    """

    registry: ProjectRegistry
    registered: Path
    physical: Path
    elsewhere: Path

    @property
    def paths(self) -> ProjectPaths:
        return ProjectPaths.of(self.registered)

    @property
    def divergence(self) -> str:
        """The shortest string only the *resolved* layout can put on the wire.

        ``registered`` and ``elsewhere`` are siblings, so a published path is
        consistent with the registered spelling right up to the directory below
        their common parent -- and the first character of that directory's name is
        where the two part company. Any response carrying ``<parent>/o`` has
        published a byte the registered spelling does not contain, whatever a
        length bound did to the rest of it.

        This is the sweep's sound key, and the reason it is not simply the
        physical home's name: a value cut at ``_publishable_field``'s 120
        characters can lose the name and still disclose the divergence.
        Measured 2026-09-11 -- ``project.list`` taught to publish
        ``Path(rootPath).resolve()`` answered
        ``…/test_no_registered_tool_publis0/opa… (cut)``, which names no
        directory this corpus created and is still a layout bit on the wire. The
        name keys stay beside this one as labels saying *how much* leaked.
        """
        return f"{self.elsewhere.parent}/{self.elsewhere.name[:1]}"


#: What a plant returns: the named escape targets it created, so a failure can say
#: *which* path leaked rather than only that one did.
PlantedPaths = dict[str, Path]


@dataclass(frozen=True)
class Plant:
    """One doctored state, and the guard it is there to drive."""

    key: str
    apply: Callable[[Corpus], PlantedPaths]
    drives: str


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _check_out(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create, register and migrate one project checkout at ``root``.

    Adapted from ``test_review_findings_tool.py``'s helper of the same name, and
    for the same reason: everything ``_resolve`` needs has to be real -- registry
    entry, active state pointer, ADR-0004/SEC-7 provenance on the canonical state
    -- or a refusal measured here is a refusal about an unresolvable project
    rather than about the plant.
    """
    root.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY, encoding="utf-8")
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(
        MIGRATION, encoding="utf-8"
    )
    _run("project", "register")
    _run("migrate", "apply")


def _one_finding() -> FindingLoad:
    """A landed corpus of one, so ``review.findings`` has a served payload to sweep.

    The content is irrelevant to this file -- what matters is that the tool
    answers with rows rather than with its unavailable constant, so the baseline
    case sweeps a *success* payload and not a refusal.
    """
    return FindingLoad(
        accepted=(
            ReviewFinding(
                reviewer=ReviewerToken.SECURITY,
                severity=FindingSeverity.HIGH,
                finding_text="a refusal named the operator's filesystem layout",
                anchor=SourceAnchor(provider="git", source_uri="a" * 40, commit_sha="a" * 40),
                pull_request=11,
                date=datetime.fromisoformat("2026-09-11T09:00:00+00:00"),
                family="a published field",
                specialist="theurian-tests",
            ),
        ),
        rejected=(),
    )


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Corpus]:
    """A healthy project whose registered spelling resolves somewhere else.

    Built by the real CLI at ``<tmp>/demo``, then **moved**: the tree goes to
    ``<tmp>/opaque-elsewhere/real-demo`` and ``<tmp>/demo`` becomes a symbolic
    link to it. The registry is not rewritten, so the registered spelling stays
    ``<tmp>/demo`` while ``ProjectPaths.of`` resolves to the opaque home -- which
    is the whole differential this file turns on.

    **Moved afterwards, because registering from inside the link cannot produce
    it.** ``cli/context.py``'s ``resolve_context`` opens with
    ``(start or Path.cwd()).resolve()`` and finds the git root from there, so a
    ``project register`` run inside the link records the *physical* path and the
    registry holds the resolved form -- no differential at all. Measured
    2026-09-11 on Darwin 25.6 for the half below that too: ``os.getcwd()`` inside
    a symbolic link answers with the physical directory, so the ``.resolve()`` is
    not even what decides it.

    **The build record is re-made after the move, and that is a fixture act with
    a reason.** :class:`BuildProvenance` keys on ``str(root.resolve())``, so
    moving the tree strands the record ``migrate apply`` wrote and every
    project-scoped tool would refuse at the provenance gate -- a corpus in which
    no plant is attributable to itself. Re-recording under the resolved root
    models the installation that built the state where the tree now is. The
    delivered-state plant below re-creates the unrecorded condition deliberately,
    in the tree rather than in the data directory, which is where a hostile
    repository can reach.
    """
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    registered = tmp_path / PROJECT_ID
    _check_out(registered, monkeypatch)
    registry = ProjectRegistry.default(data_dir)

    SqliteReviewFindingStore(
        ProjectPaths.of(registered).findings_for(FINDINGS_STORE_ID)
    ).replace_all(_one_finding())

    monkeypatch.chdir(tmp_path)  # step out of the tree before moving it
    elsewhere = tmp_path / "opaque-elsewhere"
    elsewhere.mkdir()
    physical = elsewhere / "real-demo"
    registered.rename(physical)
    registered.symlink_to(physical)

    built = Corpus(registry=registry, registered=registered, physical=physical, elsewhere=elsewhere)
    paths = built.paths
    active = read_active_state(paths)
    assert active is not None, "the premise: the CLI-built project has an active state pointer"
    provenance = BuildProvenance.for_registry(registry)
    provenance.record_state(paths.root, str(active.state_hash))
    provenance.record_findings(paths.root, FINDINGS_STORE_ID)

    assert paths.root != registered, (
        "the premise: the registered spelling must resolve to a different directory, or "
        "the resolved form this file looks for is the string the registry already "
        "publishes and the whole differential is vacuous"
    )
    assert paths.root == physical.resolve(), "the premise: the link reaches the moved tree"
    assert built.divergence not in str(registered), (
        "the premise: the population key must not be a substring of the registered "
        "spelling, or the sweep bans the `rootPath` `project.list` publishes by design"
    )
    # The divergence is one character wide, so it is sound only while nothing else
    # this run put under the temporary tree shares that character. Asserted rather
    # than arranged by naming: a directory added to this fixture later would
    # otherwise make the key fire on a path that is not the project's at all.
    diverging = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(elsewhere.name[:1]))
    assert diverging == [elsewhere.name], (
        f"the population key `{built.divergence}` matches more than the physical home, "
        f"so a hit no longer means the project's resolved layout: {diverging}"
    )

    yield built


def _arguments_for(tool: Any) -> dict[str, Any]:
    """Minimal valid-shaped arguments for one registered tool, from its own schema.

    Read off ``Tool.parameters`` -- the JSON schema a client is handed -- rather
    than from a table of tool names, so a tool registered later is called rather
    than skipped. Optional parameters are left at their defaults; a *required*
    one this file has no value for is an assertion failure, because the
    alternative is calling the new tool with an incomplete payload and sweeping
    an argument-validation error that reaches no project at all.
    """
    schema: dict[str, Any] = tool.parameters
    required: list[str] = list(schema.get("required", ()))
    unknown = [name for name in required if name not in ARGUMENT_VALUES]
    assert not unknown, (
        f"{tool.name} requires arguments this sweep has no value for: {unknown}. Add one "
        f"to ARGUMENT_VALUES -- an unfilled required argument is a call refused by "
        f"schema validation, which reaches no project layout and sweeps nothing."
    )
    return {name: ARGUMENT_VALUES[name] for name in required}


async def _response_text(server: Any, name: str, arguments: dict[str, Any]) -> str:
    """Everything one call produced, as one string.

    A success is serialised whole -- structured content, every content block, and
    the ``isError`` flag -- through the result model's own dump, so a field added
    to the wire joins the sweep without this helper being taught about it. A
    refusal is the exception's text, which is what the transport turns into the
    caller's error content.

    **The ``except`` is deliberately wider than the expected type.** ``call_tool``
    re-raises a failing tool as the SDK's ``ToolError``, and catching only that
    would let a refusal arriving as anything else -- the SDK's own
    ``UnexpectedToolError``, an ``OSError`` escaping a guard -- go unswept, which
    is precisely the arm most likely to be carrying an un-suppressed path. The
    caller saw whatever came back; so does this sweep.
    """
    try:
        result = await server.call_tool(name, arguments)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str)


def _context(haystack: str, needle: str, *, window: int = 70) -> str | None:
    """Where ``needle`` appears in ``haystack``, with enough around it to read."""
    at = haystack.find(needle)
    if at < 0:
        return None
    return haystack[max(0, at - window) : at + len(needle) + window]


def _candidates(corpus: Corpus, planted: PlantedPaths) -> dict[str, str]:
    """What a response must not contain, keyed first and labelled after.

    The first entry is the sound key -- :attr:`Corpus.divergence`, the shortest
    string only the resolved layout can produce, and the one a length bound cannot
    shorten past without removing the disclosure with it. Everything after it is a
    **label**: two directory names and then whole paths, in increasing order of
    how much of the layout their presence proves. They say which member leaked and
    how far it got; they do not decide whether one did.
    """
    return {
        "the divergence from the registered spelling": corpus.divergence,
        "the physical home's directory name": corpus.elsewhere.name,
        "the resolved root's own directory name": corpus.physical.name,
        "the resolved project root, in full": str(corpus.physical),
        **{label: str(path) for label, path in planted.items()},
    }


def _layout_hits(candidates: dict[str, str], response: str) -> dict[str, str]:
    """Which candidates this response names, each with the text around it.

    One function rather than an inline comprehension in the sweep, so the
    positive control below governs the *same* scan the sweep runs. A check
    asserted in one place and exercised in another proves nothing about the check
    that ships.
    """
    return {
        label: seen
        for label, needle in candidates.items()
        if (seen := _context(response, needle)) is not None
    }


def _plant_nothing(corpus: Corpus) -> PlantedPaths:
    """No plant at all: the differential has to hold over healthy responses too.

    Without this case the sweep would only ever read refusals, and "no resolved
    path in any refusal" is a strictly narrower claim than the one the class
    needs -- a served payload is a response like any other.
    """
    return {}


def _plant_escaping_findings_leaf(corpus: Corpus) -> PlantedPaths:
    """The findings **store leaf** delivered as a link out of the tree.

    What ``git add -f`` reaches past ADR-0004's ignore, aimed at where the file is
    rather than at what it holds. The build record still covers the store, so the
    call reaches the containment check rather than stopping at the provenance
    gate.
    """
    paths = corpus.paths
    leaf = paths.findings_for(FINDINGS_STORE_ID)
    target_dir = corpus.elsewhere / "escaped-findings"
    target_dir.mkdir()
    target = target_dir / leaf.name
    leaf.rename(target)
    leaf.symlink_to(target)

    assert not leaf.resolve().is_relative_to(paths.root), (
        "the premise: the findings leaf must really resolve outside the project root, "
        "or the containment refusal this plant drives never fires"
    )
    assert BuildProvenance.for_registry(corpus.registry).has_findings(
        paths.root, FINDINGS_STORE_ID
    ), (
        "the premise: this installation's build record still covers the store, so the "
        "call reaches the containment check rather than stopping at the provenance gate"
    )
    return {"the directory the findings leaf escaped to": target_dir}


def _plant_escaping_state_directory(corpus: Corpus) -> PlantedPaths:
    """``.theurian/state`` delivered as a link out of the tree.

    Refused a layer above the tool, in ``_resolve``'s ``read_active_state`` arm,
    and therefore on every project-scoped tool at once.
    """
    paths = corpus.paths
    state = paths.knowledge_dir / "state"
    target = corpus.elsewhere / "escaped-state"
    state.rename(target)
    state.symlink_to(target)

    assert not state.resolve().is_relative_to(paths.root), (
        "the premise: `.theurian/state` must really resolve outside the project root, or "
        "the containment refusal this plant drives never fires"
    )
    assert (target / "active.json").exists(), (
        "the premise: the state directory's contents moved with it, so what refuses is "
        "containment and not a pointer that is simply gone"
    )
    return {"the directory the state link escaped to": target}


def _plant_escaping_knowledge_directory(corpus: Corpus) -> PlantedPaths:
    """``.theurian`` itself delivered as a link out of the tree (#237, #550).

    Refused earlier still, by ``ProjectPaths.of``'s own join check, before a
    single derived path is built.
    """
    # Read before the plant lands: `ProjectPaths.of` is what refuses afterwards,
    # so deriving the directory after the rename would raise here instead of in
    # the tool. Derived rather than spelled `.theurian`, so a renamed default
    # moves the plant with it.
    knowledge_dir = corpus.paths.knowledge_dir
    target = corpus.elsewhere / "escaped-knowledge"
    knowledge_dir.rename(target)
    knowledge_dir.symlink_to(target)

    assert not knowledge_dir.resolve().is_relative_to(corpus.physical.resolve()), (
        "the premise: `.theurian` must really resolve outside the project root, or the "
        "join check this plant drives never fires"
    )
    return {"the directory the knowledge link escaped to": target}


def _plant_delivered_state(corpus: Corpus) -> PlantedPaths:
    """Canonical state this installation has no record of building (ADR-0004, T-19).

    The pointer and the database are both present and self-consistent; what is
    absent is any record that *this* Theurian built them, which is exactly what a
    repository shipping its own ``.theurian/state/`` produces. Made in the tree
    rather than by deleting the build record, because the tree is the half a
    hostile clone can reach.
    """
    paths = corpus.paths
    active = read_active_state(paths)
    assert active is not None, "the premise: there is a pointer to re-write"
    delivered = f"theurian-state-{UNBUILT_STATE_HASH}.sqlite"
    (paths.state / active.database_filename).rename(paths.state / delivered)
    (paths.state / "active.json").write_text(
        json.dumps(
            {
                "stateHash": UNBUILT_STATE_HASH,
                "databaseFilename": delivered,
                "migrationCount": active.migration_count,
                "updatedAt": active.updated_at,
            }
        ),
        encoding="utf-8",
    )

    assert not BuildProvenance.for_registry(corpus.registry).has_state(
        paths.root, UNBUILT_STATE_HASH
    ), "the premise: nothing records this installation as having built the delivered state"
    return {}


PLANTS: Final = (
    Plant("healthy", _plant_nothing, "nothing -- the responses are the served ones"),
    Plant(
        "escaping-findings-leaf",
        _plant_escaping_findings_leaf,
        "`ProjectPaths._contained`, reached from `review.findings`' own body",
    ),
    Plant(
        "escaping-state-directory",
        _plant_escaping_state_directory,
        "`ProjectPaths._contained`, reached from `_resolve`'s `read_active_state`",
    ),
    Plant(
        "escaping-knowledge-directory",
        _plant_escaping_knowledge_directory,
        "`ProjectPaths.of`'s join check, reached from `_resolve`",
    ),
    Plant(
        "delivered-state",
        _plant_delivered_state,
        "`verify_state_provenance`, reached from `_resolve`",
    ),
)


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", PLANTS, ids=lambda plant: plant.key)
async def test_no_registered_tool_publishes_the_resolved_project_layout(
    plant: Plant, corpus: Corpus
) -> None:
    """The class invariant: the resolved physical form crosses no tool, ever.

    Over every registered tool from the built server rather than a list written
    here, so an eighth tool joins this sweep by being registered. Over a healthy
    corpus as well as four doctored ones, because a served payload is a response
    like any other and the claim is about responses, not about refusals.

    A tool that refuses for a reason unrelated to the plant is a fine cell: what
    is asserted is what came back, not why. Every cell is collected before the
    assertion rather than failing at the first, so one run reports the whole
    matrix -- which is what says whether a leak is one raise site or several.
    """
    candidates = _candidates(corpus, plant.apply(corpus))

    server = build_server(corpus.registry)
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this sweep vacuously"

    leaked: dict[str, str] = {}
    for tool in tools:
        response = await _response_text(server, tool.name, _arguments_for(tool))
        for label, seen in _layout_hits(candidates, response).items():
            leaked[f"{plant.key} x {tool.name} -- {label}"] = seen

    assert not leaked, (
        f"a response published the operator's resolved filesystem layout to an MCP "
        f"caller (GHSA-97q9). The plant drives {plant.drives}. Cells, each with the "
        f"offending substring in context:\n"
        + "\n".join(f"  {cell}:\n    ...{seen}..." for cell, seen in sorted(leaked.items()))
    )


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
async def test_the_corpus_serves_before_any_plant(corpus: Corpus) -> None:
    """Without this, the healthy case of the sweep above proves nothing.

    The sweep asserts an **absence**, and a corpus every tool refused would
    satisfy it perfectly while reading no served payload at all -- the move
    behind a symbolic link alone is enough to produce that, because
    :class:`BuildProvenance` keys on the resolved root and the record ``migrate
    apply`` wrote is stranded by it. So the fixture's re-record is checked by its
    effect: the knowledge tools answer with content and ``review.findings``
    answers with the row this file landed.

    It also fixes what the doctored cases are a difference *from*. A plant whose
    refusal is indistinguishable from the corpus having been broken all along is
    not attributable to the plant.
    """
    server = build_server(corpus.registry)

    # The same argument values the sweep sends, so this premise is about the
    # calls the sweep really makes and not about a second, friendlier set.
    query = {"projectId": PROJECT_ID, "query": ARGUMENT_VALUES["query"]}
    status = await _response_text(server, "knowledge.status", {"projectId": PROJECT_ID})
    search = await _response_text(server, "knowledge.search", query)
    findings = await _response_text(server, "review.findings", {"projectId": PROJECT_ID})

    assert '"itemCount": 1' in status, f"the migrated item is not being served: {status}"
    assert f'"itemId": "{ITEM_ID}"' in search, f"the search returned no served row: {search}"
    assert '"count": 1' in findings, f"the landed finding is not being served: {findings}"


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
async def test_project_list_publishes_the_registered_spelling(corpus: Corpus) -> None:
    """Why the sweep above is a differential and not "no absolute path".

    ``project.list`` hands back each registered project's ``rootPath``, which is
    an absolute path, deliberately: the daemon is per-user (ADR-0002) and an
    operator's own checkout location is not another project's content (SEC-13). A
    sweep that forbade every absolute path would either fail on this by design or
    need a carve-out for it and stop being universal, so the sweep is keyed on the
    **resolved** form instead.

    This is what keeps that key honest in both directions. If the registered
    spelling were ever withdrawn from this response, the sweep above would still
    pass while asserting something much weaker than it reads, and this test is
    what goes RED instead.

    **Asserted against the bounded form, computed by the daemon's own helper.**
    ``rootPath`` goes out through
    :func:`~theurian.mcp.tools._publishable_field`, which cuts at 120 characters,
    and pytest's temporary root plus a parametrised test's directory name already
    exceeds that -- so an assertion spelling the whole path out fails on a bound
    rather than on the contract. The helper is called rather than its number
    copied, and the cut prefix is checked to still *be* a prefix of the registered
    spelling, so this cannot pass on a helper that started returning something
    else entirely.
    """
    server = build_server(corpus.registry)

    response = await _response_text(server, "project.list", {})

    visible = _publishable_field(str(corpus.registered)).removesuffix(_CUT_MARKER)
    assert visible and str(corpus.registered).startswith(visible), (
        f"the premise: what `_publishable_field` renders for this root is no longer a "
        f"prefix of the registered spelling, so the assertion below would be about "
        f"something other than the published `rootPath`: {visible!r}"
    )
    assert visible in response, (
        f"`project.list` no longer publishes the registered `rootPath`, so the sweep "
        f"beside this test is no longer distinguishing a designed publication from a "
        f"disclosure: {response}"
    )
    assert corpus.elsewhere.name not in response, (
        f"`project.list` published the resolved physical location behind the registered "
        f"spelling, which is the operator's machine layout (GHSA-97q9): {response}"
    )


def test_a_tool_with_an_unknown_required_argument_stops_the_sweep() -> None:
    """An eighth tool joins the sweep or halts it; it is never swept vacuously.

    Enumerating from the built server is only half of "every registered tool".
    The other half is the call: a tool whose required arguments this file cannot
    fill would be refused by schema validation before it reached a project, and
    the sweep would read that refusal as a clean cell. :func:`_arguments_for`
    fails instead, naming the argument, so the next tool's author is told what to
    add rather than getting a green sweep that covered six tools out of eight.

    Driven through a stub carrying only the two attributes the helper reads.
    Registering a real tool on a real server would exercise the SDK's schema
    generation, which is not what is under test here and has its own tests.
    """

    class _NewTool:
        name = "knowledge.summarise"
        parameters: dict[str, Any] = {  # noqa: RUF012 - a stub's literal, never mutated
            "required": ["projectId", "outlineDepth"],
            "properties": {},
        }

    with pytest.raises(AssertionError, match="outlineDepth"):
        _arguments_for(_NewTool())


def _synthetic(tmp_path: Path) -> Corpus:
    """A corpus-shaped value with no project behind it, for the scan's own tests.

    :func:`_layout_hits` reads strings, never the filesystem, so the control below
    needs the *shape* of a corpus and not a checkout -- and building one through
    the CLI would make the scan's positive control depend on the thing it exists
    to keep honest.
    """
    return Corpus(
        registry=ProjectRegistry.default(tmp_path / "datadir"),
        registered=tmp_path / PROJECT_ID,
        physical=tmp_path / "opaque-elsewhere" / "real-demo",
        elsewhere=tmp_path / "opaque-elsewhere",
    )


def test_the_layout_scan_finds_a_planted_disclosure(tmp_path: Path) -> None:
    """The positive control. A sweep that reports absence must be able to report presence.

    The sweep's whole output is "nothing found", and a scan that could never find
    anything produces that perfectly. This plants the resolved layout in a
    response-shaped string and requires every key to name it.

    **The sample sentence is the pre-fix wording, kept deliberately as a shape
    known to disclose.** No production refusal interpolates a path like this any
    more: since this commit ``verify_state_provenance`` raises the constant
    ``_UNBUILT_STATE_REFUSAL``, which names only the project-relative
    ``.theurian/state/``, and the sole other occurrence of this exact opening in
    the tree is that constant's own note quoting what the refusal used to read.
    That costs the control nothing -- :func:`_layout_hits` reads strings and never
    the filesystem, so what it needs is a string that really carries the layout,
    not a string production still emits.
    """
    corpus = _synthetic(tmp_path)
    candidates = _candidates(corpus, {})
    leaked = json.dumps(
        {"error": f"The derived knowledge state under {corpus.physical}/.theurian/state ..."}
    )

    hits = _layout_hits(candidates, leaked)

    assert set(hits) == set(candidates), (
        f"the scan missed a resolved path that is plainly in the response; found only "
        f"{sorted(hits)}"
    )
    for label, seen in hits.items():
        assert corpus.elsewhere.name in seen, f"{label}: the context window lost its own match"


def test_the_layout_scan_does_not_report_the_registered_spelling(tmp_path: Path) -> None:
    """The near miss. A scan matching everything is as useless as one matching nothing.

    ``project.list``'s published ``rootPath`` is the registered spelling, and the
    sweep must read it as clean -- that is the whole difference between this
    invariant and "no absolute path in any response", which the design would fail
    by construction.
    """
    corpus = _synthetic(tmp_path)

    clean = json.dumps(
        {"projects": [{"projectId": PROJECT_ID, "rootPath": str(corpus.registered)}]}
    )

    assert _layout_hits(_candidates(corpus, {}), clean) == {}, (
        "the scan reported the resolved layout in a response naming only the registered "
        "spelling, so it cannot tell a disclosure from a designed publication"
    )


def test_a_disclosure_cut_short_of_the_names_is_still_caught(tmp_path: Path) -> None:
    """Why the key is the divergence and not the physical home's name.

    A published value is bounded -- ``_publishable_field`` cuts at 120 characters
    -- so a disclosure can reach the wire with everything after ``<tmp>/opa``
    removed. That is the exact shape a perturbed ``project.list`` produced on
    2026-09-11, and a sweep keyed on the *name* read it as clean while a layout
    bit was on the wire. This is what says the key does not have that hole,
    rather than the module docstring alone.

    The sample sentence is the pre-fix wording, kept as a shape known to disclose
    for the reason :func:`test_the_layout_scan_finds_a_planted_disclosure` gives.
    """
    corpus = _synthetic(tmp_path)
    cut_inside_the_home_name = f"The derived knowledge state under {tmp_path}/opa" + _CUT_MARKER

    hits = _layout_hits(_candidates(corpus, {}), cut_inside_the_home_name)

    assert "the divergence from the registered spelling" in hits, (
        f"a value that diverges from the registered spelling went unreported, so a "
        f"bounded field is a channel this sweep cannot see: {cut_inside_the_home_name}"
    )
    assert "the physical home's directory name" not in hits, (
        "the name key matched a cut that removed the name, so this test is not "
        "measuring the cut it was written for"
    )


def test_a_cut_inside_the_common_parent_is_not_a_disclosure(tmp_path: Path) -> None:
    """The boundary on the other side, and the one that could be wrong.

    The key's justification is that a cut short enough to remove the divergence
    removes the disclosure with it: what survives is the common parent of the
    registered spelling and the physical home, equally consistent with either, and
    it carries no bit about where the tree really is. This pins that claim. If it
    ever failed, the sweep would be reporting a disclosure that is not there --
    and a sweep that cries wolf on a designed publication is how a carve-out gets
    added and the invariant stops being universal.

    The sample sentence is the pre-fix wording, kept as a shape known to disclose
    for the reason :func:`test_the_layout_scan_finds_a_planted_disclosure` gives.
    """
    corpus = _synthetic(tmp_path)
    cut_before_the_divergence = f"The derived knowledge state under {tmp_path}" + _CUT_MARKER

    hits = _layout_hits(_candidates(corpus, {}), cut_before_the_divergence)

    assert hits == {}, (
        f"a value cut before the divergence was reported as a disclosure, which the "
        f"key's justification says it cannot be: {hits}"
    )
