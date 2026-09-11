"""No MCP response's **content** names the operator's resolved filesystem layout.

GHSA-97q9. *Content* is in the first line because it is the whole scope: this
file reads what a call returns or raises, and a layout bit carried by a duration,
by which rows reach a field, or by a resource the query consumes is a different
family, recorded elsewhere (``docs/security/threat-model.md``). The paragraph
near the end of this docstring says the same thing again, for a reader who
arrived at the plants rather than at the title.

The closure instrument for the disclosure class *"an MCP response interpolates
the operator's resolved absolute filesystem layout"*. Every earlier member of the
class was found and fixed one raise site at a time -- an escaping findings leaf,
an escaping ``.theurian/state``, an escaping ``.theurian`` -- and each fix was
pinned by a test naming the tool and the guard that produced it. This file pins
the *property* instead.

**A sweep has two axes, and only one of them was derived** (round one, code
review HIGH). The tools are enumerated from the built server, so an eighth tool
joins by being registered; the plants were a hand-written five, so a raise site
nobody enumerated joined nothing at all and the "by existing rather than by being
noticed" this paragraph used to claim was true of half the matrix. Both axes are
governed now:

- **tools** -- :func:`test_no_registered_tool_publishes_the_resolved_project_layout`
  walks ``server._tool_manager.list_tools()``, and
  :func:`test_a_tool_with_an_unknown_required_argument_stops_the_sweep` makes a
  tool this file cannot call halt the sweep rather than pass it.
- **plants** -- :func:`test_every_resolved_path_refusal_has_a_recorded_disposition`
  derives the raise sites that *could* put a resolved path on this wire straight
  out of the source and requires every one of them to carry a disposition:
  a plant here, a guard elsewhere, a conversion upstream, or a recorded reason
  why no registered tool reaches it. A site nobody enumerated reddens that test
  instead of being silently unswept.

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
through the same interpolation. It has been GREEN since ``74e32a21``, where that
refusal became the module-level constant ``_UNBUILT_STATE_REFUSAL`` and names only
the project-relative ``.theurian/state/``; the plant goes RED again the moment an
interpolation returns, and so does
:func:`test_a_departed_raise_site_has_really_left_the_key`.

**The two store guards stopped folding their containment refusals into their
availability constants, and no cell in this matrix moved.** ``review.findings``
answered an escaping store leaf with ``PATH_ESCAPE_REFUSAL`` and the escape cure
from ``c7da702e``, because that constant's cure -- ``theurian findings build`` --
resolves the same leaf through the same helper and exits 4 on it.
``review.search``'s *reachable* containment refusal is the other one:
``review_search_for`` makes its own state-scoped check and raises the plain
``ProjectError`` beneath that class, and that one kept folding while its message
interpolated the resolved ``.theurian/state``, since folding was then the only
thing keeping the directory off this wire. The message is built from the store's
file name and a relative literal now, so the raise site left this file's
population (:data:`DEPARTED`) and the guard forwards it with its cure. Both
plants stay -- ``escaping-findings-leaf`` and ``escaping-review-search-leaf`` --
and neither change moves which paths cross, only which cure does, which is why
the invariant above is unchanged by them.

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
elsewhere (``docs/security/threat-model.md``).

**The installation's own private directories -- ``THEURIAN_DATA_DIR``, ``HOME``
-- are outside the key, and that exclusion is load-bearing rather than tidy**
(round one, adversarial LOW). What the class is about is the *project's*
physical location, and widening the key to the data directory would sweep an
unrelated population under one name -- the registry file's own path among them,
which :class:`~theurian.application.project_service.ProjectRegistry`
interpolates into several refusals. What makes that affordable is a recorded
design decision and not an argument from this file: ``/health`` publishes
``dataDir`` to an **unauthenticated** local caller by design, measured and
deferred in the threat model's T-2 (*A web page reaches the daemon via DNS
rebinding*), so the data directory is not a string an MCP caller has to reach a
refusal to learn. The exclusion's safety is contingent on that: if ``dataDir``
is ever fingerprinted rather than published -- the option T-2 records for
whoever takes it -- the data directory becomes withheld, and this key has to
grow to cover it.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application import project_service
from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    REVIEW_SEARCH_STORE_FILENAME,
    REVIEW_SEARCH_STORE_ID,
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
from theurian.domain.review_search import ReviewSearchLoad
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore
from theurian.mcp.search import INDEX_POINTER_INVALID
from theurian.mcp.tools import (
    _CUT_MARKER,
    PATH_ESCAPE_REFUSAL,
    REVIEW_SEARCH_UNAVAILABLE_REFUSAL,
    ToolError,
    _publishable_field,
)

pytestmark = pytest.mark.integration

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

PROJECT_ID: Final = "demo"
ITEM_ID: Final = "architecture.auth-policy"

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
#: The build the escaping index pointer claims to publish. Never read -- the path
#: refuses before the open -- but written so the plant's premise is containment
#: rather than a pointer that cannot be parsed.
INDEX_BUILD_ID: Final = "01K1AAANDX01234567890ABCDE"
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
    # under the common parent shares that character. That check lives in
    # `_candidates`, not here: this fixture runs before any plant does, so a
    # plant creating a sibling would invalidate the key after this line passed.

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

    **The key's soundness is re-checked here, per call, and that is a move**
    (round one, code review MEDIUM). The divergence is one character wide, so it
    means "the project's resolved layout" only while nothing else under the
    common parent shares that character. The check used to sit in the
    :func:`corpus` fixture, which runs *before* any plant does: a plant creating
    a sibling under the temporary tree invalidated the key silently, after the
    fixture's version had already passed. Asked here, the plant's own
    directories are on disk and counted.
    """
    parent = corpus.elsewhere.parent
    diverging = sorted(
        path.name for path in parent.iterdir() if path.name.startswith(corpus.elsewhere.name[:1])
    )
    assert diverging == [corpus.elsewhere.name], (
        f"the population key `{corpus.divergence}` matches more than the physical home, "
        f"so a hit no longer means the project's resolved layout: {diverging}"
    )

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


async def _sweep_for_leaks(server: Any, candidates: dict[str, str], *, case: str) -> dict[str, str]:
    """Call every registered tool and report which candidates the responses named.

    The sweep's whole body, in a function, for the reason :func:`_layout_hits` is
    one: the pipeline control below has to drive the *same* three steps the sweep
    runs -- the arguments derived from each tool's own schema, the call, and the
    scan over what came back -- or it is keeping a re-implementation honest while
    the shipped one is blinded. Blinding :func:`_arguments_for` or
    :func:`_response_text` now takes the control down with the sweep.

    Every cell is collected before anything is asserted, so one run reports the
    whole matrix -- which is what says whether a leak is one raise site or
    several.
    """
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this sweep vacuously"

    leaked: dict[str, str] = {}
    for tool in tools:
        response = await _response_text(server, tool.name, _arguments_for(tool))
        for label, seen in _layout_hits(candidates, response).items():
            leaked[f"{case} x {tool.name} -- {label}"] = seen
    return leaked


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


def _plant_escaping_review_search_leaf(corpus: Corpus) -> PlantedPaths:
    """The review search **store leaf** delivered as a link out of the tree.

    The findings plant's twin at the tool beside it, and it is a *different raise
    site*, which is why it is a plant of its own rather than a second case of the
    same one. ``findings_for`` routes through ``ProjectPaths._contained`` and so
    raises ``ProjectPathEscapeError``; ``review_search_for`` runs its own
    state-scoped check and raises the plain ``ProjectError`` beneath it. That
    message used to interpolate the resolved ``.theurian/state`` directory and is
    now built from the store's file name and a project-relative literal, which is
    why it left this file's population and took its disposition into
    :data:`DEPARTED`. The plant stays: a refusal that is layout-free *by
    construction* is a claim about the source, and this is what still reads the
    wire.

    The store is built and recorded here rather than in the fixture: it is the
    only plant that needs one, and a fourth derived database in every corpus
    would be paid for by every other case.

    **Provenance first, or the call never reaches the path.** ``review.search``
    checks its build record before it asks for the store path at all (ADR-0004,
    SEC-7, T-19), so without the record the tool refuses at the gate above and
    this plant sweeps the absent-store arm while looking like a containment one.
    """
    paths = corpus.paths
    leaf = paths.review_search_for(REVIEW_SEARCH_STORE_ID)
    SqliteReviewSearchStore(leaf).replace_all(ReviewSearchLoad(records=()))
    BuildProvenance.for_registry(corpus.registry).record_review(paths.root, REVIEW_SEARCH_STORE_ID)

    target_dir = corpus.elsewhere / "escaped-review-search"
    target_dir.mkdir()
    target = target_dir / leaf.name
    leaf.rename(target)
    leaf.symlink_to(target)

    assert not leaf.resolve().is_relative_to(paths.root), (
        "the premise: the review search leaf must really resolve outside the project "
        "root, or the containment refusal this plant drives never fires"
    )
    assert paths.state.resolve().is_relative_to(paths.root), (
        "the premise: `.theurian/state` itself is healthy, so what refuses is "
        "`review_search_for`'s own check and not `_resolve` a layer above it"
    )
    assert BuildProvenance.for_registry(corpus.registry).has_review(
        paths.root, REVIEW_SEARCH_STORE_ID
    ), (
        "the premise: this installation's build record covers the store, so the call "
        "reaches the containment check rather than stopping at the provenance gate"
    )
    return {"the directory the review search leaf escaped to": target_dir}


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


def _plant_escaping_index_pointer(corpus: Corpus) -> PlantedPaths:
    """``.theurian/state/active-index.json`` delivered as a link out of the tree.

    The raise site that was still open when this file was written (GHSA-97q9,
    round one, security HIGH). ``read_active_index_pointer`` resolves the pointer
    *before* it has a file to probe, so the containment refusal leaves it by
    design -- and ``knowledge.search`` has no ``except`` in front of that read, so
    the refusal crossed at ``_forwarding``, the one tool boundary that had no
    substitution, carrying ``_contain``'s absolute leaf and resolved root whole.

    **What it drives now is the degrade, not the refusal.**
    :func:`~theurian.mcp.search._published_index` converts the refusal to
    ``index-pointer-invalid``, so ``knowledge.search`` *serves* a substring
    fallback here rather than refusing, and the sweep reads a served payload for
    this plant.
    :func:`test_an_escaping_index_pointer_is_served_rather_than_refused` is what
    pins that, because the sweep asserts an absence and an absence is equally
    satisfied by a refusal.

    Created at the target rather than moved there, because this corpus never ran
    ``index build`` and so has no pointer to move -- which is the same shape a
    clone delivers, a link committed where no local build ever wrote. The
    payload is a well-formed pointer so that what refuses is containment rather
    than an unreadable file; nothing reads it, because the path refuses before
    the open.
    """
    paths = corpus.paths
    # Derived before the plant lands, for `_plant_escaping_knowledge_directory`'s
    # reason: `active_index_pointer` is itself a contained path, so asking for it
    # after the link is in place raises here instead of inside the tool.
    pointer = paths.active_index_pointer
    target_dir = corpus.elsewhere / "escaped-index-pointer"
    target_dir.mkdir()
    target = target_dir / pointer.name
    target.write_text(
        json.dumps({"indexBuildId": INDEX_BUILD_ID, "projectId": PROJECT_ID}), encoding="utf-8"
    )
    pointer.symlink_to(target)

    assert not pointer.resolve().is_relative_to(paths.root), (
        "the premise: the index pointer must really resolve outside the project root, or "
        "the containment refusal this plant drives never fires"
    )
    assert target.is_file(), (
        "the premise: the pointer the link names is really there, so what refuses is "
        "containment and not a pointer that is simply gone"
    )
    return {"the directory the index pointer escaped to": target_dir}


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
        "escaping-review-search-leaf",
        _plant_escaping_review_search_leaf,
        "`ProjectPaths.review_search_for`'s own state-scoped check, reached from "
        "`review.search`' body -- a plain `ProjectError`, published by that guard as its "
        "own message beside its own cure",
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
        "escaping-index-pointer",
        _plant_escaping_index_pointer,
        "`ProjectPaths._contained`, reached from `knowledge.search`'s index-pointer read "
        "-- converted to a served fallback at `mcp.search._published_index`",
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
    is asserted is what came back, not why.

    The plants are not a list someone remembered to extend, and that is
    :func:`test_every_resolved_path_refusal_has_a_recorded_disposition`'s to
    hold: it derives the raise sites that could put a resolved path here and
    requires each one to name the plant, guard or conversion that covers it.
    """
    candidates = _candidates(corpus, plant.apply(corpus))

    server = build_server(corpus.registry)

    leaked = await _sweep_for_leaks(server, candidates, case=plant.key)

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
async def test_an_escaping_index_pointer_is_served_rather_than_refused(corpus: Corpus) -> None:
    """The degrade the pointer plant's cell is a *served* payload because of.

    The sweep asserts an absence, and a refusal satisfies it as well as an answer
    does -- so a regression that turned this back into a refusal would leave the
    sweep green while breaking the promise ADR-0004 makes about a derived
    artefact: every problem with the index is a missing optimisation, and the
    caller answers without one. That promise is what
    :func:`~theurian.mcp.search._published_index`'s conversion exists for, and
    what the ``except ProjectPathEscapeError`` in it would silently stop keeping.

    Four things are asserted because each fails on its own: the call answered
    rather than raising, the answer is not an error result, the reason given is
    ``index-pointer-invalid`` rather than one of the seven neighbouring fallbacks,
    and the degraded scan really returned the migrated row rather than an empty
    result with a tidy reason attached.

    Read through the sweep's own :func:`_response_text`, so this is measuring the
    same bytes the pointer plant's cells read rather than a friendlier second
    call.
    """
    _plant_escaping_index_pointer(corpus)
    server = build_server(corpus.registry)

    response = await _response_text(
        server, "knowledge.search", {"projectId": PROJECT_ID, "query": ARGUMENT_VALUES["query"]}
    )

    # `_response_text` renders a raised refusal as `"<type>: <message>"`, which is
    # not JSON; a result -- error or not -- is the dumped model.
    assert response.startswith("{"), (
        f"`knowledge.search` raised on an escaping index pointer instead of degrading "
        f"past it, so a derived artefact is failing the whole query (ADR-0004): {response}"
    )
    served = json.loads(response)
    assert served["is_error"] is False, (
        f"`knowledge.search` answered with an error result rather than a degraded "
        f"answer: {response}"
    )
    assert served["structured_content"]["retrieval"]["fallbackReason"] == INDEX_POINTER_INVALID, (
        f"the degraded answer did not name the pointer as the reason, so the conversion "
        f"at `_published_index` is not the branch that produced it: {response}"
    )
    assert [row["itemId"] for row in served["structured_content"]["results"]] == [ITEM_ID], (
        f"the substring fallback returned no row, so `fallbackReason` is describing a "
        f"degrade that served nothing: {response}"
    )


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
async def test_an_escaping_review_search_leaf_is_refused_by_the_raise_site_this_plant_aims_at(
    corpus: Corpus,
) -> None:
    """What the review-search plant's cell is an *absence* over, said positively.

    The sweep asserts that no response names the resolved layout, and a plant that
    never reaches its raise site satisfies that perfectly. This is what stops the
    plant from being a corpus the sweep walks past: the call must refuse, the
    refusal must be ``review_search_for``'s own -- it names the store file, which
    no other refusal on this path does -- and not ``PATH_ESCAPE_REFUSAL``, which
    is what ``_with_remedy`` substitutes for the ``ProjectPathEscapeError`` beside
    it and would mean this plant is driving the subclass. Nor may it be the
    provenance gate's refusal reached a step early, which the plant's own premise
    rules out and which the availability constant is now the sole mark of.

    Read through the sweep's own :func:`_response_text`, so this measures the same
    bytes the plant's cells read rather than a friendlier second call.
    """
    _plant_escaping_review_search_leaf(corpus)
    server = build_server(corpus.registry)

    response = await _response_text(server, "review.search", {"projectId": PROJECT_ID})

    assert REVIEW_SEARCH_STORE_FILENAME in response, (
        f"the escaping review search leaf did not reach `review_search_for`'s own "
        f"check, so the sweep's cell for this plant asserts an absence over a call that "
        f"never got there: {response}"
    )
    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL not in response, (
        f"the refusal was folded into the availability constant, whose cure meets this "
        f"same fault first -- and the plant would then be sweeping the provenance gate's "
        f"words rather than the containment refusal's: {response}"
    )
    assert PATH_ESCAPE_REFUSAL not in response, (
        f"the leaf took the escape class's substitution -- so this plant is driving "
        f"`ProjectPathEscapeError` and `review_search_for`'s own plain `ProjectError` is "
        f"still reached by nothing: {response}"
    )
    assert str(corpus.paths.state) not in response, (
        f"the refusal carried the resolved `.theurian/state` directory that "
        f"`review_search_for`'s message used to interpolate, which is the disclosure "
        f"that departure closed (GHSA-97q9): {response}"
    )


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


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
async def test_the_sweep_reports_a_leak_a_registered_tool_really_published(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control over the **pipeline**, not over the scan (round one, adversarial).

    :func:`test_the_layout_scan_finds_a_planted_disclosure` keeps
    :func:`_layout_hits` honest by handing it a string. That leaves the two steps
    in front of it uncontrolled, and either one blinds the sweep while every test
    in this file stays green: :func:`_response_text` returning ``""`` makes every
    cell clean, and :func:`_arguments_for` returning ``{}`` makes every call fail
    schema validation before it reaches a project. The disclosure the sweep
    exists to catch would be restorable behind either.

    So this drives the whole pipeline against a leak that is really there: one
    registered tool is made to refuse with the resolved project root, and the
    sweep's own :func:`_sweep_for_leaks` must report it -- under every candidate
    key, because the root contains all of them, and under that tool's name alone,
    because the rest of the corpus is healthy.

    The victim is chosen by sorted name rather than by position, so the cell this
    reports is the same one on every run and on every machine.
    """
    server = build_server(corpus.registry)
    victim = sorted(tool.name for tool in server._tool_manager.list_tools())[0]
    tool = server._tool_manager.get_tool(victim)
    assert tool is not None, f"the premise: `{victim}` came from this server's own tool list"

    def leak(**_: object) -> dict[str, str]:
        raise ToolError(str(corpus.physical))

    async def leak_async(**_: object) -> dict[str, str]:
        raise ToolError(str(corpus.physical))

    # Matched to the registration the SDK made, because `call_fn` awaits an async
    # tool's return value and calls a sync one on a worker thread; the wrong
    # shape here would fail for a reason that has nothing to do with the sweep.
    monkeypatch.setattr(tool, "fn", leak_async if tool.is_async else leak)

    candidates = _candidates(corpus, {})
    leaked = await _sweep_for_leaks(server, candidates, case="planted")

    assert set(leaked) == {f"planted x {victim} -- {label}" for label in candidates}, (
        f"the sweep did not report a resolved project root that a registered tool "
        f"really published, so its absence result is not evidence of anything. "
        f"Reported: {sorted(leaked)}"
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

    The two directories *are* created, because :func:`_candidates` now checks the
    divergence key against what is on disk beside the physical home. Making them
    is what puts these tests under the same soundness check the sweep runs, which
    is the point of asking it there rather than in the fixture.
    """
    physical = tmp_path / "opaque-elsewhere" / "real-demo"
    physical.mkdir(parents=True)
    return Corpus(
        registry=ProjectRegistry.default(tmp_path / "datadir"),
        registered=tmp_path / PROJECT_ID,
        physical=physical,
        elsewhere=physical.parent,
    )


def test_the_layout_scan_finds_a_planted_disclosure(tmp_path: Path) -> None:
    """The positive control. A sweep that reports absence must be able to report presence.

    The sweep's whole output is "nothing found", and a scan that could never find
    anything produces that perfectly. This plants the resolved layout in a
    response-shaped string and requires every key to name it.

    **The sample sentence is the pre-fix wording, kept deliberately as a shape
    known to disclose.** No production refusal interpolates a path like this any
    more: since ``74e32a21`` ``verify_state_provenance`` raises the constant
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


# -- The plant axis: a derived population, not a hand-written five --------------
#
# The sweep above walks every tool the server registers, so a tool added later
# joins it by existing. Its plants had no such property: they were five functions
# someone wrote, and a raise site that could put a resolved path on this wire but
# that no plant reaches was swept by nothing at all. That is how the
# `active-index.json` face reached a release (GHSA-97q9, round one) -- the site
# was there, the sweep was green, and no plant went near it.
#
# What follows derives the *candidate* sites from the source and requires each one
# to name what covers it. It cannot prove a site is safe; it makes an uncovered
# site impossible to leave unnoticed, which is the property the plants were
# claiming and did not have.


@dataclass(frozen=True)
class RaiseSite:
    """One ``raise SomethingError(f"...")`` that interpolates a resolved path.

    Identified by enclosing function and ordinal rather than by line number:
    every edit to a docstring in ``project_service.py`` moves every line below
    it, and a population keyed on line numbers would be a population that has to
    be rewritten to stay green.
    """

    module: str
    qualname: str
    ordinal: int
    interpolated: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.module}::{self.qualname}#{self.ordinal}"


@dataclass(frozen=True)
class Disposition:
    """What keeps one raise site's message off an MCP response.

    ``because`` is the argument. The fields under it are the parts of that
    argument this file can *check*, so a citation cannot rot into a sentence
    naming a plant that was renamed or a test that was deleted:

    ``plants``
        keys in :data:`PLANTS`. The sweep drives this site.
    ``tests``
        ``<path under tests/>::<test function>``. Another test holds it.
    ``converter``
        ``<importable module>:<attribute>``. The refusal is converted before it
        can reach a response, at the named function.
    ``no_call_from_mcp`` / ``no_method_call_from_mcp``
        a callable name that must appear in no call under ``theurian/mcp/`` or
        ``theurian/daemon/`` -- in any form, or in the ``receiver.name(...)``
        form respectively. The second spelling exists because ``mcp/tools.py``
        defines a ``register`` of its own that ``daemon/runner.py`` calls by bare
        name, which says nothing about
        :meth:`~theurian.application.project_service.ProjectRegistry.register`.
    """

    because: str
    plants: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    converter: str | None = None
    no_call_from_mcp: str | None = None
    no_method_call_from_mcp: str | None = None


#: The tool boundaries' substitution is keyed on the exception *type*, and both
#: replace the whole message rather than editing it. Cited by the two arms below
#: that no input drives: an arm that cannot be reached by data is still covered,
#: because nothing about the substitution depends on which arm produced the text.
_TYPE_KEYED_SUBSTITUTION: Final = (
    "integration/test_escaping_knowledge_dir_grading.py"
    "::test_the_mcp_surface_publishes_the_cure_for_an_escaping_knowledge_directory",
    "unit/test_tool_error_type_contract.py"
    "::test_a_containment_refusal_crosses_the_seam_as_the_constant_and_nothing_else",
)

#: One entry per member of the population below. A site with no entry reddens
#: :func:`test_every_resolved_path_refusal_has_a_recorded_disposition`, which is
#: the whole point: the next raise site that interpolates a resolved path has to
#: be given an answer here before this suite is green again.
DISPOSITIONS: Final[dict[str, Disposition]] = {
    "application/project_service.py::_contain#1": Disposition(
        because=(
            "`_contain`'s unresolvable-path arm, which no plant drives and which needs "
            "none. Every symlink planted in this file resolves without raising, so the "
            "escape arm below is the one they take; this arm is the contract guarantee "
            "its own comment describes. Both arms raise `ProjectPathEscapeError`, and "
            "both tool boundaries substitute `PATH_ESCAPE_REFUSAL` for the *whole* "
            "message on that type -- never on its text -- so which arm wrote the text "
            "cannot change what crosses."
        ),
        tests=_TYPE_KEYED_SUBSTITUTION,
    ),
    "application/project_service.py::_contain#2": Disposition(
        because=(
            "`_contain`'s escape arm -- the one a committed symlink under `.theurian` "
            "reaches. Three plants drive it, at three different depths: the findings "
            "store leaf, the `.theurian/state` directory, and the index pointer."
        ),
        plants=("escaping-findings-leaf", "escaping-state-directory", "escaping-index-pointer"),
    ),
    "application/project_service.py::ProjectPaths.of#1": Disposition(
        because=(
            "`of`'s unresolvable-join arm, covered by the type-keyed substitution for "
            "`_contain#1`'s reason. Unlike that one it does have a driving test for the "
            "refusal itself -- an embedded NUL makes `resolve` raise -- but that test is "
            "about the type and the remedy, not about the tool boundary."
        ),
        tests=(
            *_TYPE_KEYED_SUBSTITUTION,
            "unit/test_project_paths_containment.py"
            "::test_a_knowledge_directory_that_will_not_resolve_is_a_project_error_not_a_valueerror",
        ),
    ),
    "application/project_service.py::ProjectPaths.of#2": Disposition(
        because=(
            "`of`'s join check -- `.theurian` itself delivered as a link out of the tree "
            "(#237, #550). Refused before a single derived path is built, so it reaches "
            "every project-scoped tool at once."
        ),
        plants=("escaping-knowledge-directory",),
    ),
    "application/project_service.py::ProjectPaths.index_for#1": Disposition(
        because=(
            "Converted before it can reach a response: `_searchable_file` answers every "
            "`TheurianError` from `index_for` with the `index-pointer-invalid` fallback "
            "and drops the refusal's own text, because `active-index.json` is derived "
            "and a caller gets a degraded answer rather than a failure (ADR-0004)."
        ),
        converter="theurian.mcp.search:_searchable_file",
        tests=(
            "integration/test_index_fallback.py"
            "::test_a_rejected_pointer_does_not_echo_the_path_it_rejected",
        ),
    ),
    "application/project_service.py::ProjectPaths.state_database_named#1": Disposition(
        because=(
            "Held by the state-database envelope: `active.json`'s `databaseFilename` is "
            "taken verbatim from a file any local process can edit (SEC-7), and the "
            "refusal for a name that escapes `.theurian/state/` is answered on the MCP "
            "surface without its text."
        ),
        tests=(
            "integration/test_derived_state_value_envelope.py"
            "::test_the_mcp_surface_refuses_a_state_database_outside_the_state_directory",
        ),
    ),
    "application/project_service.py::initialize_project#1": Disposition(
        because=(
            "Outside the MCP surface. `initialize_project` creates the `.theurian/` "
            "layout and is `theurian init`'s only consumer; no module under "
            "`theurian/mcp/` or `theurian/daemon/` calls it, which is checked rather "
            "than asserted. Its refusal is graded on a terminal, where the reader owns "
            "the checkout and the path is not a disclosure."
        ),
        no_call_from_mcp="initialize_project",
    ),
    "application/project_service.py::ProjectRegistry.ids_for_root#1": Disposition(
        because=(
            "Outside the MCP surface. `ids_for_root` answers *which project is this "
            "directory*, a question only a caller standing in a directory has -- "
            "`cli/context.py`, `cli/commands.py` and the `setup` steps. The daemon is "
            "addressed by project id and never resolves one from a working directory."
        ),
        no_method_call_from_mcp="ids_for_root",
    ),
    "application/project_service.py::ProjectRegistry.ids_for_root#2": Disposition(
        because="The second refusal of the same method; the same reason covers it.",
        no_method_call_from_mcp="ids_for_root",
    ),
    "application/project_service.py::ProjectRegistry.id_for_root#1": Disposition(
        because=(
            "Outside the MCP surface, for `ids_for_root`'s reason -- it is that method's caller."
        ),
        no_method_call_from_mcp="id_for_root",
    ),
    "application/project_service.py::ProjectRegistry.register#1": Disposition(
        because=(
            "Outside the MCP surface. Registration is a write, and ADR-0013 records "
            "that no write-intent MCP tool is registered yet; `theurian project "
            "register` is the caller. Checked in the `receiver.register(...)` form on "
            "purpose: `mcp/tools.py` defines a `register` of its own, which "
            "`daemon/runner.py` calls by bare name, and a name-only check would read "
            "that as a hit."
        ),
        no_method_call_from_mcp="register",
    ),
}

#: Sites that **left** the population, and what holds them now. The key is a
#: property of the source at scan time, so a fix can take a member out of it --
#: that is the fix working, not the record going stale. Each entry is asserted
#: *absent* from the measured population, so re-introducing an interpolation
#: reddens rather than quietly re-joining a set nobody re-reads.
DEPARTED: Final[dict[str, Disposition]] = {
    "application/project_service.py::ProjectPaths.review_search_for#1": Disposition(
        because=(
            "Left the key when the state-scoped check's refusal stopped interpolating "
            "the resolved `.theurian/state` and began naming the store's own file "
            "beside the project-relative `.theurian/state/`. It still raises and the "
            "`escaping-review-search-leaf` plant still drives it -- what it no longer "
            "does is interpolate a path. That departure is what let `review.search`'s "
            "guard stop folding this refusal into `REVIEW_SEARCH_UNAVAILABLE_REFUSAL`: "
            "the fold suppressed the layout and the cure alike, and the constant's own "
            "cure -- `theurian review build` -- resolves the same leaf through the same "
            "helper and exits 1 on it, so a caller who ran it came back to a "
            "byte-identical refusal."
        ),
        plants=("escaping-review-search-leaf",),
        tests=(
            "integration/test_review_search_tool.py"
            "::test_an_escaping_store_leaf_publishes_its_own_refusal_and_the_cure_that_clears_it",
            "integration/test_published_cures_are_executable.py"
            "::test_a_published_cure_moves_the_caller_off_the_refusal_that_published_it",
        ),
    ),
    "application/project_service.py::verify_state_provenance#1": Disposition(
        because=(
            "Left the key at 74e32a21, where the refusal became the module constant "
            "`_UNBUILT_STATE_REFUSAL` and named only the project-relative "
            "`.theurian/state/`. It still raises, and the `delivered-state` plant still "
            "drives it -- what it no longer does is interpolate a path."
        ),
        plants=("delivered-state",),
    ),
}


#: Where the scan runs. Derived from the imported package, so it is the source
#: this test run is actually executing rather than a path spelled out here.
_PACKAGE_ROOT: Final = Path(project_service.__file__).parent.parent
#: ``cli/`` is excluded because the class is about what crosses the *MCP* wire.
#: A CLI refusal names paths on purpose: its reader owns the checkout, and
#: ``_fail_a_path_escape`` publishes the message whole for exactly that reason.
_EXCLUDED_PACKAGE: Final = "cli"
#: Where a site's disposition may claim nothing reaches it from.
_SERVING_PACKAGES: Final = ("mcp", "daemon")
#: The tests these dispositions may cite.
_TESTS_ROOT: Final = Path(__file__).parent.parent

#: Annotations whose ``str()`` is a whole filesystem location. A ``str`` field
#: holding a *name* is not one of these, which is what keeps
#: ``self._path.name`` -- a basename, published deliberately in several store
#: refusals -- out of the population.
_PATH_TYPES: Final = frozenset({"Path", "PurePath", "PurePosixPath", "PureWindowsPath"})
#: Calls that answer with a *resolved* path.
_RESOLVING_CALLS: Final = frozenset({"resolve", "absolute"})
#: Calls on a path that answer with another path.
_PATH_CALLS: Final = _RESOLVING_CALLS | {
    "expanduser",
    "joinpath",
    "with_name",
    "with_suffix",
    "relative_to",
}
#: Attributes of a path that are themselves paths. ``name``, ``stem`` and
#: ``suffix`` are deliberately not here: they are strings naming a component,
#: and a component is not the layout.
_PATH_ATTRIBUTES: Final = frozenset({"parent"})
#: The class every one of whose paths is built from an already-resolved root:
#: ``ProjectPaths.of`` resolves ``root`` and derives ``knowledge_dir`` from it,
#: so anything reached through an instance carries the resolved form.
_RESOLVED_BY_CONSTRUCTION: Final = "ProjectPaths"


def _annotation_names(node: ast.expr | None) -> frozenset[str]:
    """Every identifier an annotation mentions, so ``Path | None`` reads as Path."""
    if node is None:
        return frozenset()
    return frozenset(
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Name | ast.Attribute)
    )


class _Types:
    """What each class in the scanned source says its attributes answer with.

    Built from annotations only -- dataclass fields, ``@property`` returns and
    method returns -- because an annotation is the source's own statement about
    the value, and reading it is what lets ``self.state`` and ``paths.state`` be
    recognised as paths without a list of blessed attribute names here.
    """

    def __init__(self) -> None:
        self.classes: dict[str, dict[str, frozenset[str]]] = {}
        self.functions: dict[str, frozenset[str]] = {}

    def add_module(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                table = self.classes.setdefault(node.name, {})
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        table[stmt.target.id] = _annotation_names(stmt.annotation)
                    elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                        table[stmt.name] = _annotation_names(stmt.returns)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self.functions[node.name] = _annotation_names(node.returns)


class _Scope:
    """The names one function body binds, and what each was bound from."""

    def __init__(self, fn: ast.FunctionDef | ast.AsyncFunctionDef | None, cls: str | None) -> None:
        self.cls = cls
        self.annotations: dict[str, frozenset[str]] = {}
        self.bound: dict[str, ast.expr] = {}
        if fn is None:
            return
        args = fn.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self.annotations[arg.arg] = _annotation_names(arg.annotation)
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.bound.setdefault(target.id, node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.annotations.setdefault(node.target.id, _annotation_names(node.annotation))
                if node.value is not None:
                    self.bound.setdefault(node.target.id, node.value)


def _stated_class(types: _Types, owner: str | None, attribute: str) -> str | None:
    """The class one attribute of ``owner`` is annotated as answering with."""
    for name in types.classes.get(owner or "", {}).get(attribute, frozenset()):
        if name in types.classes:
            return name
    return None


def _owner_class(expr: ast.expr, types: _Types, scope: _Scope, seen: frozenset[str]) -> str | None:
    """Which class in the scanned source an expression is an instance of, if stated.

    Only ever as much as the source itself says: a parameter's annotation, a
    constructor, ``self``, or a method whose return annotation names a class.
    Anything less definite answers ``None``, which keeps the population from
    growing by guesswork.
    """
    if isinstance(expr, ast.Name):
        return _owner_of_name(expr, types, scope, seen)
    if isinstance(expr, ast.Attribute):
        return _stated_class(types, _owner_class(expr.value, types, scope, seen), expr.attr)
    if isinstance(expr, ast.Call):
        return _owner_of_call(expr, types, scope, seen)
    return None


def _owner_of_name(
    expr: ast.Name, types: _Types, scope: _Scope, seen: frozenset[str]
) -> str | None:
    """:func:`_owner_class` for a bare name: ``self``, an annotation, or a binding."""
    if expr.id == "self":
        return scope.cls
    for name in scope.annotations.get(expr.id, frozenset()):
        if name in types.classes:
            return name
    bound = scope.bound.get(expr.id)
    if bound is None or expr.id in seen:
        return None
    return _owner_class(bound, types, scope, seen | {expr.id})


def _owner_of_call(
    expr: ast.Call, types: _Types, scope: _Scope, seen: frozenset[str]
) -> str | None:
    """:func:`_owner_class` for a call: a constructor, a classmethod, or a method."""
    func = expr.func
    if isinstance(func, ast.Name):
        return func.id if func.id in types.classes else None
    if not isinstance(func, ast.Attribute):
        return None
    if isinstance(func.value, ast.Name) and func.value.id in types.classes:
        # `ProjectPaths.of(...)`: its return annotation if that names a class,
        # and otherwise the class it was called on -- which is what an alternate
        # constructor answers with.
        return _stated_class(types, func.value.id, func.attr) or func.value.id
    return _stated_class(types, _owner_class(func.value, types, scope, seen), func.attr)


def _path_facts(
    expr: ast.expr, types: _Types, scope: _Scope, seen: frozenset[str] = frozenset()
) -> tuple[bool, bool]:
    """``(is a whole path, is a resolved one)`` for one interpolated expression.

    Derived from the source's own annotations and calls rather than from a list
    of names, so a refusal that starts interpolating a new path-valued helper
    joins the population without this function being taught about it. Both halves
    are needed: the class is about the **resolved** layout, and an unresolved
    ``root`` is the registered spelling ``project.list`` publishes by design.
    """
    if isinstance(expr, ast.Name):
        return _name_facts(expr, types, scope, seen)
    if isinstance(expr, ast.Attribute):
        return _attribute_facts(expr, types, scope, seen)
    if isinstance(expr, ast.Call):
        return _call_facts(expr, types, scope, seen)
    if isinstance(expr, ast.BoolOp | ast.IfExp):
        branches = expr.values if isinstance(expr, ast.BoolOp) else [expr.body, expr.orelse]
        facts = [_path_facts(value, types, scope, seen) for value in branches]
        return any(is_path for is_path, _ in facts), any(resolved for _, resolved in facts)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        # `<path> / "child"` is the join, and the left operand decides both.
        return _path_facts(expr.left, types, scope, seen)
    return False, False


def _name_facts(
    expr: ast.Name, types: _Types, scope: _Scope, seen: frozenset[str]
) -> tuple[bool, bool]:
    """:func:`_path_facts` for a bare name: its annotation, then its binding."""
    bound = scope.bound.get(expr.id)
    rebound = None if bound is None or expr.id in seen else bound
    if _PATH_TYPES & scope.annotations.get(expr.id, frozenset()):
        if rebound is None:
            # An annotated parameter nothing re-bound: a path, and nothing here
            # says it is a resolved one. `project.list` publishes an unresolved
            # `rootPath` by design, so this half has to stay narrow.
            return True, False
        return True, _path_facts(rebound, types, scope, seen | {expr.id})[1]
    if rebound is None:
        return False, False
    return _path_facts(rebound, types, scope, seen | {expr.id})


def _attribute_facts(
    expr: ast.Attribute, types: _Types, scope: _Scope, seen: frozenset[str]
) -> tuple[bool, bool]:
    """:func:`_path_facts` for ``owner.attribute``."""
    owner = _owner_class(expr.value, types, scope, seen)
    stated = types.classes.get(owner or "", {}).get(expr.attr, frozenset())
    if _PATH_TYPES & stated:
        return True, owner == _RESOLVED_BY_CONSTRUCTION
    if expr.attr in _PATH_ATTRIBUTES:
        return _path_facts(expr.value, types, scope, seen)
    return False, False


def _call_facts(
    expr: ast.Call, types: _Types, scope: _Scope, seen: frozenset[str]
) -> tuple[bool, bool]:
    """:func:`_path_facts` for a call, split so either half stays readable."""
    func = expr.func
    if isinstance(func, ast.Name):
        return _function_call_facts(func, expr, types, scope, seen)
    if isinstance(func, ast.Attribute):
        return _method_call_facts(func, types, scope, seen)
    return False, False


def _function_call_facts(
    func: ast.Name, expr: ast.Call, types: _Types, scope: _Scope, seen: frozenset[str]
) -> tuple[bool, bool]:
    """:func:`_path_facts` for ``helper(...)`` -- a constructor or a free function."""
    if func.id in _PATH_TYPES:
        return True, False
    if not _PATH_TYPES & types.functions.get(func.id, frozenset()):
        return False, False
    # A module-level helper answering with a path -- `_contain` is the one this
    # codebase has -- passes its argument's resolvedness through, because what
    # it answers with is one of the paths it was handed.
    return True, any(_path_facts(argument, types, scope, seen)[1] for argument in expr.args)


def _method_call_facts(
    func: ast.Attribute, types: _Types, scope: _Scope, seen: frozenset[str]
) -> tuple[bool, bool]:
    """:func:`_path_facts` for ``receiver.method(...)``."""
    if func.attr in _PATH_CALLS:
        is_path, resolved = _path_facts(func.value, types, scope, seen)
        return is_path, is_path and (func.attr in _RESOLVING_CALLS or resolved)
    if isinstance(func.value, ast.Name) and func.value.id in types.classes:
        stated = types.classes[func.value.id].get(func.attr, frozenset())
        return bool(_PATH_TYPES & stated), func.value.id == _RESOLVED_BY_CONSTRUCTION
    owner = _owner_class(func.value, types, scope, seen)
    stated = types.classes.get(owner or "", {}).get(func.attr, frozenset())
    return bool(_PATH_TYPES & stated), owner == _RESOLVED_BY_CONSTRUCTION


def _scanned_modules() -> dict[str, ast.Module]:
    """Every module the key ranges over, keyed by its path under the package."""
    modules: dict[str, ast.Module] = {}
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(_PACKAGE_ROOT)
        if relative.parts[0] == _EXCLUDED_PACKAGE:
            continue
        modules[relative.as_posix()] = ast.parse(path.read_text(encoding="utf-8"))
    return modules


def _resolved_path_raise_sites() -> tuple[RaiseSite, ...]:
    """Every ``raise SomethingError(f"...")`` that puts a resolved path in its message.

    The key, stated plainly: a ``raise`` of a class whose name ends in ``Error``,
    whose first argument is an f-string, one of whose replacement fields has no
    ``!r`` conversion -- so ``str()`` of the value is what lands in the message --
    and whose expression is a **resolved** whole path by the source's own
    annotations.

    ``!r`` is what separates ``index_for``'s two arms: *"names {index_build_id!r},
    which is not a usable filename"* quotes a caller-supplied string and no path,
    while *"resolves outside {state}"* interpolates the resolved state directory.
    """
    types = _Types()
    modules = _scanned_modules()
    for tree in modules.values():
        types.add_module(tree)

    return tuple(
        site for module, tree in modules.items() for site in _sites_in(module, tree, types)
    )


def _sites_in(module: str, tree: ast.Module, types: _Types) -> tuple[RaiseSite, ...]:
    """The key applied to one module, so a control can hand it a synthetic one.

    Split from :func:`_resolved_path_raise_sites` for the reason
    :func:`_layout_hits` is split from the sweep: a key asserted in one place and
    exercised in another proves nothing about the key that ships.
    """
    sites: list[RaiseSite] = []
    counts: dict[str, int] = {}
    for qualname, cls, fn, node in _raises(tree):
        call = node.exc
        assert isinstance(call, ast.Call)  # `_raises` yields only these
        template = call.args[0]
        assert isinstance(template, ast.JoinedStr)  # likewise
        scope = _Scope(fn, cls)
        interpolated = []
        for value in template.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            if value.conversion != -1 or value.format_spec is not None:
                continue
            is_path, resolved = _path_facts(value.value, types, scope)
            if is_path and resolved:
                interpolated.append(ast.unparse(value.value))
        if not interpolated:
            continue
        counts[qualname] = counts.get(qualname, 0) + 1
        sites.append(RaiseSite(module, qualname, counts[qualname], tuple(interpolated)))
    return tuple(sites)


def _raises(
    tree: ast.Module,
) -> Iterator[tuple[str, str | None, ast.FunctionDef | ast.AsyncFunctionDef | None, ast.Raise]]:
    """Each ``raise <Something>Error(f"...")`` with the class and function around it.

    Walked with the enclosing scope carried down rather than recovered afterwards,
    because the qualified name is the population's identity and a raise inside a
    nested helper must not be attributed to the function that contains it.
    """

    def walk(
        node: ast.AST,
        cls: str | None,
        fn: ast.FunctionDef | ast.AsyncFunctionDef | None,
        qualname: str,
    ) -> Iterator[tuple[str, str | None, ast.FunctionDef | ast.AsyncFunctionDef | None, ast.Raise]]:
        if isinstance(node, ast.ClassDef):
            cls, qualname = node.name, node.name
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            fn = node
            qualname = f"{qualname}.{node.name}" if qualname else node.name
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            called = node.exc.func
            name = called.id if isinstance(called, ast.Name) else getattr(called, "attr", "")
            if (
                name.endswith("Error")
                and node.exc.args
                and isinstance(node.exc.args[0], ast.JoinedStr)
            ):
                yield qualname, cls, fn, node
        for child in ast.iter_child_nodes(node):
            yield from walk(child, cls, fn, qualname)

    yield from walk(tree, None, None, "")


def _called_names(*, methods_only: bool) -> frozenset[str]:
    """Every callable name called under ``theurian/mcp/`` and ``theurian/daemon/``.

    ``methods_only`` reads ``receiver.name(...)`` alone. That distinction is
    load-bearing for ``register``: ``mcp/tools.py`` defines one and
    ``daemon/runner.py`` calls it by bare name, which says nothing at all about
    :meth:`ProjectRegistry.register`.
    """
    return _names_called_in(_scanned_modules(), methods_only=methods_only)


def _names_called_in(modules: Mapping[str, ast.Module], *, methods_only: bool) -> frozenset[str]:
    """The key applied to a given set of modules, so a control can hand it synthetic ones.

    Split from :func:`_called_names` for the reason :func:`_sites_in` is split
    from :func:`_resolved_path_raise_sites`, and the split is a fix rather than a
    tidy-up (verdict pass, adversarial MEDIUM). Every citation this key serves
    asserts an **absence** -- ``<name> not in called`` -- so a key that answered
    the empty set satisfied all of them, and blinding it either way left the whole
    suite green. There was no control because there was no seam to hand a
    synthetic module to; this is that seam, and
    :func:`test_the_out_of_reach_key_sees_a_serving_module_that_calls_the_name`
    is the control.
    """
    called: set[str] = set()
    for module, tree in modules.items():
        if Path(module).parts[0] not in _SERVING_PACKAGES:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
            elif isinstance(node.func, ast.Name) and not methods_only:
                called.add(node.func.id)
    return frozenset(called)


def test_every_resolved_path_refusal_has_a_recorded_disposition() -> None:
    """The plant axis, made fail-closed (round one, code review HIGH).

    The sweep's plants were five hand-written functions, so a raise site that
    interpolates the operator's resolved layout and that no plant reaches was
    covered by nothing -- and the module docstring claimed the opposite. This
    derives the candidate sites from the source and requires each one to carry an
    answer in :data:`DISPOSITIONS`: a plant that drives it, a test that holds it,
    a conversion that absorbs it, or a recorded reason no registered tool reaches
    it.

    **The population is "sites matching the key at scan time", not a fixed list.**
    A member can leave it -- ``verify_state_provenance`` did, when its refusal
    became a constant -- and that is the fix working rather than the record going
    stale. :data:`DEPARTED` is where such a member's guard stays recorded, and
    :func:`test_a_departed_raise_site_has_really_left_the_key` asserts the
    departure rather than assuming it.

    What this test cannot do is prove a disposition is *true*; what it does is
    make an undispositioned site impossible to ship quietly.
    """
    sites = _resolved_path_raise_sites()

    assert sites, (
        "the key matched no raise site at all, which is not a state this codebase has "
        "ever been in -- `ProjectPaths` alone carries several. A key that matches "
        "nothing satisfies every assertion below vacuously."
    )
    undisposed = sorted(site.key for site in sites if site.key not in DISPOSITIONS)
    assert not undisposed, (
        f"these raise sites interpolate the operator's resolved filesystem layout and "
        f"nothing in this file says what keeps them off an MCP response (GHSA-97q9). "
        f"Add a plant to `PLANTS`, or an entry to `DISPOSITIONS` naming the guard that "
        f"already holds them: {undisposed}"
    )
    stale = sorted(set(DISPOSITIONS) - {site.key for site in sites})
    assert not stale, (
        f"these dispositions name raise sites the key no longer matches. If the "
        f"interpolation was removed, move the entry to `DEPARTED`; if the site was "
        f"renamed, rename the key: {stale}"
    )


def test_a_departed_raise_site_has_really_left_the_key() -> None:
    """A record of a fix is a claim, and this is the claim being checked.

    :data:`DEPARTED` says a site used to interpolate a resolved path and no longer
    does. Left unchecked that is prose: an interpolation reintroduced at the same
    site would rejoin the population, find its own key already spoken for, and be
    read as dispositioned. This is what goes RED instead.
    """
    measured = {site.key for site in _resolved_path_raise_sites()}

    returned = sorted(measured & set(DEPARTED))
    assert not returned, (
        f"a raise site recorded as having stopped interpolating a resolved path is "
        f"interpolating one again, and its `DEPARTED` entry describes a world that no "
        f"longer holds: {returned}"
    )


def test_the_population_key_finds_a_new_site_and_not_the_designed_publications() -> None:
    """The key's own control, over the same function the population test calls.

    An absence result is worth what its key is worth, and this key has two ways to
    be quietly wrong. It can match nothing -- in which case every site is
    "dispositioned" because there are none -- or it can match the two things this
    codebase publishes on purpose: a value quoted with ``!r``, which is a caller's
    own string and not a path, and an **unresolved** root, which is the registered
    spelling ``project.list`` hands out by design.

    Driven through :func:`_sites_in` with synthetic modules, so the control governs
    the shipped key rather than a second copy of it.
    """
    types = _Types()
    for tree in _scanned_modules().values():
        types.add_module(tree)

    found = _sites_in(
        "application/new_guard.py",
        ast.parse(
            "def _new_guard(paths: ProjectPaths) -> None:\n"
            "    raise ProjectError(f'{paths.state} cannot be used')\n"
        ),
        types,
    )
    assert [site.key for site in found] == ["application/new_guard.py::_new_guard#1"], (
        f"a new raise site interpolating a resolved path went unseen, so the population "
        f"test cannot notice the next one either: {found}"
    )
    assert found[0].interpolated == ("paths.state",)

    quoted = _sites_in(
        "application/new_guard.py",
        ast.parse(
            "def _new_guard(name: str) -> None:\n"
            "    raise ProjectError(f'{name!r} is not a usable filename')\n"
        ),
        types,
    )
    assert quoted == (), (
        f"the key matched a `!r`-quoted caller string, which carries no layout and is "
        f"what every `not a usable filename` arm publishes: {quoted}"
    )

    registered = _sites_in(
        "application/new_guard.py",
        ast.parse(
            "from pathlib import Path\n"
            "def _new_guard(root: Path) -> None:\n"
            "    raise ProjectError(f'{root} is not registered')\n"
        ),
        types,
    )
    assert registered == (), (
        f"the key matched an unresolved root, which is the registered spelling "
        f"`project.list` publishes by design -- a key that bans it is a key that has "
        f"to be carved out, and stops being universal: {registered}"
    )


@pytest.mark.parametrize("key", sorted(DISPOSITIONS | DEPARTED))
def test_a_disposition_cites_only_things_that_exist(key: str) -> None:
    """The citations are checked, so a table of names cannot rot into fiction.

    A disposition is an argument, and the parts of an argument this file can reach
    are checked here: a plant name that is really a key in :data:`PLANTS`, a test
    that is really a function in a file under ``tests/``, and a converter that is
    really an attribute of an importable module. A renamed plant or a deleted test
    fails here rather than leaving a sentence that reads like coverage.
    """
    disposition = (DISPOSITIONS | DEPARTED)[key]

    assert disposition.because.strip(), f"{key}: a disposition with no argument is a label"
    plant_keys = {plant.key for plant in PLANTS}
    unknown = sorted(set(disposition.plants) - plant_keys)
    assert not unknown, f"{key}: names plants this file does not define: {unknown}"

    for citation in disposition.tests:
        relative, _, name = citation.partition("::")
        path = _TESTS_ROOT / relative
        assert path.is_file(), f"{key}: cites a test file that is not there: {citation}"
        assert f"def {name}(" in path.read_text(encoding="utf-8"), (
            f"{key}: cites a test its own file does not define: {citation}"
        )

    if disposition.converter is not None:
        module_name, _, attribute = disposition.converter.partition(":")
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), (
            f"{key}: cites a converter that is not there: {disposition.converter}"
        )

    if disposition.no_call_from_mcp is not None:
        called = _called_names(methods_only=False)
        assert disposition.no_call_from_mcp not in called, (
            f"{key}: `{disposition.no_call_from_mcp}` is now called from `theurian/mcp/` "
            f"or `theurian/daemon/`, so the reason this site is outside the class no "
            f"longer holds -- it needs a plant or a guard"
        )

    if disposition.no_method_call_from_mcp is not None:
        called = _called_names(methods_only=True)
        assert disposition.no_method_call_from_mcp not in called, (
            f"{key}: `.{disposition.no_method_call_from_mcp}(...)` is now called from "
            f"`theurian/mcp/` or `theurian/daemon/`, so the reason this site is outside "
            f"the class no longer holds -- it needs a plant or a guard"
        )


#: Two dispositions whose whole argument is "no serving module calls this", one
#: per spelling of the key. Named rather than searched for, so the control below
#: fails if either argument is rewritten to rest on something else -- a control
#: that hunted for *any* such entry would quietly stop controlling anything the
#: day the last one changed shape.
_OUT_OF_REACH: Final = {
    False: (
        "application/project_service.py::initialize_project#1",
        "initialize_project",
    ),
    True: (
        "application/project_service.py::ProjectRegistry.ids_for_root#1",
        "ids_for_root",
    ),
}


@pytest.mark.parametrize("methods_only", [False, True], ids=["any-call", "method-call"])
def test_the_out_of_reach_key_sees_a_serving_module_that_calls_the_name(
    methods_only: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control the ``no_call_from_mcp`` citations never had (verdict pass, MEDIUM).

    Four dispositions rest on "nothing under ``theurian/mcp/`` or
    ``theurian/daemon/`` calls this", and every one of them is asserted as an
    **absence**. An absence result is worth exactly what its key is worth, and
    this key could be blinded in either direction with the whole suite still
    green: answer the empty set and every citation passes; answer every name and
    the ``methods_only`` distinction -- the one thing that keeps ``register``
    honest -- stops existing without a single case noticing.

    So this hands the shipped key a synthetic serving module that calls the name
    a disposition says is uncalled, in both spellings, and then re-runs the
    citation test itself over the same doctored population. Three separate ways to
    fail, in order of how badly a blinded key would have to be broken: the key
    sees the call, the key still tells a bare call from a method call, and the
    citation test built on it reports the site as no longer out of reach.

    Driven through :func:`_names_called_in` and then through
    :func:`test_a_disposition_cites_only_things_that_exist` itself, rather than a
    second copy of either, so what is controlled is the code that ships.
    """
    key, name = _OUT_OF_REACH[methods_only]
    call = f"receiver.{name}(root)" if methods_only else f"{name}(root)"
    synthetic = ast.parse(f"def _new_tool(root):\n    return {call}\n")

    seen = _names_called_in({"mcp/new_tool.py": synthetic}, methods_only=methods_only)
    outside_the_serving_packages = _names_called_in(
        {"application/new_tool.py": synthetic}, methods_only=methods_only
    )
    bare_under_the_method_key = _names_called_in(
        {"mcp/new_tool.py": ast.parse(f"def _new_tool(root):\n    return {name}(root)\n")},
        methods_only=True,
    )

    assert name in seen, (
        f"the key did not see `{call}` in a module under `theurian/mcp/`, so every "
        f"disposition resting on it is asserting an absence nothing could contradict"
    )
    assert name not in outside_the_serving_packages, (
        f"the key counted a call from outside `{_SERVING_PACKAGES}`, so 'no serving "
        f"module calls this' would fail over the CLI, which calls all four by design"
    )
    assert name not in bare_under_the_method_key, (
        "the method-only key counted a bare call, which is the distinction that keeps "
        "`daemon/runner.py`'s call to `mcp/tools.py`'s own `register` from reading as a "
        "call to `ProjectRegistry.register`"
    )

    # Patched in this module's own globals, which is where `_called_names` looks
    # the scanner up -- so what runs below is the shipped citation test over a
    # doctored population, not a re-implementation of its last two clauses.
    monkeypatch.setitem(globals(), "_scanned_modules", lambda: {"mcp/new_tool.py": synthetic})

    with pytest.raises(AssertionError, match=name):
        test_a_disposition_cites_only_things_that_exist(key)
